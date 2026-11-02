"""The posts as the assistant and the menu see them: read from the post threads, never written here."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from content_system.assistant.texts import STATUS, WEEKDAYS
from content_system.engine import PostView, view
from content_system.manifest import review_line
from content_system.telegram import review_keyboard
from prompts.system_prompts import CONTACT_LINE_LUX
from services.design_system import Style, caption_ending


@dataclass(frozen=True)
class PostInfo:
    campaign_id: str
    day: date
    label: str
    name: str
    city: str
    caption_city: str
    route: str
    style: str
    status: str
    version: int
    waiting: bool
    caption: str
    tg_file_id: str
    image_url: str

    @property
    def week(self) -> str:
        return self.campaign_id[:8]

    @property
    def short(self) -> str:
        return f"{self.day:%a %d %b} · {self.label}"

    @property
    def body(self) -> str:
        """The caption without the fare line and the contact line the system adds."""
        text = self.caption.strip()
        if text.endswith(CONTACT_LINE_LUX):
            text = text[: -len(CONTACT_LINE_LUX)].rstrip()
        ending = caption_ending(Style(self.style), self.caption_city)
        if text.endswith(ending):
            text = text[: -len(ending)].rstrip()
        return text

    def line(self) -> str:
        return f"{self.short} · v{self.version} · {STATUS.get(self.status, self.status)}  [{self.campaign_id}]"


def info(v: PostView) -> PostInfo | None:
    s = v.values
    if not v.exists or "spec" not in s:
        return None
    spec = s["spec"]
    return PostInfo(
        campaign_id=v.campaign_id, day=date.fromisoformat(s["day"]), label=spec.get("label", ""),
        name=spec.get("name", ""), city=spec.get("city", ""), caption_city=spec.get("caption_city", ""),
        route=spec.get("route", ""), style=s["design"]["style"], status=v.status, version=v.version or 1,
        waiting=v.waiting, caption=s.get("caption", ""), tg_file_id=s.get("tg_file_id", ""),
        image_url=s.get("image_url", ""))


async def recent(graph: Any, store: Any, limit: int = 40) -> list[PostInfo]:
    """The posts that went to Telegram, newest week first, in publishing order within a week."""
    out = [p for cid in await store.reviewed_posts(limit) if (p := info(await view(graph, cid)))]
    return sorted(out, key=lambda p: (p.week, p.day), reverse=True)


def latest_week(posts: list[PostInfo]) -> list[PostInfo]:
    if not posts:
        return []
    week = max(p.week for p in posts)
    return sorted((p for p in posts if p.week == week), key=lambda p: p.day)


def overview(posts: list[PostInfo]) -> str:
    week = latest_week(posts)
    if not week:
        return ""
    waiting = sum(p.waiting for p in week)
    lines = [f"📋 {week[0].week} · {len(week)} postări, {waiting} așteaptă decizia", ""]
    lines += [("⏳ " if p.waiting else "• ") + p.line() for p in week]
    return "\n".join(lines)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def find(posts: list[PostInfo], query: str) -> list[PostInfo]:
    """Posts matching a day (Romanian or English), a place, a name, a route or an id. Newest week only,
    unless the query names an id or a week."""
    q = _plain(query).strip()
    if not q:
        return []
    exact = [p for p in posts if p.campaign_id.lower() == q or (len(q) >= 3 and p.campaign_id.lower().endswith(q))]
    if exact:
        return exact
    pool = [p for p in posts if p.week.lower() in q] or latest_week(posts)
    words = re.findall(r"[\w-]+", q)
    days = {WEEKDAYS[w] for w in words if w in WEEKDAYS}
    out = []
    for p in pool:
        hay = _plain(" ".join((p.label, p.name, p.city, p.caption_city, p.route)))
        if (days and p.day.strftime("%a") in days) or any(len(w) >= 3 and w in hay for w in words if w not in WEEKDAYS):
            out.append(p)
    return out


async def send_card(telegram: Any, store: Any, chat_id: int | str, p: PostInfo) -> int:
    """The post again, as on the review message: photo, caption with its version line, and the buttons
    while it waits. Its buttons decide the same version; a tap on an older card is refused by version."""
    data: dict[str, Any] = {"chat_id": chat_id, "caption": p.caption + review_line(p.campaign_id, p.version),
                            "photo": p.tg_file_id or p.image_url}
    if p.waiting:
        data["reply_markup"] = review_keyboard(p.campaign_id, p.version)
    msg = await telegram.call("sendPhoto", data)
    await store.add_message(msg["chat"]["id"], msg["message_id"], p.campaign_id, p.version, "review")
    return int(msg["message_id"])
