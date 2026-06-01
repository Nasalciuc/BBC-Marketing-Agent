"""Verifică ce font folosește Playwright cu add_style_tag (Inter.woff2)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from services.branding_engine import _inject_font_style_tag

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(
        """<!DOCTYPE html>
<html><head></head>
<body><div id="test" style="font-family: Inter, Arial, sans-serif; font-size: 40px; font-weight: 800;">Test Inter Bold</div></body></html>""",
        wait_until="domcontentloaded",
    )
    _inject_font_style_tag(page)
    page.wait_for_timeout(500)
    result = page.evaluate(
        """() => {
            const el = document.getElementById('test');
            const cs = window.getComputedStyle(el);
            const loaded = [];
            document.fonts.forEach(f => loaded.push(`${f.family} w${f.weight} ${f.status}`));
            return { fontFamily: cs.fontFamily, fontWeight: cs.fontWeight, loadedFonts: loaded };
        }"""
    )
    print("Computed fontFamily:", result["fontFamily"])
    print("Computed fontWeight:", result["fontWeight"])
    print("Loaded fonts:", result["loadedFonts"])
    browser.close()
