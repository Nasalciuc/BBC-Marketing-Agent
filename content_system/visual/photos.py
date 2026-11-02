"""
One post's photo from its brief: N candidates, each checked without AI and then by the vision model;
the best one that passes becomes <photos>/<photo>.jpg. Every run keeps what it tried in its own
folder, <photos>/.candidates/<photo>/<UTC time>/ (1.jpg, 2.jpg … and qc.json, the report), so a person
can see why one was chosen — or pick another by copying it over. Nothing from an earlier run is
overwritten.

A later candidate is told why the earlier one failed (what was wrong with the picture, not API
errors). A photo already in the folder is kept unless `again`; then the old one moves into that run's
folder as previous.<ext>. The new photo is written next to it and renamed into place, so an
interrupted run never leaves half a file under the photo's name.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from content_system.manifest import PlannedPost, find_photo
from content_system.visual.brief import ImageBrief, prompt
from content_system.visual.models import Generate, Judge, ModelSetupError
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
    folder: str = ""                 # this run's .candidates/<photo>/<time>/


class Verdict(BaseModel):
    """The vision model's answers, read strictly: "false" is not False, "8/10" is not 8."""
    model_config = ConfigDict(extra="ignore")
    one_photograph: StrictBool
    crowd: StrictBool
    people: Literal["none", "few_left_small_blurred", "elsewhere"]
    text_or_logo: StrictBool
    matches_brief: StrictBool
    calm_top: StrictBool
    artifacts: StrictBool
    score: StrictInt = Field(ge=1, le=10)
    problems: list[StrictStr] = Field(default_factory=list, max_length=30)


MALFORMED = "vision check failed: malformed answer"
NOT_ABOUT_THE_PICTURE = ("generation failed", "vision check failed", "(model)")


def read_verdict(raw: Any) -> Verdict | None:
    try:
        return Verdict.model_validate(raw)
    except ValidationError:
        return None


def verdict_problems(v: dict[str, Any] | Verdict, brief: ImageBrief) -> list[str]:
    """Whether a candidate passes is decided here, from the model's answers — not by the model.
    An answer that is missing or not of its type fails."""
    verdict = v if isinstance(v, Verdict) else read_verdict(v)
    if verdict is None:
        return [MALFORMED]
    out: list[str] = []
    if not verdict.one_photograph:
        out.append("not one single photograph (a collage, joined or pasted)")
    if verdict.crowd:
        out.append("a crowd in the frame")
    if verdict.people == "elsewhere":
        out.append("people where they must not be")
    elif verdict.people != "none" and brief.people == "none":
        out.append("people, and the brief wants none")
    if verdict.text_or_logo:
        out.append("text or a logo in the picture")
    if not verdict.matches_brief:
        out.append("it doesn't show the brief (scene, vantage or foreground element)")
    if not verdict.calm_top:
        out.append("the upper part is busy: no calm space for the logo and the headline")
    if verdict.artifacts:
        out.append("visible generation errors")
    if verdict.score < PASS_SCORE:
        out.append(f"scored {verdict.score}/10 (needs {PASS_SCORE})")
    return out


def _run_folder(work: Path, photo: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder, n = work / photo / stamp, 2
    while folder.exists():
        folder, n = work / photo / f"{stamp}-{n}", n + 1
    folder.mkdir(parents=True)
    return folder


def _move_aside(existing: Path, run: Path) -> Path:
    target, n = run / f"previous{existing.suffix}", 2
    while target.exists():
        target, n = run / f"previous-{n}{existing.suffix}", n + 1
    shutil.move(str(existing), str(target))
    return target


def _put_in_place(best: Path, final: Path, existing: Path | None, run: Path) -> None:
    part = final.with_name(f".{final.name}.part")
    shutil.copyfile(best, part)
    if existing is not None:
        _move_aside(existing, run)
    os.replace(part, final)


async def make_photo(post: PlannedPost, folder: Path, generate: Generate, judge: Judge, *,
                     candidates: int = CANDIDATES, again: bool = False) -> PhotoResult:
    brief = post.spec.image
    cid = post.campaign_id
    if brief is None:
        return PhotoResult(cid, "no_brief", problems=["no [post.image] brief in the manifest"])
    existing = find_photo(folder, post.spec.photo)
    if existing is not None and not again:
        return PhotoResult(cid, "kept", photo=str(existing))
    run = await asyncio.to_thread(_run_folder, folder / ".candidates", post.spec.photo)
    tried: list[Candidate] = []
    note = ""
    for i in range(1, candidates + 1):
        path = run / f"{i}.jpg"
        try:
            data = await generate(prompt(brief, attempt_note=note))
            img, size = await asyncio.to_thread(fit, data)
        except ModelSetupError:
            raise                           # a wrong key or model: every candidate would fail and cost
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
                raw = await judge(encoded, brief)
            except ModelSetupError:
                raise
            except Exception as e:  # noqa: BLE001
                c.problems.append(f"vision check failed: {type(e).__name__}: {e}"[:300])
            else:
                c.verdict = raw if isinstance(raw, dict) else {"answer": str(raw)[:500]}
                verdict = read_verdict(raw)
                c.problems += verdict_problems(verdict if verdict is not None else c.verdict, brief)
                if verdict is not None:
                    c.score = verdict.score
                    if c.problems:
                        c.problems += [f"(model) {p}" for p in verdict.problems[:3]]
        c.ok = not c.problems
        tried.append(c)
        if not c.ok:
            about_it = [p for p in c.problems if not p.startswith(NOT_ABOUT_THE_PICTURE)]
            if about_it:
                note = "; ".join(about_it)[:300]
    report = {"campaign_id": cid, "photo": post.spec.photo, "brief": brief.model_dump(),
              "at": datetime.now(UTC).isoformat(timespec="seconds"), "candidates": [asdict(c) for c in tried]}
    best = max((c for c in tried if c.ok), key=lambda c: c.score, default=None)
    if best is None:
        report["chosen"] = None
        await asyncio.to_thread(_write_json, run / "qc.json", report)
        reasons = sorted({p for c in tried for p in c.problems if not p.startswith("(model)")})
        return PhotoResult(cid, "failed", candidates=tried, problems=reasons, folder=str(run))
    final = folder / f"{post.spec.photo}.jpg"
    await asyncio.to_thread(_put_in_place, Path(best.path), final, existing, run)
    report["chosen"] = best.index
    await asyncio.to_thread(_write_json, run / "qc.json", report)
    return PhotoResult(cid, "made", photo=str(final), candidates=tried, folder=str(run))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
