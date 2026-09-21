"""PAS 1: one vertical branded JPEG — no LLM, no DB. Avoids PowerShell $ expansion."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from services.branding_engine import generate_branded_image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
OUT.mkdir(parents=True, exist_ok=True)
bg = ROOT / "assets" / "curated" / "week_2026_09_21" / "02_voiles_saint_tropez.jpg"

img_bytes = generate_branded_image(
    event_name="SAINT-TROPEZ",
    route="Business Class To NICE",
    price="from $2,069",
    background_url_or_path=str(bg),
    headline="Saint-Tropez. Nine days. Only sail.",
    urgency="Limited Nice season fares",
    cta="Check available seats",
    cta_url="buybusinessclass.com",
    orientation="vertical",
    badge_text="SAINT-TROPEZ",
    subtitle="Business Class to Nice",
)
path = OUT / "test_vertical.jpg"
path.write_bytes(img_bytes)
size = Image.open(path).size
print(f"{path} {len(img_bytes) // 1024}KB {size}")
