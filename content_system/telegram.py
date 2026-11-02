"""
A small Telegram Bot API client for the content system, plus its buttons.

Unlike services/telegram_client.py it raises instead of returning None, so the graph knows when a
message did not go out (and retries only what is worth retrying). Text is sent without parse_mode:
a caption with a stray "_" or "*" can never break a send.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

BASE_URL = "https://api.telegram.org"
PREFIX = "cs"                                    # callback_data "cs:<a|r|e>:<campaign_id>:<version>"
ACTIONS = {"a": "approve", "r": "reject", "e": "edit"}
_CALLBACK = re.compile(r"^cs:([are]):(\d{4}-W\d{2}-[A-Z0-9]{1,6}-\d{3}):(\d{1,3})$")


class TelegramError(RuntimeError):
    def __init__(self, method: str, description: str, *, transient: bool, retry_after: float | None = None):
        super().__init__(f"Telegram {method}: {description}")
        self.method = method
        self.description = description
        self.transient = transient
        self.retry_after = retry_after


class TelegramAPI:
    def __init__(self, token: str, *, timeout: float = 30.0, base_url: str = BASE_URL):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        self._token = token
        self._timeout = timeout
        self._base = base_url

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "<token>")

    async def call(self, method: str, data: dict[str, Any], *, files: dict | None = None) -> Any:
        """POST one Bot API method; returns `result`. A 429 asking to wait up to 30 s is waited out once."""
        for attempt in (1, 2):
            try:
                return await self._post(method, data, files)
            except TelegramError as e:
                if attempt == 1 and e.retry_after is not None and e.retry_after <= 30:
                    await asyncio.sleep(e.retry_after)
                    continue
                raise
        raise AssertionError("unreachable")

    async def _post(self, method: str, data: dict[str, Any], files: dict | None) -> Any:
        url = f"{self._base}/bot{self._token}/{method}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                if files:
                    form = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                            for k, v in data.items() if v is not None}
                    resp = await http.post(url, data=form, files=files)
                else:
                    resp = await http.post(url, json={k: v for k, v in data.items() if v is not None})
        except httpx.HTTPError as e:
            raise TelegramError(method, self._scrub(f"{type(e).__name__}: {e}"), transient=True) from None
        try:
            body = resp.json()
        except ValueError:
            raise TelegramError(method, f"HTTP {resp.status_code}", transient=resp.status_code >= 500) from None
        if body.get("ok"):
            return body.get("result")
        description = self._scrub(str(body.get("description", f"HTTP {resp.status_code}")))
        retry_after = (body.get("parameters") or {}).get("retry_after")
        transient = resp.status_code == 429 or resp.status_code >= 500
        raise TelegramError(method, description, transient=transient, retry_after=retry_after)


def is_not_modified(err: TelegramError) -> bool:
    return "message is not modified" in err.description


# ── buttons ───────────────────────────────────────────────────────────────────

def review_keyboard(campaign_id: str, version: int) -> dict:
    data = f"{campaign_id}:{version}"
    return {"inline_keyboard": [
        [{"text": "✅ Approve", "callback_data": f"{PREFIX}:a:{data}"},
         {"text": "❌ Reject", "callback_data": f"{PREFIX}:r:{data}"}],
        [{"text": "✏️ Edit caption", "callback_data": f"{PREFIX}:e:{data}"}],
    ]}


def status_keyboard(text: str) -> dict:
    """One inert button that shows the outcome on the review message."""
    return {"inline_keyboard": [[{"text": text, "callback_data": "noop"}]]}


def parse_callback(data: str) -> tuple[str, str, int] | None:
    """'cs:a:2026-W43-V4-001:2' → ('approve', '2026-W43-V4-001', 2); anything else → None."""
    m = _CALLBACK.match(data or "")
    if not m:
        return None
    return ACTIONS[m.group(1)], m.group(2), int(m.group(3))
