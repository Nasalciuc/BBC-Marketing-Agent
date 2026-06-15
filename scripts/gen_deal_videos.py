"""Generate BBC deal videos via Gemini Veo (paid tier)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output"


async def _gen(label: str, output_name: str, **kwargs) -> None:
    from services.veo_client import generate_video

    out = OUTPUT / output_name
    print(f"\n{'=' * 50}\n{label}\n-> {out.name}\n{'=' * 50}")
    try:
        data = await generate_video(output_path=out, fast=True, duration_seconds=8, **kwargs)
        print(f"OK {out.name} ({len(data):,} bytes)")
    except Exception as e:
        print(f"FAIL {label}: {e}")


async def main() -> None:
    OUTPUT.mkdir(exist_ok=True)

    await _gen(
        "London text-to-video",
        "LONDON_video.mp4",
        category="destination",
        subcategory="london",
    )

    if (OUTPUT / "LONDON_DEAL_v4.jpg").exists():
        await _gen(
            "London image-to-video (deal banner v4)",
            "LONDON_video_from_banner.mp4",
            category="destination",
            subcategory="london",
            image_path=OUTPUT / "LONDON_DEAL_v4.jpg",
        )

    if (OUTPUT / "MONACO_GP_DEAL.jpg").exists():
        await _gen(
            "Monaco GP image-to-video",
            "MONACO_GP_video.mp4",
            category="event",
            subcategory="f1_monaco",
            image_path=OUTPUT / "MONACO_GP_DEAL.jpg",
        )

    if (OUTPUT / "ROME_DEAL.jpg").exists():
        await _gen(
            "Rome image-to-video",
            "ROME_video.mp4",
            category="destination",
            subcategory="rome",
            image_path=OUTPUT / "ROME_DEAL.jpg",
        )

    print("\nDone. Videos in output/")


if __name__ == "__main__":
    asyncio.run(main())
