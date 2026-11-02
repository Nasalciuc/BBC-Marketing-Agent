"""
The post workflow (one LangGraph thread per post, thread_id "post:<campaign_id>").

    START → render ─(mode=render)→ END
               └─(mode=send)→ upload → publish ─(locked)→ END
                                          │
                                          ▼
                              review ⏸ interrupt({campaign_id, version})
                     approve │        │ reject        │ edit (new caption)
                             ▼        ▼               ▼
                         approved  rejected        edited ─(invalid)→ review
                             │        │               └─(valid, version+1)→ publish
                            END      END

State holds only small JSON values (paths, ids, the caption) — never image bytes. The services
(store, Telegram, renderer, storage) come in through the run context, which is not checkpointed,
so a thread started by the CLI can be resumed by the webhook.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, interrupt

from content_system.manifest import (
    CATEGORY,
    PlannedPost,
    Week,
    caption_from_edit,
    caption_problems,
    review_line,
)
from content_system.store import DECIDABLE, EffectBusy
from content_system.telegram import TelegramError, is_not_modified, review_keyboard, status_keyboard
from services.design_system import PostDesign, Style

log = logging.getLogger("bbc.content.graph")

Mode = Literal["render", "send"]


class PostState(TypedDict, total=False):
    campaign_id: str
    week: str
    manifest_sha: str
    plan_sha: str             # this post's spec + design + caption, to spot manifest edits
    index: int
    mode: Mode
    day: str                  # ISO publishing date
    scheduled_day: str        # "Wed"
    stem: str
    spec: dict[str, Any]      # PostSpec as JSON
    design: dict[str, Any]    # PostDesign as JSON
    caption: str              # current version's caption, without the review line
    version: int
    image_path: str
    image_sha256: str
    tone: str
    fare: int | None
    text_bottom: float
    image_url: str
    tg_file_id: str
    review_chat_id: int
    review_message_id: int
    decision: dict[str, Any]
    status: str               # planned | rendered | in_review | approved | rejected | locked
    last_error: str


@dataclass
class Services:
    store: Any                                                # MemoryStore | PgStore
    render: Callable[[PostDesign, Path, str, str], dict]     # sync; runs in a worker thread
    output_root: Path
    put_image: Callable[[bytes, str], str] | None = None     # sync; returns the public URL
    get_image: Callable[[str], bytes] | None = None          # sync; downloads a public URL
    telegram: Any = None                                      # TelegramAPI
    chat_id: int | str | None = None


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TelegramError):
        return exc.transient
    status = getattr(exc, "status", None)                     # storage3.StorageApiError
    if isinstance(status, (int, str)) and str(status).isdigit() and int(status) >= 500:
        return True
    try:
        import psycopg

        if isinstance(exc, psycopg.OperationalError):
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(exc, (EffectBusy, httpx.TransportError, ConnectionError, TimeoutError))


RETRY = RetryPolicy(max_attempts=4, initial_interval=1.0, backoff_factor=2.0, max_interval=10.0,
                    retry_on=is_transient)


def thread_id(campaign_id: str) -> str:
    return f"post:{campaign_id}"


def thread_config(campaign_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id(campaign_id)}}


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def plan_sha(post: PlannedPost) -> str:
    """Fingerprint of everything the manifest says about this post, the photo's bytes included."""
    photo = _read(Path(post.design.background))
    plan = {"spec": post.spec.model_dump(mode="json"), "caption": post.caption,
            "design": post.design.model_dump(mode="json", exclude={"background"}),
            "photo": hashlib.sha256(photo).hexdigest() if photo is not None else None}
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def initial_state(week: Week, post: PlannedPost, mode: Mode) -> PostState:
    return {
        "campaign_id": post.campaign_id, "week": week.name, "manifest_sha": week.sha256,
        "plan_sha": plan_sha(post),
        "index": post.index, "mode": mode, "day": post.spec.day.isoformat(),
        "scheduled_day": post.scheduled_day, "stem": post.stem,
        "spec": post.spec.model_dump(mode="json"), "design": post.design.model_dump(mode="json"),
        "caption": post.caption, "version": 1, "status": "planned", "last_error": "",
    }


def day_label(state: PostState) -> str:
    return date.fromisoformat(state["day"]).strftime("%a %d %b")


def campaign_row(state: PostState) -> dict[str, Any]:
    from services.pricing_engine import format_price

    spec, style, fare = state["spec"], state["design"]["style"], state.get("fare")
    return {
        "campaign_id": state["campaign_id"],
        "event_name": spec["name"],
        "city": spec["city"],
        "dates_start": spec.get("dates_start"),
        "dates_end": spec.get("dates_end"),
        "category": spec.get("category") or CATEGORY[Style(style)],
        "route": spec["route"],
        "price": format_price(fare) if fare else None,
        "price_raw": fare,
        "image_url": state["image_url"],
        "caption": state["caption"],
        "event_context": f"{state['week']} · publish {state['day']} · design v4 {style} · "
                         f"content_system v{state['version']}",
        "scheduled_day": state["scheduled_day"],
        "design_style": style,
        "design_tone": state["tone"],
    }


# ── helpers ───────────────────────────────────────────────────────────────────

async def _render(state: PostState, s: Services) -> dict[str, Any]:
    out_dir = s.output_root / state["week"]
    return await asyncio.to_thread(s.render, PostDesign(**state["design"]), out_dir, state["stem"], state["caption"])


async def _local_image(state: PostState, s: Services) -> tuple[bytes, dict[str, Any]]:
    """The rendered JPEG; rendered again when this machine doesn't have it (resume elsewhere)."""
    data = await asyncio.to_thread(_read, Path(state["image_path"]))
    if data is not None and hashlib.sha256(data).hexdigest() == state["image_sha256"]:
        return data, {}
    info = await _render(state, s)
    return await asyncio.to_thread(Path(info["image_path"]).read_bytes), info


async def _mark(s: Services, key: str, chat_id: int, message_id: int, text: str) -> None:
    """Swap the buttons on a review message for an inert status button. Cosmetic: never fails a post."""
    async def edit() -> str:
        try:
            await s.telegram.call("editMessageReplyMarkup", {
                "chat_id": chat_id, "message_id": message_id, "reply_markup": status_keyboard(text)})
        except TelegramError as e:
            if e.transient:
                raise
            if not is_not_modified(e):
                log.warning("could not update the buttons on %s/%s: %s", chat_id, message_id, e)
        return "ok"

    await s.store.once(key, edit)


def _certainly_not_sent(exc: Exception) -> bool:
    """Telegram refused the request (4xx): no message exists. A timeout or a 5xx proves nothing."""
    return isinstance(exc, TelegramError) and not exc.transient


async def _send_review(state: PostState, s: Services, caption: str) -> str:
    cid, v = state["campaign_id"], state["version"]
    chat_id = state.get("review_chat_id") or s.chat_id       # every version goes to the chat that has v1
    data = {"chat_id": chat_id, "caption": caption, "reply_markup": review_keyboard(cid, v)}
    try:
        msg = await s.telegram.call("sendPhoto", {**data, "photo": state.get("tg_file_id") or state["image_url"]})
    except TelegramError as e:
        if e.transient:
            raise
        # Telegram could not fetch the URL (or the file id expired): send the bytes themselves.
        log.warning("sendPhoto by reference failed for %s (%s) — uploading the file", cid, e.description)
        local = await asyncio.to_thread(_read, Path(state["image_path"]))
        if local is None or hashlib.sha256(local).hexdigest() != state["image_sha256"]:
            local = await asyncio.to_thread(s.get_image, state["image_url"])
        msg = await s.telegram.call("sendPhoto", data, files={"photo": (f"{cid}.jpg", local, "image/jpeg")})
    return f"{msg['chat']['id']}|{msg['message_id']}|{msg['photo'][-1]['file_id']}"


# ── nodes ─────────────────────────────────────────────────────────────────────

async def render_node(state: PostState, runtime: Runtime[Services]) -> dict[str, Any]:
    info = await _render(state, runtime.context)
    return {**info, "status": "rendered"}


async def upload_node(state: PostState, runtime: Runtime[Services]) -> dict[str, Any]:
    s, cid = runtime.context, state["campaign_id"]
    current = await s.store.campaign_status(cid)
    if current is not None and current not in DECIDABLE:
        log.warning("%s is %s — the content system never overwrites it", cid, current)
        return {"status": "locked", "last_error": f"campaign is {current}: never overwritten"}
    data, update = await _local_image(state, s)
    sha = hashlib.sha256(data).hexdigest()
    path = f"deals/{cid}/vertical-{sha[:12]}.jpg"     # a new image never lands on an old file
    url = await s.store.once(f"upload:{cid}:{sha[:16]}", lambda: asyncio.to_thread(s.put_image, data, path))
    return {**update, "image_url": url}


async def publish_node(state: PostState, runtime: Runtime[Services]) -> dict[str, Any]:
    s, cid, v = runtime.context, state["campaign_id"], state["version"]
    if not await s.store.upsert_campaign(campaign_row(state)):
        current = await s.store.campaign_status(cid)
        log.warning("%s is %s — the content system never overwrites it", cid, current)
        return {"status": "locked", "last_error": f"campaign is {current}: never overwritten"}
    caption = state["caption"] + review_line(cid, v)
    ref = await s.store.once(f"review:{cid}:v{v}", lambda: _send_review(state, s, caption),
                             forget_on=_certainly_not_sent)
    chat, message, file_id = ref.split("|", 2)
    chat_id, message_id = int(chat), int(message)
    await s.store.add_message(chat_id, message_id, cid, v, "review")
    await s.store.set_review(cid, chat_id, message_id)
    old = state.get("review_message_id")
    if v > 1 and old and old != message_id:
        await _mark(s, f"supersede:{cid}:v{v - 1}", state["review_chat_id"], old, f"↪ Replaced by v{v}")
    return {"review_chat_id": chat_id, "review_message_id": message_id, "tg_file_id": file_id,
            "status": "in_review", "last_error": ""}


def review_node(state: PostState) -> dict[str, Any]:
    decision = interrupt({"campaign_id": state["campaign_id"], "version": state["version"],
                          "last_error": state.get("last_error", "")})
    return {"decision": decision}


async def _decided(state: PostState, s: Services, status: str, label: str) -> dict[str, Any]:
    """Close the post. Safe to run again: a retry finds its own earlier write and carries on."""
    cid, v = state["campaign_id"], state["version"]
    key = f"status:{cid}:v{v}:{status}"
    if not await s.store.decide(cid, status, state["caption"]):
        current = await s.store.campaign_status(cid)
        if current != status:   # closed by something else first: report it, change nothing
            await _mark(s, key, state["review_chat_id"], state["review_message_id"],
                        f"Already {current} — nothing changed")
            return {"status": current if current in ("approved", "rejected") else "locked",
                    "last_error": f"campaign was already {current}"}
    await _mark(s, key, state["review_chat_id"], state["review_message_id"], label)
    return {"status": status, "last_error": ""}


async def approved_node(state: PostState, runtime: Runtime[Services]) -> dict[str, Any]:
    return await _decided(state, runtime.context, "approved", f"✅ Approved · v{state['version']} · {day_label(state)}")


async def rejected_node(state: PostState, runtime: Runtime[Services]) -> dict[str, Any]:
    return await _decided(state, runtime.context, "rejected", f"❌ Rejected · v{state['version']}")


def edited_node(state: PostState) -> dict[str, Any]:
    cid = state["campaign_id"]
    caption = caption_from_edit(state["decision"].get("caption", ""), Style(state["design"]["style"]),
                                state["spec"]["caption_city"])
    problems = caption_problems(caption, cid)
    if caption.strip() == state["caption"].strip():
        problems.append("same text as the current version")
    if problems:
        return {"last_error": "; ".join(problems)}
    # next_version: after an edit that failed half-way, its number is never given to another text
    version = max(state["version"] + 1, int(state["decision"].get("next_version") or 0))
    return {"caption": caption, "version": version, "last_error": ""}


def _after_review(state: PostState) -> str:
    action = (state.get("decision") or {}).get("action")
    routes = {"approve": "approved", "reject": "rejected", "edit": "edited"}
    if action not in routes:
        raise ValueError(f"unknown review decision {state.get('decision')!r}")
    return routes[action]


def build_graph(checkpointer: Any, *, retry: RetryPolicy = RETRY):
    g = StateGraph(PostState, context_schema=Services)
    g.add_node("render", render_node)
    g.add_node("upload", upload_node, retry_policy=retry)
    g.add_node("publish", publish_node, retry_policy=retry)
    g.add_node("review", review_node)
    g.add_node("approved", approved_node, retry_policy=retry)
    g.add_node("rejected", rejected_node, retry_policy=retry)
    g.add_node("edited", edited_node)
    g.add_edge(START, "render")
    g.add_conditional_edges("render", lambda s: "upload" if s["mode"] == "send" else END, ["upload", END])
    g.add_conditional_edges("upload", lambda s: END if s["status"] == "locked" else "publish", ["publish", END])
    g.add_conditional_edges("publish", lambda s: "review" if s["status"] == "in_review" else END, ["review", END])
    g.add_conditional_edges("review", _after_review, ["approved", "rejected", "edited"])
    g.add_conditional_edges("edited", lambda s: "review" if s.get("last_error") else "publish", ["review", "publish"])
    g.add_edge("approved", END)
    g.add_edge("rejected", END)
    return g.compile(checkpointer=checkpointer)
