"""Verify FFmpeg + Pexels setup."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output"


def check_ffmpeg() -> None:
    from services.ffmpeg_utils import ffmpeg_version, get_ffmpeg_path, run_ffmpeg

    path = get_ffmpeg_path()
    ver = ffmpeg_version()
    if path and ver:
        print(f"FFmpeg: INSTALLED\n  path: {path}\n  {ver}")
    else:
        print("FFmpeg: NOT INSTALLED")
        return

    OUTPUT.mkdir(exist_ok=True)
    out = OUTPUT / "ffmpeg_test.mp4"
    result = run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:d=3",
            "-vf",
            "drawtext=text='BBC TEST':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v",
            "libx264",
            "-t",
            "3",
            str(out),
        ]
    )
    if result.returncode == 0 and out.exists():
        print(f"FFmpeg branding test: OK {out} ({out.stat().st_size:,} bytes)")
    else:
        print(f"FFmpeg branding test: FAIL\n{result.stderr[:300]}")


def check_pexels_config() -> None:
    from config import settings

    key = settings.pexels_api_key
    if key:
        print(f"Pexels .env: KEY SET ({len(key)} chars)")
    else:
        print("Pexels .env: EMPTY — get key from https://www.pexels.com/api/")


async def check_pexels_api() -> None:
    from services.pexels_client import best_video_file, is_configured, search_photos, search_videos

    if not is_configured():
        print("Pexels API test: SKIPPED (no key)")
        return

    try:
        data = await search_videos("London aerial sunset", per_page=3)
        total = data.get("total_results", 0)
        print(f"Pexels video API: OK — {total} results")
        for v in data.get("videos", [])[:3]:
            best = best_video_file(v)
            if best:
                print(
                    f"  {v.get('duration')}s "
                    f"{best.get('width')}x{best.get('height')} "
                    f"{str(best.get('link', ''))[:60]}..."
                )
    except Exception as e:
        print(f"Pexels video API: FAIL — {e}")

    try:
        data = await search_photos("business class cabin", per_page=3)
        total = data.get("total_results", 0)
        print(f"Pexels image API: OK — {total} results")
        for p in data.get("photos", [])[:3]:
            print(f"  {p['width']}x{p['height']} {p['src']['large'][:60]}...")
    except Exception as e:
        print(f"Pexels image API: FAIL — {e}")


async def main() -> None:
    print("=== FFmpeg ===")
    check_ffmpeg()
    print("\n=== Pexels ===")
    check_pexels_config()
    await check_pexels_api()


if __name__ == "__main__":
    asyncio.run(main())
