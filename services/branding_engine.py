"""
BBC Branding Engine — HTML/CSS template → Playwright screenshot → JPEG.
Generează imagini branded profesionale, calitate SkyLux.
"""
from __future__ import annotations

import base64
import html
import logging
from pathlib import Path

log = logging.getLogger("bbc.branding")

ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"

_logo_base64: str | None = None


def _get_logo_base64() -> str | None:
    """Încarcă logo-ul BBC ca data URI base64."""
    global _logo_base64
    if _logo_base64 is not None:
        return _logo_base64 if _logo_base64 else None

    candidates = [
        ASSETS_DIR / "logos" / "bbc_logo_horizontal.png",
        ASSETS_DIR / "logos" / "LOGO_BBC_1200X300.png",
        ASSETS_DIR / "logos" / "bbc_logo_white.png",
        ASSETS_DIR / "logos" / "LOGO_BBC_1200X1200.png",
    ]

    for path in candidates:
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode()
            _logo_base64 = f"data:image/png;base64,{data}"
            log.info("Logo loaded: %s", path.name)
            return _logo_base64

    _logo_base64 = ""
    log.warning("No logo found — using text fallback")
    return None


def _get_logo_html() -> str:
    """Returnează markup HTML pentru logo (imagine sau text fallback)."""
    logo_uri = _get_logo_base64()
    if logo_uri:
        return f'<img src="{logo_uri}" alt="BuyBusinessClass">'
    return '<span class="logo-text">BuyBusinessClass.com</span>'


def _load_template(name: str = "deal_landscape") -> str:
    """Citește template-ul HTML."""
    html_path = TEMPLATES_DIR / f"{name}.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Template not found: {html_path}")


def _background_to_data_uri(background_path: str) -> str:
    """Convertește background image la data URI sau returnează URL-ul direct."""
    if background_path.startswith("http://") or background_path.startswith("https://"):
        return background_path

    path = Path(background_path)
    if path.exists():
        data = base64.b64encode(path.read_bytes()).decode()
        mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        return f"data:{mime};base64,{data}"

    default = ASSETS_DIR / "defaults" / "default_background.jpg"
    if default.exists():
        data = base64.b64encode(default.read_bytes()).decode()
        return f"data:image/jpeg;base64,{data}"

    return ""


def _normalize_route(route: str) -> str:
    """Asigură săgeată Unicode și preț cu simbol $."""
    route = route.replace("->", "→").replace("—>", "→")
    return route


def _normalize_price(price: str) -> str:
    """Asigură că prețul începe cu $ dacă e numeric formatat."""
    price = price.strip()
    if price and price[0].isdigit() and not price.startswith("$"):
        return f"${price}"
    return price


def generate_branded_image(
    event_name: str,
    route: str,
    price: str,
    background_url_or_path: str,
    template_name: str = "deal_landscape",
    badge_text: str | None = None,
    caption: str = "",
) -> bytes:
    """
    Generează imagine branded BBC din HTML template + Playwright screenshot.

    Returns:
        JPEG bytes al imaginii generate (1200×628)
    """
    from playwright.sync_api import sync_playwright

    html_template = _load_template(template_name)
    bg_uri = _background_to_data_uri(background_url_or_path)
    route = _normalize_route(route)
    price = _normalize_price(price)

    html_template = html_template.replace("{{BACKGROUND_IMAGE}}", bg_uri)
    html_template = html_template.replace("{{LOGO_HTML}}", _get_logo_html())
    html_template = html_template.replace(
        "{{EVENT_NAME}}", html.escape(badge_text or event_name)
    )
    html_template = html_template.replace("{{ROUTE}}", html.escape(route))
    html_template = html_template.replace("{{PRICE}}", html.escape(price))
    html_template = html_template.replace("{{CAPTION}}", html.escape(caption or ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1200, "height": 628},
            device_scale_factor=1,
        )
        page = context.new_page()
        page.set_content(html_template, wait_until="networkidle")

        screenshot_bytes = page.screenshot(
            type="jpeg",
            quality=92,
            clip={"x": 0, "y": 0, "width": 1200, "height": 628},
        )

        browser.close()

    log.info(
        "Generated branded image: %s bytes (%s)",
        f"{len(screenshot_bytes):,}",
        event_name,
    )
    return screenshot_bytes
