"""
"✅ Aprob …" / "❌ Resping …" cards. The assistant (and the week's send, and ✅ De aprobat) only
proposes: the card lists the posts with the exact versions shown, and nothing changes until Betty
taps. A tap decides each post through engine.decide on the version that was on the card, so a post
edited in the meantime is refused with the reason. Every card is one group: a tap on any of its
buttons closes the whole card (no second decision from the same card).

callback_data: "ca:y:<proposal>" — do what this button says; "ca:n:<group>" — not now.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from content_system.assistant.posts import PostInfo
from content_system.engine import decide
from content_system.store import LeaseBusy

log = logging.getLogger("bbc.content.assistant.proposals")
_CALLBACK = re.compile(r"^ca:([yn]):([0-9a-f]{12})$")
VERB = {"approve": ("✅", "Aprob", "aprobată"), "reject": ("❌", "Resping", "respinsă")}
REFUSED = {
    "used": "Cardul acesta a fost deja folosit — nimic nou nu s-a schimbat",
    "dismissed": "Cardul acesta e închis — nimic nou nu s-a schimbat. Cere-mi altul dacă e nevoie",
    "expired": "Cardul a expirat (48 h) — cere-mi altul",
    "missing": "Nu mai găsesc acest card — cere-mi altul",
}


def parse(data: str) -> tuple[str, str] | None:
    m = _CALLBACK.match(data or "")
    return (m.group(1), m.group(2)) if m else None


def _id() -> str:
    return uuid.uuid4().hex[:12]


async def propose(store: Any, telegram: Any, chat_id: int | str, action: str, posts: list[PostInfo],
                  intro: str = "") -> str:
    """Send the card. Returns one line for the assistant about what the card holds."""
    icon, verb, _ = VERB[action]
    waiting = [p for p in posts if p.waiting]
    if not waiting:
        return "none of these posts is waiting for a decision: no card sent"
    group = _id()
    items = [{"campaign_id": p.campaign_id, "version": p.version, "label": p.short} for p in waiting]
    rows: list[list[dict[str, str]]] = []
    if len(waiting) > 1:
        for p, item in zip(waiting, items, strict=True):
            pid = _id()
            await store.add_proposal(pid, group, chat_id, action, [item])
            rows.append([{"text": f"{icon} {p.short} · v{p.version}", "callback_data": f"ca:y:{pid}"}])
    every = _id()
    await store.add_proposal(every, group, chat_id, action, items)
    label = f"{icon} {verb} toate ({len(waiting)})" if len(waiting) > 1 else f"{icon} Da, {verb.lower()}"
    rows.append([{"text": label, "callback_data": f"ca:y:{every}"}])
    rows.append([{"text": "✖ Nu acum", "callback_data": f"ca:n:{group}"}])
    lines = "\n".join(f"• {i['label']} · v{i['version']}" for i in items)
    text = (intro + "\n\n" if intro else "") + (
        f"{icon} {verb} {'aceste postări' if len(waiting) > 1 else 'postarea'}?\n{lines}\n\n"
        "Exact versiunile de mai sus. Nimic nu se schimbă până nu apeși.")
    await telegram.call("sendMessage", {"chat_id": chat_id, "text": text,
                                        "reply_markup": {"inline_keyboard": rows}})
    return f"card sent: {verb.lower()} " + ", ".join(f"{i['campaign_id']} v{i['version']}" for i in items) + \
        " — nothing changes until she taps"


async def _answer(telegram: Any, callback_id: str, text: str) -> None:
    try:
        await telegram.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
    except Exception as e:  # noqa: BLE001 — the toast is cosmetic
        log.warning("answerCallbackQuery failed: %s", e)


async def _close_card(telegram: Any, message: dict, text: str) -> None:
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None or message.get("message_id") is None:
        return
    try:
        await telegram.call("editMessageText", {"chat_id": chat_id, "message_id": message["message_id"],
                                                "text": text, "reply_markup": {"inline_keyboard": []}})
    except Exception as e:  # noqa: BLE001 — the decisions are made; the card text is cosmetic
        log.warning("could not update the card: %s", e)


async def handle_card(lv: Any, cb: dict, update_id: int | None) -> None:
    """A tap on a card. Never raises into the webhook."""
    tg, store = lv.services.telegram, lv.services.store
    callback_id, message = cb.get("id", ""), cb.get("message") or {}
    parsed = parse(cb.get("data", ""))
    if parsed is None:
        await _answer(tg, callback_id, "Buton necunoscut")
        return
    kind, ident = parsed
    key = f"tg:update:{update_id}" if update_id is not None else None
    try:
        if key and not await store.claim(key):
            await _answer(tg, callback_id, "")              # Telegram delivered this tap twice
            return
        if kind == "n":
            await store.dismiss_group(ident)
            await _close_card(tg, message, "✖ Lăsat așa — nimic schimbat.")
            await _answer(tg, callback_id, "Nimic schimbat")
            return
        outcome, row = await store.take_proposal(ident)
    except Exception:
        log.exception("card tap failed before deciding anything")
        if key:
            await store.release(key)
        await _answer(tg, callback_id, "⚠️ N-a mers — apasă din nou")
        return
    if outcome != "taken" or row is None:
        await _answer(tg, callback_id, REFUSED.get(outcome, REFUSED["missing"]))
        return
    action = row["action"]
    icon, _, done = VERB[action]
    results, lines = [], []
    for item in row["items"]:
        cid, version = item["campaign_id"], int(item["version"])
        try:
            out = await decide(lv.graph, lv.services, cid, action, version=version,
                               lock_timeout=lv.settings.lock_timeout_s)
            ok, why = out.ok, out.message
        except LeaseBusy:
            ok, why = False, "another tap on this post is still running — use the post's own buttons"
        except Exception:
            log.exception("card decision failed: %s %s v%s", action, cid, version)
            ok, why = False, "didn't go through — use the post's own buttons"
        results.append({"campaign_id": cid, "version": version, "ok": ok, "message": why})
        lines.append(f"{icon if ok else '⚠️'} {item.get('label') or cid} · v{version} — {done if ok else why}")
    try:
        await store.set_proposal_result(ident, results)
    except Exception:
        log.exception("could not store the card's result")
    n_ok = sum(r["ok"] for r in results)
    await _close_card(tg, message, "\n".join(lines))
    await _answer(tg, callback_id, f"{icon} {n_ok} din {len(results)} {done if len(results) == 1 else done[:-1] + 'e'}")
