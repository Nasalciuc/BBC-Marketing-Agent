"""
5 video+text posts for this week.
All topics discovered fresh by Gemini (zero hardcoded).
Format: VIDEO clip (4K multi-source) + caption + Approve/Reject buttons.
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
from services.campaign_store import save_campaign_local  # noqa: E402
from services.telegram_client import send_approval_request, send_message  # noqa: E402
from services.video_processor import (  # noqa: E402
    brand_video,
    concat_videos,
    normalize_to_landscape,
    probe_video_stream,
    trim_video,
    video_duration,
)
from services.gemini_client import (  # noqa: E402
    analyze_video_file,
    analyze_youtube_video,
    find_pinterest_videos,
)
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

# Săptămâna asta: TOATE subiectele descoperite proaspăt (zero hardcodat)
EXISTING: list[dict] = []


# ═══════════════════════════════════════
# STEP 1 — Find 5 FRESH topics (Gemini)
# ═══════════════════════════════════════

def step1_find_new_topics(gemini: genai.Client) -> list[dict]:
    print(f"\n🔍 STEP 1 — Finding 5 fresh topics ({TODAY})...\n")

    prompt = f"""Today is {TODAY}. Search the web for 5 CURRENT premium travel topics
for businessmen 45+ (executives, entrepreneurs).
Cover THIS WEEK's fresh angles. Prefer subjects with official brand video
or strong cinematic footage available (YouTube or Pinterest).

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

Set access: null for cabin reveals. Return JSON array, exactly 5 items — mix: cabin + destination + sports event + hotel/lounge + one more. No markdown fences."""

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
    (OUT / "step1_findings.json").write_text(
        json.dumps(new_items, indent=2, ensure_ascii=False), encoding="utf-8"
    )
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

_JUNK_TITLE_WORDS = (
    "preview |",
    "power rankings",
    "analysts",
    "can i break",
    "golfers that can win",
    "needs an open",
    "walking tour",
    "how to walk",
    "my trip",
    "i tried",
    "honest review",
    "real look inside",
)


def _bad_title(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in _JUNK_TITLE_WORDS)


def _collect_candidates(queries: list[str], limit: int = 5) -> list[dict]:
    candidates: list[dict] = []
    for q in queries:
        for v in search_youtube(q, max_results=3):
            if v["url"] in {c["url"] for c in candidates}:
                continue
            if _bad_title(v.get("title", "")):
                continue
            candidates.append(v)

    if queries:
        pins = find_pinterest_videos(queries[0], max_results=3)
        for p in pins:
            if p["url"] not in {c["url"] for c in candidates}:
                candidates.append(p)

    candidates.sort(
        key=lambda v: (
            0 if _is_official_channel(v.get("uploader", "")) else 1,
            -(v.get("view_count") or 0),
        )
    )
    return candidates[:limit]


def _fallback_queries(item: dict) -> list[str]:
    name = item.get("name", "")
    city = item.get("city", "")
    if item.get("type") == "event":
        if "birkdale" in name.lower() or "open" in name.lower():
            return [
                "Royal Birkdale golf club drone flyover 4K",
                "The Open Championship trophy cinematic official",
            ]
        if "spa" in name.lower() or "grand prix" in name.lower():
            return [
                "Spa Francorchamps Eau Rouge drone 4K cinematic",
                "Formula 1 Spa circuit cinematic official",
            ]
    if item.get("type") == "cabin":
        return [name + " official reveal cinematic 4K", name + " cabin tour official"]
    return [name + " official promo cinematic 4K", f"{city} luxury cinematic drone 4K" if city else name + " cinematic 4K"]


def _analyze_candidates(candidates: list[dict], post_index: int) -> list[dict]:
    analyses: list[dict] = []
    for c in candidates:
        print(f"     👁️ Gemini watching: \"{c['title'][:45]}\" ({c.get('uploader', '?')})")
        if c.get("source") == "pinterest":
            tmp = download_clip(
                c["url"],
                str(OUT / f"pin_{post_index}_{len(analyses)}.mp4"),
                max_seconds=60,
            )
            if not tmp:
                print("        ⚠️ pin download failed")
                continue
            a = analyze_video_file(tmp)
            if a:
                a["_local_file"] = tmp
        else:
            a = analyze_youtube_video(c["url"])
        if a:
            analyses.append({"candidate": c, "analysis": a})
            print(f"        → {a.get('main_subject', '?')[:60]}")
        else:
            print("        ⚠️ analysis failed — skipping candidate")
    return analyses


def pick_intro(item: dict, used_in_batch: set, week: str) -> str | None:
    """Airline-match > window > None. No duplicates in batch, no back-to-back weeks."""
    lib_file = ROOT / "assets" / "journey_clips" / "library.json"
    if not lib_file.exists():
        return None
    lib = json.loads(lib_file.read_text(encoding="utf-8"))

    def usable(k: str, v: dict) -> bool:
        return (
            k not in used_in_batch
            and Path(v["file"]).exists()
            and v.get("last_used_week") != week
        )

    gateway_airlines = {
        "MAN": ["british airways"],
        "LHR": ["british airways"],
        "BRU": ["lufthansa", "british airways"],
        "DXB": ["emirates"],
        "DOH": ["qatar airways"],
        "NRT": ["ana"],
        "HND": ["ana"],
    }
    iata = (item.get("access") or {}).get("iata", "")
    for airline in gateway_airlines.get(iata, []):
        for k, v in lib.items():
            if v.get("airline") == airline and usable(k, v):
                used_in_batch.add(k)
                v["last_used_week"] = week
                lib_file.write_text(json.dumps(lib, indent=2), encoding="utf-8")
                return v["file"]

    if item.get("type") in ("destination", "event", "hotel"):
        for k, v in lib.items():
            if v.get("type") == "window" and usable(k, v):
                used_in_batch.add(k)
                v["last_used_week"] = week
                lib_file.write_text(json.dumps(lib, indent=2), encoding="utf-8")
                return v["file"]

    return None


def _claude_verdict(
    claude_client: anthropic.Anthropic,
    item: dict,
    analyses: list[dict],
    verdict_context: str,
    *,
    must_pick: bool = False,
) -> dict:
    digest = "\n\n".join(
        f"CANDIDATE {j}:\n"
        f"Title: {x['candidate']['title']}\nUploader: {x['candidate'].get('uploader', '?')}\n"
        f"Analysis: {json.dumps(x['analysis'], ensure_ascii=False)}"
        for j, x in enumerate(analyses)
    )
    must_pick_note = (
        "\nYou MUST pick the best available candidate (pick >= 0). "
        "Prefer clean cinematic segments showing the venue/course/circuit/cabin even if not perfect."
        if must_pick
        else ""
    )
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
or {{"pick": -1, "reason": "..."}} if none qualify.{must_pick_note}""",
            }
        ],
    )
    vt = v.content[0].text.strip()
    if vt.startswith("```"):
        vt = vt.split("\n", 1)[1].rstrip("`").strip()
    return json.loads(vt)


def _gemini_best_fallback(analyses: list[dict]) -> dict | None:
    rank = {
        "official_brand_promo": 0,
        "professional": 1,
        "broadcast": 2,
        "amateur_vlog": 3,
        "analysis_content": 4,
    }
    scored: list[tuple[int, dict]] = []
    for x in analyses:
        a = x["analysis"]
        seg = a.get("best_clean_segment") or {}
        if not seg.get("end_s"):
            continue
        luxury = str(a.get("shows_luxury_product", "")).lower()
        if luxury == "no":
            continue
        pq = a.get("production_quality", "analysis_content")
        scored.append((rank.get(pq, 50), x))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0])
    best = scored[0][1]
    seg = best["analysis"].get("best_clean_segment") or {}
    return {
        "pick": analyses.index(best),
        "start_s": seg.get("start_s", 5),
        "end_s": seg.get("end_s", 15),
        "reason": "gemini_best_fallback",
    }


def _download_branded_segment(
    item: dict,
    i: int,
    chosen: dict,
    analysis: dict,
    seg_start: int,
    seg_len: int,
    batch_used: set,
) -> bool:
    local_file = analysis.get("_local_file")
    if local_file and Path(local_file).exists():
        clip = local_file
    else:
        clip = download_clip(
            chosen["url"],
            str(OUT / f"vid_raw_{i}.mp4"),
            max_seconds=seg_start + seg_len + 2,
        )
    if not clip:
        print("     ⚠️ Download failed")
        return False

    trimmed = trim_video(clip, str(OUT / f"vid_clip_{i}.mp4"), start=seg_start, duration=seg_len)
    if not trimmed:
        return False

    if analysis.get("aspect") == "vertical":
        normalized = normalize_to_landscape(trimmed, str(OUT / f"vid_norm_{i}.mp4"))
        if normalized:
            trimmed = normalized

    branded = brand_video(
        trimmed,
        str(OUT / f"vid_final_{i}.mp4"),
        headline=item.get("headline", ""),
        footer="buybusinessclass.com",
    )
    if not branded:
        return False

    item["video_final"] = branded
    print(f"     ✅ Final: {Path(branded).stat().st_size // 1024}KB")

    if branded and item.get("type") in ("event", "destination", "hotel"):
        intro = pick_intro(item, batch_used, WEEK)
        if intro:
            dual = concat_videos(intro, branded, str(OUT / f"vid_dual_{i}.mp4"))
            if dual:
                intro_d = video_duration(intro)
                branded_d = video_duration(branded)
                dual_d = video_duration(dual)
                if abs(dual_d - (intro_d + branded_d)) <= 1.0:
                    item["video_final"] = dual
                    print(f"     ✅ DUAL ({Path(intro).stem} + dest)")
                else:
                    print("     ⚠️ Concat duration mismatch → solo destination")
    return True


def _process_one_video_item(
    claude_client: anthropic.Anthropic,
    item: dict,
    i: int,
    verdict_context: str,
    batch_used: set,
) -> bool:
    """Run Gemini→Claude→download for a single post. Returns True if video produced."""
    queries = item.get("youtube_queries") or [item["name"] + " official promo luxury"]
    print(f"  {i}. {item.get('headline', item['name'])[:50]}")

    candidates = _collect_candidates(queries)
    if not candidates:
        print("     ⬜ No candidates — trying fallback queries")
        candidates = _collect_candidates(_fallback_queries(item))

    if not candidates:
        print("     ⬜ No candidates")
        return False

    analyses = _analyze_candidates(candidates, i)

    if not analyses:
        print("     ⬜ No analyzable videos — trying fresh candidates")
        extra = _collect_candidates(_fallback_queries(item))
        extra = [c for c in extra if c["url"] not in {a["candidate"]["url"] for a in analyses}]
        if extra:
            analyses.extend(_analyze_candidates(extra, i))

    if not analyses:
        print("     ⬜ No analyzable videos — skipping video for this post")
        return False

    try:
        verdict = _claude_verdict(claude_client, item, analyses, verdict_context)
    except Exception as exc:
        print(f"     ⚠️ Verdict parse failed: {exc} — using first analysis")
        a0 = analyses[0]["analysis"].get("best_clean_segment") or {}
        verdict = {"pick": 0, "start_s": a0.get("start_s", 5), "end_s": a0.get("end_s", 15)}

    if verdict.get("pick", -1) < 0:
        print(f"     🚫 Claude: {verdict.get('reason', 'none qualify')[:80]}")
        print("     🔄 Round 2 — expanded search + softer pick...")
        extra_queries = _fallback_queries(item)
        round2 = _collect_candidates(extra_queries)
        round2 = [c for c in round2 if c["url"] not in {a["candidate"]["url"] for a in analyses}]
        if round2:
            analyses.extend(_analyze_candidates(round2, i))
        try:
            verdict = _claude_verdict(claude_client, item, analyses, verdict_context, must_pick=True)
        except Exception:
            verdict = _gemini_best_fallback(analyses) or {"pick": -1}

    if verdict.get("pick", -1) < 0:
        verdict = _gemini_best_fallback(analyses) or verdict

    if verdict.get("pick", -1) < 0:
        print("     ⬜ No usable video after all rounds")
        return False

    winner = analyses[min(verdict["pick"], len(analyses) - 1)]
    chosen = winner["candidate"]
    winner_analysis = winner["analysis"]
    seg_start = max(0, int(verdict.get("start_s", 5)))
    seg_end = int(verdict.get("end_s", seg_start + 10))
    seg_len = max(6, min(12, seg_end - seg_start))
    print(f"     ✅ WINNER: \"{chosen['title'][:45]}\" · segment {seg_start}-{seg_start + seg_len}s")

    return _download_branded_segment(
        item, i, chosen, winner_analysis, seg_start, seg_len, batch_used
    )


def _verdict_context() -> str:
    return (
        brand_dna
        + """
You judge videos by their FULL content description (Gemini watched them).
APPLY the VIDEO CONTENT REQUIREMENT: the client must SEE the premium product.
REJECT: analysis/educational content, split-screens, stats graphics,
feature demos without the product, heavy burned-in text, third-party logos."""
    )


def regenerate_video_for_item(claude_client: anthropic.Anthropic, item: dict, index: int) -> str | None:
    """Re-run video pipeline for one campaign item. Returns path to final video."""
    item.pop("video_final", None)
    item.pop("video_clip", None)
    if _process_one_video_item(claude_client, item, index, _verdict_context(), set()):
        return item.get("video_final") or item.get("video_clip")
    return None


def _qc_text(val) -> str:
    if val is None:
        return ""
    if isinstance(val, list):
        return " ".join(str(x) for x in val).lower()
    return str(val).lower()


def qc_gate(all_items: list[dict]) -> None:
    """Gemini watches OUR final videos. FAIL → fallback or drop."""
    print("\n🛡️ QC GATE — Gemini checking our output...\n")

    for i, item in enumerate(all_items, 1):
        vid = item.get("video_final")
        if not vid or not Path(vid).exists():
            continue
        try:
            a = analyze_video_file(vid)
            if not a:
                print(f"  {i}. ⚠️ QC unavailable — keeping (manual check)")
                continue

            problems = []
            scenes = _qc_text(a.get("scenes"))
            if any(w in scenes for w in ("frozen", "static", "same frame", "repeat")):
                problems.append("frozen/repeated frames")
            logos = str(a.get("third_party_logos", "none"))
            if logos.lower() != "none":
                problems.append(f"logos: {logos[:40]}")
            luxury = _qc_text(a.get("shows_luxury_product", "no"))
            if luxury == "no" or luxury.startswith("no "):
                problems.append("no visible product")

            if problems:
                print(f"  {i}. 🚫 QC FAIL: {'; '.join(problems)}")
                solo = str(OUT / f"vid_final_{i}.mp4")
                if item["video_final"] != solo and Path(solo).exists():
                    item["video_final"] = solo
                    print("       → fallback: solo destination")
                else:
                    item.pop("video_final", None)
                    print("       → dropped video (text+buttons only)")
            else:
                subject = str(a.get("main_subject", ""))[:50]
                print(f"  {i}. ✅ QC PASS — {subject}")
        except Exception as exc:
            print(f"  {i}. ⚠️ QC error ({exc}) — keeping (manual check)")


def step3_download_videos(claude_client: anthropic.Anthropic, all_items: list[dict]) -> None:
    """Candidates → Gemini WATCHES each → Claude verdict on full description →
    download ONLY the winner, ONLY the clean segment, at 4K."""
    print("\n📹 STEP 3 — Gemini watches, Claude decides, targeted download...\n")

    ctx = _verdict_context()
    batch_used: set[str] = set()
    for i, item in enumerate(all_items, 1):
        _process_one_video_item(claude_client, item, i, ctx, batch_used)


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
            "index": i,
            "media_type": "video",
            "youtube_queries": item.get("youtube_queries"),
            "headline": item.get("headline", ""),
            "type": item.get("type", ""),
        }
        save_campaign_local(campaign)
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

    all_items = step1_find_new_topics(gemini)[:5]

    if len(all_items) < 5:
        print(f"  ⚠️ Only {len(all_items)} topics (need 5)")

    all_items = step2_write_captions(claude_client, all_items)
    step3_download_videos(claude_client, all_items)
    qc_gate(all_items)
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
