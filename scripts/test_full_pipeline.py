"""Full pipeline test: discover → price → image → brand. Run: python scripts/test_full_pipeline.py"""
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from config import settings
from services.branding_engine import generate_branded_image
from services.gemini_client import discover_and_verify, generate_event_image
from services.pricing_engine import format_price, get_all_cabin_prices


async def main() -> None:
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY not set — pipeline requires Gemini for discovery")
        return

    print("\n" + "=" * 60)
    print("BBC MARKETING AGENT — FULL PIPELINE TEST")
    print("=" * 60)

    output_dir = ROOT / "output" / "pipeline_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\nStep 1: Discovering events...")
    events = await discover_and_verify(week_offset=1, max_events=3)
    if not events:
        print("No events found. Pipeline stopped.")
        return

    results: list[dict] = []

    for i, event in enumerate(events, 1):
        print(f"\n{'=' * 40}")
        print(f"Event {i}/{len(events)}: {event.get('name')}")
        print(f"{'=' * 40}")

        route = (event.get("routes") or [{}])[0]
        from_iata = route.get("from", "JFK")
        to_iata = route.get("to", "LHR")

        prices = get_all_cabin_prices(from_iata, to_iata, "round_trip")
        if prices:
            price_str = format_price(prices["business"])
            print(f"Price: {from_iata}->{to_iata} = {price_str} (business)")
        else:
            price_str = "Contact us"
            print(f"Price: {from_iata}->{to_iata} = N/A")

        event["price"] = price_str
        event["price_all"] = prices

        print("Generating background image...")
        image_prompt = event.get(
            "image_prompt",
            f"Beautiful aerial view of {event.get('city', 'city')}, cinematic photography",
        )
        bg_image = await generate_event_image(image_prompt)

        if bg_image:
            bg_path = output_dir / f"bg_{i}.jpg"
            bg_path.write_bytes(bg_image)
            print(f"  Background: {bg_path} ({len(bg_image):,} bytes)")
            background_source = str(bg_path)
        else:
            print("  Using default background (Gemini image gen failed)")
            background_source = str(ROOT / "assets" / "defaults" / "default_background.jpg")

        print("Applying BBC branding...")
        route_str = f"{from_iata} → {to_iata}"
        badge = event.get("category", "")
        badge_text = badge.replace("_", " ").title() if badge else None

        try:
            branded = generate_branded_image(
                event_name=event.get("name", "Premium Event"),
                route=route_str,
                price=price_str,
                background_url_or_path=background_source,
                badge_text=badge_text,
            )
            final_path = output_dir / f"deal_{i}_{from_iata}_{to_iata}.jpg"
            final_path.write_bytes(branded)
            print(f"  Final image: {final_path} ({len(branded):,} bytes)")
            event["image_path"] = str(final_path)
            event["image_size"] = len(branded)
        except Exception as exc:
            print(f"  Branding failed: {exc}")
            event["image_path"] = None

        results.append(event)

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE — SUMMARY")
    print("=" * 60)
    for result in results:
        route = (result.get("routes") or [{}])[0]
        ok = "OK" if result.get("image_path") else "FAIL"
        print(f"  [{ok}] {result.get('name')}")
        print(
            f"    {route.get('from', '?')}->{route.get('to', '?')} | "
            f"{result.get('price')} | {result.get('image_size', 0):,} bytes"
        )

    for result in results:
        result.pop("price_all", None)

    summary_path = output_dir / "pipeline_results.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults saved: {summary_path}")
    print(f"Images saved in: {output_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
