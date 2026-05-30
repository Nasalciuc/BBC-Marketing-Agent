"""Generate default BBC test background if missing."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent.parent
OUT = ROOT / "assets" / "defaults" / "default_background.jpg"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        print(f"Already exists: {OUT}")
        return

    w, h = 1600, 900
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(11 + (201 - 11) * t)
        g = int(24 + (168 - 24) * t)
        b = int(41 + (220 - 41) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # subtle cloud-like shapes
    for cx, cy, radius, alpha in [(400, 200, 180, 40), (1100, 350, 220, 35), (800, 600, 160, 30)]:
        for r in range(radius, 0, -4):
            c = min(255, 200 + alpha * (1 - r / radius))
            draw.ellipse((cx - r, cy - r // 2, cx + r, cy + r // 2), fill=(int(c), int(c), int(c + 10)))

    img.save(OUT, format="JPEG", quality=90, optimize=True)
    print(f"Created {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
