"""
BBC Video Generation — Gemini Veo prompt library.

Categories: destination, lifestyle, event, multi.
Use with services.veo_client.generate_video().
"""

from __future__ import annotations

DESTINATION_VIDEO_PROMPTS: dict[str, str] = {
    "london": (
        "Slow cinematic camera pan from left to right across London skyline. "
        "Thames water gently rippling with golden reflections. "
        "Clouds slowly drifting across dramatic sunset sky. "
        "Tower Bridge lights beginning to glow warmly. "
        "70mm lens, shallow depth of field, warm amber color grading. "
        "Professional travel documentary quality."
    ),
    "rome": (
        "Gentle dolly forward toward the Colosseum at golden hour. "
        "Warm Italian light casting long shadows on ancient weathered stone. "
        "Soft breeze moving cypress trees in foreground. "
        "Birds flying gracefully across amber sky in distance. "
        "Cinematic color grading, professional editorial quality."
    ),
    "monaco": (
        "Slow aerial pan across Monte Carlo harbor at sunset. "
        "Superyachts gently rocking on deep Mediterranean blue water. "
        "City lights beginning to illuminate the dramatic hillside. "
        "Golden clouds reflecting in calm harbor water. "
        "Luxury lifestyle cinematography, IMAX quality."
    ),
    "paris": (
        "Slow orbit around Eiffel Tower silhouette at golden hour. "
        "Seine river sparkling with soft warm reflections below. "
        "Parisian zinc rooftops stretching into warm pink sunset. "
        "Gentle lens flare from setting sun behind iron lattice. "
        "Romantic, cinematic, professional editorial quality."
    ),
    "dubai": (
        "Dramatic slow tilt upward along Dubai skyline at blue hour. "
        "Burj Khalifa piercing through layers of soft lavender clouds. "
        "City lights twinkling against deep cobalt blue sky. "
        "Smooth glass reflections shimmering on modern architecture. "
        "Luxury, futuristic, cinematic quality."
    ),
    "tokyo": (
        "Slow pan across Tokyo skyline transitioning from dusk to twilight. "
        "Neon signs beginning to glow softly in Shibuya district. "
        "Mount Fuji faintly visible through gentle evening mist. "
        "Traditional temples contrasting with ultramodern glass towers. "
        "Atmospheric, contemplative, cinematic quality."
    ),
    "nice": (
        "Slow drone arc along the Côte d'Azur coastline at golden hour. "
        "Turquoise Mediterranean water meeting white rocky cliffs. "
        "Colorful buildings of Nice old town glowing in warm light. "
        "Sailboats gently rocking in the harbor below. "
        "Luxury Riviera lifestyle, professional travel cinematography."
    ),
    "generic": (
        "Slow cinematic camera pan across beautiful cityscape at golden hour. "
        "Warm light reflecting on water and historic buildings. "
        "Clouds gently moving across dramatic painted sky. "
        "Professional travel documentary, 70mm lens, shallow depth of field."
    ),
}

LIFESTYLE_VIDEO_PROMPTS: dict[str, str] = {
    "cabin_lieflat": (
        "Slow dolly shot along business class cabin aisle at night. "
        "Lie-flat seats with crisp white linens and premium blankets. "
        "Soft warm amber cabin lighting creating intimate atmosphere. "
        "Champagne glass on tray table catching golden light beautifully. "
        "Window showing moonlit clouds passing at high altitude. "
        "Serene, luxurious, professional airline interior photography."
    ),
    "lounge_bar": (
        "Gentle tracking shot across premium airport lounge at dusk. "
        "Elegant cocktail bar with warm pendant lighting, crystal glasses gleaming. "
        "Floor-to-ceiling windows with aircraft silhouettes taxiing outside. "
        "Leather armchairs, marble surfaces, fresh flowers. "
        "Sophisticated, calm, luxury hospitality atmosphere."
    ),
    "window_clouds": (
        "Static shot from business class window seat, slight parallax. "
        "Dramatic cloud formations rolling past at cruising altitude. "
        "Golden sunset light streaming through oval window, warming cabin. "
        "Premium seat fabric and polished wood trim visible at edge of frame. "
        "Meditative, serene, the luxury of time above the world."
    ),
    "dining_service": (
        "Slow overhead descending shot toward premium airline dining setting. "
        "Beautifully plated three-course meal on fine bone china. "
        "Crystal wine glass with deep burgundy wine catching warm cabin light. "
        "White linen napkin, polished silver cutlery arranged precisely. "
        "Elegant, appetizing, Michelin-star presentation in the sky."
    ),
    "boarding_experience": (
        "Tracking shot following silhouette through empty priority boarding bridge. "
        "Soft directional lighting creating long cinematic shadows. "
        "Business class cabin entrance visible ahead, warm and inviting. "
        "Premium luggage, confident stride, sense of anticipation. "
        "Professional, aspirational, the journey begins in style."
    ),
    "arrival_moment": (
        "Slow motion silhouette stepping through airport glass doors into sunlight. "
        "Beautiful destination city skyline visible in background. "
        "Golden morning light, subtle lens flare, fresh energy. "
        "The payoff of business class — arrived refreshed, ready for anything. "
        "Aspirational, premium, cinematic travel lifestyle."
    ),
}

EVENT_VIDEO_PROMPTS: dict[str, str] = {
    "f1_monaco": (
        "Formula 1 car racing at high speed through Monaco street circuit. "
        "Dramatic low angle, intense motion blur on spinning wheels. "
        "Monte Carlo harbor with gleaming superyachts visible in background. "
        "Red sparks flying from floor, heat haze rising from asphalt. "
        "Slow motion transition revealing exclusive terrace viewing. "
        "Professional motorsport cinematography, IMAX quality."
    ),
    "wimbledon": (
        "Slow pan across pristine Wimbledon Centre Court emerald grass. "
        "Chalk white lines perfectly painted, morning dew glistening like diamonds. "
        "Famous ivy-covered walls and royal purple accents. "
        "Empty seats filling with anticipation of the day ahead. "
        "Quintessentially British, sporting heritage, cinematic documentary."
    ),
    "fashion_week": (
        "Slow dolly along empty fashion runway moments before the show. "
        "Dramatic overhead spotlights creating pools of brilliant white. "
        "Silhouettes of front-row guests settling into their seats. "
        "Camera flashes beginning to strobe from the press pit. "
        "High-fashion anticipation, Vogue-quality production values."
    ),
    "art_basel": (
        "Slow tracking shot through contemporary art gallery at night. "
        "Dramatic directional lighting sculpting modern installations. "
        "Well-dressed silhouettes contemplating large-scale artworks. "
        "Polished concrete floors reflecting colored gallery lighting. "
        "Cultural sophistication, documentary art-house cinematography."
    ),
    "grand_prix_general": (
        "Aerial establishing shot of iconic racing circuit at sunset. "
        "Cars streaming through S-curves like colored ribbons of light. "
        "Crowd energy visible in grandstands, flags waving. "
        "Engine roar implied through visual intensity and motion blur. "
        "Cinematic motorsport spectacle, the pinnacle of speed and style."
    ),
}

MULTI_VIDEO_PROMPTS: dict[str, str] = {
    "world_montage": (
        "Dreamy slow crossfade montage through world landmarks. "
        "Eiffel Tower at golden hour dissolving gracefully into London Big Ben. "
        "Rome Colosseum at sunset transitioning into Dubai futuristic skyline. "
        "Each destination bathed in warm cinematic golden light. "
        "Sense of endless possibility, luxury, and adventure awaiting. "
        "Professional travel documentary montage, editorial quality."
    ),
    "above_clouds": (
        "Business class window view at 40,000 feet cruising altitude. "
        "Endless sea of cotton-white clouds stretching to curved horizon. "
        "Golden sunlight slowly moving across dramatic cloud formations. "
        "Camera gently pushing toward window, light warming the frame. "
        "Sense of freedom, privilege, floating above the ordinary world. "
        "Peaceful, serene, the luxury of business class perspective."
    ),
    "brand_essence": (
        "Seamless cinematic sequence: champagne being poured in crystal flute, "
        "dissolve to lie-flat seat with city lights below, "
        "dissolve to sunrise over Mediterranean coastline, "
        "dissolve to traveler stepping into luxury hotel lobby. "
        "Warm amber tones throughout, cohesive luxury narrative. "
        "The complete business class experience in eight seconds."
    ),
}

NEGATIVE_PROMPT_VIDEO = (
    "text, titles, subtitles, captions, watermarks, logos, brand names, "
    "UI elements, borders, frames, split screen, "
    "distorted faces, morphing body parts, extra limbs, uncanny valley, "
    "glitches, artifacts, flickering, strobing, "
    "low quality, blurry, pixelated, grainy, overexposed, "
    "cartoon, anime, illustration, painting, sketch, "
    "jerky motion, unnatural movement, speed ramping, "
    "stock footage watermark, shutterstock, getty"
)

_PROMPT_REGISTRY: dict[str, dict[str, str]] = {
    "destination": DESTINATION_VIDEO_PROMPTS,
    "lifestyle": LIFESTYLE_VIDEO_PROMPTS,
    "event": EVENT_VIDEO_PROMPTS,
    "multi": MULTI_VIDEO_PROMPTS,
}


def get_video_prompt(category: str, subcategory: str, custom: str = "") -> str:
    """Return optimized Veo prompt for BBC content."""
    cat = _PROMPT_REGISTRY.get(category, DESTINATION_VIDEO_PROMPTS)
    prompt = cat.get(subcategory)
    if not prompt:
        prompt = cat.get("generic", DESTINATION_VIDEO_PROMPTS["generic"])
    if custom:
        prompt = f"{prompt} Additionally: {custom}"
    return prompt


def get_all_categories() -> dict[str, list[str]]:
    """Return all video categories and subcategory keys."""
    return {name: list(prompts.keys()) for name, prompts in _PROMPT_REGISTRY.items()}


def resolve_video_prompt(
    category: str | None = None,
    subcategory: str | None = None,
    custom_prompt: str = "",
) -> str:
    """Resolve prompt from category/subcategory or use custom_prompt directly."""
    if custom_prompt.strip():
        return custom_prompt.strip()
    if category and subcategory:
        return get_video_prompt(category, subcategory)
    raise ValueError("Provide custom_prompt or both category and subcategory")
