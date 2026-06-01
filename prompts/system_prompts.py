"""
BBC Marketing Agent — System Prompts.
Fiecare prompt e scris ca un top 0.1% sales copywriter pentru luxury travel.

Principii de bază:
- BBC vinde EXPERIENȚE, nu bilete
- Clientul nu cumpără un scaun business class — cumpără Monaco, Wimbledon, Fashion Week
- Urgență REALĂ (evenimente au date fixe) > urgență fabricată
- Premium feel: puține cuvinte, impact mare, zero spam
- Fiecare mesaj e o invitație exclusivă, nu o reclamă
"""
from __future__ import annotations

import random

# ═══════════════════════════════════════════════════════════
# 1. DISCOVERY PROMPT — Gemini caută evenimente premium
# ═══════════════════════════════════════════════════════════

DISCOVERY_PROMPT = """
You are an elite concierge for the world's most discerning business class travelers.
Your clients are C-suite executives, entrepreneurs, and high-net-worth individuals 
who fly business class ($2,000-$5,000 per ticket) to attend premium global events.

TASK: Find the 5 most compelling premium events happening in the week of 
{week_start} — {week_end} that would motivate a wealthy traveler to book 
a business class flight RIGHT NOW.

WHAT MAKES AN EVENT "PREMIUM" FOR OUR CLIENTS:
- Events where seats/tickets are LIMITED and sell out (FOMO factor)
- Events with MASSIVE global prestige (bragging rights)
- Events where business networking happens naturally
- Events in ASPIRATIONAL destinations (Monaco, London, Paris, Tokyo, Milan)
- Events with a clear "once a year" or "once in a lifetime" angle

CATEGORY PRIORITY (highest conversion → lowest):
1. 🏎️ Motorsport: F1 Grand Prix (Monaco, Silverstone, Monza), Le Mans, MotoGP
2. 🎾 Tennis: Wimbledon, Roland Garros, US Open, Australian Open
3. ⚽ Football: Champions League final, World Cup, Euro final
4. 👗 Fashion: Paris/Milan/NY/London Fashion Week, Met Gala proximity
5. 🎬 Film: Cannes, Venice Film Festival, TIFF, Sundance
6. 💼 Business: Davos/WEF, Web Summit, CES, Dreamforce, WWDC
7. 🎨 Art/Design: Art Basel (Miami/Basel/HK), Venice Biennale, Salone del Mobile
8. 🛥️ Yachting: Monaco Yacht Show, Rolex regattas, America's Cup
9. 🏇 Racing: Royal Ascot, Kentucky Derby, Melbourne Cup
10. 🎵 Music: Salzburg Festival, Bayreuth, Glastonbury VIP

FOR EACH EVENT, return:
- name: Official full name (e.g., "Formula 1 Grand Prix de Monaco 2026")
- city: City, Country (e.g., "Monte Carlo, Monaco")
- country_code: ISO 2-letter (e.g., "MC")
- dates_start: YYYY-MM-DD
- dates_end: YYYY-MM-DD
- category: From the list above (lowercase: "motorsport", "tennis", etc.)
- premium_score: 1.0–10.0 based on:
    10: F1 Monaco, Wimbledon Final, Champions League Final (guaranteed sell-out)
    8-9: Grand Slam weeks, Fashion Week main days, Cannes opening/closing
    6-7: Art Basel, business conferences, regional F1 races
    4-5: Smaller festivals, regional events
- routes: Top 2 routes from BBC hubs. Format:
    [{{"from": "JFK", "to": "NCE", "from_continent": "NA", "to_continent": "EU"}}]
    BBC HUBS: JFK, LAX, MIA, ORD, SFO, BOS, SEA, YYZ, EWR
    Choose the hub CLOSEST to the event + the most popular hub (usually JFK)
- image_prompt: 1-2 sentences describing the EVENT IN ACTION for image generation.
    MUST show what the client will EXPERIENCE:
    F1 → racing cars on track, speed, circuit atmosphere
    Tennis → match on court, grass/clay, crowd energy
    Fashion → runway show, models, front row glamour
    NOT the city skyline or a random scene.
    NEVER include text, logos, or watermarks in description.
    Style: professional photography, cinematic, dramatic lighting.
- sales_hook: One powerful sentence (max 15 words) that makes someone 
    IMMEDIATELY want to book. Think: what would a luxury travel concierge 
    whisper to a billionaire? Examples:
    "Three seats left on the grid-side terrace. Your move."
    "Centre Court. Royal Box view. You know you want to."
    "The runway awaits. So does your seat in the front row."
- whatsapp_caption: Complete WhatsApp message (max 250 chars) that:
    - Opens with relevant emoji
    - Names the event clearly
    - Creates genuine urgency (dates are REAL, events DO sell out)
    - Mentions "business class" naturally
    - Ends with buybusinessclass.com
    - Tone: exclusive invitation, NOT screaming ad
    - NO hashtags (this isn't Instagram)
    - NO excessive emoji (max 3)

STRICT RULES:
- ONLY real events. Search the web and CONFIRM dates.
- ONLY routes involving North America (BBC hubs)
- DO NOT include prices (calculated automatically)
- SORT by premium_score descending
- If you can't find 5 premium events, return fewer — quality > quantity
- Events must be 5-14 days in the future (enough time to book flights)

Respond in valid JSON only, no markdown:
{{"events": [...]}}
"""

# ═══════════════════════════════════════════════════════════
# 2. VERIFICATION PROMPT — Confirmare eveniment real
# ═══════════════════════════════════════════════════════════

VERIFICATION_PROMPT = """
Quick fact-check: Is the event "{event_name}" happening in {city} 
on {dates_start} to {dates_end}?

Search the web and confirm:
1. Does this event exist?
2. Are the dates correct (within ±3 days)?
3. Is the city/venue correct?

Reply with ONLY: YES or NO
If dates are off by 1-3 days, still say YES but note the correct dates.
"""

# ═══════════════════════════════════════════════════════════
# 3. IMAGE GENERATION PROMPT — Gemini generează fundaluri
# ═══════════════════════════════════════════════════════════

IMAGE_GEN_SYSTEM = """
You are a world-class sports and events photographer working for 
Getty Images and Sports Illustrated. Your photographs have won 
World Press Photo awards and appear in Condé Nast Traveler.

Generate a stunning photorealistic image that captures the ENERGY, 
DRAMA, and EMOTION of a premium event. The viewer should FEEL like 
they're THERE — in the front row, on the pit wall, courtside.

CRITICAL RULES:
- Show the EVENT IN ACTION — the moment of peak excitement
- F1: Cars racing at speed, dramatic angle, motion blur on wheels
- Tennis: Rally in progress, ball in air, player at full stretch
- Football: Stadium atmosphere, floodlights, crowd energy
- Fashion: Model mid-stride on runway, dramatic lighting
- Film Festival: Red carpet arrival, flashbulbs, elegance
- Yachting: Superyachts under sail or in harbor, luxury lifestyle
- Art: Gallery space, dramatic installation, visitors in awe

PHOTOGRAPHY STYLE:
- Cinematic wide angle (16:9 aspect ratio)
- Shallow depth of field where appropriate
- Golden hour / dramatic lighting preferred
- Rich, warm color grading (not cold/clinical)
- Professional DSLR quality (not phone snapshot)
- The kind of image that would be a DOUBLE-PAGE SPREAD in a luxury magazine

ABSOLUTE PROHIBITIONS:
- NEVER include text, words, letters, numbers, logos, or watermarks
- NEVER make text readable on any surface (signs, banners, screens)
- NEVER show people's faces clearly (privacy + avoid uncanny valley)
- NEVER include airplane interiors (that's OUR brand territory)
- NEVER generate generic stock photo vibes — this must feel REAL

OUTPUT: Landscape orientation, 16:9, photorealistic quality.
"""

# ═══════════════════════════════════════════════════════════
# 4. TELEGRAM PREVIEW — Mesaj de aprobare pentru Scaler
# ═══════════════════════════════════════════════════════════

TELEGRAM_PREVIEW_TEMPLATE = """
{emoji} *{event_name}*
📍 {city}
📅 {dates_start} — {dates_end}
✈️ {from_iata} → {to_iata}
💰 *{price}* business round-trip
⭐ Score: {premium_score}/10

_{sales_hook}_

📋 Campaign: `{campaign_id}`
"""

# ═══════════════════════════════════════════════════════════
# 5. WHATSAPP BROADCAST — Mesaj către clienți (Twilio template)
# ═══════════════════════════════════════════════════════════

WHATSAPP_CAPTION_FALLBACK = """
{emoji} {event_name}

{from_iata} → {to_iata} Business Class
From {price} round-trip

{sales_hook}

Book: buybusinessclass.com
"""

# ═══════════════════════════════════════════════════════════
# 6. WHATSAPP GROUP POST — Mesaj în grup BBC VIP (WAHA)
# ═══════════════════════════════════════════════════════════

WHATSAPP_GROUP_TEMPLATE = """
{emoji} *{event_name}*
📍 {city}
📅 {dates}

✈️ {from_iata} → {to_iata} _Business Class_
💰 *From {price}* round-trip

{sales_hook}

📞 Book now: buybusinessclass.com
"""

# ═══════════════════════════════════════════════════════════
# 7. CAPTION REWRITE — Dacă Gemini caption e slab, rescrie
# ═══════════════════════════════════════════════════════════

CAPTION_REWRITE_PROMPT = """
You are the head copywriter at the world's most exclusive luxury travel agency.
Your words have sold $50M in business class tickets last year alone.

Rewrite this WhatsApp marketing message for a business class flight deal:

EVENT: {event_name}
CITY: {city}
DATES: {dates}
ROUTE: {from_iata} → {to_iata}
PRICE: {price} business class round-trip
ORIGINAL CAPTION: {original_caption}

RULES:
- Max 250 characters (WhatsApp preview cuts at ~300)
- Open with ONE relevant emoji (not three, not five — ONE)
- First line: the event name (what they're going TO)
- Second line: the route + class (what they're FLYING)
- Third line: the price (what they're PAYING — always with "From")
- Last line: the hook (why they should book NOW)
- End with: buybusinessclass.com
- Tone: exclusive whisper, not shouting ad
- Language: English, simple, powerful
- NO hashtags, NO "🔥🔥🔥", NO "AMAZING DEAL!!!"
- Think: how a concierge at The Ritz would text a VIP guest

Write ONLY the caption, nothing else.
"""

# ═══════════════════════════════════════════════════════════
# 8. EMOJI MAP — Consistent emoji per categorie
# ═══════════════════════════════════════════════════════════

CATEGORY_EMOJI = {
    "motorsport": "🏎️",
    "tennis": "🎾",
    "football": "⚽",
    "fashion": "👗",
    "film": "🎬",
    "business": "💼",
    "art": "🎨",
    "yachting": "🛥️",
    "racing": "🏇",
    "music": "🎵",
    "golf": "⛳",
    "rugby": "🏉",
    "default": "✈️",
}

# ═══════════════════════════════════════════════════════════
# 9. SALES HOOKS LIBRARY — Fallback dacă Gemini nu generează
# ═══════════════════════════════════════════════════════════

SALES_HOOKS = {
    "motorsport": [
        "Grid-side terrace. Champagne in hand. Your move.",
        "The engines are warming up. Your seat is waiting.",
        "Pit lane access. Podium views. Business class there and back.",
    ],
    "tennis": [
        "Centre Court. Debenture seats. You know you want to.",
        "Strawberries, Pimm's, and a seat behind the baseline.",
        "Match point. Front row. This is why you fly business.",
    ],
    "football": [
        "90 minutes that write history. Be there to see it.",
        "VIP box. Champions League night. Unforgettable.",
        "Some matches you watch on TV. This isn't one of them.",
    ],
    "fashion": [
        "Front row awaits. So does your lie-flat seat.",
        "The runway is calling. Answer in business class.",
        "See tomorrow's trends today. From the best seat in the house.",
    ],
    "film": [
        "Red carpet. Standing ovation. The flight home in a flat bed.",
        "Where cinema meets the Côte d'Azur. Be there.",
        "Premieres, parties, and a business class window seat.",
    ],
    "business": [
        "Where deals are made over coffee, not conference calls.",
        "Network at the top. Fly at the top.",
        "The meeting that could change everything. Don't dial in.",
    ],
    "art": [
        "Art that moves you. A flight that pampers you.",
        "Gallery hopping is better after a lie-flat nap.",
        "See it in person. That's the whole point.",
    ],
    "default": [
        "Some experiences demand business class.",
        "Life's too short for economy on a trip like this.",
        "Arrive refreshed. Leave inspired.",
    ],
}

# F1 Monaco — image prompt aliniat cu DISCOVERY (event in action)
F1_MONACO_IMAGE_PROMPT = (
    "Formula 1 cars racing at full speed through the Monaco street circuit, "
    "dramatic low angle with motion blur on wheels, Mediterranean harbor and "
    "luxury yachts in background, packed grandstands, cinematic golden-hour "
    "lighting, professional sports photography"
)


def format_badge_text(event_name: str, max_len: int = 30) -> str:
    """Badge specific eveniment — uppercase, trunchiat la max_len."""
    text = event_name.strip().upper()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def get_urgency_text(event_name: str, category: str = "") -> str:
    """Linie urgență sub preț — din Gemini sau fallback."""
    lower = event_name.lower()
    if "monaco" in lower or "grand prix" in lower:
        return "Limited Grand Prix season fares"
    if "wimbledon" in lower:
        return "Limited Wimbledon season fares"
    if "champions league" in lower:
        return "Limited Champions League season fares"
    if "fashion week" in lower:
        return "Limited Fashion Week season fares"
    label = event_name.strip()
    for suffix in (" 2026", " 2027", " 2025"):
        label = label.replace(suffix, "")
    if len(label) > 24:
        label = " ".join(label.split()[:4])
    if label:
        return f"Limited {label} season fares"
    return "Premium cabin availability limited"


def get_emoji(category: str) -> str:
    """Returnează emoji pentru categorie."""
    return CATEGORY_EMOJI.get(category, CATEGORY_EMOJI["default"])


def get_sales_hook(category: str) -> str:
    """Returnează un sales hook random pentru categorie."""
    hooks = SALES_HOOKS.get(category, SALES_HOOKS["default"])
    return random.choice(hooks)


def normalize_discovered_event(event: dict) -> dict:
    """Mapează câmpuri noi Gemini → câmpuri folosite în pipeline."""
    if event.get("whatsapp_caption") and not event.get("caption"):
        event["caption"] = event["whatsapp_caption"]
    if not event.get("sales_hook"):
        event["sales_hook"] = get_sales_hook(event.get("category", "default"))
    if not event.get("caption_draft"):
        event["caption_draft"] = event.get("sales_hook", "")
    return event


def format_telegram_preview(event: dict) -> str:
    """Formatează mesajul de preview pentru Telegram."""
    routes = event.get("routes", [{}])
    r = routes[0] if routes else {}

    return TELEGRAM_PREVIEW_TEMPLATE.format(
        emoji=get_emoji(event.get("category", "default")),
        event_name=event.get("name", ""),
        city=event.get("city", ""),
        dates_start=event.get("dates_start", ""),
        dates_end=event.get("dates_end", ""),
        from_iata=r.get("from", "JFK"),
        to_iata=r.get("to", "LHR"),
        price=event.get("price", ""),
        premium_score=event.get("premium_score", ""),
        sales_hook=event.get("sales_hook", get_sales_hook(event.get("category", ""))),
        campaign_id=event.get("campaign_id", ""),
    ).strip()


def format_whatsapp_group(event: dict) -> str:
    """Formatează mesajul pentru grupul WhatsApp BBC VIP."""
    routes = event.get("routes", [{}])
    r = routes[0] if routes else {}
    dates = ""
    if event.get("dates_start") and event.get("dates_end"):
        dates = f"{event['dates_start']} — {event['dates_end']}"

    return WHATSAPP_GROUP_TEMPLATE.format(
        emoji=get_emoji(event.get("category", "default")),
        event_name=event.get("name", event.get("event_name", "")),
        city=event.get("city", ""),
        dates=dates,
        from_iata=r.get("from", event.get("from_iata", "JFK")),
        to_iata=r.get("to", event.get("to_iata", "LHR")),
        price=event.get("price", ""),
        sales_hook=event.get("sales_hook", get_sales_hook(event.get("category", ""))),
    ).strip()


def format_whatsapp_caption(event: dict) -> str:
    """Caption WhatsApp — din Gemini sau fallback premium."""
    if event.get("whatsapp_caption"):
        return event["whatsapp_caption"].strip()
    if event.get("caption"):
        return event["caption"].strip()

    routes = event.get("routes", [{}])
    r = routes[0] if routes else {}
    return WHATSAPP_CAPTION_FALLBACK.format(
        emoji=get_emoji(event.get("category", "default")),
        event_name=event.get("name", event.get("event_name", "")),
        from_iata=r.get("from", event.get("from_iata", "JFK")),
        to_iata=r.get("to", event.get("to_iata", "LHR")),
        price=event.get("price", ""),
        sales_hook=event.get("sales_hook", get_sales_hook(event.get("category", ""))),
    ).strip()
