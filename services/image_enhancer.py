"""
BBC Image Enhancer — post-processing pipeline.
Adapted from GhostWriter image_quality_enhancer.py (FMF-specific code removed).
"""
from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

log = logging.getLogger("bbc.image_enhancer")

PLATFORM_SIZES: dict[str, tuple[int, int]] = {
    "whatsapp": (1200, 628),
    "whatsapp_story": (1080, 1920),
    "instagram": (1080, 1080),
    "telegram": (1280, 720),
}

EXPORT_QUALITY: dict[str, int] = {
    "whatsapp": 92,
    "instagram": 95,
    "telegram": 90,
    "whatsapp_story": 92,
}


def ensure_rgb(img: Image.Image) -> Image.Image:
    """Convert RGBA/palette modes to RGB with white background."""
    if img.mode in ("RGBA", "LA", "P"):
        rgb = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        mask = img.split()[-1] if img.mode in ("RGBA", "LA") else None
        rgb.paste(img, mask=mask)
        return rgb
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def upscale_if_needed(img: Image.Image, target_size: tuple[int, int], max_factor: float = 2.0) -> Image.Image:
    """
    Upscale small images before cover-crop (max 2× to limit quality loss).
    Adapted from GhostWriter _upscale_image().
    """
    target_max = max(target_size)
    current_max = max(img.size)
    if current_max >= target_max:
        return img

    scale = min(max_factor, target_max / current_max)
    new_size = (int(img.width * scale), int(img.height * scale))
    log.debug("Upscale %s → %s (factor %.2f)", img.size, new_size, scale)
    return img.resize(new_size, Image.Resampling.LANCZOS)


def smart_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Resize like CSS object-fit:cover — fill target, center-crop.
    Adapted from GhostWriter _smart_resize() (cover variant).
    """
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        new_h = target_h
        new_w = int(target_h * img_ratio)
    else:
        new_w = target_w
        new_h = int(target_w / img_ratio)

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def enhance_quality(img: Image.Image) -> Image.Image:
    """
    Sharpen + contrast + color (+ slight brightness).
    Adapted from GhostWriter _enhance_quality() with softer sharpen for marketing assets.
    """
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=30, threshold=0))
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = ImageEnhance.Brightness(img).enhance(1.02)
    return img


def prepare_image(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Full prepare pipeline: EXIF fix → RGB → upscale → cover resize → enhance."""
    img = ImageOps.exif_transpose(img)
    img = ensure_rgb(img)
    img = upscale_if_needed(img, target_size)
    img = smart_resize(img, target_size[0], target_size[1])
    return enhance_quality(img)


def enhance_for_platform(image_bytes: bytes, platform: str = "whatsapp") -> bytes:
    """
    Pipeline complet: resize + enhance + export JPEG.

    Args:
        image_bytes: JPEG/PNG input bytes
        platform: whatsapp | whatsapp_story | instagram | telegram

    Returns:
        Optimized JPEG bytes
    """
    target = PLATFORM_SIZES.get(platform, (1200, 628))
    img = Image.open(BytesIO(image_bytes))
    img = prepare_image(img, target)

    quality = EXPORT_QUALITY.get(platform, 92)
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
