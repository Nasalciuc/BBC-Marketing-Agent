"""Regenerate London deal v2 after trip-type branding fix."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FINAL_IMG = ROOT / "output" / "LONDON_DEAL_v2.jpg"
CAPTION_FILE = ROOT / "output" / "LONDON_CAPTION_v2.txt"
BG = ROOT / "output" / "london_bg_gemini.jpg"


async def main() -> None:
    from services.anthropic_client import generate_caption
    from services.branding_engine import generate_branded_image
    from services.telegram_client import send_message

    bg = str(BG if BG.exists() else "assets/defaults/default_background.jpg")
    branded = await asyncio.to_thread(
        generate_branded_image,
        "London",
        "JFK -> London",
        "from $1,511 One-Way",
        bg,
    )
    FINAL_IMG.write_bytes(branded)
    print(f"Image v2: {FINAL_IMG} ({len(branded):,} bytes)")

    event_data = {
        "name": "London",
        "event_name": "London",
        "city": "London, United Kingdom",
        "category": "travel",
        "routes": [{"from": "JFK", "to": "LHR"}],
        "from_iata": "JFK",
        "to_iata": "LHR",
        "price": "$1,511 one-way",
        "event_context": (
            "London is calling. Explore historic landmarks, vibrant culture, "
            "and world-class cuisine—all while enjoying the comfort of Business Class."
        ),
        "sales_hook": "London is calling. Answer in Business Class.",
    }

    caption = await generate_caption(branded, event_data)
    if "round-trip" in caption.lower() or "round trip" in caption.lower():
        caption = (
            caption.replace("round-trip", "one-way")
            .replace("Round-Trip", "One-Way")
            .replace("round trip", "one-way")
        )
    CAPTION_FILE.write_text(caption, encoding="utf-8")
    print(f"Caption v2 ({len(caption)} chars):\n{caption}")

    import httpx

    from config import settings
    from services.supabase_client import upload_image

    chat_id = settings.telegram_chat_id
    tg_caption = (
        "*LONDON DEAL v2 — Fixed*\n\n"
        "Verifica: One-Way pe imagine + caption\n\n"
        "_Forward to WhatsApp Channel daca e OK_"
    )

    image_url = None
    if settings.supabase_url and settings.supabase_key:
        image_url = await upload_image(branded, "deals/LONDON-OW-v2/landscape.jpg")

    if image_url:
        from services.telegram_client import send_photo

        await send_photo(chat_id=chat_id, photo_url=image_url, caption=tg_caption)
    elif settings.telegram_bot_token:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(
                url,
                data={"chat_id": str(chat_id), "caption": tg_caption, "parse_mode": "Markdown"},
                files={"photo": ("LONDON_DEAL_v2.jpg", branded, "image/jpeg")},
            )

    await send_message(chat_id=chat_id, text=f"*Caption WhatsApp (copiaza):*\n\n{caption}")
    print("Telegram v2 sent")


if __name__ == "__main__":
    asyncio.run(main())
