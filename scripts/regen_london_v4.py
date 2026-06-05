"""Regenerate London v4 — split price layout."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main() -> None:
    from services.branding_engine import generate_branded_image
    from services.telegram_client import send_message

    bg = ROOT / "output" / "london_bg_gemini.jpg"
    bg_path = str(bg if bg.exists() else ROOT / "assets" / "defaults" / "default_background.jpg")

    branded = await asyncio.to_thread(
        generate_branded_image,
        "London",
        "London is calling",
        "$1,511 one-way",
        bg_path,
        urgency="Your seat is waiting",
        cta_text="Check available seats",
        cta_url="buybusinessclass.com",
    )
    out = ROOT / "output" / "LONDON_DEAL_v4.jpg"
    out.write_bytes(branded)
    print(f"Image v4: {out} ({len(branded):,} bytes)")

    import httpx

    from config import settings

    chat_id = settings.telegram_chat_id
    if settings.telegram_bot_token and chat_id:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                data={
                    "chat_id": str(chat_id),
                    "caption": "*LONDON v4 — split price layout*\nfrom $1,511 + one-way on line 2",
                    "parse_mode": "Markdown",
                },
                files={"photo": ("LONDON_DEAL_v4.jpg", branded, "image/jpeg")},
            )
            print("Telegram:", resp.json().get("ok"))
        caption_path = ROOT / "output" / "LONDON_CAPTION_v3.txt"
        if caption_path.exists():
            await send_message(
                chat_id=chat_id,
                text=f"*Caption (unchanged):*\n\n{caption_path.read_text(encoding='utf-8')}",
            )


if __name__ == "__main__":
    asyncio.run(main())
