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
from datetime import datetime
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
from services.video_processor import brand_video, extract_frames, select_best_frame, trim_video  # noqa: E402
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

    prompt = f"""Today is {TODAY}. Search the web for:

1. UPCOMING PREMIUM EVENTS in next 2-4 weeks (F1, tennis, fashion, art, film)
2. BUSINESS CLASS AIRLINE NEWS this week (new cabins, lounges, routes, awards)
3. TRENDING LUXURY DESTINATIONS right now

Per item return:
{{"type":"event|news|destination","name":"...","city":"...","dates":"...","details":"3-4 specific fact sentences","youtube_query":"best YouTube search for footage of this","visual":"what this looks like visually"}}

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

    for i, it in enumerate(items, 1):
        print(f"  {i}. [{it.get('type', '?'):11s}] {it.get('name', '')}")

    (OUT / "step1_findings.json").write_text(
        json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return items


def step2_select(claude_client: anthropic.Anthropic, findings: list[dict]) -> list[dict]:
    print("\n🧠 STEP 2 — Claude selecting top 3...\n")

    resp = claude_client.messages.create(
        model=getattr(settings, "anthropic_model", "claude-sonnet-4-20250514"),
        max_tokens=2500,
        system=BBC_BRAND_DNA + "\n\n" + BBC_CONTENT_SELECTION_CONTEXT,
        messages=[
            {
                "role": "user",
                "content": f"""Gemini found these items from this week's web search:

{json.dumps(findings, ensure_ascii=False)}

Select 3 BEST for our WhatsApp Channel.

REMEMBER THE BRAND:
- We sell the LUXURY EXPERIENCE around events, not the event action
- F1 → show the paddock champagne, NOT the crashes
- We want our client to feel INSPIRED, not thrilled by danger
- Mix: 1 event + 1 airline news + 1 destination

For each, also write the YouTube search query that will find
LUXURY/VIP/PREMIUM footage — NOT action/crash/controversy footage.

Per pick return:
{{"index":0,"headline":"Emotional max 8 words","youtube_query":"luxury-focused YouTube search","caption":"Full WhatsApp caption. Emoji start. Premium whisper tone. End with:\\n\\nbuybusinessclass.com\\n☎️ +1 888-322-7999 📩 deals@buybusinessclass.com","post_type":"deal|news"}}

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
        if p.get("youtube_query"):
            item["youtube_query"] = p["youtube_query"]
        selected.append(item)
        print(f"  ✅ {item['headline']}")

    (OUT / "step2_selected.json").write_text(
        json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return selected


def step3_footage(claude_client: anthropic.Anthropic, selected: list[dict]) -> None:
    print("\n📹 STEP 3 — YouTube download + frame extraction...\n")

    for i, item in enumerate(selected, 1):
        q = item.get("youtube_query", item.get("name", "") + " luxury VIP experience")
        print(f"  {i}. Search: '{q[:50]}'")

        videos = search_youtube(q, max_results=3)
        if not videos:
            print("     ⚠️ No YouTube results")
            continue

        titles = "\n".join(
            f"  {j}. {v['title']} ({v['duration']}s, {v.get('uploader', '')})"
            for j, v in enumerate(videos)
        )

        try:
            pick = claude_client.messages.create(
                model=getattr(settings, "anthropic_model", "claude-sonnet-4-20250514"),
                max_tokens=200,
                system=BBC_YOUTUBE_SEARCH_CONTEXT,
                messages=[
                    {
                        "role": "user",
                        "content": f"""Which YouTube video is best for BuyBusinessClass.com post about "{item.get('name', '')}"?

Videos found:
{titles}

Reply with JUST the number (0, 1, or 2).
If NONE are suitable (all show crashes, controversy, negativity), reply "NONE".
Pick the one showing LUXURY, VIP EXPERIENCE, BEAUTIFUL ATMOSPHERE.""",
                    }
                ],
            )
            choice = pick.content[0].text.strip()

            if choice.upper() == "NONE":
                print("     🚫 Claude rejected all videos (not luxury content)")
                continue

            idx = int(choice) if choice.isdigit() else 0
            idx = min(idx, len(videos) - 1)
            chosen = videos[idx]
            print(f"     🧠 Claude picked: {chosen['title'][:50]}")
        except Exception as exc:
            chosen = videos[0]
            print(f"     ⚠️ Claude pick failed ({exc}), using first: {chosen['title'][:50]}")

        url = chosen["url"]

        clip = download_clip(url, str(OUT / f"raw_{i}.mp4"), max_seconds=30)
        if not clip:
            print("     ⚠️ Download failed")
            continue

        print(f"     ✅ Downloaded: {Path(clip).stat().st_size // 1024}KB")
        item["video_raw"] = clip

        frames = extract_frames(clip, str(OUT / f"frames_{i}"), interval_sec=3)
        if frames:
            best = select_best_frame(frames)
            item["frames"] = frames
            item["best_frame"] = best
            print(f"     ✅ {len(frames)} frames → best: {Path(best).name}")

        trimmed = trim_video(clip, str(OUT / f"clip_{i}.mp4"), start=2, duration=10)
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
            iata = CITY_IATA.get(city.lower().split(",")[0].strip(), "")
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
    print("\n📱 STEP 5 — Telegram...\n")

    import httpx

    from services.telegram_client import send_message, send_photo, send_video

    cid = settings.telegram_chat_id
    if not cid:
        print("⬜ No TELEGRAM_CHAT_ID — files saved locally")
        return

    await send_message(
        chat_id=cid,
        text=(
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 *This Week — Business Class*\n"
            f"_{TODAY}_\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        ),
    )

    for i, item in enumerate(selected, 1):
        await asyncio.sleep(1)

        src = "📸 Real" if item.get("frames") else "🎨 AI"
        img = item.get("image")

        if img and Path(img).exists():
            img_bytes = Path(img).read_bytes()
            url = None
            if settings.supabase_url and settings.supabase_key:
                try:
                    from services.supabase_client import upload_image

                    url = await upload_image(img_bytes, f"deals/pipeline/post_{i}.jpg")
                except Exception as exc:
                    log.warning("Supabase upload failed: %s", exc)

            if url:
                await send_photo(chat_id=cid, photo_url=url, caption=f"*{item['headline']}*\n{src}")
            elif settings.telegram_bot_token:
                tg_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
                async with httpx.AsyncClient(timeout=60) as client:
                    await client.post(
                        tg_url,
                        data={
                            "chat_id": str(cid),
                            "caption": f"*{item['headline']}*\n{src}",
                            "parse_mode": "Markdown",
                        },
                        files={"photo": (Path(img).name, img_bytes, "image/jpeg")},
                    )
            else:
                await send_message(chat_id=cid, text=f"*{item['headline']}*\n{src}")

        await asyncio.sleep(0.5)

        vid = item.get("video")
        if vid and Path(vid).exists():
            await send_video(
                chat_id=cid,
                video_path=vid,
                caption=f"🎬 *{item.get('name', item['headline'])}*",
            )

        await asyncio.sleep(0.5)

        cap_file = OUT / f"caption_{i}.txt"
        if cap_file.exists():
            await send_message(chat_id=cid, text=f"📋 *Caption:*\n\n{cap_file.read_text(encoding='utf-8')}")
        await asyncio.sleep(1)

    await send_message(
        chat_id=cid,
        text=(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ *Pipeline complete*\n\n"
            "🔍 Gemini → web search\n🧠 Claude → select & write\n"
            "📹 yt-dlp → real footage\n🖼️ FFmpeg → frames\n"
            "🎨 Branding → BBC overlay\n📱 Ready for Channel"
        ),
    )


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
        has_real = "📸" if s.get("frames") else "🎨"
        has_vid = "📹" if s.get("video") else "—"
        print(f"  {i}. {has_real} {has_vid} {s['headline']}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
