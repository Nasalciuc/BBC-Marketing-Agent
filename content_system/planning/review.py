"""
The plan on Telegram, for Betty: a header, one message per planned post (day, style, headline,
the text, the photo brief, the sources) with ❌ Scot / ↩️ Pun înapoi, and a last card:
✅ Aprob planul · 🔁 Vreau alt plan. No AI here — buttons only.

callback_data: "pl:d:<plan>:<n>" drop post n · "pl:k:<plan>:<n>" keep it · "pl:ok:<plan>" · "pl:no:<plan>"

When she approves, Scaler's chat gets the command that makes the photos and the posts; each post
then still comes to her for its own ✅ (nothing is approved or published here).
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("bbc.content.planning.review")
_CALLBACK = re.compile(r"^pl:(d|k|ok|no):([0-9a-f]{12})(?::(\d{1,2}))?$")
STYLE_RO = {"offer": "eveniment, cu tarif", "price_hero": "tarif", "multi": "mai multe destinații, cu tarif",
            "editorial": "destinație", "fact": "știre", "minimal": "brand"}


def parse(data: str) -> tuple[str, str, int | None] | None:
    m = _CALLBACK.match(data or "")
    if not m or (m.group(1) in ("d", "k")) != (m.group(3) is not None):
        return None
    return m.group(1), m.group(2), int(m.group(3)) if m.group(3) else None


def _day(post: dict[str, Any]) -> str:
    from datetime import date

    return date.fromisoformat(post["day"]).strftime("%a %d %b")


def item_text(n: int, post: dict[str, Any], sources: list[str]) -> str:
    d = post.get("design") or {}
    title = d.get("headline") or " ".join(x for x in (d.get("lead"), d.get("follow")) if x)
    image = post.get("image") or {}
    head = f"{n}. {_day(post)} · {post['label']} · {STYLE_RO.get(d.get('style', ''), d.get('style', ''))}"
    lines = [head + (f"\n„{title}”" if title else ""), post["body"].strip(),
             f"📷 {image.get('scene', '')} — văzut din {image.get('vantage', '')}; "
             f"în prim-plan {image.get('device', '')}"]
    if sources:
        lines.append("\n".join(f"🔗 {u}" for u in sources[:3]))
    return "\n\n".join(lines)[:4000]


def still_valid(plan: dict[str, Any]) -> list[str]:
    """What the manifest checks (week mix, captions) say about the plan without its dropped posts."""
    from content_system.planning.plan import check_week, kept_manifest
    from content_system.planning.research import week_span

    return check_week(kept_manifest(week_span(plan["week"]), plan["posts"], plan["dropped"]))


def item_buttons(plan_id: str, n: int, dropped: bool) -> dict:
    if dropped:
        return {"inline_keyboard": [[{"text": "❌ Scoasă · ↩️ Pun-o înapoi", "callback_data": f"pl:k:{plan_id}:{n}"}]]}
    return {"inline_keyboard": [[{"text": "❌ Scot postarea asta", "callback_data": f"pl:d:{plan_id}:{n}"}]]}


def final_buttons(plan_id: str) -> dict:
    return {"inline_keyboard": [[{"text": "✅ Aprob planul", "callback_data": f"pl:ok:{plan_id}"}],
                                [{"text": "🔁 Vreau alt plan", "callback_data": f"pl:no:{plan_id}"}]]}


async def send_plan(store: Any, telegram: Any, chat_id: int | str, plan: dict[str, Any]) -> None:
    """Once per plan (the caller wraps it in store.once)."""
    posts, sources = plan["posts"], plan["sources"]
    lines = "\n".join(f"{i}. {_day(p)} · {p['label']}" for i, p in enumerate(posts, 1))
    await telegram.call("sendMessage", {"chat_id": chat_id, "text": (
        f"🗓 Planul pentru {plan['week']} · {len(posts)} postări\n\n{lines}\n\n"
        "Mai jos, fiecare cu textul, poza descrisă și sursele. ❌ scoate ce nu vrei, apoi ✅ Aprob planul. "
        "Pozele și postările se fac după aprobare și vin la tine, fiecare cu butoanele ei.")})
    for i, post in enumerate(posts, 1):
        await telegram.call("sendMessage", {"chat_id": chat_id, "text": item_text(i, post, sources[i - 1]),
                                            "reply_markup": item_buttons(plan["id"], i, False),
                                            "link_preview_options": {"is_disabled": True}})
    await telegram.call("sendMessage", {"chat_id": chat_id, "text": f"Planul {plan['week']}: gata?",
                                        "reply_markup": final_buttons(plan["id"])})
    await store.plan_sent(plan["id"], int(chat_id))


async def _answer(telegram: Any, callback_id: str, text: str) -> None:
    try:
        await telegram.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
    except Exception as e:  # noqa: BLE001 — the toast is cosmetic
        log.warning("answerCallbackQuery failed: %s", e)


async def _edit(telegram: Any, message: dict, *, text: str | None = None, markup: dict | None = None) -> None:
    chat_id, message_id = (message.get("chat") or {}).get("id"), message.get("message_id")
    if chat_id is None or message_id is None:
        return
    try:
        if text is not None:
            await telegram.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                                    "reply_markup": markup or {"inline_keyboard": []}})
        else:
            await telegram.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                           "reply_markup": markup})
    except Exception as e:  # noqa: BLE001 — the decision is stored; the message is cosmetic
        log.warning("could not update the plan message: %s", e)


async def handle_callback(lv: Any, cb: dict, update_id: int | None, owner_chat_id: str = "") -> None:
    """A tap on the plan. Never raises into the webhook."""
    tg, store = lv.services.telegram, lv.services.store
    callback_id, message = cb.get("id", ""), cb.get("message") or {}
    parsed = parse(cb.get("data", ""))
    if parsed is None:
        await _answer(tg, callback_id, "Buton necunoscut")
        return
    action, plan_id, n = parsed
    key = f"tg:update:{update_id}" if update_id is not None else None
    try:
        if key and not await store.claim(key):
            await _answer(tg, callback_id, "")
            return
        if action in ("d", "k"):
            dropped = await store.set_dropped(plan_id, n, action == "d")
            if dropped is None:
                await _answer(tg, callback_id, "Planul acesta e deja închis")
                return
            await _edit(tg, message, markup=item_buttons(plan_id, n, action == "d"))
            await _answer(tg, callback_id, "Scoasă din plan" if action == "d" else "Pusă înapoi în plan")
            return
        plan = await store.get_plan(plan_id)
        if plan is None or plan["status"] != "sent":
            await _answer(tg, callback_id, "Planul acesta e deja închis")
            return
        kept = len(plan["posts"]) - len(plan["dropped"])
        if action == "ok" and kept == 0:
            await _answer(tg, callback_id, "Ai scos toate postările — pune înapoi măcar una sau cere alt plan")
            return
        if action == "ok" and plan["dropped"]:
            found = still_valid(plan)
            if found:
                await _answer(tg, callback_id, "Fără cele scoase, săptămâna nu mai trece regulile: "
                                               f"{found[0]} — pune una înapoi sau cere alt plan")
                return
        decided = await store.decide_plan(plan_id, "approved" if action == "ok" else "rejected")
    except Exception:
        log.exception("plan tap failed")
        if key:
            await store.release(key)
        await _answer(tg, callback_id, "⚠️ N-a mers — apasă din nou")
        return
    if decided is None:
        await _answer(tg, callback_id, "Planul acesta e deja închis")
        return
    week = decided["week"]
    if action == "ok" and decided.get("idea_ids"):
        try:
            await store.set_ideas(list(decided["idea_ids"]), "planned")
        except Exception:  # the plan is approved; the ideas stay "new" and come up again next week
            log.exception("could not mark the plan's ideas")
    if action == "ok":
        await _edit(tg, message, text=f"✅ Plan aprobat: {kept} postări ({len(decided['dropped'])} scoase). "
                                      "Urmează pozele și postările, fiecare cu butoanele ei.")
        await _answer(tg, callback_id, "✅ Plan aprobat")
        note = (f"🗓 Betty a aprobat planul {week}: {kept} postări"
                + (f", scoase: {', '.join(map(str, decided['dropped']))}" if decided["dropped"] else "")
                + f".\nPe laptop: railway run python -m content_system build --week {week}")
    else:
        await _edit(tg, message, text="🔁 Am cerut alt plan. Vine de la Scaler.")
        await _answer(tg, callback_id, "Am cerut alt plan")
        note = f"🔁 Betty vrea alt plan pentru {week}.\nPe laptop: railway run python -m content_system plan --week {week} --send"
    if owner_chat_id:
        try:
            await store.once(f"plan-note:{plan_id}", lambda: tg.call("sendMessage", {"chat_id": owner_chat_id, "text": note}))
        except Exception:  # the decision is stored; Scaler can read it with `plan --status`
            log.exception("could not tell Scaler about the plan")


async def route(cb: dict, update_id: int | None) -> None:
    """webhook.py → here for "pl:" buttons (after its admin check)."""
    from content_system.bridge import _answer as answer
    from content_system.bridge import live
    from content_system.settings import get_settings

    cs = get_settings()
    if not cs.enabled:
        await answer(cb.get("id", ""), "The content system is off (CONTENT_ENABLED=false)")
        return
    try:
        lv = await live()
    except Exception:
        log.exception("content system unavailable")
        await answer(cb.get("id", ""), "⚠️ Nu ajung la baza de date — încearcă peste un minut")
        return
    await handle_callback(lv, cb, update_id, cs.owner_chat_id)
