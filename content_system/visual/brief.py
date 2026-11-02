"""
The photo brief in a week's manifest, and the image prompt made from it with Scaler's rules.

    [post.image]
    scene   = "Table Mountain at golden hour over Clifton, the Atlantic below"   # the defining moment
    vantage = "a villa terrace above Clifton beach"                              # where the client is
    device  = "the edge of a teak sun lounger with a folded towel"               # foreground, from there
    time    = "golden hour"                     # optional
    people  = "none"                            # "none" (default) or "few": small, left, out of focus

The rules (his, Oct 2026): no crowds — the client's own vantage, never the public's; the event's
defining moment in one perspective, one whole scene (no table with the event pasted behind, no two
photos joined); at a sports event the client watches; a hotel post shows the hotel itself; people
not by default, and when they appear they are small, on the left, out of focus — the brand sits on
the right; the foreground device changes from post to post (empty armchairs everywhere read as a
template). The design puts the logo and the headline in the upper half: it must stay calm.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ImageBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene: str = Field(min_length=10, max_length=300)
    vantage: str = Field(min_length=5, max_length=200)
    device: str = Field(min_length=3, max_length=120)
    time: str = Field(default="", max_length=60)
    people: Literal["none", "few"] = "none"
    avoid: str = Field(default="", max_length=200)     # anything else to keep out of this one


RULES = """\
Rules for the photograph:
- One single photograph, one camera, one perspective: a real moment, not a composite, not a collage, \
not two images joined, no background pasted behind a foreground.
- The viewpoint is the traveller's own, private vantage: never the crowd's view, never a crowd in the frame.
- The subject is the defining moment of the place or the event, seen from there.
- The upper 45% of the frame is calm and uncluttered (sky, water, soft distant architecture): a headline \
is set there. The main subject sits in the middle and lower part.
- No text anywhere: no letters, signs, logos, brand names, numbers, watermarks or captions.
- Photorealistic, natural light, editorial travel photography, sharp, 9:16 vertical.
"""

PEOPLE = {
    "none": "- No people at all.",
    "few": "- At most two or three people, small, on the left third only, out of focus and turned away; "
           "nobody in the right half and nobody in the foreground.",
}


def prompt(brief: ImageBrief, *, attempt_note: str = "") -> str:
    lines = [
        f"A vertical editorial travel photograph. {brief.scene.strip().rstrip('.')}.",
        f"Seen from {brief.vantage.strip().rstrip('.')}.",
        f"In the near foreground, from that same place: {brief.device.strip().rstrip('.')}.",
    ]
    if brief.time:
        lines.append(f"Light: {brief.time.strip().rstrip('.')}.")
    lines += ["", RULES.rstrip(), PEOPLE[brief.people]]
    if brief.avoid:
        lines.append(f"- Also avoid: {brief.avoid.strip().rstrip('.')}.")
    if attempt_note:
        lines += ["", f"A previous attempt was rejected because: {attempt_note}. Avoid that."]
    return "\n".join(lines)


def device_key(device: str) -> str:
    return " ".join(device.lower().replace(",", " ").split())


# Words that don't make a foreground element different: "the teak lounger" is "a teak lounger",
# "two empty armchairs" is "an empty armchair".
_FILLER = frozenset([
    "a", "an", "the", "this", "that", "these", "those", "some", "any", "each", "every",
    "its", "their", "his", "her", "our", "your",
    "of", "with", "on", "in", "at", "by", "from", "to", "for", "into", "onto", "near", "beside", "next",
    "under", "over", "above", "below", "behind", "and", "or",
    "one", "two", "three", "four", "five", "six", "pair", "couple", "set", "few", "several", "many", "single",
])


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith(("sses", "shes", "ches", "xes")):
        return word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def device_words(device: str) -> frozenset[str]:
    """The words that name the element: no punctuation, no articles or counts, singular."""
    words = re.findall(r"[a-z0-9]+", device.lower())          # "sun-lounger." → sun, lounger
    return frozenset(_singular(w) for w in words if w not in _FILLER)


def same_device(a: str, b: str) -> bool:
    """Close enough to read as the same foreground element: the same words, one inside the other
    ("an empty armchair" in "an empty leather armchair"), or mostly the same words."""
    x, y = device_words(a), device_words(b)
    if not x or not y:
        return device_key(a) == device_key(b)
    return x <= y or y <= x or len(x & y) / len(x | y) >= 0.6


def week_problems(briefs: list[tuple[str, ImageBrief | None]]) -> list[str]:
    """The foreground device must change from post to post within a week."""
    seen: list[tuple[str, str]] = []
    out = []
    for label, b in briefs:
        if b is None:
            continue
        twin = next((other for other, device in seen if same_device(b.device, device)), None)
        if twin is not None:
            out.append(f"{label}: the foreground device {b.device!r} repeats {twin} — use a different one")
        else:
            seen.append((label, b.device))
    return out
