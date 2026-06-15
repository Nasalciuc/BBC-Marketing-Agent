"""
Autonomous news pipeline:
  Gemini searches web (raw facts)
  → Claude selects & writes all copy
  → Gemini generates images from Claude's prompts
  → Branding overlay + Telegram delivery

Run: python scripts/auto_news_pipeline.py
"""
from __future__ import annotations

import argparse
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bbc.auto_news")

OUTPUT = ROOT / "output"
DEFAULT_BG = ROOT / "assets" / "defaults" / "default_background.jpg"
CONTACT_BLOCK = (
    "\n\nbuybusinessclass.com\n"
    "☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"
)

CATEGORY_EMOJI = {
    "cabin": "✈️",
    "route": "🗺️",
    "lounge": "🥂",
    "award": "🏆",
    "event": "🎯",
    "trend": "📈",
}

CITY_TO_IATA = {
    "london": "LHR",
    "paris": "CDG",
    "rome": "FCO",
    "tokyo": "NRT",
    "nice": "NCE",
    "monaco": "NCE",
    "dubai": "DXB",
    "sydney": "SYD",
    "miami": "MIA",
    "doha": "DOH",
    "singapore": "SIN",
    "new york": "JFK",
    "los angeles": "LAX",
    "hong kong": "HKG",
    "bangkok": "BKK",
    "istanbul": "IST",
    "zurich": "ZRH",
    "frankfurt": "FRA",
}

RAW_SEARCH_PROMPT = """Search the web for recent business class airline news and premium travel news.

Today is {today}. Find articles from THIS WEEK or LAST WEEK.

Search for:
- business class new cabin seat launch 2026
- new airline route business class
- airport lounge opening 2026
- best business class award 2026
- luxury travel event upcoming
- premium travel trend news

For each article found, return RAW data:
{{
    "title": "Exact article title",
    "source": "Publication name",
    "date": "Article date",
    "summary": "3-4 sentence summary of what the article says",
    "url": "URL if available",
    "category": "cabin|route|lounge|award|event|trend",
    "key_details": "Airline name, city, specific numbers or facts mentioned"
}}

Return 7-10 articles as JSON array. Just facts, no creative writing.
"""

CLAUDE_ANALYZE_PROMPT = """You are the content strategist for BuyBusinessClass.com —
a premium business class flight booking company with 100,000+ clients in the USA.

Gemini found these news articles from this week:

{articles}

YOUR JOB:
1. Select the 3 BEST stories for our WhatsApp Channel audience
2. For each, create a COMPLETE social media post

SELECTION CRITERIA:
- Our audience = wealthy American travelers who fly business class
- They care about: new cabins, better lounges, exclusive events, luxury routes
- They DON'T care about: economy class, budget airlines, generic travel tips
- Mix categories: don't pick 3 of the same type
- Prefer stories with strong VISUAL potential (landmarks, interiors, destinations)

FOR EACH SELECTED STORY, create:
{{
    "headline": "Catchy emotional headline, max 8 words. Premium whisper tone.",
    "body": "2-3 sentences. Factual but aspirational. Make the reader FEEL it.",
    "category": "cabin|route|lounge|award|event|trend",
    "post_type": "deal|brand|news",
    "source": "Original publication",
    "destination_city": "City name or null",
    "image_prompt": "DETAILED cinematic photo prompt. 16:9 landscape. Specific to THIS story. Include: exact location/interior described, golden hour or dramatic lighting, architectural details, atmosphere. Professional travel photography quality. NO TEXT, NO LOGOS, NO WATERMARKS, NO WORDS, NO PEOPLE FACES.",
    "caption": "Complete WhatsApp caption. Start with relevant emoji. Premium tone. Include the key fact from the story. If there's a destination, mention 'Business Class to [city]'. MUST end with exactly:\\n\\nbuybusinessclass.com\\n☎️ +1 888-322-7999 📩 deals@buybusinessclass.com",
    "why_selected": "Why this story matters to BBC clients"
}}

Return ONLY valid JSON array with exactly 3 objects. No markdown.
"""


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    if text.startswith("json"):
        text = text[4:].strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Expected JSON array")
    return data


def _gemini_search_raw_sync() -> list[dict]:
    from google import genai
    from google.genai import types

    from config import settings

    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")

    today = datetime.now().strftime("%B %d, %Y")
    client = genai.Client(api_key=settings.gemini_api_key)

    print("🔍 Gemini searching web (raw data only)...")
    print(f"   Date: {today}\n")

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=RAW_SEARCH_PROMPT.format(today=today),
        config=types.GenerateContentConfig(
            temperature=0.1,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    text = (response.text or "").strip()
    articles = _parse_json_array(text)

    print(f"✅ Gemini found {len(articles)} articles\n")
    for i, article in enumerate(articles, 1):
        print(f"  {i}. [{article.get('category', '')}] {article.get('title', '')[:60]}")
        print(f"     {article.get('source', '?')} · {article.get('date', '?')}")

    return articles


async def gemini_search_raw() -> list[dict]:
    return await asyncio.to_thread(_gemini_search_raw_sync)


def _claude_analyze_sync(raw_articles: list[dict]) -> list[dict]:
    from anthropic import Anthropic

    from config import settings

    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured")

    print("\n🧠 Claude analyzing articles and creating posts...")

    client = Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=3000,
        messages=[
            {
                "role": "user",
                "content": CLAUDE_ANALYZE_PROMPT.format(
                    articles=json.dumps(raw_articles, indent=2, ensure_ascii=False)
                ),
            }
        ],
    )

    posts = _parse_json_array(response.content[0].text)

    print(f"\n✅ Claude selected {len(posts)} stories:\n")
    for i, post in enumerate(posts, 1):
        print(f"  {i}. [{post.get('category', '').upper()}] {post.get('headline', '')}")
        print(f"     Type: {post.get('post_type', 'news')}")
        why = post.get("why_selected", "")
        print(f"     Why: {why[:80]}{'...' if len(why) > 80 else ''}")
        print()

    return posts


async def claude_analyze(raw_articles: list[dict]) -> list[dict]:
    return await asyncio.to_thread(_claude_analyze_sync, raw_articles)


def _resolve_price(post: dict) -> str:
    from services.pricing_engine import calculate_price, format_price

    city = post.get("destination_city") or ""
    if post.get("post_type") != "deal" or not city:
        return ""

    iata = CITY_TO_IATA.get(city.lower().split(",")[0].strip(), "")
    if not iata:
        return ""

    price = calculate_price("JFK", iata, "round_trip", "business")
    if not price:
        return ""

    price_text = f"from {format_price(price)}"
    print(f"     💰 {city}: {price_text}")
    return price_text


async def generate_post_assets(index: int, post: dict) -> dict:
    from services.branding_engine import generate_branded_image
    from services.gemini_client import generate_event_image

    print(f"\n{'═' * 55}")
    print(f"  POST {index}: {post.get('headline', '')}")
    print(f"{'═' * 55}")

    print("\n  🎨 Gemini generating image (Claude's prompt)...")
    bg_path = OUTPUT / f"POST_{index}_bg.jpg"
    bg_bytes = None
    try:
        bg_bytes = await generate_event_image(post.get("image_prompt", ""))
        if bg_bytes:
            bg_path.write_bytes(bg_bytes)
            print(f"     ✅ Photo: {len(bg_bytes):,} bytes")
        else:
            print("     ⚠️ Gemini returned no image → default bg")
    except Exception as exc:
        print(f"     ⚠️ {exc} → default bg")

    background = str(bg_path if bg_bytes else (DEFAULT_BG if DEFAULT_BG.exists() else bg_path))

    price_text = _resolve_price(post)
    city = post.get("destination_city") or ""
    post_type = post.get("post_type", "news")
    event_label = city.split(",")[0].strip() if city else post.get("category", "NEWS").upper()

    print("  🎨 Branding overlay...")
    if post_type == "deal" and price_text:
        branded = await asyncio.to_thread(
            generate_branded_image,
            event_name=event_label,
            route=post.get("headline", ""),
            price=price_text,
            background_url_or_path=background,
        )
    elif post_type == "brand":
        branded = await asyncio.to_thread(
            generate_branded_image,
            event_name="BUSINESS CLASS",
            route=post.get("headline", ""),
            price="Lie-flat seats · Lounge access · Premium dining",
            background_url_or_path=background,
        )
    else:
        branded = await asyncio.to_thread(
            generate_branded_image,
            event_name=event_label,
            route=post.get("headline", ""),
            price="",
            background_url_or_path=background,
        )

    branded_path = OUTPUT / f"POST_{index}_branded.jpg"
    branded_path.write_bytes(branded)
    print(f"     ✅ Branded: {len(branded):,} bytes")

    caption = post.get("caption", "")
    if "888-322-7999" not in caption:
        caption += CONTACT_BLOCK

    caption_path = OUTPUT / f"POST_{index}_caption.txt"
    caption_path.write_text(caption, encoding="utf-8")
    print(f"     ✅ Caption: {len(caption)} chars")
    print(f"\n  📋 Preview:\n     {caption[:120]}...")

    return {
        "index": index,
        "post": post,
        "image_path": branded_path,
        "caption_path": caption_path,
        "caption": caption,
    }


async def send_to_telegram(posts: list[dict], results: list[dict]) -> None:
    import httpx

    from config import settings
    from services.supabase_client import upload_image
    from services.telegram_client import send_message, send_photo

    chat_id = settings.telegram_chat_id
    if not chat_id:
        print("\n⬜ No TELEGRAM_CHAT_ID — saved locally in output/POST_*")
        return

    if not settings.telegram_bot_token:
        print("\n⬜ No TELEGRAM_BOT_TOKEN — saved locally only")
        return

    await send_message(
        chat_id=chat_id,
        text=(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📰 *This Week in Business Class*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "_Gemini searched → Claude selected & wrote → ready for review_"
        ),
    )

    for result in results:
        post = result["post"]
        idx = result["index"]
        img_path: Path = result["image_path"]
        caption = result["caption"]

        if not img_path.exists():
            continue

        await asyncio.sleep(1)

        img_bytes = img_path.read_bytes()
        image_url = None
        if settings.supabase_url and settings.supabase_key:
            try:
                image_url = await upload_image(img_bytes, f"deals/weekly/post_{idx}.jpg")
            except Exception as exc:
                log.warning("Supabase upload failed: %s", exc)

        emoji = CATEGORY_EMOJI.get(post.get("category", ""), "📰")
        photo_caption = (
            f"{emoji} *{post.get('headline', '')}*\n"
            f"_{post.get('source', '')}_ · {post.get('post_type', 'news')}"
        )

        if image_url:
            await send_photo(chat_id=chat_id, photo_url=image_url, caption=photo_caption)
        else:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url,
                    data={
                        "chat_id": str(chat_id),
                        "caption": photo_caption,
                        "parse_mode": "Markdown",
                    },
                    files={"photo": (img_path.name, img_bytes, "image/jpeg")},
                )
                if not resp.json().get("ok"):
                    await send_message(
                        chat_id=chat_id,
                        text=f"{emoji} *{post.get('headline', '')}*\n_Image: {img_path.name}_",
                    )

        await asyncio.sleep(0.5)
        await send_message(chat_id=chat_id, text=f"📋 *Caption:*\n\n{caption}")
        await asyncio.sleep(1)

    await send_message(
        chat_id=chat_id,
        text=(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ *3 posts ready*\n\n"
            "Gemini searched 🔍 → Claude decided 🧠 → Images generated 🎨\n\n"
            "Forward favorites to WhatsApp Channel 📲"
        ),
    )
    print("\n✅ Sent to Telegram!")


def print_summary(raw_articles: list[dict], posts: list[dict], results: list[dict]) -> None:
    print(f"""
{'═' * 55}
  PIPELINE — CINE A FĂCUT CE
{'═' * 55}

  🔍 GEMINI (ochii):
     → A căutat pe web {len(raw_articles)} articole business class
     → A generat {len(posts)} imagini din prompturile Claude
     → Zero decizii creative — doar execuție

  🧠 CLAUDE (creierul):
     → A analizat {len(raw_articles)} articole
     → A selectat top 3 pentru publicul BBC
     → A scris headline-uri, prompt-uri, caption-uri
     → TOATE deciziile creative — Claude

  🎨 BRANDING ENGINE (local):
     → Overlay BBC pe fiecare imagine

  📱 TELEGRAM:
     → {len(results)} postări procesate
""")

    for result in results:
        post = result["post"]
        img_ok = "✅" if result["image_path"].exists() else "❌"
        cap_ok = "✅" if result["caption_path"].exists() else "❌"
        print(f"  {result['index']}. [{post.get('category', '').upper():6s}] {post.get('headline', '')}")
        print(f"     Image: {img_ok}  Caption: {cap_ok}")
        print(f"     Source: {post.get('source', '?')}")
        print()

    print(f"{'═' * 55}")


async def main(send: bool = True, use_cache: bool = False) -> None:
    from config import settings

    OUTPUT.mkdir(exist_ok=True)

    raw_path = OUTPUT / "RAW_ARTICLES.json"
    posts_path = OUTPUT / "CLAUDE_POSTS.json"

    if use_cache and raw_path.exists() and posts_path.exists():
        print("📂 Using cached RAW_ARTICLES.json + CLAUDE_POSTS.json")
        raw_articles = json.loads(raw_path.read_text(encoding="utf-8"))
        posts = json.loads(posts_path.read_text(encoding="utf-8"))
    else:
        if not settings.gemini_api_key:
            raise SystemExit("GEMINI_API_KEY not set in .env")

        raw_articles = await gemini_search_raw()
        raw_path.write_text(
            json.dumps(raw_articles, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n💾 Saved: {raw_path}")

        if not settings.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY not set in .env")

        posts = await claude_analyze(raw_articles)
        posts_path.write_text(
            json.dumps(posts, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"💾 Saved: {posts_path}")

    results: list[dict] = []
    for i, post in enumerate(posts, 1):
        results.append(await generate_post_assets(i, post))

    if send:
        await send_to_telegram(posts, results)

    print_summary(raw_articles, posts, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gemini search → Claude decide → Gemini images → Telegram")
    parser.add_argument("--no-send", action="store_true", help="Skip Telegram delivery")
    parser.add_argument("--cache", action="store_true", help="Reuse output/RAW_ARTICLES.json + CLAUDE_POSTS.json")
    args = parser.parse_args()
    asyncio.run(main(send=not args.no_send, use_cache=args.cache))
