"""
One post's photo from its brief: N candidates, each checked without AI and then by the vision model;
the best one that passes becomes <photos>/<photo>.jpg. Everything tried stays in
<photos>/.candidates/ with a report (<photo>.qc.json), so a person can see why one was chosen —
or pick another by copying it over.

A later candidate is told why the earlier one failed. A photo already in the folder is kept unless
`again`; then the old one moves to .candidates/<photo>.previous.<ext> instead of being lost.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from content_system.manifest import PlannedPost, find_photo
from content_system.visual.brief import ImageBrief, prompt
from content_system.visual.models import Generate, Judge
from content_system.visual.qc import fit, inspect, jpeg

log = logging.getLogger("bbc.content.visual")
PASS_SCORE = 7
CANDIDATES = 3


@dataclass
class Candidate:
    index: int
    path: str
    ok: bool
    score: int = 0
    problems: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    verdict: dict[str, Any] | None = None


@dataclass
class PhotoResult:
    campaign_id: str
    status: Literal["made", "kept", "failed", "no_brief"]
    photo: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def verdict_problems(v: dict[str, Any], brief: ImageBrief) -> list[str]:
    """Whether a candidate passes is decided here, from the model's answers — not by the model."""
    out: list[str] = []
    if not v.get("one_photograph", False):
        out.append("not one single photograph (a collage, joined or pasted)")
    if v.get("crowd", True):
        out.append("a crowd in the frame")
    people = v.get("people", "elsewhere")
    if people == "elsewhere" or (people != "none" and brief.people == "none"):
        out.append("people where they must not be" if people == "elsewhere" else "people, and the brief wants none")
    if v.get("text_or_logo", True):
        out.append("text or a logo in the picture")
    if not v.get("matches_brief", False):
        out.append("it doesn't show the brief (scene, vantage or foreground element)")
    if v.get("artifacts", True):
        out.append("visible generation errors")
    score = int(v.get("score") or 0)
    if score < PASS_SCORE:
        out.append(f"scored {score}/10 (needs {PASS_SCORE})")
    return out


async def make_photo(post: PlannedPost, folder: Path, generate: Generate, judge: Judge, *,
                     candidates: int = CANDIDATES, again: bool = False) -> PhotoResult:
    brief = post.spec.image
    cid = post.campaign_id
    if brief is None:
        return PhotoResult(cid, "no_brief", problems=["no [post.image] brief in the manifest"])
    existing = find_photo(folder, post.spec.photo)
    if existing is not None and not again:
        return PhotoResult(cid, "kept", photo=str(existing))
    work = folder / ".candidates"
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    tried: list[Candidate] = []
    note = ""
    for i in range(1, candidates + 1):
        path = work / f"{post.spec.photo}.{i}.jpg"
        try:
            data = await generate(prompt(brief, attempt_note=note))
            img, size = await asyncio.to_thread(fit, data)
        except Exception as e:  # noqa: BLE001 — one failed candidate is reported; the next one is tried
            log.warning("%s candidate %s: generation failed: %s", cid, i, e)
            tried.append(Candidate(i, "", False, problems=[f"generation failed: {type(e).__name__}: {e}"[:300]]))
            continue
        check = await asyncio.to_thread(inspect, img, size)
        encoded = await asyncio.to_thread(jpeg, img)
        await asyncio.to_thread(path.write_bytes, encoded)
        c = Candidate(i, str(path), False, problems=list(check.problems), notes=check.notes)
        if check.ok:
            try:
                c.verdict = await judge(encoded, brief)
            except Exception as e:  # noqa: BLE001
                c.problems.append(f"vision check failed: {type(e).__name__}: {e}"[:300])
            else:
                c.score = int(c.verdict.get("score") or 0)
                c.problems += verdict_problems(c.verdict, brief)
                c.problems += [f"(model) {p}" for p in (c.verdict.get("problems") or [])[:3] if c.problems]
        c.ok = not c.problems
        tried.append(c)
        if not c.ok:
            note = "; ".join(p for p in c.problems if not p.startswith("(model)"))[:300]
    report = {"campaign_id": cid, "photo": post.spec.photo, "brief": brief.model_dump(),
              "at": datetime.now(UTC).isoformat(timespec="seconds"), "candidates": [asdict(c) for c in tried]}
    best = max((c for c in tried if c.ok), key=lambda c: c.score, default=None)
    if best is None:
        report["chosen"] = None
        await asyncio.to_thread(_write_json, work / f"{post.spec.photo}.qc.json", report)
        reasons = sorted({p for c in tried for p in c.problems if not p.startswith("(model)")})
        return PhotoResult(cid, "failed", candidates=tried, problems=reasons)
    final = folder / f"{post.spec.photo}.jpg"
    if existing is not None:
        await asyncio.to_thread(shutil.move, str(existing), str(work / f"{post.spec.photo}.previous{existing.suffix}"))
    await asyncio.to_thread(shutil.copyfile, best.path, final)
    report["chosen"] = best.index
    await asyncio.to_thread(_write_json, work / f"{post.spec.photo}.qc.json", report)
    return PhotoResult(cid, "made", photo=str(final), candidates=tried)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
