"""
The assistant's tools. Each one reads the posts or makes one bounded change; none of them approves,
rejects or publishes. A caption change goes through engine.decide("edit") — a new version with its own
buttons, the same path as a typed caption — and an approval is only ever a card that Betty taps.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain.tools import ToolRuntime, tool

from content_system.assistant.posts import PostInfo, find, latest_week, overview, recent, send_card
from content_system.assistant.proposals import propose
from content_system.assistant.texts import NO_POSTS
from content_system.assistant.usage import Meter
from content_system.assistant.writer import Generate, rewrite
from content_system.engine import decide
from content_system.store import LeaseBusy

log = logging.getLogger("bbc.content.assistant.tools")


@dataclass
class Ctx:
    """Everything one message to the assistant needs. Not checkpointed: built for every message."""
    lv: Any                          # bridge.Live: settings, the post graph, the services
    chat_id: int
    user_id: int | None
    text: str                        # her message, exactly as she sent it
    focus: str | None                # the post she replied to, if any
    update_id: int | None
    meter: Meter
    writer: Generate
    owner_chat_id: str = ""
    done: list[str] = field(default_factory=list)       # what changed, in order (logs, tests)
    asked: bool = False                                  # one note to Scaler per message
    edited: set[str] = field(default_factory=set)        # posts given a new version by this message
    _posts: list[PostInfo] | None = None

    async def posts(self) -> list[PostInfo]:
        if self._posts is None:
            self._posts = await recent(self.lv.graph, self.lv.services.store)
        return self._posts

    async def post(self, campaign_id: str) -> PostInfo | None:
        cid = campaign_id.strip()
        return next((p for p in await self.posts() if p.campaign_id == cid), None)


def _unknown(campaign_id: str) -> str:
    return f"no post {campaign_id!r} among the posts sent for review — use find_post or week_overview"


async def _edit(ctx: Ctx, p: PostInfo, caption: str, how: str) -> str:
    if p.campaign_id in ctx.edited:
        return f"{p.campaign_id} already got a new version from this message — let her look at it first"
    try:   # on the version that was read: a newer one (her ✏️ reply meanwhile) is never overwritten
        out = await decide(ctx.lv.graph, ctx.lv.services, p.campaign_id, "edit", caption=caption,
                           version=p.version, lock_timeout=ctx.lv.settings.lock_timeout_s)
    except LeaseBusy:
        return f"{p.campaign_id}: another change on this post is running — nothing changed, ask her to try again"
    ctx._posts = None
    if not out.ok:
        return f"{p.campaign_id}: nothing changed — {out.message}"
    ctx.edited.add(p.campaign_id)
    version = out.view.version if out.view else "?"
    ctx.done.append(f"edit:{p.campaign_id}:v{version}:{how}")
    return (f"{p.short}: v{version} is in the chat with its own buttons, waiting for her decision; "
            f"v{p.version} lost its buttons. Nothing is approved.")


@tool
async def week_overview(runtime: ToolRuntime[Ctx]) -> str:
    """The posts of the newest week: day, name, version, status and id, in publishing order."""
    return overview(await runtime.context.posts()) or NO_POSTS


@tool
async def find_post(query: str, runtime: ToolRuntime[Ctx]) -> str:
    """Find posts by day ("vineri", "Friday"), place, name, route or id. Returns every match, with the
    beginning of its caption. Use it whenever it is not certain which post she means."""
    posts = await runtime.context.posts()
    found = find(posts, query)
    if not found:
        week = latest_week(posts)
        return f"no post matches {query!r}. This week: " + "; ".join(f"{p.short} [{p.campaign_id}]" for p in week)
    return "\n".join(f"{p.line()}\n  route {p.route} · caption: {p.body[:280]}" for p in found)


@tool
async def show_post(campaign_id: str, runtime: ToolRuntime[Ctx]) -> str:
    """Send one post again in this chat — photo, caption and, while it waits, its buttons."""
    ctx = runtime.context
    p = await ctx.post(campaign_id)
    if p is None:
        return _unknown(campaign_id)
    await send_card(ctx.lv.services.telegram, ctx.lv.services.store, ctx.chat_id, p)
    ctx.done.append(f"show:{p.campaign_id}")
    return f"{p.short} v{p.version} sent again in the chat" + (" with its buttons" if p.waiting else "")


@tool
async def rewrite_caption(campaign_id: str, request: str, runtime: ToolRuntime[Ctx]) -> str:
    """Rewrite the caption of one waiting post as she asks, in her words ("shorter", "without the part
    about the south of France"). The result is a new version with its own buttons; she still decides.
    Only for the caption text: never for the image, the fare, the route or the day."""
    ctx = runtime.context
    p = await ctx.post(campaign_id)
    if p is None:
        return _unknown(campaign_id)
    if not p.waiting:
        return f"{p.short} is {p.status}: its caption can't change any more"
    result = await rewrite(ctx.writer, p, request, ctx.meter, her_message=ctx.text)
    if not result.ok:
        return f"{p.short}: nothing changed — two drafts failed the checks: {'; '.join(result.problems)}"
    return await _edit(ctx, p, result.body, "rewrite")


@tool
async def use_exact_text(campaign_id: str, text: str, runtime: ToolRuntime[Ctx]) -> str:
    """Make the text she sent, exactly as she wrote it, the new caption of one waiting post (a new
    version to approve). Only when her message holds the whole new text; copy it unchanged."""
    ctx = runtime.context
    p = await ctx.post(campaign_id)
    if p is None:
        return _unknown(campaign_id)
    if not p.waiting:
        return f"{p.short} is {p.status}: its caption can't change any more"
    wanted = text.strip()
    if not wanted or wanted not in ctx.text:
        return ("refused: that text is not, character for character, in her message. Use rewrite_caption "
                "for a change she described, or ask her to send the exact text")
    return await _edit(ctx, p, wanted, "exact")


async def _propose(ctx: Ctx, action: str, campaign_ids: list[str]) -> str:
    posts = await ctx.posts()
    if any(c.strip().lower() in ("all", "toate") for c in campaign_ids):
        chosen = [p for p in latest_week(posts) if p.waiting]
        missing: list[str] = []
    else:
        by_id = {p.campaign_id: p for p in posts}
        chosen = [by_id[c.strip()] for c in campaign_ids if c.strip() in by_id]
        missing = [c for c in campaign_ids if c.strip() not in by_id]
    line = await propose(ctx.lv.services.store, ctx.lv.services.telegram, ctx.chat_id, action, chosen)
    if chosen and "card sent" in line:
        ctx.done.append(f"propose:{action}:" + ",".join(p.campaign_id for p in chosen if p.waiting))
    return line + (f"; unknown ids: {', '.join(missing)}" if missing else "")


@tool
async def propose_approve(campaign_ids: list[str], runtime: ToolRuntime[Ctx]) -> str:
    """Show her a card to approve these posts in the versions she has seen; ["all"] = every waiting
    post of the week (the card then has one button per post and one for all). You never approve:
    only her tap on the card does. Use it for "aprob", "ok", "da, merge"."""
    return await _propose(runtime.context, "approve", campaign_ids)


@tool
async def propose_reject(campaign_ids: list[str], runtime: ToolRuntime[Ctx]) -> str:
    """Show her a card to reject these posts. You never reject: only her tap on the card does."""
    return await _propose(runtime.context, "reject", campaign_ids)


@tool
async def save_idea(idea: str, runtime: ToolRuntime[Ctx]) -> str:
    """Save an idea for future posts (a place, an event, an angle) in the list for the weekly plan."""
    ctx = runtime.context
    if not idea.strip():
        return "empty idea: nothing saved"
    n = await ctx.lv.services.store.add_idea(ctx.chat_id, ctx.user_id, idea.strip()[:4000])
    ctx.done.append(f"idea:{n}")
    return "saved in the list for the weekly plan"


@tool
async def ask_scaler(request: str, runtime: ToolRuntime[Ctx], campaign_id: str = "") -> str:
    """Pass to Scaler what you can't do: the image, the fare, the route, the day, publishing, a new
    post, a problem. Write her request clearly in Romanian. Then tell her it reached Scaler."""
    ctx = runtime.context
    owner = ctx.owner_chat_id or ""
    if ctx.asked:
        return "a note to Scaler already went out for this message: put everything into that one request"
    if not owner:
        return "Scaler's chat is not set (CONTENT_OWNER_CHAT_ID): tell her to write to Scaler directly"
    p = await ctx.post(campaign_id) if campaign_id else None
    about = f"\nPostarea: {p.short} · v{p.version} [{p.campaign_id}]" if p else ""
    text = f"📨 De la Betty:\n{request.strip()}{about}\n\nMesajul ei: „{ctx.text.strip()[:1500]}”"
    key = (f"ask:{ctx.chat_id}:{ctx.update_id}" if ctx.update_id is not None     # one message → one note
           else f"ask:{ctx.chat_id}:{hashlib.sha256(ctx.text.encode()).hexdigest()[:16]}")

    async def send() -> str:
        msg = await ctx.lv.services.telegram.call("sendMessage", {"chat_id": owner, "text": text})
        return str(msg.get("message_id", ""))

    await ctx.lv.services.store.once(key, send)
    ctx.asked = True
    ctx.done.append("ask_scaler")
    return "sent to Scaler"


TOOLS = [week_overview, find_post, show_post, rewrite_caption, use_exact_text,
         propose_approve, propose_reject, save_idea, ask_scaler]
