"""Manual test: Gemini event discovery. Run: python scripts/test_discovery_manual.py"""
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from config import settings
from services.gemini_client import discover_events, verify_event


async def main() -> None:
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY not set in .env — add key from https://aistudio.google.com/apikey")
        return

    print("\n" + "=" * 60)
    print("TEST: Gemini Event Discovery")
    print("=" * 60)

    events = await discover_events(week_offset=1)
    if not events:
        print("No events found")
        return

    print(f"\nFound {len(events)} events:\n")
    for i, event in enumerate(events, 1):
        print(f"--- Event {i} ---")
        print(f"  Name:     {event.get('name')}")
        print(f"  City:     {event.get('city')}")
        print(f"  Dates:    {event.get('dates_start')} — {event.get('dates_end')}")
        print(f"  Category: {event.get('category')}")
        print(f"  Score:    {event.get('premium_score')}/10")
        print(f"  Routes:   {event.get('routes')}")
        print(f"  Img prompt: {str(event.get('image_prompt', ''))[:80]}...")
        print(f"  Caption:  {str(event.get('caption', ''))[:100]}...")
        print()

    print(f"Verifying '{events[0].get('name')}'...")
    ok = await verify_event(events[0])
    print(f"  Result: {'Confirmed' if ok else 'Not confirmed'}")

    out = ROOT / "output" / "discovered_events.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    asyncio.run(main())
