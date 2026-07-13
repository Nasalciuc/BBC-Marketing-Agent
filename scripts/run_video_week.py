"""
5 video+text posts for this week.
3 existing topics (ANA cabin, Royal Birkdale Open, Spa-Francorchamps) + 2 new.
Format: VIDEO clip (yt-dlp official) + caption + Approve/Reject buttons.
NO branded photo — video only.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
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
    _is_official_channel,
    _parse_json_array,
)
from services.supabase_client import save_campaign, update_review_tracking  # noqa: E402
from services.telegram_client import send_approval_request, send_message  # noqa: E402
from services.video_processor import brand_video, concat_videos, probe_video_stream, trim_video  # noqa: E402
from services.gemini_client import analyze_youtube_video  # noqa: E402
from services.youtube_client import download_clip, search_youtube  # noqa: E402

TODAY = datetime.now(UTC).strftime("%B %d, %Y")
WEEK = datetime.now(UTC).strftime("%Y-W%W")
MODEL = getattr(settings, "anthropic_model", None) or "claude-sonnet-4-6"

OUT.mkdir(parents=True, exist_ok=True)

CONTACT = "buybusinessclass.com\n☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"

brand_dna = ""
try:
    from prompts.brand_dna import BBC_BRAND_DNA

    brand_dna = BBC_BRAND_DNA[:3000]
except Exception:
    pass


# ═══════════════════════════════════════
# DEFINE ALL 5 TOPICS
# ═══════════════════════════════════════

EXISTING = [
    {
        "name": "ANA New Business Class Cabin",
        "headline": "ANA just redefined what business class means.",
        "type": "cabin",
        "city": "",
        "youtube_queries": [
            "ANA airlines new business class cabin official reveal 2026",
            "ANA All Nippon Airways business class suite official",
            "ANA business class cabin tour cinematic 4K",
        ],
    },
    {
        "name": "The 154th Open Championship at Royal Birkdale",
        "headline": "The Open. Royal Birkdale. Golf's oldest trophy.",
        "type": "event",
        "city": "Southport, England",
        "dates": "July 12-19, 2026",
        "access": {
            "gateway_airport": "Manchester Airport",
            "iata": "MAN",
            "us_hubs_nonstop": ["JFK", "EWR"],
            "flight_time": "about 7 hours",
            "transfer": "45-minute drive to Southport",
        },
        "youtube_queries": [
            "The Open Championship Royal Birkdale official promo 2026",
            "Royal Birkdale golf course cinematic aerial drone 4K",
            "The Open golf championship official film R&A",
        ],
    },
    {
        "name": "Belgian Grand Prix at Spa-Francorchamps",
        "headline": "Spa. The Ardennes. The longest circuit in F1.",
        "type": "event",
        "city": "Spa, Belgium",
        "dates": "July 17-19, 2026",
        "access": {
            "gateway_airport": "Brussels Airport",
            "iata": "BRU",
            "us_hubs_nonstop": ["JFK", "EWR"],
            "flight_time": "about 8 hours",
            "transfer": "1-hour drive to Spa-Francorchamps",
        },
        "youtube_queries": [
            "Belgian Grand Prix Spa-Francorchamps official F1 promo",
            "Spa-Francorchamps F1 VIP hospitality paddock experience",
            "Spa-Francorchamps circuit cinematic aerial drone 4K",
        ],
    },
]


# ═══════════════════════════════════════
# STEP 1 — Find 2 NEW topics (Gemini)
# ═══════════════════════════════════════

def step1_find_new_topics(gemini: genai.Client) -> list[dict]:
    print(f"\n🔍 STEP 1 — Finding 2 new topics ({TODAY})...\n")

    prompt = f"""Today is {TODAY}. Search the web for 2 CURRENT premium travel topics
for businessmen 45+ (executives, entrepreneurs). The topics must be DIFFERENT from:
- ANA business class cabin, Royal Birkdale Open, Spa-Francorchamps F1.

Find items where an OFFICIAL brand video likely exists on YouTube.
Prefer: new airline cabins/lounges, prestige destinations, major sports events.
NO fashion weeks. NO art fairs.

STRICT DATE RULE: last 4 weeks OR upcoming in the next 6 weeks. Include exact dates.
REJECT anything from 2024/2025.

For each:
{{"name":"...","headline":"Emotional max 8 words, 3-beat rhythm (Place. Detail. Hook.)",
"type":"cabin|destination|event|hotel",
"city":"...","dates":"exact dates",
"details":"3-4 specific facts",
"access":{{"gateway_airport":"...","iata":"XXX",
"us_hubs_nonstop":["JFK"],"flight_time":"about X hours",
"transfer":"X-minute drive to..."}},"youtube_queries":["official brand query","cinematic 4K query","backup query"]}}

Set access: null for cabin reveals. Return JSON array, exactly 2. No markdown fences."""

    # NOTE: do NOT set response_mime_type with google_search tools (causes 400)
    resp = gemini.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    new_items = _parse_json_array(resp.text or "")

    filtered = [
        it
        for it in new_items
        if not any(y in str(it.get("dates") or "") for y in ("2022", "2023", "2024", "2025"))
        or "2026" in str(it.get("dates") or "")
    ]
    new_items = filtered or new_items

    for it in new_items:
        print(f"  ✅ {it.get('headline', it.get('name', '?'))}")
    return new_items


# ═══════════════════════════════════════
# STEP 2 — Claude writes captions for ALL 5
# ═══════════════════════════════════════

def step2_write_captions(claude_client: anthropic.Anthropic, all_items: list[dict]) -> list[dict]:
    print("\n🧠 STEP 2 — Claude writing captions (Betty-style)...\n")

    resp = claude_client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=brand_dna,
        messages=[
            {
                "role": "user",
                "content": f"""Write WhatsApp captions for these 5 posts,
Betty (CMO) formula for businessmen 45+:

{json.dumps(all_items, ensure_ascii=False)}

Per post return:
{{"name":"exact name from input","caption":"Sensory opening. For EVENT posts: one context sentence (what/when/why — anchor insider terms: 'the Claret Jug — golf oldest trophy'). Specific facts. If access data exists, ONE Getting There line: '✈️ Getting there: Nonstop JFK → Brussels (about 8 hours). Spa-Francorchamps is a 1-hour drive.' Frame flight as the product where natural: 'Seven hours in a lie-flat suite. Land rested.' Quiet close. Soft CTA. End with:\\n\\n{CONTACT}"}}

RULES:
- CABIN posts: NO Getting There (no destination). Focus on the seat, the innovation.
- ON-IMAGE FACTS: the headline is ALREADY on the image. Your caption expands it.
  Spa is the LONGEST circuit (7km), not fastest. Birkdale is in SOUTHPORT not Bellagio.
- Max 250 words per caption. WhatsApp = phone screen.

Return JSON array, exactly 5. "name" must match input exactly. No markdown.""",
            }
        ],
    )
    captions = _parse_json_array(resp.content[0].text)

    for item in all_items:
        match = next((c for c in captions if c.get("name") == item.get("name")), None)
        if match:
            item["caption"] = match["caption"]
            print(f"  ✅ {item['name'][:40]}")
        else:
            print(f"  ⚠️ {item['name'][:40]} — no caption match, will generate later")

    return all_items


# ═══════════════════════════════════════
# STEP 3 — Download video for ALL 5 (official first)
# ═══════════════════════════════════════

def step3_download_videos(claude_client: anthropic.Anthropic, all_items: list[dict]) -> None:
    """Candidates → Gemini WATCHES each → Claude verdict on full description →
    download ONLY the winner, ONLY the clean segment, at 4K."""
    print("\n📹 STEP 3 — Gemini watches, Claude decides, targeted download...\n")

    verdict_context = (
        brand_dna
        + """
You judge videos by their FULL content description (Gemini watched them).
APPLY the VIDEO CONTENT REQUIREMENT: the client must SEE the premium product.
REJECT: analysis/educational content, split-screens, stats graphics,
feature demos without the product, heavy burned-in text, third-party logos."""
    )

    for i, item in enumerate(all_items, 1):
        queries = item.get("youtube_queries") or [item["name"] + " official promo luxury"]
        print(f"  {i}. {item.get('headline', item['name'])[:50]}")

        candidates: list[dict] = []
        for q in queries[:2]:
            vids = search_youtube(q, max_results=3)
            for v in vids:
                if v["url"] not in [c["url"] for c in candidates]:
                    candidates.append(v)
        candidates.sort(key=lambda v: 0 if _is_official_channel(v.get("uploader", "")) else 1)
        candidates = candidates[:3]

        if not candidates:
            print("     ⬜ No candidates")
            continue

        analyses: list[dict] = []
        for c in candidates:
            print(f"     👁️ Gemini watching: \"{c['title'][:45]}\" ({c.get('uploader', '?')})")
            a = analyze_youtube_video(c["url"])
            if a:
                analyses.append({"candidate": c, "analysis": a})
                print(f"        → {a.get('main_subject', '?')[:60]}")
            else:
                print("        ⚠️ analysis failed — skipping candidate")

        if not analyses:
            print("     ⬜ No analyzable videos — skipping video for this post")
            continue

        digest = "\n\n".join(
            f"CANDIDATE {j}:\n"
            f"Title: {x['candidate']['title']}\nUploader: {x['candidate'].get('uploader', '?')}\n"
            f"Analysis: {json.dumps(x['analysis'], ensure_ascii=False)}"
            for j, x in enumerate(analyses)
        )
        try:
            v = claude_client.messages.create(
                model=MODEL,
                max_tokens=200,
                system=verdict_context,
                messages=[
                    {
                        "role": "user",
                        "content": f"""Post: "{item.get('headline', item['name'])}" (type: {item.get('type', '')}).

Gemini watched these candidates:

{digest}

Pick the ONE where the client SEES the premium product, on-subject, clean segment available.
Reply EXACTLY: {{"pick": 0, "start_s": 12, "end_s": 22}} using the candidate's best_clean_segment,
or {{"pick": -1, "reason": "..."}} if none qualify.""",
                    }
                ],
            )
            vt = v.content[0].text.strip()
            if vt.startswith("```"):
                vt = vt.split("\n", 1)[1].rstrip("`").strip()
            verdict = json.loads(vt)
        except Exception as exc:
            print(f"     ⚠️ Verdict parse failed: {exc} — using first analysis")
            a0 = analyses[0]["analysis"].get("best_clean_segment") or {}
            verdict = {"pick": 0, "start_s": a0.get("start_s", 5), "end_s": a0.get("end_s", 15)}

        if verdict.get("pick", -1) < 0:
            print(f"     🚫 Claude: {verdict.get('reason', 'none qualify')} — no video for this post")
            continue

        chosen = analyses[min(verdict["pick"], len(analyses) - 1)]["candidate"]
        seg_start = max(0, int(verdict.get("start_s", 5)))
        seg_end = int(verdict.get("end_s", seg_start + 10))
        seg_len = max(6, min(12, seg_end - seg_start))
        print(f"     ✅ WINNER: \"{chosen['title'][:45]}\" · segment {seg_start}-{seg_start + seg_len}s")

        clip = download_clip(
            chosen["url"],
            str(OUT / f"vid_raw_{i}.mp4"),
            max_seconds=seg_start + seg_len + 2,
        )
        if not clip:
            print("     ⚠️ Download failed")
            continue

        trimmed = trim_video(clip, str(OUT / f"vid_clip_{i}.mp4"), start=seg_start, duration=seg_len)
        if not trimmed:
            continue

        branded = brand_video(
            trimmed,
            str(OUT / f"vid_final_{i}.mp4"),
            headline=item.get("headline", ""),
            footer="buybusinessclass.com",
        )
        if not branded:
            continue

        item["video_final"] = branded
        print(f"     ✅ Final: {Path(branded).stat().st_size // 1024}KB")

        if item.get("type") in ("event", "destination", "hotel"):
            cabin = ROOT / "assets" / "cabin_clips" / "qsuite_intro.mp4"
            if cabin.exists():
                dual = concat_videos(str(cabin), branded, str(OUT / f"vid_dual_{i}.mp4"))
                if dual:
                    item["video_final"] = dual
                    print(f"     ✅ DUAL (cabin+destination): {Path(dual).stat().st_size // 1024}KB")


# ═══════════════════════════════════════
# STEP 4 — Send ALL to Telegram: VIDEO + TEXT + BUTTONS
# ═══════════════════════════════════════

async def step4_send_telegram(all_items: list[dict]) -> None:
    print("\n📱 STEP 4 — Telegram: video + text + buttons...\n")

    import httpx

    from keyboards import review_keyboard

    cid = settings.telegram_chat_id
    if not cid:
        print("⬜ No TELEGRAM_CHAT_ID")
        return

    await send_message(
        chat_id=cid,
        text=(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📹 *This Week — 5 Video Posts*\n"
            "_Review each below_ 👇\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        ),
    )

    sent = 0
    for i, item in enumerate(all_items, 1):
        await asyncio.sleep(1)

        vid = item.get("video_final") or item.get("video_clip")
        caption = item.get("caption", "") or ""

        if "888-322-7999" not in caption:
            caption = (caption + f"\n\n{CONTACT}").strip() if caption else f"{item.get('headline', '')}\n\n{CONTACT}"

        campaign_id = f"{WEEK}-VID-{i:03d}"
        (OUT / f"caption_{i}.txt").write_text(caption, encoding="utf-8")

        campaign = {
            "campaign_id": campaign_id,
            "name": item.get("name", ""),
            "event_name": item.get("headline", ""),
            "city": item.get("city", ""),
            "category": item.get("type", "news"),
            "route_str": item.get("headline", ""),
            "caption": caption,
            "whatsapp_caption": caption,
            "event_context": item.get("details", ""),
            "status": "draft",
        }
        await save_campaign(campaign)

        if vid and Path(vid).exists():
            keyboard = review_keyboard(campaign_id)
            vid_bytes = Path(vid).read_bytes()
            tg_caption = caption if len(caption) <= 1024 else caption[:1020] + "…"

            try:
                async with httpx.AsyncClient(timeout=120) as http:
                    resp = await http.post(
                        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendVideo",
                        data={
                            "chat_id": str(cid),
                            "caption": tg_caption,
                            "parse_mode": "Markdown",
                            "reply_markup": json.dumps(keyboard),
                        },
                        files={"video": (f"post_{i}.mp4", vid_bytes, "video/mp4")},
                    )
                    result = resp.json()
                    if resp.status_code == 200 and result.get("ok"):
                        msg_id = result["result"]["message_id"]
                        try:
                            chat_id_int = int(cid)
                        except (TypeError, ValueError):
                            chat_id_int = cid
                        await update_review_tracking(campaign_id, chat_id_int, msg_id)
                        sent += 1
                        print(f"  ✅ {campaign_id}: {item.get('headline', '')[:40]} (video+buttons)")
                    else:
                        print(f"  ⚠️ {campaign_id}: sendVideo failed — {str(result)[:120]}")
            except Exception as e:
                print(f"  ⚠️ {campaign_id}: {e}")
        else:
            res = await send_approval_request(campaign, chat_id=cid)
            if res and res.get("message_id"):
                await update_review_tracking(campaign_id, res["chat_id"], res["message_id"])
                sent += 1
                print(f"  ⚠️ {campaign_id}: no video — sent as text+buttons")

        await asyncio.sleep(1)

    await send_message(
        chat_id=cid,
        text=(
            f"━━━━━━━━━━━━━━━━━━━━━\n✅ *{sent}/5 video posts sent*\n\n"
            "Tap *Approve* on the keepers.\n"
            "*Edit caption* → write what to change.\n"
            "Approved posts broadcast Monday 10:00."
        ),
    )
    print(f"\n✅ {sent}/5 video posts sent to Telegram with buttons!")


# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════

async def main() -> None:
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY not set")
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    print("═" * 55)
    print(f"  5 VIDEO POSTS — {TODAY}")
    print("═" * 55)

    gemini = genai.Client(api_key=settings.gemini_api_key)
    claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    new_topics = step1_find_new_topics(gemini)
    all_items = EXISTING + new_topics[:2]

    if len(all_items) < 5:
        print(f"  ⚠️ Only {len(all_items)} topics (need 5)")

    all_items = step2_write_captions(claude_client, all_items)
    step3_download_videos(claude_client, all_items)
    await step4_send_telegram(all_items)

    print("\n═══ QUALITY GATE ═══")
    for pattern in ("vid_final_*.mp4", "vid_dual_*.mp4"):
        for f in sorted(OUT.glob(pattern)):
            info = probe_video_stream(str(f))
            if info:
                w = info.get("width", "?")
                h = info.get("height", "?")
                br = info.get("bit_rate", "?")
                print(f"  {f.name}: {w}x{h}, bitrate={br}")
                if isinstance(w, int) and isinstance(h, int) and (w < 1920 or h < 1080):
                    print(f"    ⚠️ Below 1080p target")
                if isinstance(br, str) and br.isdigit() and int(br) < 6_000_000:
                    print(f"    ⚠️ Bitrate below 6Mbps")
            else:
                print(f"  {f.name}: ffprobe unavailable")

    print(f"\n{'═' * 55}")
    print("  SUMMARY")
    print(f"{'═' * 55}\n")
    for i, item in enumerate(all_items, 1):
        vid = "📹" if item.get("video_final") or item.get("video_clip") else "❌"
        cap = "📋" if item.get("caption") else "❌"
        print(f"  {i}. {vid} {cap} {item.get('headline', item.get('name', '?'))[:50]}")


if __name__ == "__main__":
    asyncio.run(main())
