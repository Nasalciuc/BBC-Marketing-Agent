"""
Design system v4 — vertical 1080×1920 posts for WhatsApp / Telegram.

Six styles, one grammar:
  OFFER       headline + boarding pass (route, fare, class)     EVENT, CABIN with a fare
  EDITORIAL   big headline, route signature, one line, no fare  DESTINATION, HOTEL, LOUNGE
  FACT        a sentence led by a key word ("Thirteen times")   NEWS, AWARD, TREND
  PRICE_HERO  the fare is the headline                          DEAL
  MULTI       the boarding pass with several destinations       MULTI-DESTINATION
  MINIMAL     one sentence + signature                          BRAND, LIFESTYLE

Tone (LIGHT / DARK) is picked from the image itself; fares always come from the pricing engine.
The legacy landscape renderer services.branding_engine.generate_branded_image() is untouched.
"""
from __future__ import annotations

import base64
import html
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger("bbc.design")

ROOT = Path(__file__).resolve().parent.parent
V4_DIR = ROOT / "templates" / "v4"
ASSETS = ROOT / "assets"
W, H = 1080, 1920
TONE_ZONE = (285, 620)            # headline area used to decide the tone
MAX_TEXT_BOTTOM = 0.45            # the text block must end within the top 45% of the image
ORIGIN_IATA = "JFK"               # pricing-engine base


class Style(StrEnum):
    OFFER = "offer"
    EDITORIAL = "editorial"
    FACT = "fact"
    PRICE_HERO = "price_hero"
    MULTI = "multi"
    MINIMAL = "minimal"


class Tone(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class Trip(StrEnum):
    ROUND_TRIP = "round_trip"
    ONE_WAY = "one_way"


PRICED_STYLES = {Style.OFFER, Style.PRICE_HERO, Style.MULTI}
CARD_STYLES = {Style.OFFER, Style.MULTI}

CONTENT_TO_STYLE: dict[str, Style] = {
    "event": Style.OFFER, "cabin": Style.OFFER,
    "deal": Style.PRICE_HERO, "multi": Style.MULTI,
    "destination": Style.EDITORIAL, "hotel": Style.EDITORIAL, "lounge": Style.EDITORIAL,
    "news": Style.FACT, "award": Style.FACT, "trend": Style.FACT,
    "brand": Style.MINIMAL, "lifestyle": Style.MINIMAL,
}

from prompts.system_prompts import (  # noqa: E402 — after the constants on purpose
    CONTACT_LINE_LUX,  # noqa: F401 — single source for the contact line
)


class DesignQAError(RuntimeError):
    """The rendered post breaks a layout rule (text too low, headline > 2 lines, misaligned pass)."""


class PostDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: Style
    background: str                                               # local path or https URL (raw image, no text)
    headline: str | None = Field(default=None, max_length=70)     # OFFER, EDITORIAL, MULTI, MINIMAL
    lead: str | None = Field(default=None, max_length=24)         # FACT, accent word(s): "Thirteen times"
    follow: str | None = Field(default=None, max_length=64)       # FACT sentence / PRICE_HERO line
    line: str | None = Field(default=None, max_length=72)         # small line under the route signature
    tag: str | None = Field(default=None, max_length=28)          # OFFER pass tag: "Longchamp · 4 Oct"
    to_iata: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    trip: Trip = Trip.ROUND_TRIP
    destinations: list[str] | None = Field(default=None, min_length=2, max_length=6)   # MULTI city names
    fare_iatas: list[str] | None = Field(default=None, min_length=1, max_length=6)     # MULTI / PRICE_HERO fare pool
    tone: Tone | None = None                                      # None → automatic
    big_headline: bool = False                                    # EDITORIAL / MINIMAL: 92px instead of 84px

    @model_validator(mode="after")
    def _required_per_style(self) -> PostDesign:
        need: dict[Style, tuple[str, ...]] = {
            Style.OFFER: ("headline", "tag", "to_iata"),
            Style.EDITORIAL: ("headline", "line"),
            Style.FACT: ("lead", "follow", "line"),
            Style.PRICE_HERO: ("follow", "line"),
            Style.MULTI: ("headline", "destinations", "fare_iatas"),
            Style.MINIMAL: ("headline", "line"),
        }
        missing = [f for f in need[self.style] if not getattr(self, f)]
        if missing:
            raise ValueError(f"{self.style} requires: {', '.join(missing)}")
        if self.style == Style.PRICE_HERO and not (self.to_iata or self.fare_iatas):
            raise ValueError("price_hero requires to_iata or fare_iatas")
        if self.style not in PRICED_STYLES and (self.to_iata or self.fare_iatas):
            raise ValueError(f"{self.style} is a no-fare style: do not pass to_iata / fare_iatas")
        return self


@dataclass
class RenderResult:
    jpeg: bytes
    tone: Tone
    fare: int | None
    metrics: dict = field(default_factory=dict)


# ── data helpers ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _airports() -> dict[str, str]:
    data = json.loads((ROOT / "data" / "airports.json").read_text(encoding="utf-8"))
    return {a["iata_code"]: a["city"] for a in data}


def city_for(iata: str) -> str:
    try:
        return _airports()[iata]
    except KeyError as exc:
        raise ValueError(f"{iata} is not in data/airports.json") from exc


def fare_for(iatas: list[str], trip: Trip) -> int:
    """Lowest business fare from JFK over the given destinations — the pricing engine is the only source."""
    from services.pricing_engine import calculate_price

    prices = [p for p in (calculate_price(ORIGIN_IATA, i, trip.value, "business") for i in iatas) if p]
    if not prices:
        raise ValueError(f"no fare for {iatas} ({trip})")
    return min(prices)


def _money(v: int) -> str:
    from services.pricing_engine import format_price

    return format_price(v)


# ── image helpers ──────────────────────────────────────────────────────────────

def _load_image(src: str) -> Image.Image:
    if src.startswith(("http://", "https://")):
        import httpx

        r = httpx.get(src, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGB")
    return Image.open(src).convert("RGB")


def _cover(img: Image.Image) -> Image.Image:
    s = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def _rel_lum(a: np.ndarray) -> np.ndarray:
    c = a / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def pick_tone(img: Image.Image, has_card: bool) -> tuple[Tone, float, float]:
    """LIGHT only when the headline area is bright and calm. With a card the data sits on the card,
    so a busier background is tolerated; without a card the text sits on the photo itself."""
    zone = np.asarray(_cover(img)).astype(float)[TONE_ZONE[0]:TONE_ZONE[1], 60:W - 60]
    lum = _rel_lum(zone)
    mean, std = float(lum.mean()), float(lum.std())
    light = (has_card and mean > 0.30 and std < 0.30) or (not has_card and mean > 0.45 and std < 0.20)
    return (Tone.LIGHT if light else Tone.DARK), round(mean, 3), round(std, 3)


def sky_rgb(img: Image.Image) -> tuple[int, int, int]:
    """Average colour of the top of the image, lifted slightly — the LIGHT haze comes from the photo, not a stock cream."""
    c = np.asarray(_cover(img))[0:520].reshape(-1, 3).mean(0)
    return tuple(int(min(255, v * 1.06 + 8)) for v in c)  # type: ignore[return-value]


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _bg_uri(img: Image.Image) -> str:
    buf = BytesIO()
    _cover(img).save(buf, format="JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _logo_uri(tone: Tone) -> str:
    name = "bbc_logo_navy.png" if tone == Tone.LIGHT else "bbc_logo_white.png"
    path = ASSETS / "logos" / name
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run: python scripts/make_logo_variants.py")
    return _data_uri(path, "image/png")


# ── HTML ───────────────────────────────────────────────────────────────────────

_ROUTE = '<div class="route"><i></i><b></b><em></em></div>'


def _e(s: str | None) -> str:
    return html.escape(s or "")


def _pass_html(tag: str, to_iata: str, trip: Trip, fare: int) -> str:
    label = "Round trip" if trip == Trip.ROUND_TRIP else "One way"
    return (
        f'<div class="pass"><div class="meta tag">{_e(tag)}</div>'
        f'<div class="codes"><div class="iata">{ORIGIN_IATA}</div><div class="path">{_ROUTE}</div>'
        f'<div class="iata">{_e(to_iata)}</div></div>'
        f'<div class="meta cities"><span>{_e(city_for(ORIGIN_IATA))}</span><span>{_e(city_for(to_iata))}</span></div>'
        f'<div class="perf"></div><div class="meta flabel">{label}</div>'
        f'<div class="fare">{_money(fare)}</div><div class="meta fclass">Business class</div></div>'
    )


def _multi_html(destinations: list[str], trip: Trip, fare: int) -> str:
    half = (len(destinations) + 1) // 2
    row1, row2 = " · ".join(destinations[:half]), " · ".join(destinations[half:])
    label = "One way from" if trip == Trip.ONE_WAY else "Round trip from"
    return (
        f'<div class="pass"><div class="meta tag multi-tag"><span>From {_e(city_for(ORIGIN_IATA))}</span>{_ROUTE}</div>'
        f'<div class="dest">{_e(row1)}</div><div class="dest">{_e(row2)}</div><div class="perf"></div>'
        f'<div class="meta flabel">{label}</div><div class="fare">{_money(fare)}</div>'
        f'<div class="meta fclass">Business class</div></div>'
    )


def _body_html(d: PostDesign, fare: int | None) -> str:
    big = " big" if d.big_headline else ""
    if d.style == Style.OFFER:
        return f'<div class="headline">{_e(d.headline)}</div>' + _pass_html(d.tag, d.to_iata, d.trip, fare)
    if d.style == Style.EDITORIAL:
        return f'<div class="headline{big}">{_e(d.headline)}</div>{_ROUTE}<div class="line">{_e(d.line)}</div>'
    if d.style == Style.FACT:
        return (f'<div class="lead">{_e(d.lead)}</div><div class="follow">{_e(d.follow)}</div>'
                f'{_ROUTE}<div class="line">{_e(d.line)}</div>')
    if d.style == Style.PRICE_HERO:
        return (f'<div class="hero-price">{_money(fare)}</div><div class="hero-line">{_e(d.follow)}</div>'
                f'{_ROUTE}<div class="line">{_e(d.line)}</div>')
    if d.style == Style.MULTI:
        return f'<div class="headline">{_e(d.headline)}</div>' + _multi_html(d.destinations, d.trip, fare)
    return f'<div class="headline{big}">{_e(d.headline)}</div>{_ROUTE}<div class="line">{_e(d.line)}</div>'


def build_post_html(d: PostDesign) -> tuple[str, Tone, int | None]:
    img = _load_image(d.background)
    has_card = d.style in CARD_STYLES
    tone = d.tone
    if tone is None:
        tone, mean, std = pick_tone(img, has_card)
        log.info("tone=%s (mean=%s std=%s card=%s)", tone, mean, std, has_card)

    fare = None
    if d.style in PRICED_STYLES:
        fare = fare_for(d.fare_iatas or [d.to_iata], d.trip)

    if tone == Tone.LIGHT:
        r, g, b = sky_rgb(img)
        lr, lg, lb = (int(c + (255 - c) * 0.6) for c in (r, g, b))
        style_vars = f"--haze:{r},{g},{b};--card:rgba({lr},{lg},{lb},.9);--pagebg:rgb({lr},{lg},{lb});--a0:.80;--a1:.62"
    else:
        a0, a1 = (0.94, 0.84) if d.style in (Style.FACT, Style.MULTI) else (0.92, 0.80)
        style_vars = f"--haze:11,24,41;--card:rgba(11,24,41,.74);--pagebg:#0B1829;--a0:{a0};--a1:{a1}"

    page = (
        (V4_DIR / "post.html").read_text(encoding="utf-8")
        .replace("{{CSS}}", (V4_DIR / "post.css").read_text(encoding="utf-8"))
        .replace("{{TONE}}", tone.value)
        .replace("{{VARS}}", style_vars)
        .replace("{{BG}}", _bg_uri(img))
        .replace("{{LOGO}}", _logo_uri(tone))
        .replace("{{BODY}}", _body_html(d, fare))
    )
    return page, tone, fare


# ── render + QA ────────────────────────────────────────────────────────────────

_FIT_JS = """() => { const h = document.querySelector('.headline'); if (!h) return;
  const sizes = h.classList.contains('big') ? [92, 84, 78] : [84, 78, 72];
  for (const s of sizes) { h.style.fontSize = s + 'px';
    if (h.getBoundingClientRect().height <= s * 1.08 * 2 + 2) return; } }"""

_MEASURE_JS = """() => { const q = s => document.querySelector(s), r = el => el.getBoundingClientRect();
  const m = { textBottom: r(q('.mast')).bottom };
  const h = q('.headline'); if (h) { const fs = parseFloat(getComputedStyle(h).fontSize);
    m.headlineLines = Math.round(r(h).height / (fs * 1.08)); }
  const p = q('.pass'); if (p) {
    const txt = el => { const x = document.createRange(); x.selectNodeContents(el); return x.getBoundingClientRect(); };
    const pr = r(p), perf = r(q('.pass .perf'));
    m.pass = { tagL: txt(q('.pass .tag')).left, flabelL: txt(q('.pass .flabel')).left, fareL: txt(q('.pass .fare')).left,
               fclassL: txt(q('.pass .fclass')).left, perfX: perf.left + 1, notchX: pr.right - 334,
               tagTop: r(q('.pass .tag')).top, flabelTop: r(q('.pass .flabel')).top };
    const codes = document.querySelectorAll('.pass .codes .iata'), cities = document.querySelectorAll('.pass .cities span');
    if (codes.length === 2 && cities.length === 2) {
      m.pass.originL = r(codes[0]).left; m.pass.originCityL = txt(cities[0]).left;
      m.pass.destR = r(codes[1]).right; m.pass.destCityR = txt(cities[1]).right; } }
  return m; }"""


def _qa(metrics: dict) -> list[str]:
    issues = []
    if metrics["textBottom"] > MAX_TEXT_BOTTOM * H:
        issues.append(f"text block ends at {metrics['textBottom']:.0f}px (> {MAX_TEXT_BOTTOM * H:.0f}px)")
    if metrics.get("headlineLines", 0) > 2:
        issues.append(f"headline wraps to {metrics['headlineLines']} lines (max 2)")
    p = metrics.get("pass")
    if p:
        def off(a, b, name):
            if abs(p[a] - p[b]) > 2:
                issues.append(f"pass: {name} misaligned ({p[a]:.0f} vs {p[b]:.0f})")
        off("flabelL", "fareL", "stub label/fare")
        off("fareL", "fclassL", "stub fare/class")
        off("perfX", "notchX", "perforation/notch")
        off("tagTop", "flabelTop", "row 1 across perforation")
        if "originL" in p:
            off("originL", "originCityL", "origin code/city")
            off("destR", "destCityR", "destination code/city")
    return issues


def render_post(d: PostDesign, *, strict: bool = True) -> RenderResult:
    """PostDesign → JPEG 1080×1920. Fonts injected, headline auto-fitted, layout measured.
    strict=True raises DesignQAError when a layout rule is broken."""
    from playwright.sync_api import sync_playwright

    page_html, tone, fare = build_post_html(d)
    inter = ASSETS / "fonts" / "Inter.woff2"
    playfair = ASSETS / "fonts" / "PlayfairDisplay.ttf"
    for f in (inter, playfair):
        if not f.exists():
            raise FileNotFoundError(f"{f} missing")
    font_css = (
        f"@font-face{{font-family:'Inter';src:url('{_data_uri(inter, 'font/woff2')}') format('woff2');font-weight:100 900}}"
        f"@font-face{{font-family:'Playfair Display';src:url('{_data_uri(playfair, 'font/ttf')}') format('truetype');font-weight:400 900}}"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": W, "height": H})
            page.set_content(page_html, wait_until="load")
            page.add_style_tag(content=font_css)
            page.evaluate("document.fonts.ready.then(() => true)")
            page.evaluate(_FIT_JS)
            metrics = page.evaluate(_MEASURE_JS)
            jpeg = page.screenshot(type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": W, "height": H})
        finally:
            browser.close()

    issues = _qa(metrics)
    metrics["issues"] = issues
    if issues:
        log.warning("design QA: %s", "; ".join(issues))
        if strict:
            raise DesignQAError("; ".join(issues))
    return RenderResult(jpeg=jpeg, tone=tone, fare=fare, metrics=metrics)


# ── copy + weekly rules ────────────────────────────────────────────────────────

def caption_ending(style: Style, city: str | None = None) -> str:
    """Priced styles point to the fare on the image; the others invite a conversation. Never a '$' in the caption."""
    if style in PRICED_STYLES:
        return "The fare above is today's. Ask us for your dates."
    return f"Ask us for today's fare to {city}." if city else "Ask us for today's fare."


def validate_week_mix(styles: list[Style]) -> list[str]:
    """≤60% priced · never three priced posts in a row · each style at most twice a week."""
    issues: list[str] = []
    if not styles:
        return issues
    priced = [s in PRICED_STYLES for s in styles]
    if sum(priced) / len(styles) > 0.6:
        issues.append(f"{sum(priced)}/{len(styles)} posts carry a fare (max 60%)")
    run = 0
    for i, is_priced in enumerate(priced):
        run = run + 1 if is_priced else 0
        if run >= 3:
            issues.append(f"three priced posts in a row ending at position {i + 1}")
            break
    for s, n in Counter(styles).items():
        if n > 2:
            issues.append(f"style {s} used {n} times (max 2)")
    return issues
