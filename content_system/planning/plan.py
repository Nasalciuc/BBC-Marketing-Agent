"""
A week's plan from start to finish, and its manifest: research → choice → one draft per post → the
whole week checked by the manifest loader (week mix, photo devices, captions) before it is offered.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from content_system.manifest import ManifestError, campaign_id_for, load_week
from content_system.planning.curate import curate
from content_system.planning.llm import Call, PlanningError
from content_system.planning.research import Candidate, WeekSpan, scout
from content_system.planning.write import slug, write_post

ID_TAG = "V4"


@dataclass
class Plan:
    week: str
    posts: list[dict[str, Any]]                 # manifest [[post]] tables, in publishing order
    sources: list[list[str]]                    # the fact URLs behind each post
    manifest: str                               # the TOML, checked by load_week
    notes: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    idea_ids: list[int] = field(default_factory=list)


DATE_KEYS = ("day", "dates_start", "dates_end")


def _toml_value(v: Any, *, is_date: bool = False) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    text = str(v)
    if is_date:
        return date.fromisoformat(text).isoformat()  # a TOML date, as in the hand-written manifests
    # JSON string escapes are valid TOML basic strings; TOML also forbids DEL, which JSON leaves as is
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def to_toml(span: WeekSpan, posts: list[dict[str, Any]], *, header: str = "", id_tag: str = ID_TAG) -> str:
    lines = [f"# {header}" if header else f"# {span.name} — planned by content_system plan", "",
             f'week = "{span.name}"', f'photos = "{span.photos}"', f'id_tag = "{id_tag}"']
    for post in posts:
        lines += ["", "[[post]]"]
        lines += [f"{k} = {_toml_value(v, is_date=k in DATE_KEYS)}" for k, v in post.items()
                  if k not in ("design", "image")]
        for table in ("design", "image"):
            if post.get(table):
                lines += ["", f"[post.{table}]"] + [f"{k} = {_toml_value(v)}" for k, v in post[table].items()]
    return "\n".join(lines) + "\n"


def check_week(text: str) -> list[str]:
    """Everything the manifest loader checks, except the photos (they are made after the approval)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.toml"
        path.write_text(text, encoding="utf-8")
        try:
            load_week(path)
        except ManifestError as e:
            return e.problems
    return []


def _add(total: dict[str, Any], usage: dict[str, Any]) -> None:
    for k, v in usage.items():
        if isinstance(v, int):
            total[k] = total.get(k, 0) + v
        else:
            total.setdefault("models", set()).add(v)


def priced_airports() -> set[str]:
    """The destinations the pricing engine gives a business fare for from JFK (data/airports.json)."""
    from services.design_system import ORIGIN_IATA
    from services.pricing_engine import _load_airports, calculate_price

    return {code for code in _load_airports() if code != ORIGIN_IATA
            and calculate_price(ORIGIN_IATA, code, "round_trip", "business")}


def photo_name(pick_day: date, candidate_id: str, tag: str) -> str:
    """The photo's file name: day, subject and the plan's tag — a later plan never reuses an older
    plan's photo (made from another brief) by accident."""
    return f"{slug(f'{pick_day:%a}_{candidate_id}')[:32]}_{tag}" if tag else slug(f"{pick_day:%a}_{candidate_id}")


async def make_plan(call: Call, span: WeekSpan, *, today: date, ideas: list[dict[str, Any]],
                    airports: set[str], candidates_n: int = 12, max_searches: int = 10, photo_tag: str = "") -> Plan:
    if span.days[0] <= today:
        raise PlanningError(f"{span.name} starts publishing on {span.days[0]}: plan a week that hasn't started")
    usage: dict[str, Any] = {}
    candidates, notes, u = await scout(call, span, today=today, ideas=ideas, airports=sorted(airports),
                                       n=candidates_n, max_searches=max_searches)
    _add(usage, u)
    if len(candidates) < len(span.days):
        raise PlanningError(f"only {len(candidates)} usable candidates for {len(span.days)} days: " + "; ".join(notes))
    picks, u = await curate(call, span, candidates, ideas=ideas, airports=airports)
    _add(usage, u)
    by_id: dict[str, Candidate] = {c.id: c for c in candidates}
    idea_text = {int(i["id"]): str(i["text"]) for i in ideas}
    posts: list[dict[str, Any]] = []
    sources: list[list[str]] = []
    devices: dict[str, str] = {}
    photos: set[str] = set()
    for index, pick in enumerate(picks, 1):
        cand = by_id[pick.candidate_id]
        photo = photo_name(pick.day, cand.id, photo_tag)
        while photo in photos:
            photo += "_2"
        photos.add(photo)
        spec, u = await write_post(call, pick, cand, campaign_id=campaign_id_for(span.name, ID_TAG, index),
                                   photo=photo, used_devices=devices, airports=airports,
                                   idea_text=idea_text.get(cand.idea_id or -1, ""))
        _add(usage, u)
        devices[spec["image"]["device"]] = f"{pick.day:%a}"
        posts.append(spec)
        sources.append(sorted({f.url for f in cand.facts}))
    manifest = to_toml(span, posts)
    problems = check_week(manifest)
    if problems:
        raise PlanningError("the week doesn't pass the manifest checks: " + "; ".join(problems))
    used_ideas = sorted({by_id[p.candidate_id].idea_id for p in picks if by_id[p.candidate_id].idea_id})
    if "models" in usage:
        usage["models"] = sorted(usage["models"])
    return Plan(span.name, posts, sources, manifest, notes, usage, used_ideas)


def kept_manifest(span: WeekSpan, posts: list[dict[str, Any]], dropped: list[int], header: str = "") -> str:
    """The approved plan as the week's manifest: the posts Betty kept, in order (1-based `dropped`)."""
    kept = [p for i, p in enumerate(posts, 1) if i not in set(dropped)]
    return to_toml(span, kept, header=header)

