"""London deal: Gemini photo -> BBC branding -> Claude caption -> Telegram."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "output"
GEMINI_BG = OUTPUT / "london_bg_gemini.jpg"
MANUAL_BG = OUTPUT / "london_bg_manual.jpg"
FINAL_IMG = OUTPUT / "LONDON_DEAL_FINAL.jpg"
CAPTION_FILE = OUTPUT / "LONDON_CAPTION_CLAUDE.txt"

LONDON_UNSplash = (
    "https://images.unsplash.com/photo-1513635269975-59663e0ac1ad"
    "?auto=format&fit=crop&w=1920&q=85"
)


async def step_price() -> str:
    from services.pricing_engine import calculate_price, format_price

    price_ow = calculate_price("JFK", "LHR", "one_way", "business")
    if price_ow:
        price_str = format_price(price_ow)
    else:
        price_rt = calculate_price("JFK", "LHR", "round_trip", "business")
        price_str = f"${int(price_rt * 0.65):,}" if price_rt else "$1,511"

    print(f"One-Way price: {price_str}")
    return price_str


async def step_gemini_photo() -> bytes | None:
    from services.gemini_client import generate_event_image

    OUTPUT.mkdir(exist_ok=True)

    if GEMINI_BG.exists() and GEMINI_BG.stat().st_size > 100_000:
        data = GEMINI_BG.read_bytes()
        print(f"Reusing existing Gemini photo: {len(data):,} bytes")
        return data

    prompt = (
        "Stunning cinematic photograph of London, England. "
        "Tower Bridge at golden hour with Thames river in foreground, "
        "Big Ben and Houses of Parliament visible in background. "
        "Warm golden light, rich colors, dramatic sky with clouds. "
        "Professional DSLR quality, landscape 16:9 composition. "
        "No text, no logos, no watermarks, no words, no letters."
    )

    print("Gemini generating London photo...")
    bg_bytes = await generate_event_image(prompt)

    if bg_bytes:
        GEMINI_BG.write_bytes(bg_bytes)
        print(f"OK Gemini photo: {len(bg_bytes):,} bytes -> {GEMINI_BG.name}")
        return bg_bytes

    print("Gemini image gen failed — trying simpler prompt...")
    prompt2 = (
        "Beautiful London cityscape at sunset, Tower Bridge, "
        "Thames river, golden hour, cinematic, no text no logos"
    )
    bg_bytes2 = await generate_event_image(prompt2)
    if bg_bytes2:
        GEMINI_BG.write_bytes(bg_bytes2)
        print(f"OK Gemini photo (alt): {len(bg_bytes2):,} bytes")
        return bg_bytes2

    print("Gemini still failed — downloading Unsplash London fallback...")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(LONDON_UNSplash)
            resp.raise_for_status()
            MANUAL_BG.write_bytes(resp.content)
            print(f"OK Manual photo: {len(resp.content):,} bytes -> {MANUAL_BG.name}")
            return resp.content
    except Exception as e:
        print(f"Unsplash fallback failed: {e}")
        return None


async def step_branding(price_str: str) -> bytes:
    from services.branding_engine import generate_branded_image

    if GEMINI_BG.exists():
        bg_path = str(GEMINI_BG)
        print(f"Using Gemini photo: {bg_path}")
    elif MANUAL_BG.exists():
        bg_path = str(MANUAL_BG)
        print(f"Using manual photo: {bg_path}")
    else:
        bg_path = "assets/defaults/default_background.jpg"
        print(f"Using default background: {bg_path}")

    branded = await asyncio.to_thread(
        generate_branded_image,
        event_name="London",
        route="JFK -> London",
        price=f"from {price_str} One-Way",
        background_url_or_path=bg_path,
    )
    FINAL_IMG.write_bytes(branded)
    print(f"OK Branded image: {FINAL_IMG} ({len(branded):,} bytes)")
    return branded


async def step_caption(price_str: str, branded_bytes: bytes) -> str:
    from prompts.system_prompts import _fallback_caption
    from services.anthropic_client import generate_caption as claude_caption

    event_data = {
        "name": "London",
        "event_name": "London",
        "city": "London, United Kingdom",
        "category": "travel",
        "routes": [{"from": "JFK", "to": "LHR"}],
        "from_iata": "JFK",
        "to_iata": "LHR",
        "price": f"{price_str} one-way",
        "event_context": (
            "London is calling. Explore historic landmarks, vibrant culture, "
            "and world-class cuisine—all while enjoying the comfort of "
            "Business Class. From Big Ben to Borough Market, from the "
            "West End to Westminster, London delivers at every turn."
        ),
        "sales_hook": "London is calling. Answer in Business Class.",
    }

    print("Claude analyzing branded image + writing caption...")
    caption = await claude_caption(branded_bytes, event_data)

    if not caption or len(caption) < 30:
        print("Claude caption too short — using fallback")
        caption = _fallback_caption(event_data)

    CAPTION_FILE.write_text(caption, encoding="utf-8")

    print("=" * 55)
    print("  CAPTION GENERAT DE CLAUDE:")
    print("=" * 55)
    print()
    print(caption)
    print()
    print("=" * 55)
    print(f"  Lungime: {len(caption)} chars")
    contact_ok = "888-322-7999" in caption
    print(f"  Contact block: {'OK' if contact_ok else 'MISSING'}")
    print("=" * 55)
    print(f"\nSaved: {CAPTION_FILE}")
    return caption


async def step_telegram(caption_text: str) -> None:
    import httpx

    from config import settings
    from services.supabase_client import upload_image
    from services.telegram_client import send_message, send_photo

    chat_id = settings.telegram_chat_id
    if not chat_id:
        print("TELEGRAM_CHAT_ID not set")
        return

    branded_bytes = FINAL_IMG.read_bytes()
    preview_caption = (
        f"*LONDON DEAL — Ready to post*\n\n"
        f"_{caption_text[:200]}{'...' if len(caption_text) > 200 else ''}_\n\n"
        f"Forward this to WhatsApp Channel"
    )

    image_url = None
    if settings.supabase_url and settings.supabase_key:
        try:
            image_url = await upload_image(branded_bytes, "deals/LONDON-OW-2026/landscape.jpg")
            if image_url:
                print(f"Supabase: {image_url}")
        except Exception as e:
            print(f"Supabase upload failed: {e}")

    if image_url:
        result = await send_photo(
            chat_id=chat_id,
            photo_url=image_url,
            caption=preview_caption,
        )
        if not result or not result.get("ok"):
            print(f"Telegram sendPhoto failed: {result}")
            image_url = None

    if not image_url and settings.telegram_bot_token:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                data={
                    "chat_id": str(chat_id),
                    "caption": preview_caption,
                    "parse_mode": "Markdown",
                },
                files={"photo": ("LONDON_DEAL_FINAL.jpg", branded_bytes, "image/jpeg")},
            )
            data = resp.json()
            if data.get("ok"):
                print("Preview photo sent (direct upload)")
            else:
                print(f"Direct upload failed: {data}")

    await send_message(
        chat_id=chat_id,
        text=f"*Caption for WhatsApp:*\n\n{caption_text}",
    )
    print("Full caption sent separately on Telegram")


async def main() -> None:
    price_str = await step_price()
    await step_gemini_photo()
    branded_bytes = await step_branding(price_str)
    caption = await step_caption(price_str, branded_bytes)
    await step_telegram(caption)

    bg_source = (
        "Gemini"
        if GEMINI_BG.exists()
        else ("Unsplash manual" if MANUAL_BG.exists() else "default")
    )

    print(
        f"""
{'=' * 43}
  LONDON DEAL — REZUMAT
{'=' * 43}

  Pret:     {price_str} one-way
  Ruta:     JFK -> London (LHR)
  Background: {bg_source}

  Fisiere:
    output/london_bg_gemini.jpg      — foto Gemini (daca exista)
    output/london_bg_manual.jpg      — fallback Unsplash (daca exista)
    output/LONDON_DEAL_FINAL.jpg     — imagine branded
    output/LONDON_CAPTION_CLAUDE.txt — caption Claude

  Telegram: preview trimis

  NEXT:
    1. Verifica pe Telegram cum arata
    2. Arata Biatriciei
    3. Daca OK -> forward imagine in WhatsApp Channel
    4. Copy-paste caption din LONDON_CAPTION_CLAUDE.txt
{'=' * 43}
"""
    )


if __name__ == "__main__":
    asyncio.run(main())
