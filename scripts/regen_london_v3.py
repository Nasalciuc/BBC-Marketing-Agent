"""London deal v3 — emotional template + Claude caption + Telegram."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FINAL = ROOT / "output" / "LONDON_DEAL_v3.jpg"
CAPTION = ROOT / "output" / "LONDON_CAPTION_v3.txt"
BG = ROOT / "output" / "london_bg_gemini.jpg"

BIATRICIA_BODY = (
    "Explore historic landmarks, vibrant culture, and world-class cuisine—all while "
    "enjoying the comfort of Business Class."
)
HUB_LINE = "from New York, Los Angeles, Miami, and more."


async def main() -> None:
    from services.anthropic_client import generate_caption, rewrite_text
    from services.branding_engine import generate_branded_image
    from services.telegram_client import send_message

    bg = str(BG if BG.exists() else "assets/defaults/default_background.jpg")
    branded = await asyncio.to_thread(
        generate_branded_image,
        "London",
        "London is calling",
        "$1,511 one-way",
        bg,
        urgency="Your seat is waiting",
        cta_text="Check available seats",
        cta_url="buybusinessclass.com",
    )
    FINAL.write_bytes(branded)
    print(f"Image v3: {FINAL} ({len(branded):,} bytes)")

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
            "and world-class cuisine—all while enjoying the comfort of Business Class. "
            "From Big Ben to Borough Market, the West End to Westminster, London delivers."
        ),
        "sales_hook": "London is calling. Your seat is waiting.",
    }

    try:
        caption = await generate_caption(branded, event_data)
        source = "Claude"
    except Exception as e:
        print(f"Claude failed: {e}")
        from prompts.system_prompts import _fallback_caption

        caption = _fallback_caption(event_data)
        source = "Fallback"

    if BIATRICIA_BODY not in caption:
        caption = await rewrite_text(
            caption,
            f"Use hook 'London is calling.' Include exactly: '{BIATRICIA_BODY}' "
            f"Price line: 'Business Class to London from $1,511 one-way, {HUB_LINE}' "
            "No JFK arrow route. No round-trip.",
            context="WhatsApp channel caption for London one-way deal",
        )

    for bad in ("round-trip", "Round-Trip", "round trip", "JFK →", "JFK->"):
        if bad.lower() in caption.lower():
            caption = caption.replace(bad, "one-way" if "trip" in bad.lower() else "")

    if "888-322-7999" not in caption:
        from prompts.system_prompts import CONTACT_BLOCK

        caption = caption.rstrip() + f"\n\n{CONTACT_BLOCK}"

    CAPTION.write_text(caption, encoding="utf-8")
    print(f"\nCaption ({source}, {len(caption)} chars):\n{caption}")

    import httpx

    from config import settings
    from services.supabase_client import upload_image

    chat_id = settings.telegram_chat_id
    image_url = None
    if settings.supabase_url and settings.supabase_key:
        image_url = await upload_image(branded, "deals/LONDON-OW-v3/landscape.jpg")

    if image_url:
        from services.telegram_client import send_photo

        await send_photo(
            chat_id=chat_id,
            photo_url=image_url,
            caption="*LONDON DEAL v3 — Final review*",
        )
    elif settings.telegram_bot_token:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(
                url,
                data={"chat_id": str(chat_id), "caption": "*LONDON DEAL v3 — Final review*", "parse_mode": "Markdown"},
                files={"photo": ("LONDON_DEAL_v3.jpg", branded, "image/jpeg")},
            )

    await send_message(chat_id=chat_id, text=f"*Caption:*\n\n{caption}")
    print("Telegram v3 sent")


if __name__ == "__main__":
    asyncio.run(main())
