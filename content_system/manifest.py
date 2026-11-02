"""
Week manifest — the contract between whoever plans a week and the content graph.

Today a person writes it (weeks/2026-W43.toml); later the agents will. One [[post]] per day:

    week   = "2026-W43"
    photos = "assets/curated/week_2026_10_19"

    [[post]]
    day = 2026-10-21              # publishing date, inside the ISO week above
    photo = "monaco_terrace"      # file in `photos` without extension (.jpg .jpeg .png .webp)
    label = "Monaco"              # short name for the calendar and the preview
    name = "…"                    # campaign name (campaigns.event_name)
    city = "Monaco"
    caption_city = "Nice"         # "Ask us for today's fare to Nice." on no-fare styles
    route = "JFK → NCE"
    body = '''…'''                # the post text; the fare line and the contact line are added here
    [post.design]                 # services.design_system.PostDesign fields, except `background`
    style = "offer"
    …
    [post.image]                  # optional: the photo brief — `photos` makes the photo from it
    scene = "…"                   # (content_system/visual/brief.py)
    vantage = "…"
    device = "…"

Everything is checked before anything is rendered or sent: the design fields per style, the week
mix (≤60% priced, never three priced in a row, a style at most twice), the caption (no "$", no
Markdown characters, Telegram's 1024 limit with the review line), dates inside the week.
"""
from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from content_system.visual.brief import ImageBrief, week_problems
from prompts.system_prompts import CONTACT_LINE_LUX
from services.design_system import PostDesign, Style, caption_ending, validate_week_mix

ROOT = Path(__file__).resolve().parent.parent
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP")
CAPTION_LIMIT = 1024                 # Telegram photo caption, counted in UTF-16 units
MARKDOWN_CHARS = frozenset("_*`[")   # formatting characters on Telegram and on WhatsApp
CATEGORY = {
    Style.FACT: "news", Style.OFFER: "event", Style.EDITORIAL: "destination",
    Style.PRICE_HERO: "deal", Style.MULTI: "multi", Style.MINIMAL: "brand",
}


class ManifestError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class PostSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    photo: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    label: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1)
    caption_city: str = Field(min_length=1)
    route: str = Field(min_length=1)
    category: str | None = None
    dates_start: date | None = None
    dates_end: date | None = None
    body: str = Field(min_length=1)
    design: dict
    image: ImageBrief | None = None      # the photo brief, when the photo is to be generated

    @model_validator(mode="after")
    def _event_dates(self) -> PostSpec:
        if self.dates_start and self.dates_end and self.dates_end < self.dates_start:
            raise ValueError("dates_end is before dates_start")
        if "background" in self.design:
            raise ValueError("design.background comes from `photo`, don't set it")
        return self


class WeekManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    week: str = Field(pattern=r"^\d{4}-W\d{2}$")
    photos: str = Field(min_length=1)
    id_tag: str = Field(default="V4", pattern=r"^[A-Z0-9]{1,6}$")
    posts: list[PostSpec] = Field(min_length=1, max_length=7)


@dataclass(frozen=True)
class PlannedPost:
    index: int               # 1-based, publishing order
    campaign_id: str         # 2026-W43-V4-001
    spec: PostSpec
    design: PostDesign       # background = the photo path (it may not exist yet)
    caption: str             # body + fare line + contact line
    scheduled_day: str       # "Wed" — same convention as W38–W42
    stem: str                # "wed_monaco_terrace" — output file name

    @property
    def day_label(self) -> str:
        return self.spec.day.strftime("%a %d %b")


@dataclass(frozen=True)
class Week:
    manifest: WeekManifest
    posts: list[PlannedPost]
    path: Path
    sha256: str

    @property
    def name(self) -> str:
        return self.manifest.week

    @property
    def photos_dir(self) -> Path:
        return (ROOT / self.manifest.photos).resolve()


# ── captions ──────────────────────────────────────────────────────────────────

def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def review_line(campaign_id: str, version: int) -> str:
    """Appended to the caption on the Telegram review message only."""
    return f"\n\n📋 {campaign_id} · v{version}"


def compose_caption(body: str, style: Style, caption_city: str) -> str:
    """Approved body + the style's fare line + the contact line. The fare itself is only on the image."""
    return body.strip() + "\n\n" + caption_ending(style, caption_city) + "\n\n" + CONTACT_LINE_LUX


def caption_from_edit(text: str, style: Style, caption_city: str) -> str:
    """A reply that already carries the contact line is the whole caption; otherwise it is a new body."""
    text = text.strip()
    if CONTACT_LINE_LUX.strip() in text:
        return text
    ending = caption_ending(style, caption_city)
    if text.endswith(ending):
        return text + "\n\n" + CONTACT_LINE_LUX
    return compose_caption(text, style, caption_city)


def caption_problems(caption: str, campaign_id: str) -> list[str]:
    problems = []
    if not caption.strip():
        problems.append("the caption is empty")
    if "$" in caption:
        problems.append("contains '$' — the fare belongs on the image")
    bad = sorted(MARKDOWN_CHARS & set(caption))
    if bad:
        problems.append(f"contains {' '.join(bad)} — Telegram and WhatsApp read them as formatting")
    n = utf16_len(caption + review_line(campaign_id, 99))
    if n > CAPTION_LIMIT:
        problems.append(f"{n} characters with the review line, max {CAPTION_LIMIT}: shorten by {n - CAPTION_LIMIT}")
    return problems


# ── loading ───────────────────────────────────────────────────────────────────

def campaign_id_for(week: str, id_tag: str, index: int) -> str:
    return f"{week}-{id_tag}-{index:03d}"


def find_photo(folder: Path, stem: str) -> Path | None:
    for ext in PHOTO_EXTENSIONS:
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _validation_lines(prefix: str, err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        where = ".".join(str(x) for x in e["loc"]) or "-"
        out.append(f"{prefix}{where}: {e['msg']}")
    return out


def load_week(path: str | Path) -> Week:
    """Read and check a manifest. Raises ManifestError with every problem found, not just the first."""
    path = Path(path)
    raw_bytes = path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ManifestError([f"{path.name}: not valid TOML ({e})"]) from None
    data["posts"] = data.pop("post", [])
    try:
        manifest = WeekManifest.model_validate(data)
    except ValidationError as e:
        raise ManifestError(_validation_lines("", e)) from None

    problems: list[str] = []
    folder = (ROOT / manifest.photos).resolve()
    days = [p.day for p in manifest.posts]
    if days != sorted(days) or len(set(days)) != len(days):
        problems.append("days must be distinct and in publishing order")

    planned: list[PlannedPost] = []
    for i, spec in enumerate(manifest.posts, 1):
        cid = campaign_id_for(manifest.week, manifest.id_tag, i)
        tag = f"post {i} ({spec.photo}): "
        if _iso_week(spec.day) != manifest.week:
            problems.append(f"{tag}{spec.day} is in {_iso_week(spec.day)}, not {manifest.week}")
        photo = find_photo(folder, spec.photo) or folder / f"{spec.photo}.jpg"
        try:
            design = PostDesign(background=str(photo), **spec.design)
        except ValidationError as e:
            problems.extend(_validation_lines(f"{tag}design.", e))
            continue
        caption = compose_caption(spec.body, design.style, spec.caption_city)
        problems.extend(tag + p for p in caption_problems(caption, cid))
        planned.append(PlannedPost(
            index=i, campaign_id=cid, spec=spec, design=design, caption=caption,
            scheduled_day=spec.day.strftime("%a"),
            stem=f"{spec.day.strftime('%a').lower()}_{spec.photo}",
        ))

    if len(planned) == len(manifest.posts):
        problems.extend("week mix: " + p for p in validate_week_mix([p.design.style for p in planned]))
        problems.extend("photos: " + p for p in week_problems([(f"post {p.index} ({p.spec.photo})", p.spec.image)
                                                                for p in planned]))
    if problems:
        raise ManifestError(problems)
    return Week(manifest=manifest, posts=planned, path=path, sha256=hashlib.sha256(raw_bytes).hexdigest())


def photo_report(week: Week) -> tuple[list[str], list[str]]:
    """(errors, warnings) about the photos: missing files are errors, size and ratio are warnings."""
    from PIL import Image

    errors, warnings = [], []
    for p in week.posts:
        found = find_photo(week.photos_dir, p.spec.photo)
        if found is None:
            hint = " — `python -m content_system photos` makes it from [post.image]" if p.spec.image else ""
            errors.append(f"{p.day_label}: {p.spec.photo}.jpg missing in {week.manifest.photos}/{hint}")
            continue
        with Image.open(found) as im:
            w, h = im.size
        if w < 1080 or h < 1920:
            warnings.append(f"{p.day_label}: {found.name} is {w}×{h}, under 1080×1920 — it will look soft")
        ratio = (w / h) / (9 / 16)
        if abs(ratio - 1) > 0.03:
            cut = "the sides" if ratio > 1 else "top and bottom"
            warnings.append(f"{p.day_label}: {found.name} is not 9:16 — the crop cuts {cut}")
    return errors, warnings
