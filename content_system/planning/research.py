"""
Scout + research in one search session: candidates for the week's posts, each with concrete facts
and the page each fact comes from. A fact whose URL was not among the pages the searches returned
is dropped (no invented sources); a candidate left with fewer than two facts is dropped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from content_system.planning.llm import Call, PlanningError
from services.design_system import CONTENT_TO_STYLE

TYPES = tuple(sorted(CONTENT_TO_STYLE))         # event, cabin, deal, destination, hotel, news, award …
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class WeekSpan:
    name: str                     # "2026-W46"
    monday: date
    days: tuple[date, ...]        # the publishing days, in order

    @property
    def photos(self) -> str:
        return f"assets/curated/week_{self.monday:%Y_%m_%d}"


def week_span(name: str, weekdays: tuple[str, ...] = ("Wed", "Thu", "Fri", "Sat", "Sun")) -> WeekSpan:
    year, week = name.split("-W")
    monday = date.fromisocalendar(int(year), int(week), 1)
    days = tuple(monday + timedelta(days=WEEKDAYS.index(d)) for d in weekdays)
    return WeekSpan(name, monday, days)


class Fact(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=5, max_length=400)
    url: str = Field(min_length=8, max_length=600)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    type: Literal[TYPES]  # type: ignore[valid-type]
    title: str = Field(min_length=3, max_length=140)
    city: str = Field(min_length=2, max_length=80)
    country: str = Field(default="", max_length=60)
    airport: str = Field(default="", pattern=r"^([A-Z]{3})?$")   # the destination airport, if any
    dates_start: date | None = None
    dates_end: date | None = None
    why_now: str = Field(min_length=10, max_length=400)
    facts: list[Fact] = Field(min_length=2, max_length=12)
    idea_id: int | None = None                                    # Betty's idea it came from

    def facts_text(self) -> str:
        return "\n".join(f.text for f in self.facts)


REPORT = {
    "name": "report_candidates",
    "description": "Report the candidates found, every fact with the URL of the page it comes from.",
    "input_schema": {"type": "object", "required": ["candidates"], "properties": {"candidates": {
        "type": "array", "items": {"type": "object", "required": ["id", "type", "title", "city", "why_now", "facts"],
                                   "properties": {
            "id": {"type": "string", "description": "short slug, a-z 0-9 -"},
            "type": {"type": "string", "enum": list(TYPES)},
            "title": {"type": "string"}, "city": {"type": "string"}, "country": {"type": "string"},
            "airport": {"type": "string", "description": "IATA code of the destination airport, or empty"},
            "dates_start": {"type": "string", "description": "YYYY-MM-DD, empty if not an event"},
            "dates_end": {"type": "string", "description": "YYYY-MM-DD, empty if not an event"},
            "why_now": {"type": "string"},
            "facts": {"type": "array", "items": {"type": "object", "required": ["text", "url"], "properties": {
                "text": {"type": "string"}, "url": {"type": "string"}}}},
            "idea_id": {"type": "integer", "description": "the id of Betty's idea this comes from, if any"},
        }}}}},
}

SYSTEM = """You research content for the WhatsApp channel of Buy Business Class, a US agency that sells \
business-class flights by phone, mostly from New York (JFK). The readers are travellers who fly business: \
they want reasons to go, and moments worth the trip.

Find candidates for posts published in the week given. Good candidates:
- premium events 2 to 10 weeks ahead that are worth flying for (grands prix, finals, auctions, art fairs, \
festivals, gala openings) — with exact dates and venue;
- destinations at their best in that season, with concrete reasons;
- airline, airport, cabin and lounge news (awards, new routes, new seats), recent and verifiable;
- hotels: openings and landmark hotels as the reason to travel.
Rules: search the web and verify every fact on a page you found; give each fact with that page's URL \
(prefer official sources). Concrete facts only — dates, places, numbers, names. No prices: fares come \
from elsewhere. Mix the types. Then call report_candidates."""


def _prompt(span: WeekSpan, today: date, n: int, ideas: list[dict[str, Any]], airports: list[str]) -> str:
    days = ", ".join(d.strftime("%a %d %b %Y") for d in span.days)
    lines = [f"Week {span.name}: posts on {days}. Today is {today:%a %d %b %Y}.",
             f"Find {n} candidates, of at least four different types among: {', '.join(TYPES)}.",
             "Destinations we can price from JFK (IATA): " + ", ".join(airports) + ".",
             "Prefer destinations from that list for events and deals; others are fine for news."]
    if ideas:
        lines.append("Betty (the marketing director) suggested these — research them first and set idea_id:")
        lines += [f"  idea {i['id']}: {i['text']}" for i in ideas]
    return "\n".join(lines)


def _norm(url: str) -> str:
    """The same page however it is written: no fragment, no trailing slash, http = https, no www."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}" + (f"?{parts.query}" if parts.query else "")


def check_candidates(raw: list[dict[str, Any]], sources: set[str], span: WeekSpan) -> tuple[list[Candidate], list[str]]:
    """Keep what holds up. Returns (candidates, notes on what was dropped)."""
    kept: list[Candidate] = []
    notes: list[str] = []
    seen: set[str] = set()
    found = {_norm(u): u for u in sources}
    for item in raw:
        item = {k: (None if v == "" and k in ("dates_start", "dates_end") else v) for k, v in item.items()}
        facts = [{**f, "url": found[_norm(f["url"])]} for f in item.get("facts") or []
                 if isinstance(f, dict) and isinstance(f.get("url"), str) and _norm(f["url"]) in found]
        dropped = len(item.get("facts") or []) - len(facts)
        try:
            c = Candidate.model_validate({**item, "facts": facts})
        except ValidationError as e:
            notes.append(f"{item.get('id', '?')}: dropped ({dropped} fact(s) without a found source; "
                         f"{e.errors()[0]['loc'][0]}: {e.errors()[0]['msg']})")
            continue
        if c.id in seen:
            notes.append(f"{c.id}: duplicate id, dropped")
            continue
        if c.dates_start and c.dates_end and c.dates_end < c.dates_start:
            notes.append(f"{c.id}: ends before it starts, dropped")
            continue
        if c.dates_end and c.dates_end < span.days[0]:
            notes.append(f"{c.id}: over before the week starts, dropped")
            continue
        if dropped:
            notes.append(f"{c.id}: {dropped} fact(s) without a found source removed")
        seen.add(c.id)
        kept.append(c)
    return kept, notes


async def scout(call: Call, span: WeekSpan, *, today: date, ideas: list[dict[str, Any]], airports: list[str],
                n: int = 12, max_searches: int = 10) -> tuple[list[Candidate], list[str], dict[str, Any]]:
    answer = await call(SYSTEM, _prompt(span, today, n, ideas, airports), REPORT, search=True,
                        max_searches=max_searches)
    raw = answer.data.get("candidates")
    if isinstance(raw, str):                        # some answers arrive as a JSON string
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not isinstance(raw, list):
        raise PlanningError("the research returned no candidate list")
    kept, notes = check_candidates(raw, answer.sources, span)
    return kept, notes, answer.usage
