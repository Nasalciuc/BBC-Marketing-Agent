"""
The choice: one candidate per publishing day, with a design style. The model proposes; the rules
below decide, and a refused choice is asked again with the reasons (at most 3 times):

  · one post per publishing day, each candidate once
  · the week mix of the design system: ≤60% with a fare, never three priced in a row, a style at most twice
  · varied content: a type at most twice, at least three different types
  · a priced style needs a destination airport the pricing engine knows
  · an event is not over on the day it is posted
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from content_system.planning.llm import Call, PlanningError
from content_system.planning.research import Candidate, WeekSpan
from services.design_system import CONTENT_TO_STYLE, PRICED_STYLES, Style, validate_week_mix

ATTEMPTS = 3


class Pick(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day: date
    candidate_id: str
    style: Style
    angle: str = ""


CHOOSE = {
    "name": "choose_posts",
    "description": "One candidate per publishing day, with its design style and the angle of the post.",
    "input_schema": {"type": "object", "required": ["picks"], "properties": {"picks": {
        "type": "array", "items": {"type": "object", "required": ["day", "candidate_id", "style", "angle"],
                                   "properties": {
            "day": {"type": "string", "description": "YYYY-MM-DD, one of the publishing days"},
            "candidate_id": {"type": "string"},
            "style": {"type": "string", "enum": [s.value for s in Style]},
            "angle": {"type": "string", "description": "one sentence: what this post is about"},
        }}}}},
}

SYSTEM = """You plan a week of posts for the WhatsApp channel of Buy Business Class (business-class flights \
from New York). Choose one candidate per publishing day and a design style for each.

Styles: offer (an event, with a fare card) · price_hero (a fare as the hero) · multi (several destinations, \
one fare) · editorial (a destination or hotel, no fare) · fact (news, an award, a number, no fare) · \
minimal (brand, lifestyle, no fare).
Rules: at most 60% of the posts with a fare (offer, price_hero, multi); never three with a fare in a row; \
a style at most twice; a content type at most twice and at least three types; a priced style only for a \
destination we can price; an event must not be over on its day. Include Betty's ideas where they fit. \
Usual fit by type: {fit}. Call choose_posts."""


def problems(picks: list[Pick], candidates: dict[str, Candidate], span: WeekSpan, airports: set[str]) -> list[str]:
    out: list[str] = []
    if len(picks) != len(span.days):
        out.append(f"{len(picks)} posts for {len(span.days)} publishing days")
    days = [p.day for p in picks]
    if sorted(days) != list(span.days)[: len(days)] or len(set(days)) != len(days):
        out.append("the days must be exactly the publishing days, one post each: "
                   + ", ".join(d.isoformat() for d in span.days))
    used = Counter(p.candidate_id for p in picks)
    out += [f"{cid} is used {n} times" for cid, n in used.items() if n > 1]
    for p in picks:
        c = candidates.get(p.candidate_id)
        if c is None:
            out.append(f"{p.day}: no candidate {p.candidate_id!r}")
            continue
        if p.style in PRICED_STYLES and c.airport not in airports:
            out.append(f"{p.day}: {p.style} needs a fare, and {c.airport or 'no airport'} can't be priced")
        if c.dates_end and c.dates_end < p.day:
            out.append(f"{p.day}: {c.id} is over by then ({c.dates_end})")
    ordered = sorted(picks, key=lambda p: p.day)
    out += validate_week_mix([p.style for p in ordered])
    types = Counter(candidates[p.candidate_id].type for p in picks if p.candidate_id in candidates)
    out += [f"type {t} used {n} times (max 2)" for t, n in types.items() if n > 2]
    if len(picks) >= 3 and len(types) < 3:
        out.append(f"only {len(types)} content type(s): at least 3")
    return out


def _prompt(span: WeekSpan, candidates: list[Candidate], ideas: list[dict[str, Any]], feedback: str) -> str:
    rows = [json.dumps({"id": c.id, "type": c.type, "title": c.title, "city": c.city, "airport": c.airport,
                        "dates": [str(c.dates_start or ""), str(c.dates_end or "")], "why_now": c.why_now,
                        "idea_id": c.idea_id}, ensure_ascii=False) for c in candidates]
    text = (f"Week {span.name}. Publishing days: " + ", ".join(d.isoformat() for d in span.days) + "\n\nCandidates:\n"
            + "\n".join(rows))
    if ideas:
        text += "\n\nBetty's ideas: " + "; ".join(f"{i['id']}: {i['text']}" for i in ideas)
    if feedback:
        text += f"\n\nYour previous choice was refused: {feedback}\nChoose again."
    return text


async def curate(call: Call, span: WeekSpan, candidates: list[Candidate], *, ideas: list[dict[str, Any]],
                 airports: set[str]) -> tuple[list[Pick], dict[str, Any]]:
    by_id = {c.id: c for c in candidates}
    fit = ", ".join(f"{t} → {s.value}" for t, s in sorted(CONTENT_TO_STYLE.items()))
    feedback, usage = "", {}
    for _ in range(ATTEMPTS):
        answer = await call(SYSTEM.format(fit=fit), _prompt(span, candidates, ideas, feedback), CHOOSE)
        for k, v in answer.usage.items():
            usage[k] = usage.get(k, 0) + v if isinstance(v, int) else v
        try:
            picks = sorted((Pick.model_validate(p) for p in answer.data.get("picks") or []), key=lambda p: p.day)
        except ValidationError as e:
            feedback = f"invalid answer: {e.errors()[0]['msg']}"
            continue
        found = problems(picks, by_id, span, airports)
        if not found:
            return picks, usage
        feedback = "; ".join(found)
    raise PlanningError(f"no valid choice in {ATTEMPTS} attempts: {feedback}")

