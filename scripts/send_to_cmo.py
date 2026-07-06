"""
Send existing pipeline posts to the CMO for approval — Claude-only, no Gemini.

Reia postările din ultima rulare (output/pipeline/step2_selected.json + post_N.jpg)
și le trimite pe Telegram cu butoane Approve/Reject. Caption-ul e verificat/rescris
de Claude (_ensure_caption) cu Brand DNA + regula EVENT CONTEXT.

Usage:
  python scripts/send_to_cmo.py                 # toate postările din ultima rulare
  python scripts/send_to_cmo.py spa birkdale    # doar cele care conțin substring-urile
  python scripts/send_to_cmo.py --rewrite spa   # forțează rescrierea caption-ului
"""
from __future__ import annotations

import argparse
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

from config import settings  # noqa: E402
from scripts.real_footage_pipeline import OUT  # noqa: E402

CONTACT_BLOCK = (
    "\n\nbuybusinessclass.com\n"
    "☎️ +1 888-322-7999 📩 deals@buybusinessclass.com"
)


def _ensure_caption(
    claude_client: anthropic.Anthropic, item: dict, force_rewrite: bool = False
) -> str:
    """Return a publish-ready Betty caption; rewrite via Claude when missing/forced."""
    existing = (item.get("caption") or "").strip()
    if existing and len(existing) > 80 and not force_rewrite:
        return existing

    from prompts.brand_dna import BBC_BRAND_DNA, BBC_CONTENT_SELECTION_CONTEXT

    resp = claude_client.messages.create(
        model=getattr(settings, "anthropic_model", "claude-sonnet-4-6"),
        max_tokens=900,
        system=BBC_BRAND_DNA + "\n\n" + BBC_CONTENT_SELECTION_CONTEXT,
        messages=[
            {
                "role": "user",
                "content": f"""Write the WhatsApp caption for this BuyBusinessClass.com post:

{json.dumps(item, ensure_ascii=False)}

BETTY FORMULA:
- Sensory opening line.
- For EVENT posts: right after the opening, ONE context sentence that
  tells a non-follower WHAT it is, WHEN, and WHY it matters (use the
  facts from 'details' — exact dates, one factual superlative). Anchor
  insider terms: 'the Claret Jug — golf's oldest trophy', never naked
  shorthand.
- Specific luxury details with real facts (names, numbers, dates).
- If the item has access data (access != null and complete), include ONE
  Getting There line AFTER the body, BEFORE the close:
  '✈️ Getting there: Nonstop JFK → Milan Malpensa (about 8 hours). ...'
  Use ONLY the provided access facts — if incomplete, OMIT the line.
- Quiet confident close. Soft business-class CTA.
- End with:{CONTACT_BLOCK}

Write ONLY the caption, nothing else.""",
            }
        ],
    )
    caption = resp.content[0].text.strip()
    if "888-322-7999" not in caption:
        caption += CONTACT_BLOCK
    return caption


async def send_post(item: dict, idx: int, campaign_base: str) -> bool:
    """Save + send one post with Approve/Reject buttons. Returns success."""
    import httpx

    from keyboards import review_keyboard
    from services.supabase_client import save_campaign, update_review_tracking, upload_image
    from services.telegram_client import approval_caption, send_approval_request

    cid = settings.telegram_chat_id
    campaign_id = f"{campaign_base}-CMO-{idx:03d}"

    img_path = item.get("image", "")
    image_url = None
    if img_path and Path(img_path).exists():
        try:
            image_url = await upload_image(
                Path(img_path).read_bytes(), f"deals/{campaign_id}/landscape.jpg"
            )
        except Exception as e:
            print(f"  ⚠️ Upload failed: {e}")

    campaign = {
        "campaign_id": campaign_id,
        "name": item.get("name", item.get("headline", "")),
        "event_name": item.get("headline", ""),
        "city": item.get("city", ""),
        "category": item.get("type", "news"),
        "image_url": image_url,
        "caption": item.get("caption", ""),
        "whatsapp_caption": item.get("caption", ""),
        "event_context": item.get("details", ""),
        "status": "draft",
    }
    await save_campaign(campaign)

    if image_url is None and img_path and Path(img_path).exists() and settings.telegram_bot_token:
        tg_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                tg_url,
                data={
                    "chat_id": str(cid),
                    "caption": approval_caption(campaign),
                    "parse_mode": "Markdown",
                    "reply_markup": json.dumps(review_keyboard(campaign_id)),
                },
                files={"photo": (Path(img_path).name, Path(img_path).read_bytes(), "image/jpeg")},
            )
            data = resp.json() if resp.status_code == 200 else {}
        result = {"chat_id": cid, "message_id": (data.get("result") or {}).get("message_id")}
    else:
        result = await send_approval_request(campaign, chat_id=cid)

    if result and result.get("message_id"):
        await update_review_tracking(campaign_id, result["chat_id"], result["message_id"])
        print(f"  ✅ Sent for approval: {campaign_id} — {item.get('headline', '')}")
        return True
    print(f"  ⚠️ {campaign_id}: no message_id returned")
    return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Re-send pipeline posts to CMO")
    parser.add_argument("filters", nargs="*", help="substring filters on name/headline")
    parser.add_argument("--rewrite", action="store_true", help="force Claude caption rewrite")
    args = parser.parse_args()

    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")
    if not settings.telegram_chat_id:
        raise SystemExit("TELEGRAM_CHAT_ID not set")

    selected_file = OUT / "step2_selected.json"
    if not selected_file.exists():
        raise SystemExit(
            f"❌ {selected_file} nu există — rulează întâi pipeline-ul "
            "(python scripts/run_betty_lux.py) ca să ai postări de trimis."
        )

    selected = json.loads(selected_file.read_text(encoding="utf-8"))

    posts: list[tuple[int, dict]] = []
    for i, item in enumerate(selected, 1):
        hay = f"{item.get('name', '')} {item.get('headline', '')}".lower()
        if args.filters and not any(f.lower() in hay for f in args.filters):
            continue
        img = OUT / f"post_{i}.jpg"
        if not img.exists():
            print(f"  ⚠️ Skip {item.get('headline', '?')} — lipsește {img.name}")
            continue
        item["image"] = str(img)
        posts.append((i, item))

    if not posts:
        raise SystemExit("❌ Niciun post de trimis (filtre prea stricte sau imagini lipsă).")

    print(f"📤 Sending {len(posts)} post(s) to CMO...\n")

    claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    campaign_base = datetime.now(UTC).strftime("%Y-W%W")

    for idx, (orig_i, item) in enumerate(posts, 1):
        item["caption"] = _ensure_caption(claude_client, item, force_rewrite=args.rewrite)
        (OUT / f"caption_{orig_i}.txt").write_text(item["caption"], encoding="utf-8")
        await send_post(item, idx, campaign_base)
        await asyncio.sleep(1)

    print(f"\n✅ Done — {len(posts)} post(s) sent for approval.")


if __name__ == "__main__":
    asyncio.run(main())
