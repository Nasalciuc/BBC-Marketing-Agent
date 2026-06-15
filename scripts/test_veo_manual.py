"""Manual Veo test — requires Gemini paid tier + billing."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main() -> None:
    from services.veo_client import generate_video

    out = ROOT / "output" / "test_london_video.mp4"

    # Text-to-video smoke test
    print("Generating 8s London destination video (Veo fast)...")
    video = await generate_video(
        category="destination",
        subcategory="london",
        output_path=out,
        fast=True,
        generate_audio=False,
        duration_seconds=8,
    )
    print(f"OK: {out} ({len(video):,} bytes)")

    # Image-to-video if London deal exists
    deal = ROOT / "output" / "LONDON_DEAL_v4.jpg"
    if deal.exists():
        out2 = ROOT / "output" / "test_london_video_from_image.mp4"
        print("Image-to-video from London deal banner...")
        await generate_video(
            category="destination",
            subcategory="london",
            image_path=deal,
            output_path=out2,
            fast=True,
            custom_prompt="",
        )
        print(f"OK: {out2}")


if __name__ == "__main__":
    asyncio.run(main())
