"""
Betty LUX v3 — 5 postări FOTO+TEXT pentru businessman 45+, cu Getting There.
Format aprobat: imagine branded (stil London 👍) + caption Betty-style + access line.
Zero video. Zero fashion. Subiect vizibil clar. Butoane Approve/Reject (via step5).
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import anthropic  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from config import settings  # noqa: E402
from scripts.real_footage_pipeline import (  # noqa: E402
    OUT,
    _parse_json_array,
    step4_brand,
    step5_telegram,
)

TODAY = datetime.now(UTC).strftime("%B %d, %Y")


# ═══════════════════════════════════════
# STEP 1 — Gemini: subiecte 45+ CU access info
# ═══════════════════════════════════════

def step1_search_45plus(gemini: genai.Client) -> list[dict]:
    print(f"\n🔍 STEP 1 — Topics for businessmen 45+ ({TODAY})...\n")

    now = datetime.now(UTC)
    cutoff_past = now - timedelta(weeks=4)
    future_end = now + timedelta(weeks=6)

    prompt = f"""Today is {TODAY}. Search the web for CURRENT premium travel content
targeted at successful businessmen 45+ (executives, entrepreneurs).

STRICT DATE RULE: last 4 weeks ({cutoff_past.strftime("%B %d")} - {now.strftime("%B %d, %Y")})
OR upcoming in the next 6 weeks ({now.strftime("%B %d")} - {future_end.strftime("%B %d, %Y")}).
REJECT anything from 2024/2025. Include exact dates for every item.

Search these 5 themes (2 items each):

1. NEW BUSINESS/FIRST CLASS CABINS — official airline reveals
   (Qatar, Emirates, Singapore, ANA, Cathay, Lufthansa Allegris)

2. BUSINESS + PRESTIGE DESTINATIONS in season now
   (Dubai, Singapore, London, Lake Como, Monaco, Zurich)

3. PREMIUM SPORTS EVENTS next 6 weeks — what men 45+ watch
   (F1 races, golf majors, tennis Grand Slams, yacht shows)
   NO fashion weeks. NO art fairs.

4. NEW LUXURY HOTELS / AIRLINE LOUNGES opening
   (Four Seasons, Ritz-Carlton, Mandarin Oriental, airline flagship lounges)

5. PREMIUM TRAVEL EFFICIENCY & STATUS
   (new nonstop routes, business class demand, airport fast-track news)

For each item:
{{"type":"cabin|destination|event|hotel|trend",
"name":"...","city":"...","dates":"exact dates",
"details":"3-4 specific facts with numbers",
"visual":"the ICONIC recognizable visual of this subject — what a stranger would instantly recognize. Be SPECIFIC.",
"access":{{"gateway_airport":"full airport name","iata":"3-letter code","us_hubs_nonstop":["JFK"],"flight_time":"about X hours","transfer":"X-minute drive/train to [destination]"}}}}

ACCESS INFO RULES (search the web for each):
- For every destination/event/hotel item, SEARCH for: the main gateway
  airport, which US hubs have NONSTOP service there, approximate flight
  time from the East Coast, and ground transfer time to the exact spot.
- Only include "us_hubs_nonstop" entries you actually verified via search.
- Use approximate times ("about 8 hours", "45-minute drive").
- If you cannot verify access info for an item, set "access": null.
- For cabin reveals and trends (no physical destination), set "access": null.

Return JSON array, 8-10 items. REAL facts only. No markdown fences."""

    resp = gemini.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    findings = _parse_json_array(resp.text or "")

    filtered: list[dict] = []
    for it in findings:
        dates = str(it.get("dates") or "")
        if any(y in dates for y in ("2022", "2023", "2024", "2025")) and "2026" not in dates:
            print(f"  ❌ STALE: {it.get('name')} ({dates})")
            continue
        filtered.append(it)
    findings = filtered or findings

    for i, f in enumerate(findings, 1):
        acc = "🛬" if f.get("access") else "—"
        print(f"  {i}. [{f.get('type', '?'):11s}] {acc} {f.get('name', '?')}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "step1_findings.json").write_text(
        json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return findings


# ═══════════════════════════════════════
# STEP 2 — Claude: 5 postări, stil Betty + Getting There
# ═══════════════════════════════════════

def step2_select_45plus(
    claude_client: anthropic.Anthropic, findings: list[dict]
) -> list[dict]:
    print("\n🧠 STEP 2 — Claude: 5 posts, Betty style + Getting There...\n")

    brand_context = ""
    try:
        from prompts.brand_dna import BBC_BRAND_DNA, BBC_CONTENT_SELECTION_CONTEXT

        brand_context = BBC_BRAND_DNA + "\n\n" + BBC_CONTENT_SELECTION_CONTEXT
    except Exception:
        pass

    betty_style = """
THE CMO'S TASTE (Betty) — from her REAL verdicts:
✅ APPROVED: Qatar QSuite (premium cabin close-ups), Lake Como villas
   ("A private terrace. Still water."), London deal (👍 — iconic Big Ben
   golden hour + price on image)
❌ REJECTED: F1 crash footage, vlogger content, fashion/couture posts
   (wrong audience — our buyers are businessmen 45+), unclear visuals
   (a resort post showing desert ruins — nobody understood it)

HER CAPTION FORMULA:
- Sensory opening: "A private terrace. Still water."
- Specific facts: named places, real numbers, exact dates
- Quiet confident close: "This is the trip you remember forever."
- Soft CTA with business class: "Let us find your seat. The rest is pure Italy."
"""

    resp = claude_client.messages.create(
        model=getattr(settings, "anthropic_model", "claude-sonnet-4-6"),
        max_tokens=4500,
        system=brand_context + "\n\n" + betty_style,
        messages=[
            {
                "role": "user",
                "content": f"""Gemini found:

{json.dumps(findings, ensure_ascii=False)}

Select exactly 5 posts a businessman 45+ would stop scrolling for.
Mix: cabin + destination + sports event + hotel/lounge + one more.
ZERO fashion. ZERO ambiguous subjects.

For each, write:

{{"index":0,
"headline":"Betty-style whisper, max 8 words, speaks to a 45+ executive",
"caption":"Betty formula: sensory opening. For EVENT posts: right after the opening, ONE context sentence that tells a non-follower WHAT it is, WHEN, and WHY it matters (use the facts from 'details' — exact dates, one factual superlative). Anchor insider terms: 'the Claret Jug — golf's oldest trophy', never naked shorthand. Specific facts (dates, names, numbers). If the item has access data (access != null), include ONE Getting There line AFTER the body, BEFORE the close, in this exact style: '✈️ Getting there: Nonstop JFK → Milan Malpensa (about 8 hours). Lake Como is a 45-minute drive from landing.' — using ONLY the access facts provided (approximate wording; if access is null or incomplete, OMIT the line entirely — never invent airports or times). Where natural, frame flight time as the product: 'Eight hours in a lie-flat suite. Land rested.' Then quiet close. Soft business-class CTA. End with:\\n\\nbuybusinessclass.com\\n☎️ +1 888-322-7999 📩 deals@buybusinessclass.com",
"image_prompt":"CRITICAL — 60-90 words. The image MUST clearly show THE SUBJECT so a stranger instantly knows what we sell: cabin post → the seat/suite interior with premium materials; destination → the ICONIC recognizable view (Big Ben, Dubai skyline, Como villas on the water); event → the recognizable venue (circuit, centre court, harbor with yachts); hotel/lounge → the interior (pool, bar, seating). Golden hour or warm premium lighting. Cinematic 16:9. Professional photography. MUST END WITH: No text, no logos, no watermarks, no words, no recognizable faces.",
"post_type":"deal|news"}}

Return JSON array, exactly 5. index = 0-based position in findings. No markdown.""",
            }
        ],
    )

    picks = _parse_json_array(resp.content[0].text)
    selected: list[dict] = []
    for p in picks:
        item = findings[p["index"]].copy()
        item["headline"] = p["headline"]
        item["caption"] = p["caption"]
        item["image_prompt"] = p.get("image_prompt", "")
        item["post_type"] = p.get("post_type", "news")

        # image_prompt e sursa pentru step4 (care citește item["visual"])
        if item["image_prompt"]:
            item["visual"] = item["image_prompt"]

        # O sursă de adevăr pentru IATA: access.iata alimentează pricing-ul
        access = item.get("access") or {}
        if isinstance(access, dict) and access.get("iata"):
            item["pricing_iata"] = access["iata"].strip().upper()

        # FOTO-ONLY: golește orice câmp de footage → step4 merge pe AI image
        item.pop("best_frame", None)
        item.pop("frames", None)
        item.pop("video_clip", None)
        selected.append(item)
        print(f"  ✅ {item['headline']}")

    (OUT / "step2_selected.json").write_text(
        json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return selected


# ═══════════════════════════════════════
# MAIN — FOTO + TEXT, cu butoane (moștenite din step5)
# ═══════════════════════════════════════

async def main() -> None:
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY not set")
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    print("═" * 55)
    print("  BETTY LUX v3 — 5 posts: 45+, Getting There, Approve buttons")
    print("═" * 55)

    OUT.mkdir(parents=True, exist_ok=True)

    gemini = genai.Client(api_key=settings.gemini_api_key)
    claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    findings = step1_search_45plus(gemini)
    selected = step2_select_45plus(claude_client, findings)

    # SKIP video complet — step4 generează AI images din image_prompt
    await step4_brand(selected)
    await step5_telegram(selected)  # ← salvează + trimite CU butoane

    print(f"\n{'═' * 55}")
    print("  ✅ DONE — 5 posts sent for approval")
    print(f"{'═' * 55}\n")
    for i, s in enumerate(selected, 1):
        img = "🖼️" if s.get("image") else "❌"
        acc = "🛬" if "Getting there" in s.get("caption", "") else "—"
        print(f"  {i}. {img} {acc} {s['headline']}")


if __name__ == "__main__":
    asyncio.run(main())
