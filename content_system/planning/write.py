"""
Writing one post of the plan: the text, the design fields of its style and the photo brief, from
the candidate's facts only. The draft is checked as the manifest would check it (fields per style,
caption rules, a fare the pricing engine can give for the post's own destination) and by the fact
check without AI: every number, airport code, month and weekday in what the post says — its text,
its design fields, its airports and cities — must be in its facts (or be the post's own day). The
candidate's title is not a source: it is the model's own summary. A refused draft is written once
more with the reasons; a malformed one is refused the same way, never a crash.
"""
from __future__ import annotations

import json
import re
import tomllib
from datetime import date
from functools import lru_cache
from typing import Any

from pydantic import ValidationError

from content_system.assistant.facts import invented
from content_system.manifest import ROOT, PostSpec, caption_problems, compose_caption
from content_system.planning.curate import Pick
from content_system.planning.llm import Call, PlanningError
from content_system.planning.research import Candidate
from content_system.visual.brief import RULES as PHOTO_RULES
from content_system.visual.brief import same_device
from services.design_system import PRICED_STYLES, PostDesign, Style, city_for, fare_for

ATTEMPTS = 2
TEXT_FIELDS = ("headline", "lead", "follow", "line", "tag")

STYLE_FIELDS = {
    Style.OFFER: "headline (≤70), tag (≤28, e.g. 'Longchamp · 4 Oct'), to_iata = the destination airport",
    Style.EDITORIAL: "headline (≤70), line (≤72)",
    Style.FACT: "lead (≤24, the accent words, e.g. 'Six times'), follow (≤64), line (≤72)",
    Style.PRICE_HERO: "follow (≤64), line (≤72), to_iata = the destination airport",
    Style.MULTI: "headline (≤70), fare_iatas (2–6 airports, the destination first, each a city in the facts), "
                 "destinations = the city of each of those airports, in the same order",
    Style.MINIMAL: "headline (≤70), line (≤72)",
}

WRITE = {
    "name": "write_post",
    "description": "The post: its fields, its body, its design fields and its photo brief.",
    "input_schema": {"type": "object", "required": ["label", "name", "city", "caption_city", "route", "body",
                                                    "design", "image"], "properties": {
        "label": {"type": "string", "description": "short name for the calendar, ≤40"},
        "name": {"type": "string", "description": "campaign name, ≤120"},
        "city": {"type": "string"},
        "caption_city": {"type": "string", "description": "city of the airport flown to"},
        "route": {"type": "string", "description": "e.g. 'JFK → CDG' or 'New York → Cape Town'"},
        "dates_start": {"type": "string", "description": "YYYY-MM-DD of the event, or empty"},
        "dates_end": {"type": "string", "description": "YYYY-MM-DD of the event, or empty"},
        "body": {"type": "string", "description": "the post text in English, paragraphs separated by a blank line"},
        "design": {"type": "object", "description": "style plus the fields that style needs", "properties": {
            "style": {"type": "string", "enum": [s.value for s in Style]},
            "headline": {"type": "string"}, "lead": {"type": "string"}, "follow": {"type": "string"},
            "line": {"type": "string"}, "tag": {"type": "string"}, "to_iata": {"type": "string"},
            "trip": {"type": "string", "enum": ["round_trip", "one_way"]},
            "destinations": {"type": "array", "items": {"type": "string"}},
            "fare_iatas": {"type": "array", "items": {"type": "string"}}}},
        "image": {"type": "object", "required": ["scene", "vantage", "device"], "properties": {
            "scene": {"type": "string"}, "vantage": {"type": "string"}, "device": {"type": "string"},
            "time": {"type": "string"}, "people": {"type": "string", "enum": ["none", "few", "subject"],
                                               "description": "subject: the riders or players the scene names "
                                                              "are the picture, and nobody else"}}},
    }},
}

SYSTEM = """You write posts for the WhatsApp channel of Buy Business Class, a US agency that sells business-class \
flights by phone from New York. Calm, precise, concrete; short sentences; the reader flies business and wants a \
reason to go. English. No exclamation marks, no hashtags, no emoji.

Use only the facts given: every date, number, time, name and place must come from them. No prices and no "$" \
(the fare is printed on the image; the system adds the fare line and the contact line). Don't invent flight \
times: say which flight ("Business class, New York to Paris.") without departure or arrival times unless a fact \
gives them. None of the characters _ * ` [.

The design fields depend on the style: {fields}. Headlines are short and quiet (examples below).

The photo brief: scene = the defining moment of the place or event; vantage = where the traveller stands or \
sits, privately; device = one foreground element seen from there (it must differ from the other posts of the \
week: {devices}). The photo rules:
{photo_rules}
Examples of approved posts:
{examples}
Call write_post."""


@lru_cache(maxsize=1)
def _examples() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted((ROOT / "weeks").glob("20*.toml"), reverse=True):     # the weeks as they were sent
        try:
            out += tomllib.loads(path.read_text(encoding="utf-8")).get("post", [])
        except (OSError, tomllib.TOMLDecodeError):
            continue
    return out


def examples(style: Style, n: int = 2) -> str:
    posts = _examples()
    same = [p for p in posts if (p.get("design") or {}).get("style") == style.value]
    chosen = (same + [p for p in posts if p not in same])[:n]
    return "\n\n".join(f"style {p['design']['style']} · design {p['design']}\n{p['body'].strip()}" for p in chosen)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "post"


def _object(value: Any) -> dict[str, Any]:
    """A nested object of the tool's answer: as given, from a JSON string, or empty (then refused)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def to_spec(draft: Any, pick: Pick, photo: str, cand: Candidate | None = None) -> dict[str, Any]:
    """The manifest [[post]] table for this draft. The event's dates are the researched ones (the
    candidate's), never the draft's."""
    draft = _object(draft)
    spec: dict[str, Any] = {"day": pick.day.isoformat(), "photo": photo}
    for key in ("label", "name", "city", "caption_city", "route"):
        spec[key] = _text(draft.get(key))
    if cand is not None:
        for key in ("dates_start", "dates_end"):
            if getattr(cand, key):
                spec[key] = getattr(cand, key).isoformat()
    spec["body"] = _text(draft.get("body"))
    design = {k: v for k, v in _object(draft.get("design")).items() if v not in (None, "", [])}
    design["style"] = pick.style.value
    spec["design"] = design
    image = {k: v for k, v in _object(draft.get("image")).items() if v not in (None, "")}
    if image:
        spec["image"] = image
    return spec


def _in_facts(city: str, cand: Candidate) -> bool:
    text = cand.facts_text().lower()
    return city.lower() in text or city.lower() == cand.city.lower()


def fare_problems(design: PostDesign, cand: Candidate, airports: set[str]) -> tuple[list[str], list[str]]:
    """(problems, the airports the post may name). The fare printed is the lowest over the codes, so
    every code must belong to this post: offer and price_hero price exactly the destination; multi
    prices the destination and other cities from the facts, and names exactly those cities."""
    if design.style not in PRICED_STYLES:
        return [], []
    dest = cand.airport
    if not dest:
        return [f"{design.style} needs a fare, and this subject has no destination airport"], []
    out: list[str] = []
    if design.style == Style.MULTI:
        codes = list(design.fare_iatas or [])
        if dest not in codes:
            out.append(f"multi: fare_iatas must include the destination, {dest}")
        unknown = [c for c in codes if c not in airports]
        if unknown:
            return [*out, f"no fare for {', '.join(unknown)}: use airports from the list"], []
        cities = [city_for(c) for c in codes]
        if [d.strip().lower() for d in design.destinations or []] != [c.lower() for c in cities]:
            out.append("multi: destinations must be the cities of fare_iatas, in order: " + ", ".join(cities))
        out += [f"multi: {c} ({code}) is not in the research" for c, code in zip(cities, codes, strict=True)
                if code != dest and not _in_facts(c, cand)]
    else:
        codes = [c for c in (design.fare_iatas or [design.to_iata]) if c]
        if codes != [dest] or (design.to_iata and design.to_iata != dest):
            out.append(f"the fare must be for the destination only: to_iata = {dest}, no other airports")
        if design.style == Style.OFFER and design.fare_iatas:
            out.append("offer: no fare_iatas, only to_iata")
        if dest not in airports:
            out.append(f"no fare for {dest}: a priced style needs an airport from the list")
    if not out:
        try:
            fare_for(codes, design.trip)
        except ValueError as e:
            out.append(str(e))
    return out, codes


def check(spec: dict[str, Any], cand: Candidate, campaign_id: str, used_devices: dict[str, str],
          airports: set[str], idea_text: str = "") -> list[str]:
    """What is wrong with this draft; [] when it can go in the plan. `used_devices` maps the other
    posts' foreground devices to their day."""
    out: list[str] = []
    try:
        post = PostSpec.model_validate({**spec, "day": date.fromisoformat(spec["day"])})
        design = PostDesign(background="photo.jpg", **post.design)
    except (ValidationError, ValueError, TypeError) as e:
        errs = e.errors() if isinstance(e, ValidationError) else [{"loc": ("design",), "msg": str(e)}]
        return [f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}" for er in errs[:6]]
    found, codes = fare_problems(design, cand, airports)
    out += found
    out += caption_problems(compose_caption(post.body, design.style, post.caption_city), campaign_id)
    said = "\n".join([post.body, post.label, post.name, post.route, post.city, post.caption_city,
                      design.to_iata or "", " ".join(design.fare_iatas or []), " ".join(design.destinations or [])]
                     + [str(getattr(design, f) or "") for f in TEXT_FIELDS])
    # the researched dates are checked against the facts in research.check_candidates
    dated = [d for d in (post.day, cand.dates_start, cand.dates_end) if d]     # "Sunday 15 November" is a fact
    known = "\n".join([cand.facts_text(), cand.city, cand.country, cand.airport, "JFK New York", idea_text,
                       " ".join(codes),             # this post's own airports, checked in fare_problems
                       " ".join(f"{d:%A %d %B %Y}" for d in dated)])
    new = invented(said, known)
    if new:
        out.append("facts that are not in the research: " + ", ".join(new))
    if post.image is None:
        out.append("no photo brief")
    else:
        twin = next((day for device, day in used_devices.items() if same_device(post.image.device, device)), None)
        if twin is not None:
            out.append(f"the photo's foreground device repeats {twin}: choose another")
    return out


def _prompt(pick: Pick, cand: Candidate, idea_text: str, feedback: str) -> str:
    facts = "\n".join(f"- {f.text}  ({f.url})" for f in cand.facts)
    text = (f"Post for {pick.day:%A %d %B %Y}, style {pick.style.value}. Angle: {pick.angle}\n"
            f"Subject: {cand.title} — {cand.city}, {cand.country} · type {cand.type} · destination airport "
            f"{cand.airport or 'none'} · dates {cand.dates_start or ''} {cand.dates_end or ''}\n"
            f"Why now: {cand.why_now}\nFacts (the only ones you may use):\n{facts}")
    if idea_text:
        text += f"\nBetty's idea behind it: {idea_text}"
    if feedback:
        text += f"\n\nYour previous draft was refused: {feedback}\nWrite it again without these problems."
    return text


async def write_post(call: Call, pick: Pick, cand: Candidate, *, campaign_id: str, photo: str,
                     used_devices: dict[str, str], airports: set[str], idea_text: str = ""
                     ) -> tuple[dict[str, Any], dict[str, Any]]:
    devices = ", ".join(used_devices) or "none yet"
    system = SYSTEM.format(fields="; ".join(f"{s.value}: {f}" for s, f in STYLE_FIELDS.items()), devices=devices,
                           photo_rules=PHOTO_RULES, examples=examples(pick.style))
    feedback, usage = "", {}
    for _ in range(ATTEMPTS):
        answer = await call(system, _prompt(pick, cand, idea_text, feedback), WRITE)
        for k, v in answer.usage.items():
            usage[k] = usage.get(k, 0) + v if isinstance(v, int) else v
        spec = to_spec(answer.data, pick, photo, cand)
        found = check(spec, cand, campaign_id, used_devices, airports, idea_text)
        if not found:
            return spec, usage
        feedback = "; ".join(found)
    raise PlanningError(f"{pick.day} {cand.id}: no draft passed in {ATTEMPTS} attempts — {feedback}")
