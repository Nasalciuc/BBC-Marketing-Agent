"""Generează imagini de test pentru verificare vizuală."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.branding_engine import generate_branded_image

output_dir = Path("output/branding_test")
output_dir.mkdir(parents=True, exist_ok=True)

variants = [
    {
        "event": "F1 Monaco Grand Prix",
        "route": "JFK → NCE",
        "price": "$2,069",
        "caption": "Fly business class to the glamour capital",
        "file": "deal_f1_monaco.jpg",
    },
    {
        "event": "Wimbledon Championships",
        "route": "JFK → LHR",
        "price": "$2,033",
        "caption": "Premium seats at the world's most prestigious tennis tournament",
        "file": "deal_wimbledon.jpg",
    },
    {
        "event": "Tokyo Fashion Week",
        "route": "LAX → NRT",
        "price": "$2,399",
        "caption": "Front row at Asia's premier fashion event",
        "file": "deal_tokyo.jpg",
    },
    {
        "event": "Art Basel Miami Beach",
        "route": "JFK → MIA",
        "price": "$812",
        "badge": "ART & CULTURE",
        "caption": "Where the art world meets the beach",
        "file": "deal_art_basel.jpg",
    },
    {
        "event": "Champions League Final",
        "route": "JFK → MXP",
        "price": "$2,180",
        "caption": "Be there for the biggest match in club football",
        "file": "deal_champions_league.jpg",
    },
]

for v in variants:
    img = generate_branded_image(
        event_name=v["event"],
        route=v["route"],
        price=v["price"],
        background_url_or_path="assets/defaults/default_background.jpg",
        badge_text=v.get("badge"),
        caption=v.get("caption", ""),
    )
    path = output_dir / v["file"]
    path.write_bytes(img)
    print(f"OK {v['file']}: {len(img):,} bytes")

print(f"\nAll images saved to {output_dir}/")
