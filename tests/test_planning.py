"""
The week's plan (content_system/planning) with a scripted model — the real one is never called. What
is checked: a fact whose page was not among the search results is dropped; the choice and every draft
are checked by rules (week mix, types, fares, facts in the research only) and asked again with the
reasons; the plan is a manifest the loader accepts; Betty's buttons drop, keep, approve and reject,
once, and a week that no longer passes without the dropped posts can't be approved.
"""
from __future__ import annotations

import asyncio
import copy
import tomllib
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from content_system.manifest import load_week
from content_system.planning import review
from content_system.planning.curate import Pick, curate, problems
from content_system.planning.llm import PlanningError, ToolAnswer
from content_system.planning.plan import check_week, kept_manifest, make_plan, priced_airports, to_toml
from content_system.planning.research import check_candidates, week_span
from content_system.planning.write import check, to_spec, write_post
from content_system.store import MemoryStore
from services.design_system import Style
from tests.content_fakes import FakeTelegram

TODAY = date(2026, 11, 2)
SPAN = week_span("2026-W47")                    # Wed 18 → Sun 22 November 2026
AIRPORTS = priced_airports()
USAGE = {"model": "claude-sonnet-5-5", "input_tokens": 1000, "output_tokens": 200}
OWNER = "777"


def run(coro):
    return asyncio.run(coro)


def fact(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


CANDIDATES = [
    {"id": "art-basel-miami", "type": "event", "title": "Art Basel Miami Beach", "city": "Miami Beach",
     "country": "United States", "airport": "MIA", "dates_start": "2026-12-03", "dates_end": "2026-12-06",
     "why_now": "The fair is two weeks after the post.",
     "facts": [fact("Art Basel Miami Beach runs 3 to 6 December 2026 at the Miami Beach Convention Center.",
                    "https://www.artbasel.com/miami-beach/"),
               fact("More than 280 galleries take part.", "https://artbasel.com/miami-beach")]},
    {"id": "tokyo-autumn", "type": "destination", "title": "Autumn leaves in Tokyo", "city": "Tokyo",
     "country": "Japan", "airport": "HND", "why_now": "The colours peak in late November.", "idea_id": 7,
     "facts": [fact("Autumn leaves in Tokyo peak in late November.", "https://www.japan.travel/en/autumn/"),
               fact("Rikugien Garden opens at night for the autumn illuminations.",
                    "https://www.tokyo-park.or.jp/rikugien")]},
    {"id": "doha-lounge", "type": "award", "title": "Best business class lounge", "city": "Doha",
     "country": "Qatar", "airport": "DOH", "why_now": "The award was announced this month.",
     "facts": [fact("Al Mourjan in Doha was named the best business class lounge.", "https://www.skytrax.com/lounge"),
               fact("The awards were announced in London.", "https://www.skytrax.com/lounge")]},
    {"id": "cape-town-summer", "type": "deal", "title": "Summer in Cape Town", "city": "Cape Town",
     "country": "South Africa", "airport": "CPT", "why_now": "Summer starts there in December.",
     "facts": [fact("December is the start of summer in Cape Town.", "https://www.capetown.travel/summer"),
               fact("The Table Mountain cableway runs late into the evening in summer.",
                    "https://www.tablemountain.net/")]},
    {"id": "mandarin-bangkok", "type": "hotel", "title": "Mandarin Oriental Bangkok", "city": "Bangkok",
     "country": "Thailand", "airport": "BKK", "why_now": "The cool season starts.",
     "facts": [fact("The Mandarin Oriental Bangkok sits on the Chao Phraya river.",
                    "https://www.mandarinoriental.com/bangkok"),
               fact("Its Authors Lounge serves afternoon tea.", "https://www.mandarinoriental.com/bangkok")]},
    {"id": "invented-news", "type": "news", "title": "A new route", "city": "Lisbon", "airport": "LIS",
     "why_now": "Announced this week.",
     "facts": [fact("A new nonstop starts in March.", "https://example.com/not-searched"),
               fact("It flies daily.", "https://example.com/not-searched-either")]},
]
SOURCES = {"https://artbasel.com/miami-beach", "https://www.japan.travel/en/autumn/",
           "https://www.tokyo-park.or.jp/rikugien", "https://www.skytrax.com/lounge",
           "https://www.capetown.travel/summer", "https://www.tablemountain.net/",
           "https://www.mandarinoriental.com/bangkok"}

PICKS = [
    {"day": "2026-11-18", "candidate_id": "art-basel-miami", "style": "offer", "angle": "The fair."},
    {"day": "2026-11-19", "candidate_id": "tokyo-autumn", "style": "editorial", "angle": "Autumn colours."},
    {"day": "2026-11-20", "candidate_id": "doha-lounge", "style": "fact", "angle": "The award."},
    {"day": "2026-11-21", "candidate_id": "cape-town-summer", "style": "price_hero", "angle": "Summer."},
    {"day": "2026-11-22", "candidate_id": "mandarin-bangkok", "style": "editorial", "angle": "The hotel."},
]


def draft(label, city, caption_city, route, body, design, device, **extra) -> dict[str, Any]:
    return {"label": label, "name": label, "city": city, "caption_city": caption_city, "route": route,
            "body": body, "design": design, **extra,
            "image": {"scene": f"{city} in late light, calm water below a clear sky", "vantage": "a private terrace",
                      "device": device}}


DRAFTS = [
    draft("Art Basel Miami Beach", "Miami Beach", "Miami", "JFK → MIA",
          "Art Basel Miami Beach runs 3 to 6 December 2026 at the Miami Beach Convention Center.\n\n"
          "More than 280 galleries take part.\n\nBusiness class, New York to Miami.",
          {"style": "offer", "headline": "Miami, for the art.", "tag": "Art Basel · 3 Dec", "to_iata": "MIA"},
          "a glass of champagne on a marble ledge", dates_start="2026-12-03", dates_end="2026-12-06"),
    draft("Tokyo in autumn", "Tokyo", "Tokyo", "New York → Tokyo",
          "Autumn leaves in Tokyo peak in late November.\n\nRikugien Garden opens at night for the autumn "
          "illuminations.\n\nBusiness class, New York to Tokyo.",
          {"style": "editorial", "headline": "Tokyo, in its autumn colours.", "line": "Rikugien · late November"},
          "a lacquered tea tray on tatami"),
    draft("Al Mourjan, Doha", "Doha", "Doha", "New York → Doha",
          "Al Mourjan in Doha was named the best business class lounge.\n\nThe awards were announced in "
          "London.\n\nBusiness class, New York to Doha.",
          {"style": "fact", "lead": "The best lounge", "follow": "Al Mourjan, in Doha.", "line": "Qatar Airways"},
          "a leather armchair with a reading lamp"),
    draft("Cape Town summer", "Cape Town", "Cape Town", "JFK → CPT",
          "December is the start of summer in Cape Town.\n\nThe Table Mountain cableway runs late into the "
          "evening.\n\nBusiness class, New York to Cape Town.",
          {"style": "price_hero", "follow": "Summer starts in Cape Town.", "line": "Table Mountain", "to_iata": "CPT"},
          "a teak sun lounger with a folded towel"),
    draft("Mandarin Oriental Bangkok", "Bangkok", "Bangkok", "New York → Bangkok",
          "The Mandarin Oriental Bangkok sits on the Chao Phraya river.\n\nIts Authors Lounge serves "
          "afternoon tea.\n\nBusiness class, New York to Bangkok.",
          {"style": "editorial", "headline": "Bangkok, from the river.", "line": "Mandarin Oriental"},
          "a silver teapot on white linen"),
]


class Model:
    """A scripted `Call`: answers per tool, in order; records every call."""

    def __init__(self, **answers: list[Any]):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, system, prompt, tool, *, search=False, max_searches=8):
        self.calls.append({"system": system, "prompt": prompt, "tool": tool["name"], "search": search})
        data = self.answers[tool["name"]].pop(0)
        if isinstance(data, Exception):
            raise data
        sources = SOURCES if tool["name"] == "report_candidates" else set()
        return ToolAnswer(copy.deepcopy(data), set(sources), dict(USAGE, searches=4 if search else 0))

    def prompts(self, tool: str) -> list[str]:
        return [c["prompt"] for c in self.calls if c["tool"] == tool]


def full_model() -> Model:
    return Model(report_candidates=[{"candidates": CANDIDATES}], choose_posts=[{"picks": PICKS}],
                 write_post=list(DRAFTS))


def a_plan():
    return run(make_plan(full_model(), SPAN, today=TODAY, ideas=[{"id": 7, "text": "Tokyo toamna"}],
                         airports=AIRPORTS))


# ── research ──

def test_week_span():
    assert SPAN.monday == date(2026, 11, 16)
    assert [d.isoformat() for d in SPAN.days] == ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21",
                                                   "2026-11-22"]
    assert SPAN.photos == "assets/curated/week_2026_11_16"
    assert week_span("2026-W47", ("Mon", "Fri")).days == (date(2026, 11, 16), date(2026, 11, 20))


def test_a_fact_counts_only_with_a_page_the_searches_returned():
    kept, notes = check_candidates(CANDIDATES, SOURCES, SPAN)
    assert [c.id for c in kept] == ["art-basel-miami", "tokyo-autumn", "doha-lounge", "cape-town-summer",
                                    "mandarin-bangkok"]
    # the same page written another way (www., trailing slash) is the page that was found
    assert [f.url for f in kept[0].facts] == ["https://artbasel.com/miami-beach"] * 2
    assert any(n.startswith("invented-news: dropped (2 fact(s) without a found source") for n in notes)


def test_research_drops_duplicates_and_events_that_are_over():
    over = dict(CANDIDATES[0], id="over-already", dates_start="2026-11-01", dates_end="2026-11-10")
    backwards = dict(CANDIDATES[0], id="backwards", dates_start="2026-12-06", dates_end="2026-12-03")
    kept, notes = check_candidates([CANDIDATES[1], CANDIDATES[1], over, backwards], SOURCES, SPAN)
    assert [c.id for c in kept] == ["tokyo-autumn"]
    assert "tokyo-autumn: duplicate id, dropped" in notes
    assert "over-already: over before the week starts, dropped" in notes
    assert any(n.startswith("backwards: dropped") or n == "backwards: ends before it starts, dropped" for n in notes)


def test_the_research_runs_with_web_search_and_ideas_in_the_prompt():
    model = full_model()
    run(make_plan(model, SPAN, today=TODAY, ideas=[{"id": 7, "text": "Tokyo toamna"}], airports=AIRPORTS))
    first = model.calls[0]
    assert first["tool"] == "report_candidates" and first["search"] is True
    assert "idea 7: Tokyo toamna" in first["prompt"] and "CPT" in first["prompt"]
    assert all(not c["search"] for c in model.calls[1:])


# ── the choice ──

def by_id():
    kept, _ = check_candidates(CANDIDATES, SOURCES, SPAN)
    return {c.id: c for c in kept}


def picks(*styles: str) -> list[Pick]:
    return [Pick.model_validate(dict(p, style=s)) for p, s in zip(PICKS, styles, strict=True)]


def test_the_choice_rules():
    cands = by_id()
    assert problems(picks("offer", "editorial", "fact", "price_hero", "editorial"), cands, SPAN, AIRPORTS) == []
    found = problems(picks("offer", "price_hero", "multi", "price_hero", "editorial"), cands, SPAN, AIRPORTS)
    assert "week mix: " not in " ".join(found)       # the curate messages are the design system's own
    assert any("carry a fare" in f for f in found) and any("in a row" in f for f in found)
    # a priced style for a place the pricing engine can't price
    no_airport = {**cands, "tokyo-autumn": cands["tokyo-autumn"].model_copy(update={"airport": ""})}
    found = problems(picks("offer", "price_hero", "fact", "editorial", "editorial"), no_airport, SPAN, AIRPORTS)
    assert any("no airport can't be priced" in f for f in found)
    # one candidate twice, a missing day
    twice = [Pick.model_validate(dict(PICKS[0], day=d)) for d in ("2026-11-18", "2026-11-19")]
    found = problems(twice, cands, SPAN, AIRPORTS)
    assert "art-basel-miami is used 2 times" in found and "2 posts for 5 publishing days" in found


def test_the_choice_needs_three_types_at_least():
    cands = by_id()
    same = {k: c.model_copy(update={"type": "destination" if i % 2 else "hotel"})
            for i, (k, c) in enumerate(cands.items())}
    found = problems(picks("offer", "editorial", "fact", "price_hero", "editorial"), same, SPAN, AIRPORTS)
    assert "only 2 content type(s): at least 3" in found
    assert any(f.startswith("type hotel used 3 times") for f in found)


def test_a_refused_choice_is_asked_again_with_the_reasons():
    bad = [dict(p, style="price_hero") for p in PICKS]
    model = Model(choose_posts=[bad, {"picks": PICKS}])
    model.answers["choose_posts"][0] = {"picks": bad}
    chosen, _ = run(curate(model, SPAN, list(by_id().values()), ideas=[], airports=AIRPORTS))
    assert [p.style for p in chosen] == [Style(p["style"]) for p in PICKS]
    second = model.prompts("choose_posts")[1]
    assert "Your previous choice was refused" in second and "carry a fare" in second


def test_the_choice_gives_up_after_three_refusals():
    bad = {"picks": [dict(p, style="price_hero") for p in PICKS]}
    with pytest.raises(PlanningError, match="no valid choice in 3 attempts"):
        run(curate(Model(choose_posts=[bad, bad, bad]), SPAN, list(by_id().values()), ideas=[], airports=AIRPORTS))


# ── the drafts ──

def checked(i: int, **change: Any) -> list[str]:
    cands = by_id()
    pick = Pick.model_validate(PICKS[i])
    spec = to_spec({**DRAFTS[i], **change}, pick, "photo")
    return check(spec, cands[pick.candidate_id], "2026-W47-V4-001", {}, AIRPORTS)


def test_the_drafts_pass_as_written():
    assert [checked(i) for i in range(5)] == [[]] * 5


def test_a_draft_cant_add_a_time_a_date_or_a_route_the_research_doesnt_have():
    body = DRAFTS[0]["body"].replace("Business class, New York to Miami.",
                                     "Business class, New York to Miami. Leave at 8pm on Friday 4 December.")
    found = checked(0, body=body)
    assert found and found[0].startswith("facts that are not in the research:")
    assert "8" in found[0] and "fri" in found[0]
    found = checked(0, route="JFK → LHR")
    assert found and "LHR" in found[0]


def test_a_draft_follows_the_caption_and_design_rules():
    assert any("'$'" in f for f in checked(0, body=DRAFTS[0]["body"] + " From $4,000."))
    assert any("requires: headline" in f for f in checked(1, design={"style": "editorial", "line": "x"}))
    # the style is the choice's, not the draft's
    assert checked(2, design=dict(DRAFTS[2]["design"], style="offer")) == []
    found = checked(3, design=dict(DRAFTS[3]["design"], to_iata="XXX"))
    assert any("no fare for XXX" in f for f in found)
    # a fare the engine can give, but for another place than the post's
    found = checked(3, design=dict(DRAFTS[3]["design"], to_iata="LHR"))
    assert "the fare must be for the destination, CPT, not LHR" in found
    assert checked(4, image=None) == ["no photo brief"]


def test_the_photo_device_must_change_from_post_to_post():
    cands = by_id()
    pick = Pick.model_validate(PICKS[1])
    spec = to_spec(dict(DRAFTS[1], image=dict(DRAFTS[1]["image"], device="A glass of champagne, on a marble ledge")),
                   pick, "photo")
    found = check(spec, cands["tokyo-autumn"], "2026-W47-V4-002",
                  {"a glass of champagne on a marble ledge": "Wed"}, AIRPORTS)
    assert found == ["the photo's foreground device repeats Wed: choose another"]


def test_a_refused_draft_is_written_again_and_then_given_up():
    cands = by_id()
    pick = Pick.model_validate(PICKS[0])
    invented = dict(DRAFTS[0], body=DRAFTS[0]["body"] + " Doors open at 11am.")
    model = Model(write_post=[invented, DRAFTS[0]])
    spec, _ = run(write_post(model, pick, cands["art-basel-miami"], campaign_id="c", photo="p", used_devices={},
                             airports=AIRPORTS))
    assert spec["body"] == DRAFTS[0]["body"]
    assert "Your previous draft was refused: facts that are not in the research: 11" in model.prompts("write_post")[1]
    with pytest.raises(PlanningError, match="no draft passed in 2 attempts"):
        run(write_post(Model(write_post=[invented, invented]), pick, cands["art-basel-miami"], campaign_id="c",
                       photo="p", used_devices={}, airports=AIRPORTS))


# ── the plan ──

def test_the_plan_is_a_manifest_the_loader_accepts(tmp_path):
    plan = a_plan()
    assert plan.week == "2026-W47" and len(plan.posts) == 5
    assert plan.idea_ids == [7]
    assert plan.sources[0] == ["https://artbasel.com/miami-beach"]
    assert plan.usage["searches"] == 4 and plan.usage["models"] == ["claude-sonnet-5-5"]
    path = tmp_path / "2026-W47.toml"
    path.write_text(plan.manifest, encoding="utf-8")
    week = load_week(path)
    assert [p.campaign_id for p in week.posts] == [f"2026-W47-V4-00{i}" for i in range(1, 6)]
    assert [p.spec.photo for p in week.posts] == ["wed_art_basel_miami", "thu_tokyo_autumn", "fri_doha_lounge",
                                                 "sat_cape_town_summer", "sun_mandarin_bangkok"]
    assert week.posts[0].spec.dates_start == date(2026, 12, 3)
    assert week.posts[1].spec.image.device == "a lacquered tea tray on tatami"
    assert week.manifest.photos == "assets/curated/week_2026_11_16"
    # the TOML reads back to the same posts
    again = tomllib.loads(plan.manifest)["post"]
    assert [p["body"] for p in again] == [p["body"] for p in plan.posts]


def test_the_toml_escapes_what_the_model_writes():
    post = dict(a_plan().posts[0], body='He said "go".\nA \\ backslash, a tab\there.')
    text = to_toml(SPAN, [post])
    assert tomllib.loads(text)["post"][0]["body"] == post["body"]


def test_a_plan_for_a_week_that_has_started_is_refused():
    with pytest.raises(PlanningError, match="hasn't started"):
        run(make_plan(full_model(), SPAN, today=date(2026, 11, 18), ideas=[], airports=AIRPORTS))


def test_too_few_candidates_stops_the_plan():
    model = Model(report_candidates=[{"candidates": CANDIDATES[:3]}])
    with pytest.raises(PlanningError, match="only 3 usable candidates for 5 days"):
        run(make_plan(model, SPAN, today=TODAY, ideas=[], airports=AIRPORTS))


def test_kept_manifest_keeps_betty_s_posts_in_order():
    plan = a_plan()
    text = kept_manifest(SPAN, plan.posts, [1, 2])
    assert [p["label"] for p in tomllib.loads(text)["post"]] == ["Al Mourjan, Doha", "Cape Town summer",
                                                                 "Mandarin Oriental Bangkok"]
    assert check_week(text) == []
    # without two of the no-fare posts, two of three carry a fare: over 60%
    assert check_week(kept_manifest(SPAN, plan.posts, [2, 5])) == ["week mix: 2/3 posts carry a fare (max 60%)"]


# ── Betty's buttons ──

@pytest.fixture
def env():
    store, tg = MemoryStore(), FakeTelegram()
    plan = a_plan()
    run(store.add_plan("a1b2c3d4e5f6", plan.week, plan.posts, plan.sources, plan.manifest, plan.idea_ids))
    run(store.add_idea(1, 1, "o idee"))
    for _ in range(6):
        run(store.add_idea(1, 1, "alta"))          # id 7 is the one the plan uses
    return SimpleNamespace(store=store, tg=tg, lv=SimpleNamespace(services=SimpleNamespace(telegram=tg, store=store)))


def tap(data: str, update_id: int) -> dict:
    return {"id": f"cb{update_id}", "data": data, "message": {"message_id": 50, "chat": {"id": 1}}}


def toast(tg: FakeTelegram) -> str:
    return tg.sent("answerCallbackQuery")[-1][1]["text"]


def send(env) -> None:
    run(review.send_plan(env.store, env.tg, 1, run(env.store.get_plan("a1b2c3d4e5f6"))))


def test_the_plan_reaches_betty_with_a_message_per_post(env):
    send(env)
    texts = [c[1] for c in env.tg.sent("sendMessage")]
    assert len(texts) == 7
    assert texts[0]["text"].startswith("🗓 Planul pentru 2026-W47 · 5 postări")
    assert texts[1]["text"].startswith("1. Wed 18 Nov · Art Basel Miami Beach · eveniment, cu tarif\n„Miami, for the art.”")
    assert "🔗 https://artbasel.com/miami-beach" in texts[1]["text"]
    assert "📷 " in texts[1]["text"] and "a glass of champagne on a marble ledge" in texts[1]["text"]
    assert texts[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "pl:d:a1b2c3d4e5f6:1"
    assert [b[0]["callback_data"] for b in texts[-1]["reply_markup"]["inline_keyboard"]] == [
        "pl:ok:a1b2c3d4e5f6", "pl:no:a1b2c3d4e5f6"]
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["status"] == "sent"


def test_drop_and_keep_toggle_a_post(env):
    send(env)
    run(review.handle_callback(env.lv, tap("pl:d:a1b2c3d4e5f6:2", 1), 1, OWNER))
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["dropped"] == [2]
    markup = env.tg.sent("editMessageReplyMarkup")[-1][1]["reply_markup"]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "pl:k:a1b2c3d4e5f6:2"
    assert toast(env.tg) == "Scoasă din plan"
    run(review.handle_callback(env.lv, tap("pl:d:a1b2c3d4e5f6:2", 1), 1, OWNER))     # the same update again
    run(review.handle_callback(env.lv, tap("pl:k:a1b2c3d4e5f6:2", 2), 2, OWNER))
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["dropped"] == []
    assert toast(env.tg) == "Pusă înapoi în plan"


def test_approve_tells_scaler_once_and_marks_the_ideas(env):
    send(env)
    run(review.handle_callback(env.lv, tap("pl:d:a1b2c3d4e5f6:5", 1), 1, OWNER))
    run(review.handle_callback(env.lv, tap("pl:ok:a1b2c3d4e5f6", 2), 2, OWNER))
    plan = run(env.store.get_plan("a1b2c3d4e5f6"))
    assert plan["status"] == "approved"
    assert toast(env.tg) == "✅ Plan aprobat"
    notes = [c[1]["text"] for c in env.tg.sent("sendMessage") if c[1]["chat_id"] == OWNER]
    assert notes == ["🗓 Betty a aprobat planul 2026-W47: 4 postări, scoase: 5.\n"
                     "Pe laptop: railway run python -m content_system build --week 2026-W47"]
    assert env.store.ideas[6]["status"] == "planned" and env.store.ideas[0]["status"] == "new"
    # later taps on the closed plan change nothing
    run(review.handle_callback(env.lv, tap("pl:no:a1b2c3d4e5f6", 3), 3, OWNER))
    run(review.handle_callback(env.lv, tap("pl:k:a1b2c3d4e5f6:5", 4), 4, OWNER))
    assert toast(env.tg) == "Planul acesta e deja închis"
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["status"] == "approved"
    assert len([c for c in env.tg.sent("sendMessage") if c[1]["chat_id"] == OWNER]) == 1


def test_reject_asks_scaler_for_another_plan(env):
    send(env)
    run(review.handle_callback(env.lv, tap("pl:no:a1b2c3d4e5f6", 1), 1, OWNER))
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["status"] == "rejected"
    note = [c[1]["text"] for c in env.tg.sent("sendMessage") if c[1]["chat_id"] == OWNER][0]
    assert note.endswith("plan --week 2026-W47 --send")
    assert env.store.ideas[6]["status"] == "new"


def test_a_week_that_breaks_the_rules_without_the_dropped_posts_cant_be_approved(env):
    send(env)
    for n, u in ((2, 1), (3, 2)):
        run(review.handle_callback(env.lv, tap(f"pl:d:a1b2c3d4e5f6:{n}", u), u, OWNER))
    run(review.handle_callback(env.lv, tap("pl:ok:a1b2c3d4e5f6", 3), 3, OWNER))
    assert toast(env.tg).startswith("Fără cele scoase, săptămâna nu mai trece regulile:")
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["status"] == "sent"
    for n in range(1, 6):
        run(review.handle_callback(env.lv, tap(f"pl:d:a1b2c3d4e5f6:{n}", 10 + n), 10 + n, OWNER))
    run(review.handle_callback(env.lv, tap("pl:ok:a1b2c3d4e5f6", 20), 20, OWNER))
    assert toast(env.tg).startswith("Ai scos toate postările")
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["status"] == "sent"


def test_a_plan_not_yet_sent_or_replaced_has_no_buttons(env):
    run(review.handle_callback(env.lv, tap("pl:ok:a1b2c3d4e5f6", 1), 1, OWNER))
    assert toast(env.tg) == "Planul acesta e deja închis"
    send(env)
    plan = a_plan()
    run(env.store.add_plan("ffffffffffff", plan.week, plan.posts, plan.sources, plan.manifest, []))
    assert run(env.store.replace_open_plans("2026-W47", "ffffffffffff")) == ["a1b2c3d4e5f6"]
    run(review.handle_callback(env.lv, tap("pl:d:a1b2c3d4e5f6:1", 2), 2, OWNER))
    assert toast(env.tg) == "Planul acesta e deja închis"
    assert [p["status"] for p in run(env.store.week_plans("2026-W47"))] == ["replaced", "draft"]


def test_a_failed_tap_can_be_tapped_again(env):
    send(env)

    async def broken(*a, **k):
        raise RuntimeError("db down")

    env.store.decide_plan, real = broken, env.store.decide_plan
    run(review.handle_callback(env.lv, tap("pl:ok:a1b2c3d4e5f6", 1), 1, OWNER))
    assert toast(env.tg) == "⚠️ N-a mers — apasă din nou"
    env.store.decide_plan = real
    run(review.handle_callback(env.lv, tap("pl:ok:a1b2c3d4e5f6", 1), 1, OWNER))
    assert run(env.store.get_plan("a1b2c3d4e5f6"))["status"] == "approved"


@pytest.mark.parametrize("data", ["pl:ok:a1b2c3d4e5f6:1", "pl:d:a1b2c3d4e5f6", "pl:x:a1b2c3d4e5f6",
                                  "pl:ok:A1B2C3D4E5F6", "pl:d:a1b2c3d4e5f6:123", "pl:ok:a1b2"])
def test_malformed_buttons_are_refused(data):
    assert review.parse(data) is None


def test_the_webhook_routes_plan_buttons(monkeypatch):
    from fastapi.testclient import TestClient

    import webhook
    from config import settings

    seen = []

    async def fake_route(cb, update_id):
        seen.append((cb["data"], update_id))

    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "telegram_admin_ids", "1")
    monkeypatch.setattr(review, "route", fake_route)
    client = TestClient(webhook.app)
    cb = {"id": "c", "data": "pl:ok:a1b2c3d4e5f6", "from": {"id": 1}, "message": {"message_id": 5, "chat": {"id": 1}}}
    r = client.post("/webhook/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
                    json={"update_id": 4, "callback_query": cb})
    assert r.status_code == 200 and seen == [("pl:ok:a1b2c3d4e5f6", 4)]


# ── the command ──

def test_plan_without_a_database_writes_the_plan_to_a_file(tmp_path, monkeypatch, capsys):
    import content_system.__main__ as cli
    import content_system.planning.llm as llm
    from config import settings as app
    from content_system.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CONTENT_DB_URL", "")
    monkeypatch.setenv("CONTENT_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(app, "anthropic_api_key", "test-key")
    monkeypatch.setattr(llm, "anthropic_call", lambda model, key: full_model())
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    try:
        cli.main(["plan", "--week", "2026-W47"])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    written = list((tmp_path / "2026-W47").glob("plan-*.toml"))
    assert len(written) == 1 and check_week(written[0].read_text(encoding="utf-8")) == []
    assert "Betty's ideas not read" in out and "1. 2026-11-18  offer" in out and "cost ≈ $" in out


@pytest.mark.parametrize("week", ["2026-47", "W47", "2026-W99"])
def test_plan_refuses_a_bad_week(week):
    import content_system.__main__ as cli

    with pytest.raises(SystemExit):
        cli.main(["plan", "--week", week])
