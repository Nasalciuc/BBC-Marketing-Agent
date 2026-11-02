"""
The two models PR-4 calls, behind plain async callables so the tests use fakes:

  Generate = prompt → image bytes          Gemini (gemini-3.1-flash-image), 9:16, 2K
  Judge    = (jpeg, brief) → verdict dict  Claude (claude-sonnet-5-5) looks at the photo against the rules

The verdict is the model's reading; whether a candidate passes is decided in photos.verdict_problems,
from its answers, not by the model.

Both calls are bounded (a timeout, a few retries on 429/5xx). A wrong key, model name or setting is
raised as ModelSetupError: every further call would fail the same way and cost money, so the run stops.
"""
from __future__ import annotations

import asyncio
import base64
import io
from collections.abc import Awaitable, Callable
from typing import Any

from PIL import Image

from content_system.visual.brief import RULES, ImageBrief

Generate = Callable[[str], Awaitable[bytes]]
Judge = Callable[[bytes, ImageBrief], Awaitable[dict[str, Any]]]


class ImageModelError(RuntimeError):
    pass


class ModelSetupError(RuntimeError):
    """A key, a model name or a setting is wrong: retrying won't help."""


TIMEOUT_S = 180                      # one Gemini request; the SDK retries up to 3 attempts
RETRY_STATUS = [408, 429, 500, 502, 503, 504]
SETUP_STATUS = (400, 401, 403, 404)


def _no_image(model: str, response: Any) -> ImageModelError:
    why: list[str] = []
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        why.append(f"prompt blocked: {feedback.block_reason}")
    for candidate in response.candidates or []:
        if candidate.finish_reason:
            why.append(str(candidate.finish_reason))
        for part in (candidate.content.parts if candidate.content else None) or []:
            if part.text and not part.thought:
                why.append(part.text.strip()[:160])
    return ImageModelError(f"{model} returned no image ({'; '.join(why) or 'empty answer'})")


def gemini_generator(model: str, api_key: str, image_size: str = "2K") -> Generate:
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=TIMEOUT_S * 1000,
        retry_options=types.HttpRetryOptions(attempts=3, initial_delay=2.0, http_status_codes=RETRY_STATUS)))
    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="9:16", image_size=image_size),
    )

    async def generate(prompt: str) -> bytes:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=model, contents=prompt, config=config),
                timeout=TIMEOUT_S * 3 + 60)
        except errors.ClientError as e:
            if e.code in SETUP_STATUS:
                raise ModelSetupError(f"Gemini {model}: {e.code} {e.status or ''} {e.message or ''}".strip()) from e
            raise
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                if part.thought:                 # a draft from the model's thinking, not the answer
                    continue
                data = getattr(part.inline_data, "data", None) if part.inline_data else None
                if data:
                    return data if isinstance(data, bytes) else base64.b64decode(data)
        raise _no_image(model, response)

    return generate


VERDICT_TOOL = {
    "name": "verdict",
    "description": "Report what the photograph shows, against the brief and the rules.",
    "input_schema": {
        "type": "object",
        "properties": {
            "one_photograph": {"type": "boolean", "description": "One real photograph, one camera, one "
                               "perspective. False for a collage, two images joined, a split frame, or a "
                               "foreground pasted in front of a separate background."},
            "crowd": {"type": "boolean", "description": "A crowd, an audience or many people are visible."},
            "people": {"type": "string", "enum": ["none", "few_left_small_blurred", "elsewhere"],
                       "description": "none; or a few people only in the left third, small and out of focus; "
                                      "or people anywhere else (right half, foreground, in focus, large)."},
            "text_or_logo": {"type": "boolean", "description": "Any readable letters, numbers, signs, logos, "
                             "brand names or watermarks."},
            "matches_brief": {"type": "boolean", "description": "It shows the brief's scene from the brief's "
                              "vantage, with the brief's foreground element."},
            "calm_top": {"type": "boolean", "description": "The upper 45% is calm enough to set a headline on."},
            "artifacts": {"type": "boolean", "description": "Visible generation errors: malformed hands, faces "
                          "or objects, warped architecture, impossible geometry."},
            "score": {"type": "integer", "minimum": 1, "maximum": 10,
                      "description": "How well it serves a premium business-class travel post, 10 = ideal."},
            "problems": {"type": "array", "items": {"type": "string"}, "description": "Short, concrete."},
        },
        "required": ["one_photograph", "crowd", "people", "text_or_logo", "matches_brief", "calm_top",
                     "artifacts", "score", "problems"],
    },
}


def _small(jpeg: bytes, width: int = 768) -> bytes:
    with Image.open(io.BytesIO(jpeg)) as im:
        im = im.convert("RGB")
        im.thumbnail((width, width * 2))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue()


def judge_prompt(brief: ImageBrief) -> str:
    return (f"Brief — scene: {brief.scene}\nvantage: {brief.vantage}\nforeground element: {brief.device}\n"
            f"time: {brief.time or 'any'}\npeople allowed: {brief.people}\n\n{RULES}\n"
            "Look at the photograph and call `verdict`. Be strict: this is for a luxury travel brand.")


def claude_judge(model: str, api_key: str) -> Judge:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=90, max_retries=2)
    setup = (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError,
             anthropic.BadRequestError)

    async def judge(jpeg: bytes, brief: ImageBrief) -> dict[str, Any]:
        try:
            response = await client.messages.create(
                model=model, max_tokens=800, tools=[VERDICT_TOOL], tool_choice={"type": "tool", "name": "verdict"},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(_small(jpeg)).decode()}},
                    {"type": "text", "text": judge_prompt(brief)},
                ]}],
            )
        except setup as e:
            raise ModelSetupError(f"Claude {model}: {type(e).__name__}: {e}"[:300]) from e
        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == "verdict":
                return dict(block.input)
        raise ImageModelError(f"{model} gave no verdict")

    return judge
