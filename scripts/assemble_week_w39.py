"""
W39: 7 vertical photo posts → branding → Supabase → Telegram with buttons.
No generation, no search, no video.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio  # noqa: E402

from config import settings  # noqa: E402
from prompts.system_prompts import CONTACT_LINE_LUX  # noqa: E402
from services.branding_engine import generate_branded_image  # noqa: E402
from services.campaign_store import save_campaign_local  # noqa: E402
from services.pricing_engine import calculate_price, format_price  # noqa: E402
from services.supabase_client import (  # noqa: E402
    save_campaign,
    update_review_tracking,
    upload_image,
)
from services.telegram_client import send_approval_request, send_message  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets" / "curated" / "week_2026_09_21"
OUT = ROOT / "output" / "pipeline_w39"
OUT.mkdir(parents=True, exist_ok=True)
WEEK = "2026-W39"

TELEGRAM_CAPTION_LIMIT = 1024

POSTS = [
    {
        "id": "monaco",
        "day": "Mon",
        "image": ASSETS / "01_monaco_night.jpg",
        "eyebrow": "MONACO",
        "headline": "Monaco. Wednesday is by invitation.",
        "route_city": "NICE",
        "to_iata": "NCE",
        "city": "Monaco",
        "name": "Monaco Yacht Show 2026 — opening night",
        "type": "event",
        "caption": """Wednesday is by invitation. Then the quays open.

Monaco Yacht Show, 23 to 26 September. More than 120 superyachts stern-to along Port Hercule, the largest past 100 metres, and the opening night on Tuesday when the harbour lights come on and the tenders start crossing. Wednesday is invitation only. Thursday to Saturday, the quays open to the rest of us.

This week. One flight.

Business class, New York to Nice. Board tonight at 7pm. Sleep. Land at 8am.
Zero days lost — Monaco is 30 minutes from the runway, or seven by helicopter.
The fare above is today's. Ask us for your dates.""",
    },
    {
        "id": "voiles",
        "day": "Tue",
        "image": ASSETS / "02_voiles_saint_tropez.jpg",
        "eyebrow": "SAINT-TROPEZ",
        "headline": "Saint-Tropez. Nine days. Only sail.",
        "route_city": "NICE",
        "to_iata": "NCE",
        "city": "Saint-Tropez",
        "name": "Les Voiles de Saint-Tropez 2026",
        "type": "event",
        "caption": """It started with a bet between two boats. The loser bought lunch.

Les Voiles de Saint-Tropez, through Sunday 4 October. Every autumn the motor yachts that fill the port all summer slide out of their berths — and for nine days the gulf belongs to sail: 253 boats, century-old gaff schooners, the J-Class yachts that once raced for the America's Cup, their masts higher than the rooftops. Starts from the old port at eleven. The Rolex Trophy on the 29th and 30th, the classic fleet's last race on Saturday, prizes on the quay on Sunday.

The last weekend. One flight.

Business class, New York to Nice. Board Thursday at 7pm. Sleep. Land at 8am.
Zero days lost — the gulf is ninety minutes down the coast, twenty-five by helicopter.
The fare above is today's. Ask us for your dates.""",
    },
    {
        "id": "arc",
        "day": "Wed",
        "image": ASSETS / "03_arc_longchamp.jpg",
        "eyebrow": "PARIS",
        "headline": "Paris. Longchamp. Five million on Sunday.",
        "route_city": "PARIS",
        "to_iata": "CDG",
        "city": "Paris",
        "name": "Prix de l'Arc de Triomphe 2026",
        "type": "event",
        "caption": """Five million euros, decided in about two and a half minutes.

The Prix de l'Arc de Triomphe, Sunday 4 October, Longchamp. Europe's richest race, run on the first Sunday of October since 1920. Daryz returns to defend the Aga Khan family's eighth win — a record — against Forever Young, Japan's two-time Saudi Cup champion. Saturday carries two Group 1 races of its own. Parade of champions at 12:50. The race at 16:05.

Eleven days away. One flight.

Business class, New York to Paris. Board Friday at 7pm. Sleep. Land at 8am.
Zero days lost — Longchamp is twenty-five minutes from the Champs-Élysées, and you're home Monday night. The fare above is today's. Ask us for your dates.""",
    },
    {
        "id": "danieli",
        "day": "Thu",
        "image": ASSETS / "04_danieli_venice.jpg",
        "eyebrow": "VENICE",
        "headline": "Venice. The Danieli. Open again.",
        "route_city": "MILAN",
        "to_iata": "MXP",
        "city": "Venice",
        "name": "Danieli, Venezia, A Four Seasons Hotel — reopening",
        "type": "destination",
        "caption": """Dickens stayed here. So did Wagner, Proust and Byron. It closed for four years. It's open.

The Danieli, Venice — reopened on 26 August as a Four Seasons after a four-year restoration. Three palaces joined by bridges on the Riva degli Schiavoni, between the Doge's Palace and the Bridge of Sighs, a position no other hotel in the city has. A golden staircase through a four-storey atrium of pink marble and Murano glass. A rooftop that looks straight across the lagoon to San Giorgio. A hundred and twenty rooms this year. Venice is at its best after dark, and October is the last of the warm nights.

One flight, one water taxi.

Business class, New York to Milan. Board at 7pm. Sleep. Land at 8am.
Zero days lost — Venice is two hours away, the last twenty minutes by water.
The fare above is today's. Ask us for your dates.""",
    },
    {
        "id": "baycurrent",
        "day": "Fri",
        "image": ASSETS / "05_baycurrent_yokohama.jpg",
        "eyebrow": "TOKYO",
        "headline": "Yokohama. The PGA Tour's one week in Asia.",
        "route_city": "TOKYO",
        "to_iata": "HND",
        "city": "Yokohama",
        "name": "Baycurrent Classic 2026 (PGA Tour)",
        "type": "event",
        "caption": """The PGA Tour plays one week a year in Asia. This is it.

The Baycurrent Classic, Yokohama Country Club, 8 to 11 October — the only PGA Tour event on the continent. Eight million dollars, seventy-eight players, a Coore & Crenshaw course thirty minutes from Tokyo. This is the tournament where Tiger Woods won his eighty-second title in 2019 and tied Sam Snead, and where Hideki Matsuyama won at home the year he took the Masters. The galleries are Japanese: you can hear the swing.

Tokyo has more Michelin stars than any city on earth. Make it a week.

Business class, New York to Haneda, nonstop. Board Saturday at 11am. Sleep, work, sleep again. Land Sunday rested — the flight is your night, not a lost day. Meetings Monday to Wednesday, the first tee on Thursday. The fare above is today's. Ask us for your dates.""",
    },
    {
        "id": "basel",
        "day": "Sat",
        "image": ASSETS / "06_basel_swiss_indoors.jpg",
        "eyebrow": "BASEL",
        "headline": "Basel. The ball boy who won it ten times.",
        "route_city": "ZURICH",
        "to_iata": "ZRH",
        "city": "Basel",
        "name": "Swiss Indoors Basel 2026",
        "type": "event",
        "caption": """He was a ball boy here in the early nineties. Then he won it ten times.

The Swiss Indoors, Basel, 24 October to 1 November — Roger Federer's hometown tournament, Switzerland's biggest sporting event, the last stop before the ATP Finals. Sixty thousand people in the St. Jakobshalle. The first prize, in 1970, was a wristwatch; it is Switzerland. Last year a nineteen-year-old won it, the second teenager to do so after Jim Courier.

Four weeks away. One flight.

Business class, New York to Zurich. Board at 7pm. Sleep. Land at 8am.
Zero days lost — Basel is an hour by train along the Rhine, every thirty minutes.
The fare above is today's. Ask us for your dates.""",
    },
    {
        "id": "suite",
        "day": "Sun",
        "image": ASSETS / "07_flagship_suite_zurich.jpg",
        "eyebrow": "ZURICH",
        "headline": "The door slides shut at 8pm. It opens over the Alps.",
        "route_city": "ZURICH",
        "to_iata": "ZRH",
        "city": "Zurich",
        "name": "American Flagship Suite — New York to Zurich",
        "type": "cabin",
        "caption": """The door slides shut at 8pm. It opens over the Alps.

American's new Flagship Suite — a fully enclosed pod with a sliding door, a flat bed and direct aisle access — is scheduled on New York to Zurich since 3 September. Fifty-one suites on the 787-9, eight of them the larger Flagship Suite Preferred at the front. In a year when United's new Polaris arrived in April and Delta's next suite waits for 2027, this is the newest door on the Zurich run.

Eight hours. One door. No one in the aisle.

Business class, New York to Zurich. Board Friday at 8pm. Sleep. Land at 10:30 over the lake.
Zero days lost — Basel is an hour by train, the Swiss Indoors from 24 October; or Zurich itself, the Dolder above the city, the lake below it. The fare above is today's. Ask us for your dates.""",
    },
]


def build_post(index: int, post: dict) -> dict | None:
    """Price + branding + caption file. Returns campaign dict or None on failure."""
    if not post["image"].exists():
        print(f"   [X] Image missing: {post['image']} — SKIP")
        return None

    price = calculate_price("JFK", post["to_iata"])
    if price is None:
        print(f"   [X] No price for JFK->{post['to_iata']} — SKIP")
        return None
    price_str = format_price(price)
    print(f"   Price JFK->{post['to_iata']}: {price_str}")

    img_bytes = generate_branded_image(
        event_name=post["eyebrow"],
        route=f"Business Class To {post['route_city']}",
        price=f"from {price_str}",
        background_url_or_path=str(post["image"]),
        badge_text=post["eyebrow"],
        subtitle=f"Business Class to {post['route_city'].title()}",
        headline=post["headline"],
        urgency=f"Limited {post['route_city'].title()} season fares",
        cta="Check available seats",
        cta_url="buybusinessclass.com",
        orientation="vertical",
    )
    out_path = OUT / f"post_{index}.jpg"
    out_path.write_bytes(img_bytes)
    print(f"   Branded: {out_path.name} ({len(img_bytes) // 1024}KB)")

    caption = post["caption"].rstrip() + "\n\n" + CONTACT_LINE_LUX
    (OUT / f"caption_{index}.txt").write_text(caption, encoding="utf-8")
    if len(caption) > TELEGRAM_CAPTION_LIMIT:
        print(f"   Caption {len(caption)} chars — full text sent as follow-up")

    return {
        "campaign_id": f"{WEEK}-CUR-{index:03d}",
        "name": post["name"],
        "event_name": post["headline"],
        "city": post["city"],
        "category": post["type"],
        "route_str": f"JFK → {post['to_iata']}",
        "price": price_str,
        "price_raw": price,
        "image_path": str(out_path),
        "caption": caption,
        "whatsapp_caption": caption,
        "status": "draft",
        "scheduled_day": post["day"],
    }


async def main(send: bool = True):
    print("=" * 55)
    print("  ASSEMBLE W39 — 7 vertical posts -> branding -> Telegram")
    print("=" * 55)

    campaigns = []
    for i, post in enumerate(POSTS, 1):
        print(f"\n{i}. {post['day']} · {post['headline']}")
        campaign = build_post(i, post)
        if not campaign:
            continue

        try:
            image_url = await upload_image(
                Path(campaign["image_path"]).read_bytes(),
                f"deals/{campaign['campaign_id']}/vertical.jpg",
            )
            if image_url:
                campaign["image_url"] = image_url
        except Exception as e:
            print(f"   [!] upload: {e}")

        await save_campaign(campaign)
        save_campaign_local(campaign)
        campaigns.append(campaign)

    if not send:
        print(f"\n[dry-run] {len(campaigns)}/7 built. Nothing sent.")
        return

    cid = settings.telegram_chat_id
    await send_message(
        chat_id=cid,
        text="━━━━━━━━━━━━━━━━━━━━━\n📰 *Week 39 — 7 posts, Mon→Sun*\n_Vertical. Review each below._\n━━━━━━━━━━━━━━━━━━━━━",
    )

    sent = 0
    for campaign in campaigns:
        campaign_id = campaign["campaign_id"]
        res = await send_approval_request(campaign, chat_id=cid)
        if res and res.get("message_id"):
            await update_review_tracking(campaign_id, res["chat_id"], res["message_id"])
            sent += 1
            print(f"   Sent: {campaign_id}")
        else:
            print(f"   [!] No message_id for {campaign_id}")

        if len(campaign["caption"]) > TELEGRAM_CAPTION_LIMIT:
            await send_message(
                chat_id=cid,
                text=f"Full caption — {campaign_id}\n\n{campaign['caption']}",
                parse_mode="",
            )
        await asyncio.sleep(1)

    await send_message(
        chat_id=cid,
        text=f"━━━━━━━━━━━━━━━━━━━━━\n✅ *{sent}/7 posts ready*\n\n"
        "Tap *Approve* on the keepers. *Edit caption* to change text.",
    )
    print(f"\n{sent}/7 sent with buttons.")


if __name__ == "__main__":
    asyncio.run(main(send="--no-send" not in sys.argv))
