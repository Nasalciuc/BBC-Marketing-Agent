"""
Assemble 5 curated posts: our finished images + BBC branding + pricing engine
+ final captions -> Telegram with Approve/Reject buttons. No generation, no search.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from config import settings  # noqa: E402
from prompts.system_prompts import CONTACT_BLOCK  # noqa: E402
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
ASSETS = ROOT / "assets" / "curated" / "week_2026_09_01"
OUT = ROOT / "output" / "pipeline"
OUT.mkdir(parents=True, exist_ok=True)
WEEK = datetime.now(UTC).strftime("%Y-W%W")

# Telegram photo captions are capped at 1024 chars — longer copy is sent as a follow-up.
TELEGRAM_CAPTION_LIMIT = 1024

CONTACT = "A specialist, not a booking form. Reply here or call.\n\n" + CONTACT_BLOCK


# ═══════════════════════════════════════
# THE 5 POSTS — image + branding fields + final caption
# ═══════════════════════════════════════

POSTS = [
    {
        "id": "monaco",
        "image": ASSETS / "01_monaco_yacht_show.jpg",
        "eyebrow": "MONACO",
        "headline": "Monaco. Four billion in one harbor.",
        "route_city": "NICE",
        "to_iata": "NCE",
        "city": "Monaco",
        "name": "Monaco Yacht Show 2026",
        "type": "event",
        "caption": """Four billion dollars, moored in one harbor.

Monaco Yacht Show, September 23 to 26. More than 120 superyachts
stern-to along Port Hercule, the largest past 100 meters. Around them,
the people who build them, design them and buy them — and the
watchmakers, helicopter builders and carmakers who follow this crowd
wherever it goes. Wednesday is by invitation. Then the quays open.

Everyone here flew in flat. Some paid list price.

Business class, New York to Nice. Board at 7pm. Sleep. Land at 8am.
Zero days lost — Monaco is 30 minutes from the runway.
The fare above is today's. Ask us for your dates.

"""
        + CONTACT,
    },
    {
        "id": "st_andrews",
        "image": ASSETS / "02_st_andrews.jpg",
        "eyebrow": "ST ANDREWS",
        "headline": "The Old Course. The 25th. October.",
        "route_city": "EDINBURGH",
        "to_iata": "EDI",
        "city": "St Andrews, Scotland",
        "name": "Alfred Dunhill Links Championship 2026",
        "type": "event",
        "caption": """In most years, the Old Course hosts exactly one professional
championship. This is it.

Alfred Dunhill Links, October 1 to 4 — the 25th edition, and a pro-am:
amateurs play the Old Course beside the professionals, on the same days.
Three courses — St Andrews, Carnoustie, Kingsbarns — then Sunday's final
on the Old Course itself: the Road Hole, the Valley of Sin, the Swilcan
Bridge, crossed by golfers since before the United States existed.

Four weeks away. One flight.

Business class, New York to Edinburgh. Board at 8pm. Sleep. Land at 8am.
Zero days lost — St Andrews is an hour from the runway.
The fare above is today's. Ask us for your dates.

"""
        + CONTACT,
    },
    {
        "id": "singapore",
        "image": ASSETS / "03_singapore_gp.jpg",
        "eyebrow": "SINGAPORE",
        "headline": "Singapore. The city is the circuit.",
        "route_city": "SINGAPORE",
        "to_iata": "SIN",
        "city": "Singapore",
        "name": "F1 Singapore Grand Prix 2026",
        "type": "event",
        "caption": """The race that put Formula 1 under the lights.

Singapore Grand Prix, October 9 to 11. Nineteen turns through downtown,
Marina Bay Sands as the backdrop, the whole city as the circuit. This
year, for the first time here, a Sprint weekend — racing on all three
days. Three hundred thousand people. Several ticket categories sold out
and were reopened. Hospitality above the pit lane is mostly gone.

If Asia is on your fourth quarter, make it this week.

Business class, New York to Singapore, nonstop. Board at 10pm. Sleep,
work, sleep again. Land at dawn, rested — the flight is your night,
not a lost day. Marina Bay is 20 minutes from the runway.
The fare above is today's. Ask us for your dates.

"""
        + CONTACT,
    },
    {
        "id": "alba",
        "image": ASSETS / "04_alba_truffle.jpg",
        "eyebrow": "PIEDMONT",
        "headline": "The one thing money can't grow.",
        "route_city": "MILAN",
        "to_iata": "MXP",
        "city": "Alba, Italy",
        "name": "Alba White Truffle Season 2026",
        "type": "destination",
        "caption": """White truffles cannot be farmed.

Every attempt has failed. What grows under the oaks of Piedmont this
autumn is decided by rain — not by anyone's money.

Alba, October 10 to December 6. The 96th season. Eight weeks a year,
then nothing until next October. Every truffle inspected by a jury
before the gates open. Barolo En Primeur poured at the fair.

Eight weeks. One flight away.

Business class, New York to Milan. Board at 7pm. Sleep. Land at 8am.
Zero days lost — the Langhe hills are an hour from the runway.
The fare above is today's. Ask us for your dates.

"""
        + CONTACT,
    },
    {
        "id": "vsoe",
        "image": ASSETS / "05_vsoe_orient_express.jpg",
        "eyebrow": "VENICE",
        "headline": "Black tie. One night. No WiFi.",
        "route_city": "PARIS",
        "to_iata": "CDG",
        "city": "Paris, France",
        "name": "Venice Simplon-Orient-Express 2026",
        "type": "destination",
        "caption": """The only train you pack a tuxedo for.

Venice Simplon-Orient-Express. The carriages are the originals — 1920s,
restored, Lalique glass in the dining car. Dinner is black tie, and they
will politely turn you away in jeans. A pianist plays the bar car until
well past midnight. There is no WiFi anywhere on board. Paris to Venice
is one night; the season runs to December 20. The Grand Suites sell out
more than a year ahead. The rest, months.

One night you sleep through. One night you dress for.

Business class, New York to Paris. Board at 7pm. Sleep. Land at 8am.
The train leaves that afternoon. You wake up in the Alps.
The fare above is today's. Ask us for your dates.

"""
        + CONTACT,
    },
]


def build_post(index: int, post: dict) -> dict | None:
    """Price + branding + caption file. Returns campaign dict or None on failure."""
    if not post["image"].exists():
        print(f"   [X] Image missing: {post['image']} — SKIP")
        return None

    # 1) Price from the company's pricing engine (never invented)
    price = calculate_price("JFK", post["to_iata"])
    if price is None:
        print(f"   [X] No price for JFK->{post['to_iata']} — SKIP")
        return None
    price_str = format_price(price)
    print(f"   Price JFK->{post['to_iata']}: {price_str}")

    # 2) Branding on OUR image
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
    )
    out_path = OUT / f"post_{index}.jpg"
    out_path.write_bytes(img_bytes)
    print(f"   Branded: {out_path.name} ({len(img_bytes) // 1024}KB)")

    caption = post["caption"]
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
    }


async def main(send: bool = True):
    print("=" * 55)
    print("  ASSEMBLE — 5 curated posts -> branding -> Telegram")
    print("=" * 55)

    campaigns = []
    for i, post in enumerate(POSTS, 1):
        print(f"\n{i}. {post['headline']}")
        campaign = build_post(i, post)
        if not campaign:
            continue

        try:
            image_url = await upload_image(
                Path(campaign["image_path"]).read_bytes(),
                f"deals/{campaign['campaign_id']}/landscape.jpg",
            )
            if image_url:
                campaign["image_url"] = image_url
        except Exception as e:
            print(f"   [!] upload: {e}")

        await save_campaign(campaign)
        save_campaign_local(campaign)
        campaigns.append(campaign)

    if not send:
        print(f"\n[dry-run] {len(campaigns)}/5 built. Nothing sent.")
        return

    cid = settings.telegram_chat_id
    await send_message(
        chat_id=cid,
        text="━━━━━━━━━━━━━━━━━━━━━\n📰 *This Week — 5 Posts*\n_Review each below_\n━━━━━━━━━━━━━━━━━━━━━",
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
                text=f"📄 Full caption — {campaign_id}\n\n{campaign['caption']}",
                parse_mode="",
            )
        await asyncio.sleep(1)

    await send_message(
        chat_id=cid,
        text=f"━━━━━━━━━━━━━━━━━━━━━\n✅ *{sent}/5 posts ready*\n\n"
        "Tap *Approve* on the keepers. *Edit caption* to change text.",
    )
    print(f"\n{sent}/5 sent with buttons.")


if __name__ == "__main__":
    asyncio.run(main(send="--no-send" not in sys.argv))
