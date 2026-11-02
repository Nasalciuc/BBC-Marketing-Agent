"""
W42 · 14–18 October 2026 — five posts on design system v4.

Takes the five finished photos from assets/curated/week_2026_10_12/, puts the v4 brand layer on each
(logo, headline, route line, boarding pass with today's fare from the pricing engine, tone picked from
the photo) and writes the final caption. Only with --send does it upload the posts, save the campaigns
and send them to Telegram for approval.

  python scripts/assemble_week_w42.py --check               photos present, size, 9:16 (no render)
  python scripts/assemble_week_w42.py --render              5 finished posts + captions in output/w42/
  railway run python scripts/assemble_week_w42.py --send    upload + campaigns + Telegram (Approve / Reject / Edit)

The approval message has no "Regenerate" button on purpose: handlers/review.py would replace the photo
with a generic Gemini image in the legacy 1200x628 layout.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

from prompts.system_prompts import CONTACT_LINE_LUX
from services.design_system import (
    DesignQAError,
    PostDesign,
    Style,
    Trip,
    caption_ending,
    render_post,
    validate_week_mix,
)
from services.pricing_engine import format_price

ROOT = Path(__file__).resolve().parent.parent
PHOTOS = ROOT / "assets" / "curated" / "week_2026_10_12"
OUT = ROOT / "output" / "w42"
WEEK = "2026-W42"        # ISO week of 14–18 Oct — fixed, never computed from the run date
LAST_DAY = date(2026, 10, 18)   # after it, --send is refused: the week is history
ID_TAG = "V4"            # ids 2026-W42-V4-001…005 cannot collide with the legacy "%Y-W%W-CUR-00n" ids
CAPTION_LIMIT = 1024     # Telegram photo caption, including the "📋 campaign id" line
EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP")
CATEGORY = {Style.FACT: "news", Style.OFFER: "event", Style.EDITORIAL: "destination", Style.PRICE_HERO: "deal"}

# Publishing order. "photo" is the file name in PHOTOS without extension.
POSTS: list[dict] = [
    {
        "day": "Wed 14 Oct", "photo": "singapore_airlines", "label": "Singapore Airlines",
        "name": "Singapore Airlines — World's Best Airline 2026 (Skytrax)", "city": "Singapore",
        "caption_city": "Singapore", "route": "New York → Singapore",
        "design": {"style": Style.FACT, "lead": "Six times", "follow": "the world’s best airline.",
                   "line": "Singapore Airlines · Skytrax 2026"},
        "body": """Named the world's best airline last month. For the sixth time.

In the 2026 Skytrax World Airline Awards, more than 24 million travellers rated over 300 airlines. Singapore Airlines came first, and was also named the Best Airline in Asia.

The longest nonstop flight in the world, with the airline that does it best. And on this aircraft there is no economy class: sixty-seven business seats, and nothing below premium economy.

Business class, New York to Singapore, nonstop. Leave at ten at night. Dinner, then a full night in a flat bed. Land before sunrise.""",
    },
    {
        "day": "Thu 15 Oct", "photo": "beaune", "label": "Hospices de Beaune",
        "name": "Hospices de Beaune wine auction 2026", "city": "Beaune, France",
        "caption_city": "Paris", "route": "JFK → CDG", "dates_start": "2026-11-15", "dates_end": "2026-11-15",
        "design": {"style": Style.OFFER, "headline": "Beaune. One weekend. All of Burgundy.",
                   "tag": "Hospices de Beaune · 15 Nov", "to_iata": "CDG"},
        "body": """In Beaune, one barrel is still sold the old way: the bidding ends when the candles burn out.

The 166th Hospices de Beaune wine auction, Sunday 15 November, under the Halles de Beaune, with Sotheby's. Held since 1859 for the hospital founded here in 1443: barrels from the Hospices' own vineyards, most of them Premiers and Grands Crus, bid for by collectors from Europe, Asia and America; last year the average barrel went for 34,000 euros. Around it, Burgundy's great weekend: the Tastevin dinner at the Clos de Vougeot on Saturday, the sale on Sunday, the Paulée de Meursault on Monday. Les Trois Glorieuses.

A month away. One flight.

Business class, New York to Paris. Leave at 6:30pm. Sleep. Land at 8am, and be in Beaune for a late lunch: about two hours from Paris by fast train. The cellars pour all weekend.""",
    },
    {
        "day": "Fri 16 Oct", "photo": "cape_town", "label": "Cape Town",
        "name": "Cape Town — summer from November", "city": "Cape Town",
        "caption_city": "Cape Town", "route": "New York → Cape Town",
        "design": {"style": Style.EDITORIAL, "big_headline": True, "headline": "Cape Town. Summer, a night away.",
                   "line": "Business class from New York · November"},
        "body": """While New York turns cold, Cape Town turns to summer.

November on the Cape: light until almost eight in the evening, Table Mountain above the city, a house on the rocks at Clifton with the Atlantic below it, and lunch in the vines at Stellenbosch, under an hour away. The morning call in New York is your afternoon here. Thanksgiving week, on the Cape, is summer.

One night. One flight.

Business class, New York to Cape Town, nonstop from Newark. Leave at half past eight in the evening. Sleep. Land the next day before sunset.""",
    },
    {
        "day": "Sat 17 Oct", "photo": "sao_paulo", "label": "F1 São Paulo",
        "name": "F1 São Paulo Grand Prix 2026", "city": "São Paulo",
        "caption_city": "São Paulo", "route": "JFK → GRU", "dates_start": "2026-11-06", "dates_end": "2026-11-08",
        "design": {"style": Style.OFFER, "headline": "Interlagos. Senna's\u00a0home. The final stretch.",  # no break inside "Senna's home"
                   "tag": "F1 São Paulo · 6–8 Nov", "to_iata": "GRU"},
        "body": """Ayrton Senna won here twice. The first time, he finished with only sixth gear left.

The São Paulo Grand Prix, Interlagos, 6 to 8 November: South America's only race of the season, and one of the last four. Seventy-one laps on Sunday at two in the afternoon, a circuit that dives downhill into the Senna S, and a crowd that turns the bowl into a wall of sound. On Sunday, the helicopter from Faria Lima leaves at eleven and puts you down beside the circuit.

Three weeks away. One flight.

Business class, New York to São Paulo, nonstop. Leave at ten at night. Sleep. Land by mid-morning. Zero days lost — and one of the lowest long-haul business fares we have this season.""",
    },
    {
        "day": "Sun 18 Oct", "photo": "europe", "label": "Europe one way",
        "name": "Europe one way — Paris, London, Rome", "city": "Paris, London, Rome",
        "caption_city": "Paris", "route": "JFK → CDG · LHR · FCO (one way)",
        "design": {"style": Style.PRICE_HERO, "fare_iatas": ["CDG", "LHR", "FCO"], "trip": Trip.ONE_WAY,
                   "follow": "One way to Paris, London or Rome.", "line": "Business class from New York"},
        "body": """Not every trip needs a return date.

One-way business class from New York to Europe. The fare above is the lowest of the three; the other two are close behind. For the trip that starts as a meeting and ends as a week in the south of France, for the long stay, for the second leg you haven't planned yet.

One seat. One direction. Your timing.

Business class, New York to Paris. Leave at 6:30pm. Sleep. Land at 8am. Breakfast in Paris — and come home whenever you're ready.""",
    },
]


# ── helpers ───────────────────────────────────────────────────────────────────

def photo_path(name: str) -> Path | None:
    for ext in EXTENSIONS:
        p = PHOTOS / f"{name}{ext}"
        if p.exists():
            return p
    return None


def campaign_id(i: int) -> str:
    return f"{WEEK}-{ID_TAG}-{i:03d}"


def out_stem(post: dict) -> str:
    return f"{post['day'][:3].lower()}_{post['photo']}"          # wed_singapore_airlines …


def compose_caption(post: dict) -> str:
    """Approved body + the style's fare line + the contact line. The fare itself lives only on the image."""
    style = post["design"]["style"]
    return post["body"].rstrip() + "\n\n" + caption_ending(style, post["caption_city"]) + "\n\n" + CONTACT_LINE_LUX


def caption_problems(caption: str, cid: str) -> list[str]:
    problems = []
    if "$" in caption:
        problems.append("contains '$' (the fare belongs on the image)")
    with_id = f"{caption}\n\n📋 {cid}"                         # what telegram_client.approval_caption sends
    if len(with_id) > CAPTION_LIMIT:
        problems.append(f"{len(with_id)} chars with the campaign line (max {CAPTION_LIMIT})")
    markdown = sorted({c for c in caption if c in "_*`["})
    if markdown:
        problems.append(f"characters {markdown} break Telegram parse_mode=Markdown")
    return problems


def check_photos() -> list[str]:
    errors = []
    print(f"Photos — {PHOTOS.relative_to(ROOT)}/")
    for post in POSTS:
        p = photo_path(post["photo"])
        if p is None:
            errors.append(f"{post['photo']}.jpg missing")
            print(f"  ✗ {post['day']:<11} {post['photo']}.jpg — MISSING")
            continue
        with Image.open(p) as im:
            w, h = im.size
        notes = []
        if w < 1080 or h < 1920:
            notes.append("small, under 1080×1920 — will look soft")
        ratio = (w / h) / (9 / 16)
        if abs(ratio - 1) > 0.03:
            notes.append("not 9:16 — the crop cuts " + ("the sides" if ratio > 1 else "top and bottom"))
        print(f"  {'✓' if not notes else '!'} {post['day']:<11} {p.name:<26} {w}×{h}  {'; '.join(notes)}")
    return errors


# ── render ────────────────────────────────────────────────────────────────────

def render_all() -> list[dict]:
    issues = validate_week_mix([p["design"]["style"] for p in POSTS])
    if issues:
        raise SystemExit("STOP — week mix: " + "; ".join(issues))
    OUT.mkdir(parents=True, exist_ok=True)
    print("\nRender — design v4")
    rendered = []
    for i, post in enumerate(POSTS, 1):
        cid = campaign_id(i)
        caption = compose_caption(post)
        problems = caption_problems(caption, cid)
        if problems:
            raise SystemExit(f"STOP — caption {post['photo']}: " + "; ".join(problems))
        design = PostDesign(background=str(photo_path(post["photo"])), **post["design"])
        try:
            res = render_post(design, strict=True)
        except DesignQAError as e:
            raise SystemExit(f"STOP — design QA on {post['photo']}: {e}") from None
        stem = out_stem(post)
        (OUT / f"{stem}.jpg").write_bytes(res.jpeg)
        (OUT / f"{stem}.txt").write_text(caption, encoding="utf-8")
        fare = format_price(res.fare) if res.fare else "—"
        print(f"  ✓ {post['day']:<11} {design.style.value:<10} tone {res.tone.value:<5} fare {fare:<7} "
              f"text ends at {res.metrics['textBottom'] / 1920:.0%}  caption {len(caption)}  → output/w42/{stem}.jpg")
        rendered.append({"post": post, "cid": cid, "design": design, "res": res, "caption": caption, "stem": stem})
    _preview(rendered)
    return rendered


def _preview(rendered: list[dict]) -> None:
    """All five side by side, in publishing order — one image to check the week at a glance."""
    tw, th, gap, label = 432, 768, 24, 60
    sheet = Image.new("RGB", (gap + len(rendered) * (tw + gap), gap + th + label), (11, 24, 41))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(str(ROOT / "assets" / "fonts" / "PlayfairDisplay.ttf"), 26)
    except OSError:
        font = ImageFont.load_default()
    for k, r in enumerate(rendered):
        x = gap + k * (tw + gap)
        with Image.open(OUT / f"{r['stem']}.jpg") as im:
            sheet.paste(im.convert("RGB").resize((tw, th), Image.LANCZOS), (x, gap))
        draw.text((x, gap + th + 16), f"{r['post']['day']} · {r['post']['label']}", fill=(201, 165, 78), font=font)
    sheet.save(OUT / "_preview_w42.jpg", quality=90)
    print("\nPreview of the week → output/w42/_preview_w42.jpg")


# ── send (upload → campaigns → Telegram) ──────────────────────────────────────

def _keyboard(cid: str) -> dict:
    """review_keyboard() without 🔄 Regenerate (it would swap in a generic image in the old layout)."""
    from keyboards import review_keyboard

    rows = [[b for b in row if not b["callback_data"].startswith("regen_")]
            for row in review_keyboard(cid)["inline_keyboard"]]
    return {"inline_keyboard": [row for row in rows if row]}


def _preflight():
    """Fail loudly before rendering: the Supabase client silently returns None without keys."""
    from config import settings
    from services.supabase_client import _get_client

    client = _get_client()
    if client is None:
        raise SystemExit("STOP — Supabase not configured. Run: railway run python scripts/assemble_week_w42.py --send")
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise SystemExit("STOP — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing. Run it with railway run.")
    try:
        client.table("campaigns").select("design_style, design_tone").limit(1).execute()
    except Exception as e:  # noqa: BLE001 — any failure here means the two columns are not usable
        raise SystemExit(f"STOP — campaigns.design_style / design_tone missing: apply migrations/009 first ({e})") from None
    return client, settings


async def send_all(rendered: list[dict], client, settings, force: bool) -> None:
    from services.campaign_store import save_campaign_local
    from services.supabase_client import (
        save_campaign,
        update_review_tracking,
        upload_image,
    )
    from services.telegram_client import (
        approval_caption,
        send_message,
        send_photo,
        send_photo_file,
    )

    chat_id = settings.telegram_chat_id
    chat_key = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    print("\nSend — upload, campaigns, Telegram")
    ready = []
    for r in rendered:
        cid, post, res, style = r["cid"], r["post"], r["res"], r["design"].style
        found = client.table("campaigns").select("status, review_message_id").eq("campaign_id", cid).execute().data
        if found and found[0].get("status") in ("approved", "sent", "posted", "rejected", "cancelled"):
            print(f"  = {cid} is {found[0]['status']} — never overwritten")
            continue
        if found and found[0].get("review_message_id") and not force:
            print(f"  = {cid} already waiting for approval — skipped (use --force to resend)")
            continue
        url = await upload_image(res.jpeg, f"deals/{cid}/vertical.jpg")
        if not url:
            raise SystemExit(f"STOP — upload failed for {cid} (see the log line above)")
        campaign = {
            "campaign_id": cid,
            "name": post["name"],
            "city": post["city"],
            "dates_start": post.get("dates_start"),
            "dates_end": post.get("dates_end"),
            "category": CATEGORY[style],
            "route_str": post["route"],
            "price": format_price(res.fare) if res.fare else None,
            "price_raw": res.fare,
            "image_url": url,
            "image_path": str(OUT / f"{r['stem']}.jpg"),
            "caption": r["caption"],
            "whatsapp_caption": r["caption"],
            "event_context": f"{WEEK} · publish {post['day']} 2026 · design v4 {style.value}",
            "status": "draft",
            "scheduled_day": post["day"][:3],          # Wed…Sun, same convention as W38 (ignored if the column is absent)
            "design_style": style.value,
            "design_tone": res.tone.value,
        }
        if not await save_campaign(campaign):
            raise SystemExit(f"STOP — save_campaign failed for {cid} (see the log line above)")
        save_campaign_local(campaign)
        ready.append((post, campaign))
        print(f"  ↑ {cid}  {post['day']}  saved, image {url}")

    if not ready:
        print("Nothing new to send.")
        return

    schedule = "\n".join(f"{p['day']} · {p['label']} · {c['campaign_id']}" for p, c in ready)
    await send_message(chat_id=chat_id, text="━━━━━━━━━━━━━━━━━━━━━\n📰 *W42 · design v4*\n━━━━━━━━━━━━━━━━━━━━━\n" + schedule)
    sent = 0
    for post, campaign in ready:
        cid = campaign["campaign_id"]
        caption, kb = approval_caption(campaign), _keyboard(cid)
        result = await send_photo(chat_id=chat_id, photo_url=campaign["image_url"], caption=caption, reply_markup=kb)
        message_id = ((result or {}).get("result") or {}).get("message_id")
        if not message_id:  # Telegram could not fetch the URL → send the file itself
            result = await send_photo_file(chat_id=chat_id, photo_path=campaign["image_path"], caption=caption, reply_markup=kb)
            message_id = ((result or {}).get("result") or {}).get("message_id")
        if not message_id:
            print(f"  [!] Telegram refused {cid}: {result}")
            continue
        await update_review_tracking(cid, chat_key, message_id)
        sent += 1
        print(f"  → Telegram  {cid}  {post['day']}  message {message_id}")
        await asyncio.sleep(1)
    await send_message(chat_id=chat_id, text=f"✅ *{sent}/{len(ready)} posts ready*\n\nApprove the keepers. Edit caption to change the text.")
    print(f"\n{sent}/{len(ready)} sent for approval.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="photos present, size, 9:16")
    mode.add_argument("--render", action="store_true", help="finished posts + captions in output/w42/, nothing sent")
    mode.add_argument("--send", action="store_true", help="render, upload, save campaigns, send to Telegram")
    ap.add_argument("--force", action="store_true", help="resend posts already waiting for approval (never approved ones)")
    args = ap.parse_args()
    if args.send and date.today() > LAST_DAY:
        raise SystemExit(f"STOP — W42 ended on {LAST_DAY:%d %B}: --send would put old posts up for approval again. "
                         "Weeks are sent with `python -m content_system send weeks/<week>.toml` now.")

    errors = check_photos()
    if errors:
        raise SystemExit("STOP — " + "; ".join(errors))
    if args.check:
        return
    if args.render:
        render_all()
        return
    client, settings = _preflight()          # before rendering: fail fast without keys or migration 009
    rendered = render_all()                  # Playwright sync API — must run outside the asyncio loop
    asyncio.run(send_all(rendered, client, settings, args.force))


if __name__ == "__main__":
    main()
