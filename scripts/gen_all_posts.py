"""Generate 5 post types: Brand x2, Rome, Multi-Destination x2 + Telegram."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output"
DEFAULT_BG = "assets/defaults/default_background.jpg"


async def _gemini_bg(path: Path, prompt: str, label: str) -> str:
    from services.gemini_client import generate_event_image

    if path.exists() and path.stat().st_size > 50_000:
        print(f"  Reuse {label}: {path.name}")
        return str(path)
    print(f"  Gemini {label}...")
    bg = await generate_event_image(prompt)
    if bg:
        path.write_bytes(bg)
        print(f"  OK {label}: {len(bg):,} bytes")
        return str(path)
    print(f"  FAIL {label} — default bg")
    return DEFAULT_BG


async def _brand(image_path: str, **kwargs) -> bytes:
    from services.branding_engine import generate_branded_image

    return await asyncio.to_thread(generate_branded_image, **kwargs, background_url_or_path=image_path)


async def brand_posts() -> None:
    print("=" * 55)
    print("TIP 1: BRAND POST — 2 variante")
    print("=" * 55)

    bg_a = await _gemini_bg(
        OUTPUT / "BRAND_bg_cabin.jpg",
        "Luxurious business class airline cabin interior, lie-flat seat fully reclined, "
        "champagne glass on tray, warm ambient lighting, premium blanket and pillow, "
        "window showing clouds at sunset, professional airline photography, "
        "cinematic, no text no logos no watermarks",
        "cabin",
    )
    bg_b = await _gemini_bg(
        OUTPUT / "BRAND_bg_lounge.jpg",
        "Premium airline lounge interior, elegant seating area with floor-to-ceiling windows "
        "overlooking airport runway, cocktail bar, businessman relaxing with newspaper, "
        "warm modern design, soft lighting, professional interior photography, "
        "cinematic, no text no logos no watermarks",
        "lounge",
    )

    common = dict(
        event_name="Business Class",
        route="Why fly Business Class?",
        price="Lie-flat seats · Lounge access · Premium dining",
        badge_text="EXPERIENCE",
        subtitle="",
        urgency="Travel should be part of the experience",
        cta_url="buybusinessclass.com",
    )

    branded_a = await _brand(bg_a, **common)
    branded_b = await _brand(bg_b, **common)
    (OUTPUT / "BRAND_A_cabin.jpg").write_bytes(branded_a)
    (OUTPUT / "BRAND_B_lounge.jpg").write_bytes(branded_b)
    print(f"Brand A: {len(branded_a):,} bytes | Brand B: {len(branded_b):,} bytes")

    caption = """✨ Why fly Business Class?

✔️ Lie-flat seats
✔️ Lounge access
✔️ Priority boarding
✔️ Premium dining

Travel should be part of the experience, not just the destination.

buybusinessclass.com
☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"""
    (OUTPUT / "BRAND_CAPTION.txt").write_text(caption, encoding="utf-8")


async def rome_deal() -> None:
    from services.anthropic_client import generate_caption
    from services.pricing_engine import calculate_price, format_price

    print("\n" + "=" * 55)
    print("TIP 2: ROME DEAL")
    print("=" * 55)

    price_ow = calculate_price("JFK", "FCO", "one_way", "business")
    if price_ow:
        price_str = format_price(price_ow)
    else:
        price_rt = calculate_price("JFK", "FCO", "round_trip", "business")
        price_str = f"${int(price_rt * 0.65):,}" if price_rt else "$1,450"
    print(f"Rome one-way: {price_str}")

    bg = await _gemini_bg(
        OUTPUT / "ROME_bg_gemini.jpg",
        "Stunning view of Rome Italy at golden hour, Colosseum in foreground, "
        "ancient Roman Forum visible, warm golden light on ancient stone, "
        "dramatic sky, professional travel photography, cinematic, "
        "landscape 16:9, no text no logos no watermarks",
        "Rome",
    )

    branded = await _brand(
        bg,
        event_name="Rome",
        route="Escape to Rome",
        price=f"{price_str} one-way",
        badge_text="ROME",
        subtitle="Business Class to Rome",
        urgency="A destination like no other",
        cta="Check available seats",
        cta_url="buybusinessclass.com",
    )
    (OUTPUT / "ROME_DEAL.jpg").write_bytes(branded)
    print(f"Rome deal: {len(branded):,} bytes")

    event_data = {
        "name": "Rome",
        "event_name": "Rome",
        "city": "Rome, Italy",
        "category": "travel",
        "routes": [{"from": "JFK", "to": "FCO"}],
        "price": f"{price_str} one-way",
        "event_context": (
            "Escape to Rome. Ancient history, authentic Italian cuisine, "
            "and breathtaking landmarks make Rome a destination like no other."
        ),
        "sales_hook": "A destination like no other. Fly in comfort.",
    }
    try:
        caption = await generate_caption(branded, event_data)
    except Exception as e:
        print(f"Claude failed: {e}")
        caption = f"""✈️ Escape to Rome.

Ancient history, authentic Italian cuisine, and breathtaking landmarks make Rome a destination like no other.

Business Class to Rome from {price_str} one-way.
Fares from New York, Los Angeles, Miami, and more.

buybusinessclass.com
☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"""

    if "888-322-7999" not in caption:
        from prompts.system_prompts import CONTACT_BLOCK

        caption = caption.rstrip() + f"\n\n{CONTACT_BLOCK}"
    (OUTPUT / "ROME_CAPTION.txt").write_text(caption, encoding="utf-8")


async def multi_destination() -> None:
    from services.pricing_engine import calculate_price, format_price

    print("\n" + "=" * 55)
    print("TIP 3: MULTI-DESTINATION — 2 variante")
    print("=" * 55)

    bg_a = await _gemini_bg(
        OUTPUT / "MULTI_bg_airplane.jpg",
        "Business class airplane flying above beautiful clouds at golden hour, "
        "dramatic sky, sun rays through clouds, wing visible from premium window, "
        "sense of luxury travel and adventure, professional aviation photography, "
        "cinematic, landscape 16:9, no text no logos no watermarks",
        "airplane",
    )
    bg_b = await _gemini_bg(
        OUTPUT / "MULTI_bg_landmarks.jpg",
        "Dreamy luxury travel concept, golden hour light, iconic world landmarks "
        "faintly visible through soft clouds, Eiffel Tower silhouette and "
        "Mediterranean coastline, sense of wonder and possibility, "
        "soft warm tones, professional artistic photography, "
        "cinematic, landscape 16:9, no text no logos no watermarks",
        "landmarks",
    )

    destinations = [("LHR", "London"), ("CDG", "Paris"), ("FCO", "Rome"), ("DXB", "Dubai"), ("NRT", "Tokyo")]
    min_price = None
    for iata, _ in destinations:
        p = calculate_price("JFK", iata, "one_way", "business")
        if p and (min_price is None or p < min_price):
            min_price = p
    price_str = format_price(min_price) if min_price else "$1,511"
    print(f"Cheapest: {price_str} one-way")

    common = dict(
        event_name="Your Next Destination",
        route="Where will you go next?",
        price=f"{price_str} one-way",
        badge_text="EXPLORE",
        subtitle="Paris · London · Rome · Dubai · Tokyo",
        urgency="Luxury travel starts with the right flight",
        cta="Check available seats",
        cta_url="buybusinessclass.com",
    )

    branded_a = await _brand(bg_a, **common)
    branded_b = await _brand(bg_b, **common)
    (OUTPUT / "MULTI_A_airplane.jpg").write_bytes(branded_a)
    (OUTPUT / "MULTI_B_landmarks.jpg").write_bytes(branded_b)

    caption_a = f"""🌍 Where will you go next?

Paris. London. Rome. Dubai. Tokyo.
Wherever the world takes you, get there in the comfort and style you deserve.

Business Class from {price_str} one-way.

Luxury travel starts with the right flight.

buybusinessclass.com
☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"""

    caption_b = f"""🌍 Where will you go next?

Whether it's Paris, London, Rome, Dubai, or Tokyo—we'll help you get there in comfort and style.

Business Class from {price_str} one-way.
Fares from New York, Los Angeles, Miami, and more.

buybusinessclass.com
☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"""

    (OUTPUT / "MULTI_CAPTION_A.txt").write_text(caption_a, encoding="utf-8")
    (OUTPUT / "MULTI_CAPTION_B.txt").write_text(caption_b, encoding="utf-8")


async def send_all() -> None:
    import httpx

    from config import settings
    from services.supabase_client import upload_image
    from services.telegram_client import send_message, send_photo

    chat_id = settings.telegram_chat_id
    if not chat_id:
        print("TELEGRAM_CHAT_ID not set")
        return

    posts = [
        ("brand_a", "BRAND_A_cabin.jpg", "TIP 1: BRAND A (Cabin)", "BRAND_CAPTION.txt", None),
        ("brand_b", "BRAND_B_lounge.jpg", "TIP 1: BRAND B (Lounge)", None, None),
        ("rome", "ROME_DEAL.jpg", "TIP 2: ROME DEAL", "ROME_CAPTION.txt", "Rome Deal"),
        ("multi_a", "MULTI_A_airplane.jpg", "TIP 3: MULTI A (Airplane)", "MULTI_CAPTION_A.txt", None),
        ("multi_b", "MULTI_B_landmarks.jpg", "TIP 3: MULTI B (Landmarks)", "MULTI_CAPTION_B.txt", None),
    ]

    for key, img_file, header, cap_file, photo_cap in posts:
        img_path = OUTPUT / img_file
        if not img_path.exists():
            continue
        await send_message(chat_id=chat_id, text=f"━━━━━━━━━━━━━━━━━━━━━\n*{header}*\n━━━━━━━━━━━━━━━━━━━━━")

        img_bytes = img_path.read_bytes()
        image_url = None
        if settings.supabase_url and settings.supabase_key:
            image_url = await upload_image(img_bytes, f"deals/posts/{key}.jpg")

        if image_url:
            await send_photo(chat_id=chat_id, photo_url=image_url, caption=photo_cap or header)
        elif settings.telegram_bot_token:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
            async with httpx.AsyncClient(timeout=60) as client:
                await client.post(
                    url,
                    data={"chat_id": str(chat_id), "caption": photo_cap or header, "parse_mode": "Markdown"},
                    files={"photo": (img_file, img_bytes, "image/jpeg")},
                )

        if cap_file:
            cap = (OUTPUT / cap_file).read_text(encoding="utf-8")
            await send_message(chat_id=chat_id, text=f"*Caption:*\n\n{cap}")
        await asyncio.sleep(1)

    print("All 5 posts sent to Telegram")


async def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    await brand_posts()
    await rome_deal()
    await multi_destination()
    await send_all()
    print("\nDone — 5 images + 5 captions in output/")


if __name__ == "__main__":
    asyncio.run(main())
