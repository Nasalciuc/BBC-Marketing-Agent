"""Generate London one-way deal: branded image, captions, Telegram preview."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.branding_engine import generate_branded_image
from services.pricing_engine import calculate_price, format_price


def main() -> None:
    price_ow = calculate_price("JFK", "LHR", "one_way", "business")
    price_rt = calculate_price("JFK", "LHR", "round_trip", "business")

    print(f"JFK -> LHR One-Way:    {format_price(price_ow) if price_ow else 'N/A'}")
    print(f"JFK -> LHR Round-Trip: {format_price(price_rt) if price_rt else 'N/A'}")

    if price_ow:
        price_str = format_price(price_ow)
    elif price_rt:
        price_str = f"${int(price_rt * 0.65):,}"
    else:
        price_str = "$1,320"

    print(f"Pret one-way: {price_str}")

    bg_path = "assets/defaults/default_background.jpg"
    london_bgs = list(Path("assets").rglob("*london*")) + list(Path("assets").rglob("*London*"))
    if london_bgs:
        bg_path = str(london_bgs[0])
        print(f"Using London bg: {bg_path}")
    else:
        print(f"Using default bg: {bg_path}")

    branded = generate_branded_image(
        event_name="London",
        route="JFK -> London",
        price=f"from {price_str} One-Way",
        background_url_or_path=bg_path,
    )

    output_path = ROOT / "output" / "LONDON_DEAL_OW.jpg"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_bytes(branded)
    print(f"\nOK Imagine: {output_path} ({len(branded):,} bytes)")

    caption_whatsapp = f"""✈️ London is calling.

Explore historic landmarks, vibrant culture, and world-class cuisine—all while enjoying the comfort of Business Class.

Business Class JFK → London from {price_str} one-way.

buybusinessclass.com
☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"""

    caption_short = f"""✈️ London is calling.

Business Class JFK → London from {price_str} one-way.

buybusinessclass.com
☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"""

    caption_premium = f"""✈️ London is calling.

Historic landmarks. World-class cuisine. West End theatre.
All wrapped in a lie-flat seat.

Business Class JFK → London from {price_str} one-way.

buybusinessclass.com
☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"""

    (ROOT / "output" / "LONDON_CAPTION.txt").write_text(caption_whatsapp, encoding="utf-8")
    (ROOT / "output" / "LONDON_CAPTION_SHORT.txt").write_text(caption_short, encoding="utf-8")
    (ROOT / "output" / "LONDON_CAPTION_PREMIUM.txt").write_text(caption_premium, encoding="utf-8")

    print("=" * 50)
    print("CAPTION WhatsApp/Channel:")
    print("=" * 50)
    print(caption_whatsapp)
    print("=" * 50)
    print(f"Lungime: {len(caption_whatsapp)} chars")
    print("=" * 50)
    print(f"\nCaption saved: output/LONDON_CAPTION.txt")

    print("\n" + "=" * 50)
    print("VARIANTA SCURTA:")
    print("=" * 50)
    print(caption_short)
    print(f"({len(caption_short)} chars)")

    print("\n" + "=" * 50)
    print("VARIANTA PREMIUM:")
    print("=" * 50)
    print(caption_premium)
    print(f"({len(caption_premium)} chars)")

    asyncio.run(send_preview(output_path, price_str))


async def send_preview(output_path: Path, price_str: str) -> None:
    import httpx

    from config import settings
    from services.supabase_client import upload_image
    from services.telegram_client import send_message, send_photo

    chat_id = settings.telegram_chat_id
    if not chat_id:
        print("❌ TELEGRAM_CHAT_ID not set")
        return

    img_bytes = output_path.read_bytes()
    preview_caption = (
        f"✈️ *London is calling.*\n\n"
        f"Business Class JFK → London from {price_str} one-way.\n\n"
        f"_Review this deal — ready to post?_"
    )

    image_url = None
    if settings.supabase_url and settings.supabase_key:
        try:
            image_url = await upload_image(img_bytes, "deals/LONDON-OW/landscape.jpg")
            if image_url:
                print(f"✅ Supabase upload: {image_url}")
        except Exception as e:
            print(f"⚠️ Supabase upload failed: {e}")

    if image_url:
        result = await send_photo(
            chat_id=chat_id,
            photo_url=image_url,
            caption=preview_caption,
        )
        if result and result.get("ok"):
            print("✅ Preview sent to Telegram (photo URL)")
        else:
            print(f"❌ Telegram sendPhoto failed: {result}")
        return

    # Fallback: upload bytes directly via multipart (no public URL needed)
    if settings.telegram_bot_token:
        try:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url,
                    data={"chat_id": str(chat_id), "caption": preview_caption, "parse_mode": "Markdown"},
                    files={"photo": ("LONDON_DEAL_OW.jpg", img_bytes, "image/jpeg")},
                )
                data = resp.json()
                if data.get("ok"):
                    print("✅ Preview sent to Telegram (direct upload)")
                    return
                print(f"❌ Telegram direct upload failed: {data}")
        except Exception as e:
            print(f"❌ Telegram direct upload error: {e}")

    await send_message(
        chat_id=chat_id,
        text=(
            f"✈️ *London is calling.*\n\n"
            f"Business Class JFK → London from {price_str} one-way.\n\n"
            f"_Image saved locally: output/LONDON_DEAL_OW.jpg_"
        ),
    )
    print("✅ Text preview sent (no image upload)")


if __name__ == "__main__":
    main()
