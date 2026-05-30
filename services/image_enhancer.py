"""BBC Image Enhancer — post-processing pipeline for branded creatives."""
from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter


def smart_resize(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Crop-to-fit resize (cover) for target dimensions."""
    target_w, target_h = target_size
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def enhance_quality(image: Image.Image) -> Image.Image:
    """UnsharpMask + contrast + color balance."""
    img = image.filter(ImageFilter.UnsharpMask(radius=1, percent=30, threshold=0))
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageEnhance.Color(img).enhance(1.05)
    return img


def enhance_for_platform(image_bytes: bytes, platform: str = "whatsapp") -> bytes:
    """Enhance image quality for target platform."""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")

    targets = {
        "whatsapp": (1200, 628),
        "instagram": (1080, 1080),
        "telegram": (1280, 720),
    }
    if platform in targets:
        img = smart_resize(img, targets[platform])

    img = enhance_quality(img)

    quality = {"whatsapp": 92, "instagram": 95, "telegram": 90}.get(platform, 92)
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
