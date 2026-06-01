"""Regenerate banner — setări SALES_V2 (ca în screenshot)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prompts.system_prompts import format_badge_text, get_sales_hook, get_urgency_text
from services.branding_engine import generate_branded_image
from services.pricing_engine import format_route_display

bg = Path("output/f1_monaco_bg.png")
if not bg.exists():
    bg = Path("assets/backgrounds/f1_monaco_test.png")

img = generate_branded_image(
    event_name="Monaco Grand Prix",
    route=format_route_display("JFK", "NCE"),
    price="$2,069",
    background_url_or_path=str(bg),
    badge_text=format_badge_text("Monaco Grand Prix"),
    hook_text=get_sales_hook("motorsport"),
    urgency_text=get_urgency_text("Monaco Grand Prix"),
    cta_text="Check available seats → buybusinessclass.com",
)

out = Path("output/deal_f1_SALES_V2.jpg")
out.write_bytes(img)
print(f"DONE -> {out} ({len(img):,} bytes)")
