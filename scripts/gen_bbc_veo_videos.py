"""BBC Veo 3.1 — 5-ingredient formula videos + Telegram."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output"

LONDON_VIDEO_PROMPT = (
    "Slow establishing shot. "
    "The London skyline with Tower Bridge spanning the Thames river, "
    "Big Ben and Houses of Parliament in the warm background. "
    "Water gently rippling with golden reflections, clouds slowly drifting "
    "across the dramatic painted sunset sky, bridge lights beginning to glow. "
    "Golden hour with warm amber sunlight casting long shadows on stone. "
    "Camera performs a slow, smooth dolly push-in toward Tower Bridge. "
    "70mm cinematic lens, shallow depth of field, film grain. "
    "No text, no subtitles, no watermarks, no logos."
)

MONACO_VIDEO_PROMPT = (
    "Low-angle tracking shot. "
    "A Formula 1 car races through the narrow Monaco street circuit, "
    "Mediterranean harbor with gleaming white superyachts visible behind barriers. "
    "Red sparks flying from car floor on asphalt, intense motion blur on wheels, "
    "then slow-motion transition revealing Monte Carlo terrace at golden sunset. "
    "Warm Mediterranean light painting harbor in amber and gold tones. "
    "Camera tracks alongside car then rises into slow crane shot over harbor. "
    "24mm wide anamorphic lens, cinematic motion blur, IMAX quality. "
    "No text, no subtitles, no watermarks, no team logos."
)

BRAND_VIDEO_PROMPT = (
    "Medium close-up shot pulling out to wide. "
    "A premium business class lie-flat seat fully reclined with crisp white duvet, "
    "a crystal champagne flute with golden bubbles rising on the tray table. "
    "Champagne bubbles slowly ascending, cabin ambient lights shifting from gold to soft blue, "
    "fabric of premium blanket gently settling, condensation on glass catching light. "
    "Night flight ambience: warm amber overhead lights, passenger window showing "
    "moonlit clouds slowly passing at 40,000 feet. "
    "Camera performs slow smooth dolly backward, revealing the full luxury cabin. "
    "50mm cinematic lens, shallow depth of field, warm Kodak film tones. "
    "No text, no airline logos, no subtitles."
)

MULTI_VIDEO_PROMPT = (
    "Wide establishing shot transitioning through dreamlike crossfades. "
    "The Eiffel Tower at golden hour, its iron lattice catching warm Paris light, "
    "dissolving into London's Tower Bridge at sunset with Thames reflections, "
    "flowing into Rome's Colosseum bathed in warm Italian amber, "
    "transitioning to Dubai's Burj Khalifa piercing through lavender twilight clouds. "
    "Each city glowing with its own warm golden character. "
    "Gentle ethereal transitions, clouds weaving between landmarks. "
    "Slow dreamy camera movements: orbit, dolly, crane — one per city. "
    "70mm anamorphic lens, extreme shallow depth, cinematic color grading. "
    "No text, no logos, no subtitles, no watermarks."
)

TEST_PROMPT = "A single golden autumn leaf slowly falling through warm sunlight"


def _find_london_image() -> Path | None:
    for p in (
        "LONDON_DEAL_v4.jpg",
        "LONDON_DEAL_v3.jpg",
        "LONDON_DEAL_FINAL.jpg",
        "LONDON_DEAL_v2.jpg",
    ):
        path = OUTPUT / p
        if path.exists():
            return path
    return None


def _find_cabin_image() -> Path | None:
    for p in ("BRAND_A_v2.jpg", "BRAND_A_cabin.jpg", "BRAND_bg_cabin.jpg"):
        path = OUTPUT / p
        if path.exists():
            return path
    return None


async def _gen(label: str, out_name: str, prompt: str, image_path: Path | None = None, fast: bool = False) -> bool:
    from services.veo_client import generate_video

    out = OUTPUT / out_name
    print(f"\n{'=' * 55}\n{label}\n-> {out.name}\n{'=' * 55}")
    try:
        kwargs: dict = {
            "prompt": prompt,
            "output_path": out,
            "model": "veo-3.1-fast-generate-preview" if fast else "veo-3.1-generate-preview",
            "duration_seconds": 4 if fast else 8,
        }
        if image_path:
            kwargs["image_path"] = image_path
        data = await generate_video(**kwargs)
        print(f"OK {out.name} ({len(data) / 1024 / 1024:.1f} MB)")
        return True
    except Exception as e:
        print(f"FAIL {label}: {e}")
        return False


async def send_all_videos() -> None:
    import httpx

    from config import settings
    from services.telegram_client import send_message

    chat_id = settings.telegram_chat_id
    token = settings.telegram_bot_token
    if not chat_id or not token:
        print("Telegram not configured")
        return

    videos = [
        ("London — Image-to-Video", "LONDON_VIDEO.mp4"),
        ("Monaco GP — Text-to-Video", "MONACO_VIDEO.mp4"),
        ("Brand — Business Class", "BRAND_VIDEO.mp4"),
        ("Multi-Destination — Montage", "MULTI_VIDEO.mp4"),
    ]

    url = f"https://api.telegram.org/bot{token}/sendVideo"
    async with httpx.AsyncClient(timeout=180) as http:
        for title, name in videos:
            path = OUTPUT / name
            if not path.exists():
                print(f"Skip {name} (missing)")
                continue
            video_bytes = path.read_bytes()
            mb = len(video_bytes) / 1024 / 1024
            print(f"Sending {title} ({mb:.1f} MB)...")
            resp = await http.post(
                url,
                data={
                    "chat_id": str(chat_id),
                    "caption": f"*{title}*\n\n_Video generat cu Veo 3.1_\n_Review — ready to post?_",
                    "parse_mode": "Markdown",
                },
                files={"video": (name, video_bytes, "video/mp4")},
            )
            data = resp.json()
            print("  OK" if data.get("ok") else f"  ERR: {data}")
            await asyncio.sleep(2)

    await send_message(
        chat_id=chat_id,
        text=(
            "*4 video generate cu Veo 3.1:*\n\n"
            "London (image-to-video)\n"
            "Monaco GP (text-to-video)\n"
            "Brand Business Class\n"
            "Multi-destination montage\n\n"
            "_Alege favoritele → postam in Channel_"
        ),
    )


async def main() -> None:
    OUTPUT.mkdir(exist_ok=True)

    # PAS 1 — quick test (4s fast)
    await _gen("Test Veo 3.1 Fast (4s)", "test_veo.mp4", TEST_PROMPT, fast=True)

    london_img = _find_london_image()
    if london_img:
        print(f"London image: {london_img.name}")
        await _gen("London image-to-video", "LONDON_VIDEO.mp4", LONDON_VIDEO_PROMPT, london_img)
    else:
        print("No London deal image — skip LONDON_VIDEO.mp4")

    await _gen("Monaco GP text-to-video", "MONACO_VIDEO.mp4", MONACO_VIDEO_PROMPT)

    cabin_img = _find_cabin_image()
    if cabin_img:
        print(f"Cabin image: {cabin_img.name}")
        await _gen("Brand image-to-video", "BRAND_VIDEO.mp4", BRAND_VIDEO_PROMPT, cabin_img)
    else:
        await _gen("Brand text-to-video", "BRAND_VIDEO.mp4", BRAND_VIDEO_PROMPT)

    await _gen("Multi-destination montage", "MULTI_VIDEO.mp4", MULTI_VIDEO_PROMPT)

    print("\n" + "=" * 55)
    print("  BBC VIDEO — REZUMAT")
    print("=" * 55)
    for name, path in [
        ("London (i2v)", "LONDON_VIDEO.mp4"),
        ("Monaco (t2v)", "MONACO_VIDEO.mp4"),
        ("Brand", "BRAND_VIDEO.mp4"),
        ("Multi (t2v)", "MULTI_VIDEO.mp4"),
        ("Test Veo", "test_veo.mp4"),
    ]:
        p = OUTPUT / path
        if p.exists():
            print(f"  OK {name:18s} {p.stat().st_size / 1024 / 1024:.1f} MB  {path}")
        else:
            print(f"  -- {name:18s} NOT GENERATED")

    await send_all_videos()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
