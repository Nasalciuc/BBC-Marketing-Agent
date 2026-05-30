"""BBC Branding Engine — Pillow pipeline driven by JSON templates."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = ROOT / "templates"
DEFAULT_LOGO = ROOT / "assets" / "logos" / "bbc_logo_white.png"

FONT_CANDIDATES = {
    True: [
        ROOT / "assets/fonts/Inter-Bold.ttf",
        ROOT / "assets/fonts/Inter-Bold.otf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ],
    False: [
        ROOT / "assets/fonts/Inter-Regular.ttf",
        ROOT / "assets/fonts/Inter-Regular.otf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ],
}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _load_template(name: str) -> dict:
    path = TEMPLATES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_font(bold: bool = True, size: int = 48) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Inter → DejaVu → Arial → Pillow default."""
    for path in FONT_CANDIDATES[bold]:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def resize_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """CSS object-fit: cover — scale and center-crop."""
    src_w, src_h = image.size
    scale = max(width / src_w, height / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _load_background(source: str, width: int, height: int) -> Image.Image:
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        with urlopen(source, timeout=30) as response:
            img = Image.open(BytesIO(response.read()))
    else:
        path = Path(source)
        if not path.is_absolute():
            path = ROOT / path
        img = Image.open(path)
    img = img.convert("RGB")
    return resize_cover(img, width, height)


def _apply_gradient(
    canvas: Image.Image,
    color: str,
    opacity_start: float,
    opacity_end: float,
    width_percent: float,
    direction: str,
) -> None:
    """Smooth left-to-right gradient overlay (pixel-by-pixel alpha)."""
    w, h = canvas.size
    gradient_width = int(w * width_percent / 100)
    rgb = _hex_to_rgb(color)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pixels = overlay.load()

    for x in range(gradient_width):
        t = x / max(gradient_width - 1, 1)
        opacity = opacity_start + (opacity_end - opacity_start) * t
        alpha = int(max(0, min(255, opacity * 255)))
        for y in range(h):
            pixels[x, y] = (*rgb, alpha)

    if direction != "left_to_right":
        overlay = overlay.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.alpha_composite(overlay)
    canvas.paste(canvas_rgba.convert("RGB"))


def _draw_logo_or_text(canvas: Image.Image, draw: ImageDraw.ImageDraw, cfg: dict) -> None:
    logo_path = cfg.get("path", str(DEFAULT_LOGO))
    path = Path(logo_path)
    if not path.is_absolute():
        path = ROOT / path

    if path.exists():
        logo = Image.open(path).convert("RGBA")
        target_w = cfg.get("width", 180)
        ratio = target_w / logo.width
        target_h = int(logo.height * ratio)
        logo = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)
        canvas.paste(logo, (cfg["x"], cfg["y"]), logo)
        return

    font = get_font(bold=True, size=cfg.get("fallback_font_size", 22))
    draw.text(
        (cfg["x"], cfg["y"]),
        cfg.get("fallback_text", "BuyBusinessClass.com"),
        fill=cfg.get("fallback_color", "#FFFFFF"),
        font=font,
    )


def _text_width(text: str, font: ImageFont.ImageFont) -> int:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def _text_height(font: ImageFont.ImageFont) -> int:
    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1]


def _draw_badge(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    text: str,
    cfg: dict,
) -> None:
    font = get_font(bold=True, size=cfg["font_size"])
    text_w = _text_width(text, font)
    text_h = _text_height(font)
    pad_h = cfg.get("padding_h", 12)
    pad_v = cfg.get("padding_v", 8)
    radius = cfg.get("border_radius", 8)

    x1 = cfg["x"]
    y1 = cfg["y"]
    x2 = x1 + text_w + pad_h * 2
    y2 = y1 + text_h + pad_v * 2

    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=radius,
        fill=cfg.get("bg_color", "#C9A54E"),
    )
    draw.text(
        (x1 + pad_h, y1 + pad_v),
        text,
        fill=cfg.get("text_color", "#0B1829"),
        font=font,
    )


def _draw_price_line(
    draw: ImageDraw.ImageDraw,
    price: str,
    prefix_cfg: dict,
    price_cfg: dict,
) -> None:
    prefix_font = get_font(bold=False, size=prefix_cfg["font_size"])
    price_font = get_font(bold=True, size=price_cfg["font_size"])
    prefix = prefix_cfg.get("text", "From ")
    prefix_color = prefix_cfg.get("color", "#C9A54E")
    price_color = price_cfg.get("color", "#C9A54E")

    x = prefix_cfg["x"]
    baseline_y = prefix_cfg["y"]

    draw.text((x, baseline_y), prefix, fill=prefix_color, font=prefix_font)
    price_x = x + _text_width(prefix, prefix_font)
    price_y = baseline_y - (price_cfg["font_size"] - prefix_cfg["font_size"])
    draw.text((price_x, price_y), price, fill=price_color, font=price_font)


def generate_branded_image(
    event_name: str,
    route: str,
    price: str,
    background_url_or_path: str,
    template_name: str = "deal_landscape",
    badge_text: str | None = None,
) -> bytes:
    """Return JPEG bytes of a BBC-branded deal image."""
    template = _load_template(template_name)
    canvas_cfg = template["canvas"]
    width = canvas_cfg["width"]
    height = canvas_cfg["height"]
    quality = canvas_cfg.get("quality", 92)

    canvas = _load_background(background_url_or_path, width, height)

    grad = template["gradient"]
    _apply_gradient(
        canvas,
        color=grad["color"],
        opacity_start=grad["opacity_start"],
        opacity_end=grad["opacity_end"],
        width_percent=grad["width_percent"],
        direction=grad.get("direction", "left_to_right"),
    )

    draw = ImageDraw.Draw(canvas)
    _draw_logo_or_text(canvas, draw, template["logo"])

    badge_label = badge_text if badge_text is not None else event_name
    _draw_badge(canvas, draw, badge_label, template["badge"])

    route_cfg = template["route"]
    route_font = get_font(bold=True, size=route_cfg["font_size"])
    draw.text(
        (route_cfg["x"], route_cfg["y"]),
        route,
        fill=route_cfg.get("color", "#FFFFFF"),
        font=route_font,
    )

    _draw_price_line(draw, price, template["price_prefix"], template["price"])

    cta = template["cta"]
    cta_font = get_font(bold=False, size=cta["font_size"])
    draw.text(
        (cta["x"], cta["y"]),
        cta.get("text", "Book now · buybusinessclass.com"),
        fill=cta.get("color", "#C9A54E"),
        font=cta_font,
    )

    bar = template["bottom_bar"]
    bar_y = bar.get("y", height - bar.get("height", 8))
    bar_h = bar.get("height", 8)
    draw.rectangle(
        (0, bar_y, width, bar_y + bar_h),
        fill=bar.get("color", "#C9A54E"),
    )

    buffer = BytesIO()
    canvas.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
