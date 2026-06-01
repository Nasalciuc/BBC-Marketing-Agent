"""Generate FINAL_v3.jpg with layout-fixed template."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.branding_engine import generate_branded_image

bg = Path("output/f1_monaco_bg.png")
if not bg.exists():
    raise FileNotFoundError(f"Background missing: {bg}")

img = generate_branded_image(
    event_name="Monaco Grand Prix",
    route="JFK → NCE",
    price="$2,069",
    background_url_or_path=str(bg),
    badge_text="MONACO GRAND PRIX SALE",
    headline="Business Class to Nice",
    route_info="JFK → NCE · Business class",
    urgency="Limited Grand Prix season fares",
    cta="Check available seats",
)

out = Path("output/FINAL_v3.jpg")
out.write_bytes(img)
print(f"DONE: {len(img):,} bytes → {out}")
