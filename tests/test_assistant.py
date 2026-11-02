"""
Betty's assistant (content_system/assistant) — in memory, with a scripted model that calls tools and
a scripted writer. The real models are never called. What is checked: the AI can't approve, reject or
publish; a caption change is a new version that waits for her tap; cards decide exact versions; the
menu and the buttons work without AI; limits, failures and duplicates are handled.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from content_system import bridge
from content_system.assistant import router, texts
from content_system.assistant.agent import Assistant, answered
from content_system.assistant.facts import invented
from content_system.assistant.posts import find, recent
from content_system.assistant.tools import TOOLS
from content_system.assistant.usage import Meter, cost
from content_system.assistant.writer import check
from content_system.engine import start, view
from content_system.graph import build_graph
from content_system.manifest import review_line
from content_system.settings import get_settings
from content_system.store import MemoryStore
from tests.content_fakes import CHAT, FAST_RETRY, make_services, week42

OWNER = 777
BOT = {"is_bot": True, "id": 123}
CAPE = "2026-W42-V4-003"
BEAUNE = "2026-W42-V4-002"


def run(coro):
    return asyncio.run(coro)


class Script(BaseChatModel):
    """A chat model that answers from a list: AIMessages (with tool calls or text) or exceptions."""
    replies: list[Any] = []
    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "script"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Script:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        if not self.replies:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="(script ended)"))])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


def call(name: str, n: int = 1, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{n}"}],
                     usage_metadata={"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
                     response_metadata={"model_name": "claude-haiku-5-5"})


def say(text: str) -> AIMessage:
    return AIMessage(content=text, usage_metadata={"input_tokens": 1200, "output_tokens": 40, "total_tokens": 1240},
                     response_metadata={"model_name": "claude-haiku-5-5"})


class Writer:
    def __init__(self, *bodies: str):
        self.bodies = list(bodies)
        self.prompts: list[str] = []

    async def __call__(self, messages):
        self.prompts.append(messages[-1].content)
        return self.bodies.pop(0), {"model": "claude-sonnet-5-5", "input_tokens": 2000, "output_tokens": 300}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENABLED", "true")
    monkeypatch.setenv("CONTENT_ASSISTANT_ENABLED", "true")
    monkeypatch.setenv("CONTENT_OWNER_CHAT_ID", str(OWNER))
    monkeypatch.setattr("config.settings.telegram_bot_token", "123:abc")
    get_settings.cache_clear()
    store = MemoryStore()
    services, tg, _ = make_services(tmp_path, store)
    graph = build_graph(InMemorySaver(), retry=FAST_RETRY)
    week = week42()
    run(_send(graph, services, week))
    lv = bridge.Live(settings=get_settings(), graph=graph, services=services)
    bridge.use(lv)

    def assistant(*replies: Any, writer: Writer | None = None, fallback: list[Any] | None = None) -> Assistant:
        a = Assistant(lv, model=Script(replies=list(replies), seen=[]),
                      fallback=Script(replies=list(fallback or [RuntimeError("fallback down")]), seen=[]),
                      writer=writer or Writer(), checkpointer=InMemorySaver())
        router.use(a)
        return a

    yield SimpleNamespace(store=store, tg=tg, graph=graph, lv=lv, week=week, assistant=assistant)
    bridge.use(None)
    router.use(None)
    get_settings.cache_clear()


async def _send(graph, services, week):
    for p in week.posts:
        await start(graph, services, week, p, "send")


def texts_to(tg, chat_id=CHAT) -> list[str]:
    return [c[1]["text"] for c in tg.sent("sendMessage") if str(c[1]["chat_id"]) == str(chat_id)]


def last_card(tg) -> dict:
    return next(c[1] for c in reversed(tg.sent("sendMessage")) if "inline_keyboard" in (c[1].get("reply_markup") or {}))


def buttons(card: dict) -> list[tuple[str, str]]:
    return [(b["text"], b["callback_data"]) for row in card["reply_markup"]["inline_keyboard"] for b in row]


def tap(data: str, update_id: int, message_id: int = 9000) -> dict:
    return {"id": f"cb{update_id}", "data": data, "from": {"id": 1},
            "message": {"message_id": message_id, "chat": {"id": CHAT}}}


def message(text: str, update_id: int, *, reply_to: dict | None = None, message_id: int = 500) -> dict:
    msg = {"message_id": message_id, "chat": {"id": CHAT}, "from": {"id": 1}, "text": text}
    if reply_to:
        msg["reply_to_message"] = reply_to
    return msg


def toasts(tg) -> list[str]:
    return [c[1]["text"] for c in tg.sent("answerCallbackQuery")]


def history(a: Assistant) -> list[Any]:
    return a.agent.checkpointer.get_tuple({"configurable": {"thread_id": f"chat:{CHAT}"}}) \
        .checkpoint["channel_values"]["messages"]


# ── the rules that need no model ─────────────────────────────────────────────

def test_no_tool_can_approve_reject_or_publish():
    names = {t.name for t in TOOLS}
    assert names == {"week_overview", "find_post", "show_post", "rewrite_caption", "use_exact_text",
                     "propose_approve", "propose_reject", "save_idea", "ask_scaler"}
    assert not any(re.search(r"^(approve|reject|publish|send|post)", n) for n in names)


def test_fact_check_catches_new_numbers_codes_months_and_days():
    old = "Cape Town in November: light until eight. JFK → CPT, 6–8 Nov. Call +1 888-322-7999."
    assert invented("Cape Town in November, light until eight. Call +1 888-322-7999.", old) == []
    assert invented("Cape Town from 5 Nov, JFK → CPT.", old) == ["5"]
    assert invented("Fly LHR to Cape Town.", old) == ["LHR"]
    assert invented("Cape Town in December.", old) == ["dec"]
    assert invented("Cape Town, every Friday.", old) == ["fri"]
    assert invented("Mai scurt, may be.", old) == []          # "mai" (more) and "may" (verb) are not months
    assert invented("Cape Town, 7 nights.", old, "fă-l pe 7 nopți") == []   # she gave the number


def test_writer_check_uses_the_caption_rules_too(env):
    p = next(p for p in run(recent(env.graph, env.store)) if p.campaign_id == CAPE)
    assert check(p, p.body, "") == ["same text as the current version"]
    assert any("$" in x for x in check(p, "Cape Town for $900.", ""))
    assert any("formatting" in x for x in check(p, "Cape Town *now*.", ""))


def test_find_post_by_romanian_day_place_and_id(env):
    posts = run(recent(env.graph, env.store))
    assert [p.campaign_id for p in find(posts, "vineri")] == [CAPE]
    assert [p.campaign_id for p in find(posts, "ce postăm vineri?")] == [CAPE]
    assert [p.campaign_id for p in find(posts, "Beaune")] == [BEAUNE]
    assert [p.campaign_id for p in find(posts, "sao paulo")] == ["2026-W42-V4-004"]
    assert [p.campaign_id for p in find(posts, "v4-005")] == ["2026-W42-V4-005"]


def test_cost_and_meter():
    assert cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert cost("claude-sonnet-5-5-20261101", 1_000_000, 0) == pytest.approx(2.0)
    assert cost("some-new-model", 1_000_000, 0) == pytest.approx(2.0)        # unknown: the higher price
    m = Meter()
    m.add_message(say("x"), "claude-haiku-5-5")
    assert m.calls == {"claude-haiku-5-5": [1, 1200, 40]}


def test_a_history_with_an_unanswered_tool_call_is_repaired():
    """A run cut off inside a tool would leave the chat unusable: the model API refuses such a history."""
    history = [call("week_overview", 1), say("hi")]
    fixed = answered(history)
    assert isinstance(fixed[1], ToolMessage) and fixed[1].tool_call_id == "c1"


# ── the menu and the cards: no AI ────────────────────────────────────────────

def test_menu_week_and_help_without_ai(env):
    env.assistant()                                   # a model that would fail if it were called
    assert run(router.route_message(message(texts.MENU_WEEK, 1), 1))
    assert run(router.route_message(message(texts.MENU_HELP, 2), 2))
    assert run(router.route_message(message("/start", 3), 3))
    sent = texts_to(env.tg)
    assert sent[-3].startswith("📋 2026-W42 · 5 postări, 5 așteaptă decizia")
    assert "Fri 16 Oct · Cape Town · v1 · așteaptă decizia" in sent[-3]
    assert sent[-2].startswith("❓ Cum lucrăm") and sent[-1].startswith("Bună, Betty")
    assert env.tg.sent("sendMessage")[-1][1]["reply_markup"]["is_persistent"] is True


def test_de_aprobat_card_approves_exact_versions_and_closes(env):
    run(router.route_message(message(texts.MENU_TODO, 1), 1))
    card = last_card(env.tg)
    labels = [b[0] for b in buttons(card)]
    assert labels[:5] == ["✅ Wed 14 Oct · Singapore Airlines · v1", "✅ Thu 15 Oct · Hospices de Beaune · v1",
                          "✅ Fri 16 Oct · Cape Town · v1", "✅ Sat 17 Oct · F1 São Paulo · v1",
                          "✅ Sun 18 Oct · Europe one way · v1"]
    assert labels[5:] == ["✅ Aprob toate (5)", "✖ Nu acum"]
    every = buttons(card)[5][1]
    run(router.route_card(tap(every, 2), 2))
    run(router.route_card(tap(every, 2), 2))                      # Telegram delivers the same tap twice
    run(router.route_card(tap(buttons(card)[0][1], 3), 3))        # another button of the same card
    assert {env.store.campaigns[p.campaign_id]["status"] for p in env.week.posts} == {"approved"}
    assert toasts(env.tg) == ["✅ 5 din 5 aprobate", "", texts_REFUSED("dismissed")]    # the card is closed
    edited = env.tg.sent("editMessageText")[-1][1]
    assert edited["reply_markup"] == {"inline_keyboard": []} and edited["text"].count("aprobată") == 5


def texts_REFUSED(kind: str) -> str:
    from content_system.assistant.proposals import REFUSED

    return REFUSED[kind]


def test_a_card_refuses_a_post_that_changed_after_it_was_shown(env):
    """'Aprob toate' can't approve something she has not seen: the card holds the versions."""
    run(router.route_message(message(texts.MENU_TODO, 1), 1))
    every = buttons(last_card(env.tg))[5][1]
    from content_system.engine import decide

    run(decide(env.graph, env.lv.services, CAPE, "edit", caption="Summer on the Cape, a night away."))
    run(router.route_card(tap(every, 2), 2))
    assert env.store.campaigns[CAPE]["status"] == "draft"
    assert (run(view(env.graph, CAPE))).version == 2 and (run(view(env.graph, CAPE))).waiting
    assert sum(env.store.campaigns[p.campaign_id]["status"] == "approved" for p in env.week.posts) == 4
    text = env.tg.sent("editMessageText")[-1][1]["text"]
    assert "⚠️ Fri 16 Oct · Cape Town · v1 — That was v1; the latest is v2" in text
    assert toasts(env.tg)[-1] == "✅ 4 din 5 aprobate"


def test_not_now_changes_nothing(env):
    run(router.route_message(message(texts.MENU_TODO, 1), 1))
    card = last_card(env.tg)
    run(router.route_card(tap(buttons(card)[-1][1], 2), 2))
    run(router.route_card(tap(buttons(card)[5][1], 3), 3))
    assert {env.store.campaigns[p.campaign_id]["status"] for p in env.week.posts} == {"draft"}
    assert toasts(env.tg) == ["Nimic schimbat", texts_REFUSED("dismissed")]


def test_an_expired_card_decides_nothing(env):
    run(router.route_message(message(texts.MENU_TODO, 1), 1))
    for p in env.store.proposals.values():
        p["t"] -= 49 * 3600
    run(router.route_card(tap(buttons(last_card(env.tg))[5][1], 2), 2))
    assert {env.store.campaigns[p.campaign_id]["status"] for p in env.week.posts} == {"draft"}
    assert toasts(env.tg) == [texts_REFUSED("expired")]


def test_an_idea_by_reply_is_saved_without_ai(env):
    env.assistant()
    run(router.route_message(message(texts.MENU_IDEA, 1), 1))
    prompt = env.tg.sent("sendMessage")[-1][1]
    assert prompt["text"].startswith(texts.IDEA_MARK) and prompt["reply_markup"]["force_reply"]
    replied = {"message_id": env.tg._next, "from": BOT, "text": prompt["text"]}
    run(router.route_message(message("Gala de la Monaco în decembrie", 2, reply_to=replied), 2))
    assert env.store.ideas == [{"chat_id": CHAT, "user_id": 1, "text": "Gala de la Monaco în decembrie",
                                "status": "new"}]
    assert texts_to(env.tg)[-1] == texts.IDEA_SAVED


def test_a_reply_to_the_edit_prompt_is_still_the_exact_caption(env):
    run(bridge.handle_callback(tap(f"cs:e:{CAPE}:1", 1), 1))
    prompt = env.tg.sent("sendMessage")[-1][1]
    replied = {"message_id": env.tg._next, "from": BOT, "text": prompt["text"]}
    assert run(router.route_message(message("Summer on the Cape, a night away.", 2, reply_to=replied), 2))
    v = run(view(env.graph, CAPE))
    assert v.version == 2 and v.values["caption"].startswith("Summer on the Cape, a night away.")


def test_a_reply_to_the_post_goes_to_the_assistant_not_onto_the_post(env):
    """PR-1 took any reply to a post as its new caption; "mai scurt" would have become the caption."""
    a = env.assistant(say("Spune-mi cum să-l scurtez: vreau să știu ce parte scot."))
    caption = run(view(env.graph, CAPE)).values["caption"] + review_line(CAPE, 1)
    replied = {"message_id": 1003, "from": BOT, "caption": caption}
    assert run(router.route_message(message("mai scurt", 1, reply_to=replied), 1))
    run(router.drain())
    assert run(view(env.graph, CAPE)).version == 1                     # nothing became the caption
    human = [m for m in history(a) if m.type == "human"][-1]           # the model knew which post
    assert f"[She replied to post {CAPE}]" in human.content and human.content.endswith("mai scurt")


# ── the assistant with a scripted model ──────────────────────────────────────

def test_a_question_is_answered_from_the_posts(env):
    a = env.assistant(call("find_post", 1, query="vineri"), say("Vineri postăm Cape Town (v1), așteaptă decizia."))
    run(a.handle(CHAT, 1, "ce postăm vineri?", update_id=1, message_id=10))
    assert texts_to(env.tg)[-1] == "Vineri postăm Cape Town (v1), așteaptă decizia."
    tool_result = next(m for m in history(a) if isinstance(m, ToolMessage))
    assert CAPE in tool_result.content and "Table Mountain" in tool_result.content
    assert env.store.usage[next(iter(env.store.usage))]["calls"] == 2          # both model calls counted


def test_rewrite_makes_a_new_version_that_still_waits(env):
    shorter = "While New York turns cold, Cape Town turns to summer. Table Mountain, Clifton, Stellenbosch."
    w = Writer(shorter)
    a = env.assistant(call("rewrite_caption", 1, campaign_id=CAPE, request="fă-l mai scurt"),
                      say("Am trimis v2, mai scurtă. Aprob-o din butoane dacă îți place."), writer=w)
    ctx = run(a.handle(CHAT, 1, "fă textul de la Cape Town mai scurt", update_id=1))
    v = run(view(env.graph, CAPE))
    assert v.version == 2 and v.waiting and v.status == "in_review"            # waits for her tap
    assert env.store.campaigns[CAPE]["status"] == "draft"
    assert v.values["caption"].startswith(shorter)
    assert env.tg.sent("sendPhoto")[-1][1]["caption"].endswith(review_line(CAPE, 2))
    assert ctx.done == [f"edit:{CAPE}:v2:rewrite"]
    assert "fă-l mai scurt" in w.prompts[0] and "Table Mountain above the city" in w.prompts[0]
    assert run(env.store.usage_today()) == pytest.approx(cost("claude-haiku-5-5", 2200, 90)
                                                         + cost("claude-sonnet-5-5", 2000, 300))


def test_a_draft_with_an_invented_fact_is_written_again(env):
    w = Writer("Cape Town from 5 November, summer a night away.", "Cape Town, summer a night away.")
    a = env.assistant(call("rewrite_caption", 1, campaign_id=CAPE, request="mai scurt"), say("Gata, v2."), writer=w)
    run(a.handle(CHAT, 1, "mai scurt la Cape Town", update_id=1))
    assert "neither in the post nor in the request: 5" in w.prompts[1]
    assert run(view(env.graph, CAPE)).values["caption"].startswith("Cape Town, summer a night away.")


def test_two_bad_drafts_change_nothing_and_say_why(env):
    w = Writer("Cape Town for $900.", "Cape Town on 5 Dec.")
    a = env.assistant(call("rewrite_caption", 1, campaign_id=CAPE, request="mai scurt"),
                      say("N-am reușit: textul nou adăuga o dată."), writer=w)
    ctx = run(a.handle(CHAT, 1, "mai scurt", update_id=1))
    assert run(view(env.graph, CAPE)).version == 1 and ctx.done == []
    result = [m for m in history(a) if isinstance(m, ToolMessage)][-1].content
    assert "nothing changed" in result and "5, dec" in result


def test_exact_text_must_be_in_her_message(env):
    a = env.assistant(call("use_exact_text", 1, campaign_id=CAPE, text="Cape Town, the best deal ever."),
                      call("use_exact_text", 2, campaign_id=CAPE, text="Summer on the Cape, a night away."),
                      say("Am pus textul tău ca v2."))
    msg = "pune exact: Summer on the Cape, a night away."
    run(a.handle(CHAT, 1, msg, update_id=1))
    v = run(view(env.graph, CAPE))
    assert v.version == 2 and v.values["caption"].startswith("Summer on the Cape, a night away.")
    first = [m for m in history(a) if isinstance(m, ToolMessage)][0].content
    assert first.startswith("refused: that text is not, word for word, in her message")


def test_aprob_only_shows_a_card(env):
    a = env.assistant(call("propose_approve", 1, campaign_ids=["all"]),
                      say("Ți-am pus cardul: apasă pe ce aprobi."))
    ctx = run(a.handle(CHAT, 1, "aprob", update_id=1))
    assert {env.store.campaigns[p.campaign_id]["status"] for p in env.week.posts} == {"draft"}   # nothing yet
    assert ctx.done == ["propose:approve:" + ",".join(p.campaign_id for p in env.week.posts)]
    assert buttons(last_card(env.tg))[5][0] == "✅ Aprob toate (5)"


def test_what_it_cant_do_goes_to_scaler_once(env):
    a = env.assistant(call("ask_scaler", 1, request="Schimbă poza la Cape Town", campaign_id=CAPE),
                      call("ask_scaler", 2, request="Schimbă poza la Cape Town", campaign_id=CAPE),
                      say("I-am trimis lui Scaler cererea cu poza."))
    run(a.handle(CHAT, 1, "schimbă poza la Cape Town", update_id=1))
    to_owner = texts_to(env.tg, OWNER)
    assert len(to_owner) == 1 and to_owner[0].startswith("📨 De la Betty:\nSchimbă poza la Cape Town")
    assert "Fri 16 Oct · Cape Town · v1" in to_owner[0]
    assert texts_to(env.tg)[-1] == "I-am trimis lui Scaler cererea cu poza."


def test_the_same_update_is_answered_once(env):
    a = env.assistant(say("Bună!"), say("Bună din nou!"))
    run(a.handle(CHAT, 1, "bună", update_id=5))
    run(a.handle(CHAT, 1, "bună", update_id=5))
    assert texts_to(env.tg).count("Bună!") == 1 and "Bună din nou!" not in texts_to(env.tg)


def test_above_the_daily_limit_no_model_is_called(env):
    a = env.assistant(say("should not be said"))
    run(env.store.add_usage("claude-sonnet-5-5", 1, 0, 300_001, 3.00001))
    assert run(a.handle(CHAT, 1, "bună", update_id=1)) is None
    assert texts_to(env.tg)[-1] == texts.OVER_CAP
    assert a.agent is not None and env.tg.sent("sendMessage")[-1][1]["reply_markup"]["is_persistent"]


def test_when_both_models_fail_the_menu_remains(env):
    a = env.assistant(RuntimeError("overloaded"), fallback=[RuntimeError("down too")])
    run(a.handle(CHAT, 1, "bună", update_id=1))
    assert texts_to(env.tg)[-1] == texts.AI_DOWN


def test_haiku_failing_falls_back_to_sonnet(env):
    a = env.assistant(RuntimeError("overloaded"), fallback=[say("Bună, sunt aici.")])
    run(a.handle(CHAT, 1, "bună", update_id=1))
    assert texts_to(env.tg)[-1] == "Bună, sunt aici."


def test_a_model_that_keeps_calling_tools_is_stopped(env):
    loop = [call("week_overview", n) for n in range(1, 20)]
    a = env.assistant(*loop)
    run(a.handle(CHAT, 1, "spune-mi tot", update_id=1))
    calls = sum(1 for m in history(a) if isinstance(m, AIMessage))
    assert calls <= 8                                           # at most 8 model calls per message
    assert texts_to(env.tg)[-1] == texts.TOO_LONG               # in her language, not the library's


def test_the_chat_remembers_only_the_last_messages(env, monkeypatch):
    monkeypatch.setattr(env.lv.settings, "assistant_history", 4)
    a = env.assistant(*[say(f"r{i}") for i in range(20)])
    for i in range(12):
        run(a.handle(CHAT, 1, f"m{i}", update_id=100 + i))
    kept = history(a)
    assert len(kept) <= 5 and kept[-1].content == "r11"
    assert [m.content for m in kept if m.type == "human"][0].endswith(("m10", "m11"))


def test_webhook_free_text_goes_to_the_assistant_and_returns_at_once(env, monkeypatch):
    from fastapi.testclient import TestClient

    import webhook
    from config import settings

    seen = []

    async def fake_route(msg, update_id):
        seen.append((msg["text"], update_id))
        return True

    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "telegram_admin_ids", "1")
    monkeypatch.setattr(router, "route_message", fake_route)
    client = TestClient(webhook.app)
    head = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
    r = client.post("/webhook/telegram", headers=head, json={"update_id": 9, "message": message("salut", 9)})
    assert r.status_code == 200 and seen == [("salut", 9)]


def test_webhook_tells_a_stranger_their_id_on_start(env, monkeypatch):
    from fastapi.testclient import TestClient

    import services.telegram_client as tgc
    import webhook
    from config import settings

    sent = []

    async def send_message(chat_id=None, text="", **kw):
        sent.append(text)

    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "telegram_admin_ids", "1")
    monkeypatch.setattr(tgc, "send_message", send_message)
    client = TestClient(webhook.app)
    stranger = {"message_id": 1, "chat": {"id": 55}, "from": {"id": 55}, "text": "/start"}
    client.post("/webhook/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
                json={"update_id": 1, "message": stranger})
    assert sent == ["⛔ Access denied.\nYour Telegram ID is 55 — send it to Scaler to get access."]


def test_assistant_off_keeps_the_old_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENABLED", "true")
    monkeypatch.setenv("CONTENT_ASSISTANT_ENABLED", "false")
    get_settings.cache_clear()
    assert get_settings().assistant_on is False
    store = MemoryStore()
    services, tg, _ = make_services(tmp_path, store)
    graph = build_graph(InMemorySaver(), retry=FAST_RETRY)
    week = week42()
    run(_send(graph, services, week))
    bridge.use(bridge.Live(settings=get_settings(), graph=graph, services=services))
    monkeypatch.setattr("config.settings.telegram_bot_token", "123:abc")
    caption = run(view(graph, CAPE)).values["caption"] + review_line(CAPE, 1)
    handled = run(bridge.handle_reply(message("Summer on the Cape, a night away.", 1,
                                              reply_to={"message_id": 1003, "from": BOT, "caption": caption}), 1))
    assert handled and run(view(graph, CAPE)).version == 2       # PR-1 behaviour: a reply to the post edits it
    bridge.use(None)
    get_settings.cache_clear()
