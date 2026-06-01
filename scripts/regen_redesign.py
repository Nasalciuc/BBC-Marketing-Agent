"""Regenerate F1 deal with redesigned v2 template."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.branding_engine import generate_branded_image

BG_CANDIDATES = [
    "output/f1_monaco_bg.png",
    "assets/backgrounds/f1_monaco_test.png",
    "assets/defaults/default_background.jpg",
]

bg = next((p for p in BG_CANDIDATES if Path(p).exists()), BG_CANDIDATES[-1])

img = generate_branded_image(
    event_name="Monaco Grand Prix",
    route="JFK → Nice",
    price="$2,069",
    background_url_or_path=bg,
    badge_text="MONACO GRAND PRIX",
    headline="Fly business to Nice",
    route_info="JFK → NCE · Business class",
    urgency="Limited Grand Prix season fares",
    cta="Check seats now",
)

out = Path("output/deal_f1_REDESIGN.jpg")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(img)
print(f"DONE → {out} ({len(img):,} bytes)")
