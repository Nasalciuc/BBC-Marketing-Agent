"""Monaco Grand Prix deal — Gemini photo, branding, exact director caption."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output"


async def monaco_deal() -> None:
    from config import settings
    from services.branding_engine import generate_branded_image
    from services.gemini_client import generate_event_image
    from services.pricing_engine import calculate_price, format_price
    from services.telegram_client import send_message, send_photo

    OUTPUT.mkdir(exist_ok=True)

    price_rt = calculate_price("JFK", "NCE", "round_trip", "business")
    if price_rt:
        price_str = format_price(price_rt)
    else:
        price_str = "$3,499"
        print("Pricing engine returned None — using $3,499 fallback")

    print(f"JFK -> NCE round-trip: {price_str}")

    print("Gemini generating Monaco photo...")
    bg = await generate_event_image(
        "Formula 1 cars racing through Monaco street circuit at high speed, "
        "Monte Carlo harbor with superyachts visible in background, "
        "dramatic low angle, motion blur on wheels, Mediterranean blue water, "
        "golden hour lighting, professional motorsport photography, "
        "cinematic, landscape 16:9, no text no logos no watermarks"
    )

    bg_path = OUTPUT / "monaco_bg_gemini.jpg"
    if bg:
        bg_path.write_bytes(bg)
        print(f"  Photo: {len(bg):,} bytes")
    else:
        print("  Gemini failed — using default bg")

    bg_file = str(bg_path if bg_path.exists() else ROOT / "assets/defaults/default_background.jpg")

    branded = await asyncio.to_thread(
        generate_branded_image,
        event_name="Monaco",
        route="Monaco Grand Prix",
        price=f"from {price_str} round-trip",
        background_url_or_path=bg_file,
        badge_text="MONACO",
        subtitle="Business Class to Nice",
        urgency="The best seats never wait",
        cta_text="Check available seats",
        cta_url="buybusinessclass.com",
    )
    (OUTPUT / "MONACO_GP_DEAL.jpg").write_bytes(branded)
    print(f"Branded: {len(branded):,} bytes")

    caption = f"""🏎️ Monaco Grand Prix

Some weekends become stories you tell for decades.

Monte Carlo. Formula 1. The world's most coveted guest list.

Business Class to Nice from {price_str} round-trip.

The paddock is filling up. The best seats never wait.

buybusinessclass.com
☎️ +1 888-322-7999
📩 deals@buybusinessclass.com"""

    try:
        from anthropic import Anthropic

        if settings.anthropic_api_key:
            client = Anthropic(api_key=settings.anthropic_api_key)
            check = await asyncio.to_thread(
                client.messages.create,
                model=settings.anthropic_model or "claude-sonnet-4-20250514",
                max_tokens=200,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Pricing engine says JFK→Nice round-trip = {price_str}.\n"
                            f"Check this caption. If the price in the caption does NOT match {price_str}, "
                            f"return the CORRECTED caption. If it matches, return EXACTLY 'OK'.\n\n"
                            f"Caption:\n{caption}"
                        ),
                    }
                ],
            )
            result = check.content[0].text.strip()
            if result != "OK" and price_str in result:
                caption = result
                print("Claude corrected caption (price mismatch fixed)")
            elif result == "OK":
                print(f"Claude verified: price {price_str} correct")
            else:
                print(f"Claude response: {result[:100]}")
    except Exception as e:
        print(f"Claude price check skipped: {e}")

    (OUTPUT / "MONACO_CAPTION.txt").write_text(caption, encoding="utf-8")
    print(f"\nCaption:\n{caption}")

    chat_id = settings.telegram_chat_id
    image_url = None

    if settings.supabase_url and settings.supabase_key:
        try:
            from services.supabase_client import upload_image

            image_url = await upload_image(branded, "deals/MONACO-GP/landscape.jpg")
            if image_url:
                print(f"Supabase: {image_url}")
        except Exception as e:
            print(f"Supabase: {e}")

    if image_url:
        await send_photo(
            chat_id=chat_id,
            photo_url=image_url,
            caption="🏎️ *Monaco Grand Prix — Ready to post*",
        )
    elif settings.telegram_bot_token and chat_id:
        import httpx

        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(
                url,
                data={
                    "chat_id": str(chat_id),
                    "caption": "🏎️ *Monaco Grand Prix — Ready to post*",
                    "parse_mode": "Markdown",
                },
                files={"photo": ("MONACO_GP_DEAL.jpg", branded, "image/jpeg")},
            )

    await send_message(chat_id=chat_id, text=f"📋 *Caption:*\n\n{caption}")
    print("\nMonaco GP sent to Telegram!")


if __name__ == "__main__":
    asyncio.run(monaco_deal())
