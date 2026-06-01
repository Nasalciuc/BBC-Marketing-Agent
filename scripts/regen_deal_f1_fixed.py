"""Regenerate F1 deal using system prompts (image gen + sales hook caption)."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from prompts.system_prompts import F1_MONACO_IMAGE_PROMPT, get_sales_hook
from services.branding_engine import generate_branded_image
from services.gemini_client import generate_event_image
from services.image_enhancer import remove_watermark_corner

FALLBACK_BGS = [
    "output/f1_monaco_bg.png",
    "output/real_pipeline/bg_1.jpg",
    "assets/backgrounds/f1_monaco_test.png",
    "assets/defaults/default_background.jpg",
]


async def resolve_background() -> tuple[str, Path | None]:
    """Gemini background (prompts §3) sau fallback local. Returnează path + temp file."""
    if settings.gemini_api_key:
        print(f"Generating background via Gemini...\n  Prompt: {F1_MONACO_IMAGE_PROMPT[:80]}...")
        bg_bytes = await generate_event_image(F1_MONACO_IMAGE_PROMPT)
        if bg_bytes:
            bg_bytes = remove_watermark_corner(bg_bytes)
            fd, tmp_name = tempfile.mkstemp(suffix=".jpg", prefix="f1_monaco_bg_")
            os.close(fd)
            tmp = Path(tmp_name)
            tmp.write_bytes(bg_bytes)
            out_copy = Path("output/f1_monaco_bg.png")
            out_copy.parent.mkdir(parents=True, exist_ok=True)
            out_copy.write_bytes(bg_bytes)
            print(f"  OK Gemini bg: {out_copy} ({len(bg_bytes):,} bytes)")
            return str(tmp), tmp

    for candidate in FALLBACK_BGS:
        if Path(candidate).exists():
            print(f"Background fallback: {candidate}")
            return candidate, None

    return FALLBACK_BGS[-1], None


async def main() -> None:
    sales_hook = get_sales_hook("motorsport")
    print(f"Sales hook (footer): {sales_hook}")

    bg_path, tmp_file = await resolve_background()

    try:
        img = await asyncio.to_thread(
            generate_branded_image,
            "Formula 1 Grand Prix de Monaco 2026",
            "JFK → NCE",
            "$2,069",
            bg_path,
            "deal_landscape",
            "MOTORSPORT",
            sales_hook,
        )
    finally:
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()

    out = Path("output/deal_f1_FIXED.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(img)
    print(f"DONE: {out} ({len(img):,} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
