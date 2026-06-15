"""
BBC AI Agent — Claude API wrapper.
Intent classification, caption (vision), rewrite, urgent parse.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

log = logging.getLogger("bbc.claude")

AGENT_SYSTEM = """You are the BBC Marketing Agent — an AI assistant for the marketing director of BuyBusinessClass.com, a premium business class flight booking company with 100,000+ clients globally.

You operate inside Telegram. The director talks to you naturally in English or Romanian.

YOUR CAPABILITIES:
- Create flight deal campaigns (event + pricing + branded image + caption)
- Show system status and recent campaigns
- Approve, reject, edit, regenerate deals
- Schedule deals for Monday broadcast or post immediately
- Answer questions about the system

CONVERSATION CONTEXT:
State: {state}
Current deal: {current_deal}
Recent messages: {history}

UNDERSTAND the director's intent. They may say things like:
- "wimbledon next week" → CREATE_DEAL
- "monaco f1" → CREATE_DEAL
- "da", "ok", "approve", "👍" → APPROVE (if deal in preview)
- "nu", "skip", "reject" → REJECT
- "schimbă caption-ul", "mai scurt", "edit" → EDIT_CAPTION
- "altă imagine", "regenerate" → REGENERATE
- "postează", "send now", "trimite" → POST_NOW
- "luni", "monday", "programează" → SCHEDULE
- "status", "ce avem", "how's it going" → STATUS
- "arată deals", "show deals", "lista" → LIST_DEALS
- "anulează", "cancel" → CANCEL
- "bună", "hello", "hi" → GREETING
- "help", "ajutor", "cum funcționează" → HELP

RULES:
- If state is "idle" and message looks like a destination/event → CREATE_DEAL
- If state is "preview" and message is short confirmation → APPROVE
- If state is "editing" and message is new text → CAPTION_UPDATE
- Emoji reactions (👍 ❤️ 🔥) on a deal preview → APPROVE
- If you're not sure (confidence < 0.6) → UNCLEAR with a helpful question
- NEVER execute destructive actions on low confidence
- Keep response SHORT (1-3 lines max, Telegram style)
- Use 1-2 emoji max in response

Return ONLY valid JSON, no markdown:
{{"intent": "CREATE_DEAL|MODIFY_DEAL|APPROVE|REJECT|POST_NOW|SCHEDULE|STATUS|LIST_DEALS|HELP|GREETING|EDIT_CAPTION|REGENERATE|CAPTION_UPDATE|CANCEL|UNCLEAR", "entities": {{"event_name": null, "from_iata": null, "to_iata": null, "changes": [], "new_caption": null}}, "response": "Short message to director", "needs_clarification": false, "confidence": 0.95}}"""


def _get_client():
    """Get Anthropic client or None."""
    try:
        from config import settings

        if not settings.anthropic_api_key:
            return None
        from anthropic import Anthropic

        return Anthropic(api_key=settings.anthropic_api_key)
    except Exception as e:
        log.warning("Anthropic client init failed: %s", e)
        return None


def _get_model() -> str:
    from config import settings

    return settings.anthropic_model or "claude-sonnet-4-20250514"


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    if text.startswith("json"):
        text = text[4:].strip()
    return json.loads(text)


async def classify_intent(message: str, context: dict) -> dict:
    """Classify director message → intent + entities + response."""
    from prompts.brand_dna import BBC_BRAND_DNA

    client = _get_client()
    if not client:
        return {
            "intent": "UNCLEAR",
            "entities": {},
            "response": "⚠️ AI not configured. Use buttons or /commands.",
            "confidence": 0.0,
        }

    state = context.get("state", "idle")
    current_deal = context.get("current_campaign_id", "none")
    history = context.get("history", [])
    history_str = (
        "\n".join(f"  {m['role']}: {m['text']}" for m in history[-5:])
        if history
        else "  (no history)"
    )

    system = BBC_BRAND_DNA + "\n\n" + AGENT_SYSTEM.format(
        state=state,
        current_deal=current_deal or "none",
        history=history_str,
    )

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=_get_model(),
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": message}],
        )

        result = _parse_json_response(response.content[0].text)
        result.setdefault("intent", "UNCLEAR")
        result.setdefault("confidence", 0.5)
        result.setdefault("response", "")
        result.setdefault("entities", {})
        return result

    except json.JSONDecodeError as e:
        log.warning("Claude JSON parse error: %s", e)
        return {
            "intent": "UNCLEAR",
            "entities": {},
            "response": "🤔 Nu am înțeles. Încearcă altfel sau folosește butoanele.",
            "confidence": 0.0,
        }
    except Exception as e:
        log.error("Claude classify error: %s", e)
        return {
            "intent": "UNCLEAR",
            "entities": {},
            "response": "⚠️ AI error. Folosește butoanele sau /commands.",
            "confidence": 0.0,
        }


async def generate_caption(image_bytes: bytes, event_data: dict) -> str:
    """Claude vision: branded banner + event data → WhatsApp caption."""
    client = _get_client()
    if not client:
        from prompts.system_prompts import _fallback_caption

        return _fallback_caption(event_data)

    try:
        from prompts.system_prompts import (
            CAPTION_FROM_IMAGE_PROMPT,
            CONTACT_BLOCK,
            get_city_name,
            get_sales_hook,
        )

        routes = event_data.get("routes", [{}])
        r = routes[0] if routes else {}
        from_iata = r.get("from", event_data.get("from_iata", "JFK"))
        to_iata = r.get("to", event_data.get("to_iata", "LHR"))
        dates = ""
        if event_data.get("dates_start") and event_data.get("dates_end"):
            dates = f"{event_data['dates_start']} — {event_data['dates_end']}"

        prompt_text = CAPTION_FROM_IMAGE_PROMPT.format(
            event_name=event_data.get("name", event_data.get("event_name", "")),
            city=event_data.get("city", ""),
            dates=dates,
            from_iata=from_iata,
            to_city=get_city_name(to_iata),
            price=event_data.get("price", "$2,500"),
            category=event_data.get("category", "travel"),
            event_context=event_data.get("event_context", "A premium global event."),
            sales_hook=event_data.get(
                "sales_hook", get_sales_hook(event_data.get("category", "default"))
            ),
        )

        img_b64 = base64.b64encode(image_bytes).decode()

        response = await asyncio.to_thread(
            client.messages.create,
            model=_get_model(),
            max_tokens=600,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
        )

        caption = response.content[0].text.strip()

        if len(caption) < 20 or len(caption) > 500:
            from prompts.system_prompts import _fallback_caption

            return _fallback_caption(event_data)

        if "+1 888-322-7999" not in caption:
            lines = caption.split("\n")
            clean_lines = [
                line
                for line in lines
                if not (
                    "buybusinessclass" in line.lower() and "888" not in line
                )
            ]
            caption = "\n".join(clean_lines).rstrip() + f"\n\n{CONTACT_BLOCK}"

        return caption

    except Exception as e:
        log.warning("Claude caption error: %s", e)
        from prompts.system_prompts import _fallback_caption

        return _fallback_caption(event_data)


async def rewrite_text(original: str, feedback: str, context: str = "") -> str:
    """Rewrite caption based on director feedback."""
    client = _get_client()
    if not client:
        return original

    try:
        from prompts.system_prompts import CONTACT_BLOCK

        system = (
            "You are a luxury travel copywriter for BuyBusinessClass.com. "
            "Rewrite the caption based on the director's feedback. "
            "Keep max 350 chars. Premium whisper tone. "
            "ALWAYS end with:\n"
            + CONTACT_BLOCK
            + "\nWrite ONLY the new caption, nothing else."
        )

        prompt = f"CURRENT CAPTION:\n{original}\n\nDIRECTOR'S FEEDBACK:\n{feedback}"
        if context:
            prompt += f"\n\nCONTEXT:\n{context}"

        response = await asyncio.to_thread(
            client.messages.create,
            model=_get_model(),
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        result = response.content[0].text.strip()
        if "+1 888-322-7999" not in result:
            result = result.rstrip() + f"\n\n{CONTACT_BLOCK}"
        return result

    except Exception as e:
        log.warning("Claude rewrite error: %s", e)
        return original


async def parse_urgent_request(text: str) -> dict:
    """Parse free text into structured deal JSON."""
    client = _get_client()
    if not client:
        return {
            "event_name": text[:50],
            "from_iata": "JFK",
            "to_iata": "LHR",
            "city": "",
            "category": "travel",
            "image_prompt": "Beautiful destination photo, cinematic, no text",
            "event_context": "",
            "sales_hook": "Fly business class. Your move.",
        }

    try:
        from prompts.system_prompts import URGENT_PARSE_PROMPT

        system = (
            "You parse travel deal requests into JSON. "
            "Return ONLY valid JSON, no markdown, no explanation."
        )

        response = await asyncio.to_thread(
            client.messages.create,
            model=_get_model(),
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": URGENT_PARSE_PROMPT.format(text=text)}],
        )

        return _parse_json_response(response.content[0].text)

    except Exception as e:
        log.warning("Claude parse error: %s", e)
        return {
            "event_name": text[:50],
            "from_iata": "JFK",
            "to_iata": "LHR",
            "city": "",
            "category": "travel",
            "image_prompt": "Beautiful destination, cinematic, no text",
            "event_context": "",
            "sales_hook": "Fly business class. Your move.",
        }
