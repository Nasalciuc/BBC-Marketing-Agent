"""
5 photo+text posts for this week — businessman 45+, Betty style.
Fresh topics from Gemini search. All content fixes applied.
Photo (AI, subject-clear) + caption + Getting There + buttons. No video.
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
from scripts.real_footage_pipeline import OUT, _parse_json_array, step4_brand  # noqa: E402
from services.campaign_store import save_campaign_local  # noqa: E402
from services.supabase_client import save_campaign, update_review_tracking, upload_image  # noqa: E402
from services.telegram_client import send_approval_request, send_message  # noqa: E402

TODAY = datetime.now(UTC).strftime("%B %d, %Y")
WEEK = datetime.now(UTC).strftime("%Y-W%W")
MODEL = getattr(settings, "anthropic_model", None) or "claude-sonnet-4-6"

OUT.mkdir(parents=True, exist_ok=True)

CONTACT = "buybusinessclass.com\n☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"

brand_dna = ""
try:
    from prompts.brand_dna import BBC_BRAND_DNA, BBC_CONTENT_SELECTION_CONTEXT

    brand_dna = BBC_BRAND_DNA + "\n\n" + BBC_CONTENT_SELECTION_CONTEXT
except Exception:
    pass

gemini = genai.Client(api_key=settings.gemini_api_key)
claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)


# ═══════════════════════════════════════
# STEP 1 — Gemini caută subiecte proaspete
# ═══════════════════════════════════════

def step1_search() -> list[dict]:
    print(f"\n🔍 STEP 1 — Fresh topics for 45+ ({TODAY})...\n")

    now = datetime.now(UTC)
    cutoff_past = now - timedelta(weeks=4)
    future_end = now + timedelta(weeks=6)

    prompt = f"""Today is {TODAY}. Search the web for CURRENT premium travel content
for businessmen 45+ (executives, entrepreneurs).

STRICT DATE RULE: last 4 weeks ({cutoff_past.strftime("%B %d")} - {now.strftime("%B %d, %Y")})
OR upcoming next 6 weeks ({now.strftime("%B %d")} - {future_end.strftime("%B %d, %Y")}).
REJECT 2024/2025. Include exact dates for every item.

5 themes (2 items each):
1. NEW BUSINESS/FIRST CLASS CABINS — official airline reveals
2. BUSINESS + PRESTIGE DESTINATIONS in season (Dubai, Singapore, Como, Monaco, Zurich)
3. PREMIUM SPORTS EVENTS next 6 weeks (F1, golf majors, tennis, yacht shows) — NO fashion
4. NEW LUXURY HOTELS / AIRLINE LOUNGES opening
5. PREMIUM TRAVEL EFFICIENCY (new nonstop routes, business class demand)

Per item:
{{"type":"cabin|destination|event|hotel|trend","name":"...","city":"...","dates":"exact dates",
"details":"3-4 specific facts with numbers (for events: what/when/why it matters)",
"visual":"the ICONIC recognizable visual — what a stranger instantly recognizes. SPECIFIC.",
"access":{{"gateway_airport":"...","iata":"XXX","us_hubs_nonstop":["JFK"],
"flight_time":"about X hours","transfer":"X-min drive to..."}}}}

ACCESS: search for real gateway/nonstop hubs/flight time/transfer. Approximate wording.
Set access:null for cabin/trend. Return JSON array, 8-10 items. REAL facts only. No markdown fences."""

    # NOTE: do NOT set response_mime_type with google_search tools (causes 400)
    resp = gemini.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    findings = _parse_json_array(resp.text or "")

    findings = [
        it
        for it in findings
        if not (
            any(y in str(it.get("dates", "")) for y in ("2022", "2023", "2024", "2025"))
            and "2026" not in str(it.get("dates", ""))
        )
    ] or findings

    for i, f in enumerate(findings, 1):
        acc = "🛬" if f.get("access") else "—"
        print(f"  {i}. [{f.get('type', '?'):11s}] {acc} {f.get('name', '?')}")
    (OUT / "step1_findings.json").write_text(
        json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return findings


# ═══════════════════════════════════════
# STEP 2 — Claude selectează 5 (NAME-match, nu index)
# ═══════════════════════════════════════

def step2_select(findings: list[dict]) -> list[dict]:
    print(f"\n🧠 STEP 2 — Claude selecting 5 (Betty style)...\n")

    betty = """
THE CMO'S TASTE (Betty) — from her REAL verdicts:
✅ APPROVED: Qatar QSuite, Lake Como villas ("A private terrace. Still water."),
   Dubai/Wimbledon/Spa/Birkdale (iconic subject + 3-beat headline + on-image price)
❌ REJECTED: crashes, vloggers, fashion (wrong audience), unclear visuals
   (resort showing desert ruins, lounge showing an ad — nobody understood)

WINNING HEADLINE PATTERNS (replicate):
- 3-beat staccato: "Wimbledon. Centre Court. Your seat is ready."
- Double meaning with product: "your seat" = grandstand + lie-flat
- Context anchored in headline: "Golf's oldest trophy"
- Insider-confident tone: "Finally."

CAPTION FORMULA:
- Sensory opening: "A private terrace. Still water."
- EVENT posts: one context sentence (what/when/why — anchor insider terms:
  'the Claret Jug — golf's oldest trophy', never naked shorthand)
- Specific facts, exact dates
- Getting There line if access exists (approximate; omit if null)
- Quiet close + soft business-class CTA
"""

    resp = claude.messages.create(
        model=MODEL,
        max_tokens=4500,
        system=brand_dna + "\n\n" + betty,
        messages=[
            {
                "role": "user",
                "content": f"""Gemini found:

{json.dumps(findings, ensure_ascii=False)}

Select exactly 5 a businessman 45+ stops scrolling for.
Mix: cabin + destination + sports event + hotel/lounge + one more.
ZERO fashion. ZERO ambiguous subjects.

Per pick:
{{"name":"EXACT name from input (I match by name, not position)",
"headline":"3-beat Betty-style, max 8 words, for a 45+ executive",
"caption":"Betty formula (sensory → [event: context sentence] → facts → [Getting There if access] → quiet close → soft CTA). End with:\\n\\n{CONTACT}",
"image_prompt":"60-90 words. Image MUST clearly show THE SUBJECT (stranger test): cabin→seat/suite interior; destination→ICONIC view (Big Ben, Dubai skyline, Como villas on water); event→recognizable venue (circuit, court, harbor); hotel→interior (pool, bar). Golden hour, cinematic 16:9, professional. END WITH: No text, no logos, no watermarks, no words, no recognizable faces.",
"post_type":"deal|news"}}

ON-IMAGE FACT RULE: any superlative in the headline must be TRUE and from the data
(Spa = LONGEST circuit not fastest). Return JSON array, exactly 5.""",
            }
        ],
    )
    picks = _parse_json_array(resp.content[0].text)

    selected: list[dict] = []
    for p in picks:
        item = next((f for f in findings if f.get("name") == p.get("name")), None)
        if not item:
            print(f"  ⚠️ No name match for '{p.get('name', '?')}' — skipping")
            continue
        item = item.copy()
        item["headline"] = p["headline"]
        item["caption"] = p["caption"]
        item["image_prompt"] = p.get("image_prompt", "")
        item["visual"] = p.get("image_prompt", item.get("visual", ""))
        item["post_type"] = p.get("post_type", "news")
        access = item.get("access") or {}
        if isinstance(access, dict) and access.get("iata"):
            item["pricing_iata"] = access["iata"].strip().upper()
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
# STEP 3 — Sanity gate (anti-swap, anti-nonsense)
# ═══════════════════════════════════════

def step3_sanity(selected: list[dict]) -> None:
    print("\n🛡️ STEP 3 — Sanity check...\n")
    for i, item in enumerate(selected, 1):
        hl = item.get("headline", "")
        city = item.get("city", "")
        try:
            r = claude.messages.create(
                model=MODEL,
                max_tokens=30,
                messages=[
                    {
                        "role": "user",
                        "content": f"""Headline: "{hl}"
City label: "{city}" | Type: {item.get('type')}
Do they refer to the SAME place/subject? (e.g. headline about Como with city Southport = mismatch)
Reply exactly: OK or MISMATCH""",
                    }
                ],
            )
            if "MISMATCH" in r.content[0].text.upper():
                print(f"  {i}. ⚠️ MISMATCH: '{hl[:30]}' vs city '{city}' — check step2_selected.json")
            else:
                print(f"  {i}. ✅ {hl[:40]}")
        except Exception:
            print(f"  {i}. (check skipped)")


# ═══════════════════════════════════════
# STEP 4 — Branding (AI images) + STEP 5 Telegram
# ═══════════════════════════════════════

async def step5_send(selected: list[dict]) -> None:
    print("\n📱 STEP 5 — Telegram: photo + text + buttons...\n")
    cid = settings.telegram_chat_id
    if not cid:
        print("⬜ No TELEGRAM_CHAT_ID")
        return

    await send_message(
        chat_id=cid,
        text=(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📰 *This Week — 5 Posts*\n"
            "_Review each below_ 👇\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        ),
    )

    sent = 0
    for i, item in enumerate(selected, 1):
        await asyncio.sleep(1)
        img = item.get("image", str(OUT / f"post_{i}.jpg"))
        caption = item.get("caption", "")
        if "888-322-7999" not in caption:
            caption += f"\n\n{CONTACT}"
        (OUT / f"caption_{i}.txt").write_text(caption, encoding="utf-8")

        cid_str = f"{WEEK}-PH-{i:03d}"
        image_url = None
        if Path(img).exists():
            try:
                image_url = await upload_image(
                    Path(img).read_bytes(), f"deals/{cid_str}/landscape.jpg"
                )
            except Exception as exc:
                print(f"  ⚠️ upload {i}: {exc}")

        campaign = {
            "campaign_id": cid_str,
            "name": item.get("name", ""),
            "event_name": item.get("headline", ""),
            "city": item.get("city", ""),
            "category": item.get("type", "news"),
            "route_str": item.get("headline", ""),
            "image_url": image_url,
            "caption": caption,
            "whatsapp_caption": caption,
            "event_context": item.get("details", ""),
            "status": "draft",
            "media_type": "photo",
            "headline": item.get("headline", ""),
            "type": item.get("type", ""),
            "index": i,
        }
        save_campaign_local(campaign)
        await save_campaign(campaign)

        res = await send_approval_request(campaign, chat_id=cid)
        if res and res.get("message_id"):
            await update_review_tracking(cid_str, res["chat_id"], res["message_id"])
            sent += 1
            print(f"  ✅ {cid_str}: {item.get('headline', '')[:40]}")
        await asyncio.sleep(1)

    await send_message(
        chat_id=cid,
        text=(
            f"━━━━━━━━━━━━━━━━━━━━━\n✅ *{sent}/5 posts ready*\n\n"
            "Tap *Approve* on the keepers.\n"
            "*Edit caption* → write what to change.\n"
            "Approved posts broadcast Monday 10:00."
        ),
    )
    print(f"\n✅ {sent}/5 sent!")


async def main() -> None:
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY not set")
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    print("═" * 55)
    print(f"  5 PHOTO POSTS — {TODAY}")
    print("═" * 55)

    findings = step1_search()
    selected = step2_select(findings)[:5]
    if len(selected) < 5:
        print(f"  ⚠️ Only {len(selected)} posts selected (need 5)")

    step3_sanity(selected)
    await step4_brand(selected)
    await step5_send(selected)

    print(f"\n{'═' * 55}\n  SUMMARY\n{'═' * 55}\n")
    for i, s in enumerate(selected, 1):
        img = "🖼️" if s.get("image") else "❌"
        acc = "🛬" if "Getting there" in s.get("caption", "") else "—"
        print(f"  {i}. {img} {acc} {s.get('headline', '')[:48]}")


if __name__ == "__main__":
    asyncio.run(main())
