"""
Trimite lui Gemini: logo BBC + fundal F1 + instrucțiuni.
Gemini combină totul într-un banner branded premium.

Rulează: python scripts/test_gemini_branded_banner.py
"""
import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai
from google.genai import types

from config import settings
from services.gemini_client import IMAGEN_MODEL, _extract_image_bytes


def _extract_part_image(part: types.Part) -> bytes | None:
    inline = getattr(part, "inline_data", None)
    if not inline or not inline.mime_type or not inline.mime_type.startswith("image/"):
        return None
    data = inline.data
    if isinstance(data, str):
        return base64.b64decode(data)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return None


async def main() -> None:
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY lipseste!")
        return

    logo_candidates = [
        Path("assets/logos/bbc_logo_transparent.png"),
        Path("assets/logos/bbc_logo_horizontal.png"),
        Path("assets/logos/LOGO BBC 1200X300.png"),
        Path("assets/logos/LOGO_BBC_1200X300.png"),
    ]
    logo_path = next((p for p in logo_candidates if p.exists()), None)
    if not logo_path:
        print("Logo BBC negasit in assets/logos/!")
        return
    print(f"Logo: {logo_path}")

    bg_candidates = [
        Path("output/f1_monaco_bg.png"),
        Path("assets/backgrounds/f1_monaco_test.png"),
        Path("output/f1_monaco_cars.jpg"),
        Path("assets/defaults/default_background.jpg"),
    ]
    bg_path = next((p for p in bg_candidates if p.exists()), None)
    if not bg_path:
        print("Fundal negasit!")
        return
    print(f"Background: {bg_path}")

    logo_bytes = logo_path.read_bytes()
    bg_bytes = bg_path.read_bytes()
    logo_mime = "image/png" if logo_path.suffix.lower() == ".png" else "image/jpeg"
    bg_mime = "image/png" if bg_path.suffix.lower() == ".png" else "image/jpeg"

    print(f"   Logo: {len(logo_bytes):,} bytes")
    print(f"   Background: {len(bg_bytes):,} bytes")

    prompt_parts = [
        types.Part.from_bytes(data=logo_bytes, mime_type=logo_mime),
        types.Part.from_text(
            text="This is the company logo (BuyBusinessClass.com). Use it exactly as provided."
        ),
        types.Part.from_bytes(data=bg_bytes, mime_type=bg_mime),
        types.Part.from_text(
            text="""This is the background photo. Use it as the right side of the banner.

NOW CREATE A PREMIUM MARKETING BANNER combining these two images:

DIMENSIONS: 1200 x 628 pixels, landscape orientation.

COMPOSITION:
- RIGHT 60%: Use the Formula 1 background photo, vivid and dramatic
- LEFT 40%: Dark navy overlay (#0A1628) at 95% opacity for text area
- Smooth gradient transition between dark left and photo right
- Place the BuyBusinessClass logo in the top-left corner, white/inverted,
  small and elegant (about 40px height equivalent)

TEXT ON THE LEFT SIDE (all left-aligned, generous spacing):

1. Below logo, leave space then:
   "MONACO GRAND PRIX SALE"
   — champagne gold color #C4A44A, small uppercase, letter-spacing wide

2. Main headline:
   "Business Class"
   "to Nice"
   — white, large bold text (~38px), clean sans-serif

3. Below headline:
   "JFK → NCE · Business class"
   — white at 55% opacity, small (~14px)

4. Price section:
   "Round-trip flights from"
   — gold #C4A44A at 75% opacity, small (~14px)
   "$2,069*"
   — champagne gold #C4A44A, LARGE bold (~48px)
   THE PRICE MUST BE EXACTLY "$2,069" WITH DOLLAR SIGN AND COMMA

5. Below price:
   "Limited Grand Prix season fares"
   — white at 60% opacity, small (~13px)

6. CTA button:
   [ Check available seats → ]
   — subtle outlined button, gold border at 35% opacity,
     gold text, rounded corners, professional

7. Very bottom:
   "buybusinessclass.com"
   — white at 35% opacity, tiny

8. Thin gold accent line (3px) across the full bottom edge

STYLE:
- Premium luxury feel like Condé Nast Traveler advertisement
- Navy #0A1628 + champagne gold #C4A44A + white
- Clean modern sans-serif typography (Inter/Helvetica style)
- Generous whitespace between ALL elements
- Text shadows for depth
- Must look EXPENSIVE and EXCLUSIVE, not cheap or discount
- The logo must be recognizable and properly placed
- NO decorative sparkles, stars, or unnecessary elements
- NO distortion of the logo — use it exactly as provided

CRITICAL RULES:
- ALL text MUST be perfectly spelled and readable
- "$2,069" must be EXACT — dollar sign, comma, all digits
- The arrow → in CTA must be present
- Logo must be WHITE version (invert if needed) on dark background
- Background photo must be clearly visible on the right side
"""
        ),
    ]

    banner_prompt = """Create a premium 1200x628 marketing banner for BuyBusinessClass.com:
Monaco Grand Prix sale, business class JFK to Nice, from $2,069 round-trip.
Dark navy left panel with gold/white text, F1 racing photo on the right.
Logo top-left, headline "Business Class to Nice", CTA "Check available seats →".
Luxury Condé Nast Traveler style. Price exactly $2,069."""

    client = genai.Client(api_key=settings.gemini_api_key)
    models = [
        "gemini-2.5-flash-image",
        "gemini-3.1-flash-image",
        IMAGEN_MODEL,
    ]

    for model in models:
        try:
            print(f"\nGenerating with {model}...")

            if model.startswith("imagen"):
                response = client.models.generate_images(
                    model=model,
                    prompt=banner_prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                    ),
                )
                img_bytes = None
                if response.generated_images:
                    img_bytes = response.generated_images[0].image.image_bytes
            else:
                response = client.models.generate_content(
                    model=model,
                    contents=types.Content(parts=prompt_parts, role="user"),
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        temperature=0.5,
                    ),
                )

                img_bytes = _extract_image_bytes(response)
                if not img_bytes and response.candidates:
                    for part in response.candidates[0].content.parts or []:
                        img_bytes = _extract_part_image(part)
                        if img_bytes:
                            break

            if img_bytes:
                out = Path("output/GEMINI_BRANDED_FINAL.jpg")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(img_bytes)
                print("\nSUCCESS!")
                print(f"   Model: {model}")
                print(f"   Size: {len(img_bytes):,} bytes")
                print(f"   Saved: {out}")
                print("\nCompare:")
                print("   output/GEMINI_BRANDED_FINAL.jpg  <- Gemini (logo+bg+text)")
                print("   output/FINAL_v3.jpg              <- HTML/HCTI")
                return

            print("   No image in response")
            if not model.startswith("imagen") and response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if getattr(part, "text", None):
                        print(f"   Gemini said: {part.text[:200]}")

        except Exception as e:
            print(f"   {model} failed: {e}")

    print("\nAll models failed")


if __name__ == "__main__":
    asyncio.run(main())
