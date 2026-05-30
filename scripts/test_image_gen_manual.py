"""Manual test: Gemini image generation. Run: python scripts/test_image_gen_manual.py"""
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from config import settings
from services.gemini_client import generate_event_image


async def main() -> None:
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY not set in .env")
        return

    print("\n" + "=" * 60)
    print("TEST: Gemini Image Generation")
    print("=" * 60)

    prompts = [
        "Aerial view of Monaco harbor with luxury yachts during golden hour sunset, Mediterranean turquoise water, Monte Carlo casino visible in background, cinematic photography",
        "Wimbledon Centre Court with perfectly manicured green grass, empty seats in the royal box, soft afternoon light, professional sports photography",
        "Tokyo Shibuya crossing at dusk with neon lights reflected on wet streets, modern Japanese architecture, cinematic wide angle photography",
    ]

    out_dir = ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, prompt in enumerate(prompts, 1):
        print(f"\n--- Image {i} ---")
        print(f"Prompt: {prompt[:80]}...")
        image_bytes = await generate_event_image(prompt)
        if image_bytes:
            path = out_dir / f"gemini_image_{i}.jpg"
            path.write_bytes(image_bytes)
            print(f"Saved: {path} ({len(image_bytes):,} bytes)")
        else:
            print(f"Failed — will use default_background.jpg as fallback")


if __name__ == "__main__":
    asyncio.run(main())
