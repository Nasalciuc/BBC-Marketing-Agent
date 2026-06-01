"""
TEST REAL PIPELINE: Gemini discovery → Gemini image gen → Playwright branding.
Rulează: python scripts/test_real_pipeline.py
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pipeline_test")

OUTPUT = Path("output/real_pipeline")
OUTPUT.mkdir(parents=True, exist_ok=True)


async def main():
    from config import settings

    if not settings.gemini_api_key:
        print("WARNING: GEMINI_API_KEY nu e setat — discovery/image gen vor folosi mock/fallback")
        print("Adauga in .env: GEMINI_API_KEY=your_key")
    else:
        print(f"OK Gemini key: {settings.gemini_api_key[:10]}...")

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        print("OK Playwright instalat")
    except ImportError:
        print("ERROR: Playwright lipseste! pip install playwright && playwright install chromium")
        return

    template = Path("templates/deal_landscape.html")
    if not template.exists():
        print(f"ERROR: Template lipseste: {template}")
        return
    print(f"OK Template: {template}")

    print("\n" + "=" * 60)
    print("PASUL A: GEMINI DISCOVERY")
    print("=" * 60)

    from services.gemini_client import discover_and_verify

    events = []
    if settings.gemini_api_key:
        events = await discover_and_verify(week_offset=1, max_events=3)

    if not events:
        print("Folosesc mock data (fara API key sau discovery gol).")
        events = [
            {
                "name": "Formula 1 Monaco Grand Prix",
                "city": "Monte Carlo, Monaco",
                "dates_start": "2026-06-05",
                "dates_end": "2026-06-07",
                "category": "motorsport",
                "premium_score": 9.5,
                "routes": [{"from": "JFK", "to": "NCE"}],
                "image_prompt": (
                    "Aerial view of Monaco harbor with luxury yachts at golden hour sunset, "
                    "Mediterranean turquoise water, cinematic photography, no text"
                ),
                "caption_draft": "Fly business class to the glamour capital.",
            }
        ]

    print(f"\nOK Found {len(events)} events:")
    for i, e in enumerate(events, 1):
        print(f"  {i}. {e.get('name')} ({e.get('city')})")
        print(f"     Score: {e.get('premium_score')}/10")
        print(f"     Routes: {e.get('routes')}")

    (OUTPUT / "discovered_events.json").write_text(
        json.dumps(events, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print("PASUL B: PRICING")
    print("=" * 60)

    from services.pricing_engine import calculate_price, format_price, format_route_display

    for event in events:
        routes = event.get("routes", [{}])
        r = routes[0] if routes else {}
        from_iata = r.get("from", "JFK")
        to_iata = r.get("to", "LHR")

        price = calculate_price(from_iata, to_iata, "round_trip", "business")
        event["price"] = format_price(price) if price else "$2,500"
        event["price_raw"] = price
        event["from_iata"] = from_iata
        event["to_iata"] = to_iata
        event["route_str"] = format_route_display(from_iata, to_iata)
        print(f"  {from_iata} -> {to_iata}: {event['price']}")

    print("\n" + "=" * 60)
    print("PASUL C: GEMINI IMAGE GEN")
    print("=" * 60)

    from services.gemini_client import generate_event_image
    from services.image_enhancer import remove_watermark_corner

    for i, event in enumerate(events):
        prompt = event.get(
            "image_prompt",
            f"Beautiful aerial view of {event.get('city', 'luxury destination')}, "
            f"golden hour, cinematic, no text",
        )
        print(f"\n  Generating: {event.get('name')}...")
        print(f"  Prompt: {prompt[:80]}...")

        bg_bytes = None
        if settings.gemini_api_key:
            bg_bytes = await generate_event_image(prompt)

        if bg_bytes:
            bg_bytes = remove_watermark_corner(bg_bytes)
            bg_path = OUTPUT / f"bg_{i + 1}.jpg"
            bg_path.write_bytes(bg_bytes)
            event["bg_path"] = str(bg_path)
            print(f"  OK Background: {bg_path} ({len(bg_bytes):,} bytes)")
        else:
            event["bg_path"] = "assets/defaults/default_background.jpg"
            print("  WARN Gemini image gen skipped/failed — default background")

    print("\n" + "=" * 60)
    print("PASUL D: PLAYWRIGHT BRANDING")
    print("=" * 60)

    from prompts.system_prompts import format_badge_text, get_urgency_text
    from services.branding_engine import generate_branded_image

    for i, event in enumerate(events):
        print(f"\n  Branding: {event.get('name')}...")
        badge = format_badge_text(event.get("name", "Premium Event"))
        hook = event.get("sales_hook") or event.get("caption_draft", "")
        urgency = get_urgency_text(event.get("name", ""), event.get("category", ""))

        branded = await asyncio.to_thread(
            generate_branded_image,
            event.get("name", "Premium Event"),
            event["route_str"],
            event["price"],
            event["bg_path"],
            "deal_landscape",
            badge,
            "",
            hook,
            urgency,
        )

        final_path = OUTPUT / f"deal_{i + 1}_{event['from_iata']}_{event['to_iata']}.jpg"
        final_path.write_bytes(branded)
        event["final_path"] = str(final_path)
        print(f"  OK Final: {final_path} ({len(branded):,} bytes)")

    print("\n" + "=" * 60)
    print("REZULTAT FINAL")
    print("=" * 60)
    print(f"\nFisiere in: {OUTPUT}/")
    for f in sorted(OUTPUT.glob("*")):
        print(f"   {f.name:45s} {f.stat().st_size:>10,} bytes")

    print("\nDeschide output/real_pipeline/deal_*.jpg pentru review vizual.")


if __name__ == "__main__":
    asyncio.run(main())
