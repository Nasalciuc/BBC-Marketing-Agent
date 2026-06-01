"""
BBC Branding Engine — HTML/CSS template → Playwright screenshot → JPEG.
Generează imagini branded profesionale, calitate SkyLux.
"""
from __future__ import annotations

import base64
import html
import logging
import re
from pathlib import Path

log = logging.getLogger("bbc.branding")

ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"

_logo_base64: str | None = None
_font_css_cache: str | None = None  # legacy — unused; fonts via add_style_tag / Inter.woff2


def _ensure_transparent_logo() -> Path | None:
    """Generează logo alb pe fundal transparent (crop la conținut)."""
    dst = ASSETS_DIR / "logos" / "bbc_logo_white_transparent.png"

    sources = [
        ASSETS_DIR / "logos" / "bbc_logo_horizontal.png",
        ASSETS_DIR / "logos" / "LOGO BBC 1200X300.png",
        ASSETS_DIR / "logos" / "LOGO_BBC_1200X300.png",
    ]
    src = next((p for p in sources if p.exists()), None)
    if not src:
        return dst if dst.exists() else None

    import numpy as np
    from PIL import Image

    data = np.array(Image.open(src).convert("RGBA"))
    white_mask = (data[:, :, 0] > 220) & (data[:, :, 1] > 220) & (data[:, :, 2] > 220)
    data[white_mask, 3] = 0
    visible = data[:, :, 3] > 10
    data[visible, 0] = 255 - data[visible, 0]
    data[visible, 1] = 255 - data[visible, 1]
    data[visible, 2] = 255 - data[visible, 2]

    logo = Image.fromarray(data, "RGBA")
    bbox = logo.getbbox()
    if bbox:
        logo = logo.crop(bbox)
    logo.save(dst)
    # Alias pentru compatibilitate
    logo.save(ASSETS_DIR / "logos" / "bbc_logo_transparent.png")
    log.info("Created transparent logo: %s (%sx%s)", dst.name, logo.width, logo.height)
    return dst


def _get_logo_base64() -> str | None:
    """Încarcă logo-ul BBC ca data URI base64."""
    global _logo_base64
    if _logo_base64 is not None:
        return _logo_base64 if _logo_base64 else None

    _ensure_transparent_logo()

    candidates = [
        ASSETS_DIR / "logos" / "bbc_logo_white_transparent.png",
        ASSETS_DIR / "logos" / "bbc_logo_transparent.png",
        ASSETS_DIR / "logos" / "bbc_logo_horizontal.png",
        ASSETS_DIR / "logos" / "bbc_logo_square.png",
        ASSETS_DIR / "logos" / "bbc_logo_white.png",
        ASSETS_DIR / "logos" / "LOGO BBC 1200X300.png",
        ASSETS_DIR / "logos" / "LOGO BBC 1200X1200.png",
        ASSETS_DIR / "logos" / "LOGO_BBC_1200X300.png",
        ASSETS_DIR / "logos" / "LOGO_BBC_1200X1200.png",
    ]

    for path in candidates:
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode()
            _logo_base64 = f"data:image/png;base64,{data}"
            log.info("Logo loaded: %s", path.name)
            return _logo_base64

    logos_dir = ASSETS_DIR / "logos"
    if logos_dir.is_dir():
        for path in sorted(logos_dir.glob("*.png")):
            if path.name.lower() in (".gitkeep",):
                continue
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


def _inter_font_path() -> Path | None:
    """Calea către fontul Inter (variable woff2 sau Bold otf fallback)."""
    for name in ("Inter.woff2", "Inter-Bold.otf"):
        path = ASSETS_DIR / "fonts" / name
        if path.exists():
            return path
    return None


def _inter_font_face_css() -> str:
    """@font-face CSS cu Inter embedded base64 (pentru HCTI / fallback)."""
    font_path = _inter_font_path()
    if not font_path:
        return ""
    fmt = "woff2" if font_path.suffix == ".woff2" else "opentype"
    mime = "font/woff2" if fmt == "woff2" else "font/opentype"
    font_b64 = base64.b64encode(font_path.read_bytes()).decode()
    return (
        f"@font-face {{ font-family: 'Inter'; "
        f"src: url('data:{mime};base64,{font_b64}') format('{fmt}'); "
        f"font-weight: 100 900; font-style: normal; font-display: block; }}"
    )


def _inject_font_style_tag(page) -> None:
    """Injectează Inter via page.add_style_tag — metoda oficială Playwright."""
    font_path = _inter_font_path()
    if not font_path:
        log.warning("Inter font missing in assets/fonts/ — system fallback")
        return
    fmt = "woff2" if font_path.suffix == ".woff2" else "opentype"
    mime = "font/woff2" if fmt == "woff2" else "font/opentype"
    font_b64 = base64.b64encode(font_path.read_bytes()).decode()
    page.add_style_tag(
        content=f"""
@font-face {{
  font-family: 'Inter';
  src: url('data:{mime};base64,{font_b64}') format('{fmt}');
  font-weight: 100 900;
  font-style: normal;
  font-display: block;
}}
"""
    )


def _inject_font_into_html(html: str) -> str:
    """Inline font CSS pentru renderer-e fără add_style_tag (HCTI)."""
    css = _inter_font_face_css()
    if not css:
        return html
    return html.replace("<style>", f"<style>\n  {css}\n", 1)


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
    """Asigură format $X,XXX — repară artefacte shell (\\,069, ,069)."""
    price = price.strip()
    if not price:
        return price

    # PowerShell: "$2,069" → ",069" (variabila $2 e expandată)
    m = re.match(r"^,(\d{3})$", price)
    if m:
        return f"$2,{m.group(1)}"

    # Escape greșit: "\,069" (backslash + virgulă, $ și 2 pierdute)
    m = re.match(r"^\\,(\d{3})$", price)
    if m:
        return f"$2,{m.group(1)}"

    price = price.replace("\\", "")
    if price.startswith("$"):
        return price
    if price and price[0].isdigit():
        return f"${price}"
    return price


def _ensure_jpeg(image_bytes: bytes, quality: int = 92) -> bytes:
    """Normalizează output la JPEG (HCTI poate returna PNG)."""
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(image_bytes))
    if img.format == "JPEG":
        return image_bytes
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _build_rendered_html(
    event_name: str,
    route: str,
    price: str,
    background_url_or_path: str,
    template_name: str = "deal_landscape",
    badge_text: str | None = None,
    caption: str = "",
    hook_text: str = "",
    urgency_text: str = "",
    cta_text: str = "Check available seats → buybusinessclass.com",
    cta_url: str = "buybusinessclass.com",
    # optional overrides (ignored if empty — SALES_V2 uses hook/urgency/cta)
    headline: str | None = None,
    route_info: str | None = None,
    urgency: str | None = None,
    cta: str | None = None,
) -> str:
    """Populează template-ul HTML cu datele deal-ului."""
    from prompts.system_prompts import format_badge_text, get_urgency_text

    html_template = _load_template(template_name)
    bg_uri = _background_to_data_uri(background_url_or_path)
    route = _normalize_route(route)
    if route_info:
        route = _normalize_route(route_info.split("·")[0].strip())
    price = _normalize_price(price)
    hook = hook_text or caption

    urgency_val = urgency or urgency_text or get_urgency_text(event_name)
    cta_raw = cta or cta_text or "Check available seats → buybusinessclass.com"
    cta_val = cta_raw
    cta_url_val = cta_url
    if "→" in cta_raw:
        cta_parts = cta_raw.split("→", 1)
        cta_val = cta_parts[0].strip()
        if len(cta_parts) > 1 and cta_parts[1].strip():
            cta_url_val = cta_parts[1].strip()

    html_template = html_template.replace("{{BG}}", bg_uri)
    html_template = html_template.replace("{{LOGO_HTML}}", _get_logo_html())
    html_template = html_template.replace(
        "{{BADGE}}", html.escape(format_badge_text(badge_text or event_name))
    )
    html_template = html_template.replace("{{ROUTE}}", html.escape(route))
    html_template = html_template.replace("{{PRICE}}", html.escape(price))
    html_template = html_template.replace("{{URGENCY}}", html.escape(urgency_val))
    html_template = html_template.replace("{{HOOK}}", html.escape(hook))
    html_template = html_template.replace("{{CTA}}", html.escape(cta_val))
    html_template = html_template.replace("{{CTA_URL}}", html.escape(cta_url_val))
    return html_template


def _resolve_renderer() -> str:
    """Alege backend: hcti (cloud) sau playwright (local)."""
    from config import settings

    mode = (settings.branding_renderer or "auto").lower()
    has_hcti = bool(settings.hcti_user_id and settings.hcti_api_key)

    if mode == "hcti":
        if not has_hcti:
            raise ValueError("BRANDING_RENDERER=hcti but HCTI_USER_ID/HCTI_API_KEY missing")
        return "hcti"
    if mode == "playwright":
        return "playwright"
    return "hcti" if has_hcti else "playwright"


def _screenshot_playwright(html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 628})

        page.set_content(html, wait_until="domcontentloaded")
        _inject_font_style_tag(page)
        page.wait_for_timeout(500)

        screenshot_bytes = page.screenshot(
            type="jpeg",
            quality=92,
            clip={"x": 0, "y": 0, "width": 1200, "height": 628},
        )
        browser.close()
    return screenshot_bytes


def _screenshot_hcti(html: str) -> bytes:
    """HTML → JPEG via hcti.io (fără Chromium local)."""
    import httpx

    from config import settings

    html = _inject_font_into_html(html)

    with httpx.Client(timeout=90) as client:
        resp = client.post(
            "https://hcti.io/v1/image",
            data={
                "html": html,
                "css": "",
                "viewport_width": 1200,
                "viewport_height": 628,
                "device_scale": 1,
                "format": "jpg",
            },
            auth=(settings.hcti_user_id, settings.hcti_api_key),
        )
        resp.raise_for_status()
        image_url = resp.json()["url"]

        img_resp = client.get(image_url)
        img_resp.raise_for_status()
        return _ensure_jpeg(img_resp.content)


def generate_branded_image(
    event_name: str,
    route: str,
    price: str,
    background_url_or_path: str,
    template_name: str = "deal_landscape",
    badge_text: str | None = None,
    caption: str = "",
    hook_text: str = "",
    urgency_text: str = "",
    cta_text: str = "Check available seats → buybusinessclass.com",
    cta_url: str = "buybusinessclass.com",
    headline: str | None = None,
    route_info: str | None = None,
    urgency: str | None = None,
    cta: str | None = None,
) -> bytes:
    """
    Generează imagine branded BBC din HTML template.

    Renderer: Playwright (local) sau hcti.io (cloud) — vezi BRANDING_RENDERER.

    Returns:
        JPEG bytes al imaginii generate (1200×628)
    """
    rendered_html = _build_rendered_html(
        event_name=event_name,
        route=route,
        price=price,
        background_url_or_path=background_url_or_path,
        template_name=template_name,
        badge_text=badge_text,
        caption=caption,
        hook_text=hook_text,
        urgency_text=urgency_text,
        cta_text=cta_text,
        cta_url=cta_url,
        headline=headline,
        route_info=route_info,
        urgency=urgency,
        cta=cta,
    )

    renderer = _resolve_renderer()
    log.info("Branding renderer: %s", renderer)

    try:
        if renderer == "hcti":
            screenshot_bytes = _screenshot_hcti(rendered_html)
        else:
            screenshot_bytes = _screenshot_playwright(rendered_html)
    except Exception as exc:
        if renderer == "hcti":
            log.warning("HCTI failed (%s) — falling back to Playwright", exc)
            screenshot_bytes = _screenshot_playwright(rendered_html)
        else:
            raise

    log.info(
        "Generated branded image: %s bytes (%s)",
        f"{len(screenshot_bytes):,}",
        event_name,
    )
    return screenshot_bytes
