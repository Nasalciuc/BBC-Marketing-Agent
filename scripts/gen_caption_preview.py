"""Generează și afișează caption-uri text pentru ultimul deal (Monaco GP)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

event_data = {
    "name": "Monaco Grand Prix",
    "event_name": "Monaco Grand Prix",
    "city": "Monte Carlo, Monaco",
    "dates_start": "2026-06-05",
    "dates_end": "2026-06-07",
    "category": "motorsport",
    "premium_score": 9.5,
    "routes": [{"from": "JFK", "to": "NCE"}],
    "from_iata": "JFK",
    "to_iata": "NCE",
    "price": "$2,069",
    "sales_hook": "Grid-side terrace. Champagne in hand. Your move.",
    "campaign_id": "2026-W23-001",
}

from prompts.system_prompts import (
    CAPTION_REWRITE_PROMPT,
    format_telegram_preview,
    format_whatsapp_caption,
    format_whatsapp_group,
)
from services.pricing_engine import format_route_display

event_data["route_str"] = format_route_display("JFK", "NCE")


def section(title: str, text: str) -> None:
    print("=" * 55)
    print(f"  {title}")
    print("=" * 55)
    print(text)
    print(f"\n  Lungime: {len(text)} caractere")
    print("=" * 55)
    print()


async def main() -> None:
    wa = format_whatsapp_caption(event_data)
    section("WHATSAPP CAPTION (broadcast clienti)", wa)

    wg = format_whatsapp_group(event_data)
    section("WHATSAPP GROUP (grup VIP)", wg)

    tg = format_telegram_preview(event_data)
    section("TELEGRAM PREVIEW (aprobare interna)", tg)

    from config import settings

    if not settings.gemini_api_key:
        print("[Gemini] GEMINI_API_KEY lipsa — doar template-uri statice mai sus.")
        Path("output/caption_latest.txt").write_text(wa, encoding="utf-8")
        print("Salvat fallback: output/caption_latest.txt")
        return

    prompt = CAPTION_REWRITE_PROMPT.format(
        event_name=event_data["name"],
        city=event_data["city"],
        dates=f"{event_data['dates_start']} — {event_data['dates_end']}",
        from_iata="JFK",
        to_iata="NCE",
        price=event_data["price"],
        original_caption=wa,
    )

    print("Trimit la Gemini (rewrite copywriter)...")
    try:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        r = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
        )
        text = (r.text or "").strip()
        section("GEMINI REWRITE (copywriter)", text)
        Path("output/caption_latest.txt").write_text(text, encoding="utf-8")
        print("Salvat: output/caption_latest.txt")
    except Exception as e:
        print(f"Eroare Gemini: {e}")
        Path("output/caption_latest.txt").write_text(wa, encoding="utf-8")
        print("Salvat fallback: output/caption_latest.txt")


if __name__ == "__main__":
    asyncio.run(main())
