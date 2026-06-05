"""Regenerate brand posts v2 — no duplicate URL, benefits in price zone."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output"
DEFAULT = "assets/defaults/default_background.jpg"


async def main() -> None:
    from services.branding_engine import generate_branded_image
    from services.telegram_client import send_message

    common = dict(
        event_name="Business Class",
        route="Why fly Business Class?",
        price="Lie-flat seats · Lounge access · Premium dining",
        badge_text="EXPERIENCE",
        subtitle="",
        urgency="Travel should be part of the experience",
        cta_url="buybusinessclass.com",
    )

    for label, bg_name, out_name in [
        ("A cabin", "BRAND_bg_cabin.jpg", "BRAND_A_v2.jpg"),
        ("B lounge", "BRAND_bg_lounge.jpg", "BRAND_B_v2.jpg"),
    ]:
        bg = OUTPUT / bg_name
        bg_path = str(bg if bg.exists() else DEFAULT)
        branded = await asyncio.to_thread(generate_branded_image, **common, background_url_or_path=bg_path)
        (OUTPUT / out_name).write_bytes(branded)
        print(f"Brand {label}: {out_name} ({len(branded):,} bytes)")

    import httpx

    from config import settings

    chat_id = settings.telegram_chat_id
    caption = (OUTPUT / "BRAND_CAPTION.txt").read_text(encoding="utf-8")

    for name, path in [("Brand A v2 (Cabin)", "BRAND_A_v2.jpg"), ("Brand B v2 (Lounge)", "BRAND_B_v2.jpg")]:
        img = (OUTPUT / path).read_bytes()
        await send_message(chat_id=chat_id, text=f"*{name} — FIXED*")
        if settings.telegram_bot_token:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
            async with httpx.AsyncClient(timeout=60) as client:
                await client.post(
                    url,
                    data={"chat_id": str(chat_id), "parse_mode": "Markdown"},
                    files={"photo": (path, img, "image/jpeg")},
                )
    await send_message(chat_id=chat_id, text=f"*Caption:*\n\n{caption}")
    print("Telegram sent")


if __name__ == "__main__":
    asyncio.run(main())
