"""
BBC Real Footage Pipeline — fully autonomous.
Run: python scripts/real_footage_pipeline.py

Flow:
  1. Gemini Search → events + news from web
  2. Claude → selects top 3, writes headlines + captions
  3. yt-dlp → downloads YouTube clips per event
  4. FFmpeg → extracts real frames + trims clips
  5. Branding → BBC overlay on real frames
  6. FFmpeg → brands video clips with headline + footer
  7. Telegram → sends image + video + caption per post
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
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
from prompts.brand_dna import BBC_BRAND_DNA, BBC_CONTENT_SELECTION_CONTEXT, BBC_YOUTUBE_SEARCH_CONTEXT  # noqa: E402
from services.branding_engine import generate_branded_image  # noqa: E402
from services.gemini_client import generate_event_image  # noqa: E402
from services.pricing_engine import calculate_price, format_price  # noqa: E402
from services.video_processor import (  # noqa: E402
    brand_video,
    extract_frames,
    select_best_frame,
    select_best_frame_with_claude,
    trim_video,
)
from services.youtube_client import download_clip, search_youtube  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("pipeline")

DEFAULT_BG = ROOT / "assets" / "defaults" / "default_background.jpg"
OUT = ROOT / "output" / "pipeline"
TODAY = datetime.now().strftime("%B %d, %Y")

CITY_IATA = {
    "london": "LHR",
    "paris": "CDG",
    "rome": "FCO",
    "tokyo": "NRT",
    "nice": "NCE",
    "monaco": "NCE",
    "dubai": "DXB",
    "sydney": "SYD",
    "miami": "MIA",
    "milan": "MXP",
    "barcelona": "BCN",
    "vienna": "VIE",
    "amsterdam": "AMS",
    "lisbon": "LIS",
    "athens": "ATH",
    "doha": "DOH",
    "singapore": "SIN",
    "hong kong": "HKG",
    "seoul": "ICN",
}

CONTACT_BLOCK = (
    "\n\nbuybusinessclass.com\n"
    "☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"
)


def _parse_json_array(text: str) -> list[dict]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    if raw.startswith("json"):
        raw = raw[4:].strip()
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Expected JSON array")
    return data


def step1_search(gemini: genai.Client) -> list[dict]:
    print(f"\n🔍 STEP 1 — Gemini searching web ({TODAY})...\n")

    now = datetime.now()
    cutoff_past = now - timedelta(weeks=4)
    future_end = now + timedelta(weeks=4)

    prompt = f"""Today is {TODAY}. Search the web for:

1. UPCOMING PREMIUM EVENTS in next 2-4 weeks (F1, tennis, fashion, art, film)
2. BUSINESS CLASS AIRLINE NEWS this week (new cabins, lounges, routes, awards)
3. TRENDING LUXURY DESTINATIONS right now

CRITICAL DATE RULE:
Every item MUST be from the last 4 weeks ({cutoff_past.strftime("%B %d")} - {now.strftime("%B %d, %Y")}).
If you cannot find news from the last 4 weeks, say so — DO NOT return old articles from 2024 or 2025.
For events: must be happening in the NEXT 4 weeks ({now.strftime("%B %d")} - {future_end.strftime("%B %d, %Y")}).
Include the EXACT article date or event date for each item.
I will REJECT any item older than 4 weeks.

Per item return:
{{"type":"event|news|destination","name":"...","city":"...","dates":"exact event or article date range","article_date":"YYYY-MM-DD if news","details":"3-4 specific fact sentences","youtube_query":"best YouTube search for footage of this","visual":"what this looks like visually"}}

IMPORTANT — YOUTUBE QUERIES:
For each item, write a youtube_query that finds LUXURY/VIP/PREMIUM footage.
We are a LUXURY brand. Our clients are wealthy Americans.
- For F1 events → "Monaco Grand Prix VIP hospitality yacht party" NOT "crash highlights"
- For tennis → "Wimbledon hospitality suite experience" NOT "best rallies"
- For airline news → "Qatar Airways QSuite cabin tour" NOT "airline problems"
- Always include words: luxury, VIP, premium, cinematic, hospitality, experience
- The footage should make someone WANT to be there

Return JSON array, 6-8 items. Only REAL facts from web. No markdown fences."""

    resp = gemini.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    items = _parse_json_array(resp.text or "")

    filtered: list[dict] = []
    for item in items:
        dates = (
            (item.get("dates") or "")
            + (item.get("date_range") or "")
            + (item.get("article_date") or "")
        )
        if any(y in dates for y in ("2024", "2023", "2022")):
            print(f"  ❌ VECHI: {item.get('name', '?')} ({dates})")
            continue
        filtered.append(item)

    items = filtered if filtered else items
    print(f"  ✅ {len(items)} items after date filter\n")

    for i, it in enumerate(items, 1):
        print(f"  {i}. [{it.get('type', '?'):11s}] {it.get('name', '')}")

    (OUT / "step1_findings.json").write_text(
        json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return items


def step2_select(claude_client: anthropic.Anthropic, findings: list[dict]) -> list[dict]:
    print("\n🧠 STEP 2 — Claude selecting top 3...\n")

    resp = claude_client.messages.create(
        model=getattr(settings, "anthropic_model", "claude-sonnet-4-6"),
        max_tokens=2500,
        system=BBC_BRAND_DNA + "\n\n" + BBC_CONTENT_SELECTION_CONTEXT,
        messages=[
            {
                "role": "user",
                "content": f"""Gemini found these items from this week's web search:

{json.dumps(findings, ensure_ascii=False)}

Select 3 BEST for our WhatsApp Channel.

REJECT any item where the date is older than 4 weeks.
If an item has no date or the date is from 2024/2025, REJECT it.
Only select items from the last 4 weeks or upcoming events in the next 4 weeks.

REMEMBER THE BRAND:
- We sell the LUXURY EXPERIENCE around events, not the event action
- F1 → show the paddock champagne, NOT the crashes
- We want our client to feel INSPIRED, not thrilled by danger
- Mix: 1 event + 1 airline news + 1 destination

For each pick, write TWO YouTube search queries:

"youtube_query_official": Search that finds OFFICIAL brand/airline promo videos.
  Include the brand/airline name + "official" + "reveal" or "promo" or "commercial"
  Example: "Qatar Airways Qsuite Next Gen official reveal"
  Example: "Emirates new First Class Suite official launch 2026"
  Example: "F1 Monaco Grand Prix official promo cinematic"

"youtube_query_cinematic": Fallback search for drone/cinematic footage.
  Include destination + "4K" + "cinematic" + "drone" or "aerial"
  Example: "Monaco harbor sunset drone 4K cinematic aerial"
  Example: "Tokyo skyline night cinematic 4K"
  Example: "Lake Como Bellagio aerial drone luxury 4K"

CRITICAL: Query A (official) is ALWAYS tried first.
Only if it fails, query B (cinematic) is used.

Per pick return:
{{"index":0,"headline":"Emotional max 8 words","youtube_query_official":"brand official promo search","youtube_query_cinematic":"destination 4K cinematic drone search","caption":"Full WhatsApp caption. Emoji start. Premium whisper tone. End with:\\n\\nbuybusinessclass.com\\n☎️ +1 888-322-7999 📩 deals@buybusinessclass.com","post_type":"deal|news"}}

JSON array, exactly 3. No markdown.""",
            }
        ],
    )
    picks = _parse_json_array(resp.content[0].text)

    selected: list[dict] = []
    for p in picks:
        item = findings[p["index"]].copy()
        item["headline"] = p["headline"]
        item["caption"] = p["caption"]
        item["post_type"] = p.get("post_type", "news")
        legacy_q = p.get("youtube_query", item.get("youtube_query", ""))
        item["youtube_query_official"] = p.get(
            "youtube_query_official",
            f"{legacy_q} official promo luxury".strip() if legacy_q else f"{item['name']} official promo luxury",
        )
        item["youtube_query_cinematic"] = p.get(
            "youtube_query_cinematic",
            f"{legacy_q} 4K cinematic drone aerial".strip() if legacy_q else f"{item['name']} cinematic 4K drone aerial",
        )
        selected.append(item)
        print(f"  ✅ {item['headline']}")

    (OUT / "step2_selected.json").write_text(
        json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return selected


OFFICIAL_UPLOADERS = {
    "qatar airways",
    "emirates",
    "singapore airlines",
    "ana",
    "etihad",
    "cathay pacific",
    "british airways",
    "air france",
    "lufthansa",
    "turkish airlines",
    "japan airlines",
    "korean air",
    "eva air",
    "swiss",
    "virgin atlantic",
    "delta",
    "united airlines",
    "american airlines",
    "four seasons",
    "ritz-carlton",
    "aman",
    "mandarin oriental",
    "rosewood",
    "peninsula",
    "st regis",
    "park hyatt",
    "formula 1",
    "f1",
    "atp tour",
    "wimbledon",
    "art basel",
    "paris fashion week",
    "met gala",
    "visit japan",
    "visit dubai",
    "discover dubai",
    "visit london",
    "tourism australia",
    "singapore tourism",
}


def _is_official_channel(uploader: str) -> bool:
    """Check if uploader is a known official brand channel."""
    if not uploader:
        return False
    up_lower = uploader.lower().strip()
    return any(brand in up_lower or up_lower in brand for brand in OFFICIAL_UPLOADERS)


def step3_footage(claude_client: anthropic.Anthropic, selected: list[dict]) -> None:
    """Download YouTube footage — auto-pick official, Claude for rest, fallback download."""
    print("\n📹 STEP 3 — YouTube footage (official-first)...\n")

    yt_context = BBC_BRAND_DNA + "\n\n" + BBC_YOUTUBE_SEARCH_CONTEXT

    for i, item in enumerate(selected, 1):
        name = item.get("name", "")
        headline = item.get("headline", name)
        legacy_q = item.get("youtube_query", "")

        queries = [
            item.get(
                "youtube_query_official",
                f"{legacy_q} official promo luxury".strip() if legacy_q else f"{name} official promo luxury",
            ),
            item.get(
                "youtube_query_cinematic",
                f"{legacy_q} 4K cinematic drone aerial".strip() if legacy_q else f"{name} cinematic 4K drone aerial",
            ),
        ]

        print(f"  {i}. {headline}")
        chosen_video: dict | None = None
        all_videos_seen: list[dict] = []

        for attempt, q in enumerate(queries):
            label = "OFFICIAL" if attempt == 0 else "CINEMATIC"
            print(f"     🔍 {label}: '{q[:55]}'")

            videos = search_youtube(q, max_results=3)
            if not videos:
                print("        ⚠️ No results")
                continue

            all_videos_seen.extend(videos)

            for v in videos:
                if _is_official_channel(v.get("uploader", "")):
                    chosen_video = v
                    print(f"        🏆 AUTO-PICK official: \"{v['title'][:45]}\"")
                    print(f"           by {v['uploader']} ({v.get('view_count', 0):,} views)")
                    break

            if chosen_video:
                break

            video_info = "\n".join(
                f"  {j}. \"{v['title']}\"\n"
                f"     Uploader: {v.get('uploader', '?')} | "
                f"Views: {v.get('view_count', 0):,} | Duration: {v['duration']}s"
                for j, v in enumerate(videos)
            )

            try:
                pick = claude_client.messages.create(
                    model=getattr(settings, "anthropic_model", "claude-sonnet-4-6"),
                    max_tokens=50,
                    system=yt_context,
                    messages=[
                        {
                            "role": "user",
                            "content": f"""Best video for luxury post about "{headline}"?

{video_info}

Reply ONLY with the number: 0, 1, or 2
If ALL are amateur vloggers with shaky footage, reply exactly: NONE""",
                        }
                    ],
                )
                raw_choice = pick.content[0].text.strip()
                cleaned = raw_choice.split("\n")[0].split("—")[0].split("-")[0].strip()

                if cleaned.upper() == "NONE":
                    print("        🚫 Claude: NONE")
                    continue

                idx = None
                for ch in cleaned:
                    if ch.isdigit():
                        idx = int(ch)
                        break

                if idx is not None and 0 <= idx < len(videos):
                    chosen_video = videos[idx]
                    print(f"        ✅ Claude picked #{idx}: \"{chosen_video['title'][:45]}\"")
                    print(f"           by {chosen_video.get('uploader', '?')}")
                    break
                print(f"        ⚠️ Claude unclear: '{raw_choice}' — trying next query")

            except Exception as exc:
                print(f"        ⚠️ Claude error: {exc}")

        if not chosen_video and all_videos_seen:
            short_videos = [v for v in all_videos_seen if v.get("duration", 999) <= 300]
            fallback = short_videos[0] if short_videos else all_videos_seen[0]

            print("     📥 FALLBACK: downloading first for frame check...")
            print(f"        \"{fallback['title'][:45]}\" by {fallback.get('uploader', '?')}")
            chosen_video = {**fallback, "_is_fallback": True}

        if not chosen_video:
            print("     ⬜ Zero YouTube results — AI image only")
            continue

        clip = download_clip(chosen_video["url"], str(OUT / f"raw_{i}.mp4"), max_seconds=30)
        if not clip:
            print("     ⚠️ Download failed")
            continue

        print(f"     ✅ Downloaded: {Path(clip).stat().st_size // 1024}KB")
        item["video_raw"] = clip

        frames = extract_frames(clip, str(OUT / f"frames_{i}"), interval_sec=3, skip_start=5)
        if not frames:
            print("     ⚠️ No frames extracted")
            continue

        if chosen_video.get("_is_fallback"):
            print("     🧠 Claude checking frames (fallback video)...")
            best = select_best_frame_with_claude(
                frames,
                event_name=headline,
                anthropic_client=claude_client,
                model=getattr(settings, "anthropic_model", "claude-sonnet-4-6"),
            )
            if not best:
                best = select_best_frame(frames)
            if not best:
                print("     🚫 Claude rejected ALL frames — AI image only")
                continue
            print(f"     ✅ Claude approved frame: {Path(best).name}")
        else:
            best = select_best_frame(frames)

        item["frames"] = frames
        item["best_frame"] = best
        print(f"     ✅ {len(frames)} frames → best: {Path(best).name}")

        trimmed = trim_video(clip, str(OUT / f"clip_{i}.mp4"), start=5, duration=10)
        if trimmed:
            item["video_clip"] = trimmed
            print(f"     ✅ Clip 10s: {Path(trimmed).stat().st_size // 1024}KB")


async def step4_brand(selected: list[dict]) -> None:
    print("\n🎨 STEP 4 — Branding...\n")

    for i, item in enumerate(selected, 1):
        print(f"  {i}. {item['headline']}")

        bg = item.get("best_frame")
        if not bg or not Path(bg).exists():
            print("     🎨 No real frame → Gemini AI...")
            try:
                visual = item.get("visual", item.get("name", ""))
                ai = await generate_event_image(
                    f"{visual}. Cinematic 16:9, golden hour, no text no logos no watermarks."
                )
                if ai:
                    bg = str(OUT / f"ai_{i}.jpg")
                    Path(bg).write_bytes(ai)
                    print(f"     ✅ AI: {len(ai):,} bytes")
                else:
                    bg = str(DEFAULT_BG if DEFAULT_BG.exists() else OUT / f"ai_{i}.jpg")
            except Exception as exc:
                log.warning("AI fallback failed: %s", exc)
                bg = str(DEFAULT_BG)
        else:
            print(f"     📸 Real frame: {Path(bg).name}")

        price = ""
        city = item.get("city", "")
        if item.get("post_type") == "deal" and city:
            iata = item.get("pricing_iata") or CITY_IATA.get(
                city.lower().split(",")[0].strip(), ""
            )
            if iata:
                p = calculate_price("JFK", iata, "round_trip", "business")
                if p:
                    price = f"from {format_price(p)}"

        label = city.split(",")[0].strip().upper() if city else item.get("type", "NEWS").upper()
        branded = await asyncio.to_thread(
            generate_branded_image,
            event_name=label,
            route=item["headline"],
            price=price,
            background_url_or_path=bg,
        )
        img_path = str(OUT / f"post_{i}.jpg")
        Path(img_path).write_bytes(branded)
        item["image"] = img_path
        print(f"     ✅ Image: {len(branded):,} bytes")

        clip = item.get("video_clip")
        if clip and Path(clip).exists():
            bv = brand_video(clip, str(OUT / f"post_{i}.mp4"), headline=item["headline"])
            if bv:
                item["video"] = bv
                print(f"     ✅ Video: {Path(bv).stat().st_size // 1024}KB")

        cap = item.get("caption", "")
        if "888-322-7999" not in cap:
            cap += CONTACT_BLOCK
        (OUT / f"caption_{i}.txt").write_text(cap, encoding="utf-8")


async def step5_telegram(selected: list[dict]) -> None:
    """Save campaigns to Supabase + send with Approve/Reject buttons."""
    print("\n📱 STEP 5 — Save + send for approval...\n")

    from datetime import UTC

    from services.supabase_client import save_campaign, update_review_tracking, upload_image
    from services.telegram_client import send_approval_request, send_message, send_video

    cid = settings.telegram_chat_id
    if not cid:
        print("⬜ No TELEGRAM_CHAT_ID — saved locally only")
        return

    campaign_base = datetime.now(UTC).strftime("%Y-W%W")

    await send_message(
        chat_id=cid,
        text=(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📰 *This Week — Business Class*\n"
            "_Review each post below_ 👇\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        ),
    )

    for i, item in enumerate(selected, 1):
        await asyncio.sleep(1)

        campaign_id = f"{campaign_base}-RF-{i:03d}"

        img_path = item.get("image", str(OUT / f"post_{i}.jpg"))
        image_url = None
        if Path(img_path).exists():
            img_bytes = Path(img_path).read_bytes()
            try:
                image_url = await upload_image(img_bytes, f"deals/{campaign_id}/landscape.jpg")
            except Exception as e:
                print(f"  ⚠️ Upload failed: {e}")

        caption = ""
        cap_file = OUT / f"caption_{i}.txt"
        if cap_file.exists():
            caption = cap_file.read_text(encoding="utf-8")

        campaign = {
            "campaign_id": campaign_id,
            "name": item.get("name", item.get("headline", "")),
            "event_name": item.get("headline", ""),
            "city": item.get("city", ""),
            "category": item.get("type", "news"),
            "route_str": item.get("headline", ""),
            "price": item.get("price_display", ""),
            "price_raw": item.get("price_raw"),
            "image_url": image_url,
            "caption": caption,
            "whatsapp_caption": caption,
            "event_context": item.get("details", ""),
            "status": "draft",
        }

        await save_campaign(campaign)

        if image_url is None and Path(img_path).exists() and settings.telegram_bot_token:
            # Supabase absent (local run) → multipart upload direct, cu butoanele de review
            import httpx

            from keyboards import review_keyboard
            from prompts.system_prompts import format_telegram_preview

            tg_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    tg_url,
                    data={
                        "chat_id": str(cid),
                        "caption": format_telegram_preview(campaign),
                        "parse_mode": "Markdown",
                        "reply_markup": json.dumps(review_keyboard(campaign_id)),
                    },
                    files={"photo": (Path(img_path).name, Path(img_path).read_bytes(), "image/jpeg")},
                )
                data = resp.json() if resp.status_code == 200 else {}
            result = {
                "chat_id": cid,
                "message_id": (data.get("result") or {}).get("message_id"),
            }
        else:
            result = await send_approval_request(campaign, chat_id=cid)

        if result and result.get("message_id"):
            await update_review_tracking(campaign_id, result["chat_id"], result["message_id"])
            print(f"  ✅ Post {i} sent for approval: {campaign_id}")
        else:
            print(f"  ⚠️ Post {i}: no message_id returned — check send_approval_request")

        # Video separat (butoanele stau pe poză); pe foto-run nu există
        await asyncio.sleep(0.5)
        vid = item.get("video")
        if vid and Path(vid).exists():
            await send_video(
                chat_id=cid,
                video_path=vid,
                caption=f"🎬 *Video: {item.get('headline', '')}*\n_(for the post above)_",
            )

    await send_message(
        chat_id=cid,
        text=(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ *All posts ready for review*\n\n"
            "Tap *Approve* on the ones you like.\n"
            "Approved posts broadcast Monday 10:00.\n\n"
            "🔍 Gemini → web · 🧠 Claude → select\n"
            "🎨 BBC branding · 📍 Getting There"
        ),
    )
    print("\n✅ All posts saved + sent for approval!")


async def main() -> None:
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY not set")
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    OUT.mkdir(parents=True, exist_ok=True)

    gemini = genai.Client(api_key=settings.gemini_api_key)
    claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    findings = step1_search(gemini)
    selected = step2_select(claude_client, findings)
    step3_footage(claude_client, selected)
    await step4_brand(selected)
    await step5_telegram(selected)

    print(f"\n{'═' * 55}")
    print("  ✅ PIPELINE COMPLETE")
    print(f"{'═' * 55}\n")
    for i, s in enumerate(selected, 1):
        has_real = "📸" if s.get("best_frame") else "🎨"
        has_vid = "📹" if s.get("video") else "—"
        print(f"  {i}. {has_real} {has_vid} {s['headline']}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
