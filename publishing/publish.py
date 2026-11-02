"""
Publishing an approved campaign: the bridge between the content system and WhatsApp Web.

Only a campaign Betty approved is posted, with the caption of the version she approved and the
image that was reviewed. Each (campaign, chat) pair is posted at most once, recorded in
content.effects under publish:wa:<campaign_id>:<chat>:

  - posted / already_posted   → recorded as done; a second call returns the record
  - unconfirmed               → Send was pressed but not confirmed: recorded as done too, so it is
                                never retried on its own; a person checks the chat and, if the post
                                is not there, clears it with `python -m publishing forget`
  - failed before Send        → no record (nothing went out), it can simply be run again
  - an attempt that never finished (the process died) → refused until a person checks and clears it
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from publishing.whatsapp_web import NotConfirmed, PostResult, PublishError, WhatsAppWeb

log = logging.getLogger("bbc.publishing")
MAX_IMAGE_BYTES = 16 * 1024 * 1024
BROWSER_LEASE = "publish:whatsapp-web"     # the WhatsApp Web session is shared: posts go one at a time
LEASE_WAIT_S = 180.0
Download = Callable[[str], Awaitable[bytes]]


class NotApproved(PublishError):
    code = "not_approved"


class Unfinished(PublishError):
    code = "unfinished"


def effect_key(campaign_id: str, target: str) -> str:
    return f"publish:wa:{campaign_id}:{target}"


async def download(url: str) -> bytes:
    if urlsplit(url).scheme != "https":
        raise PublishError(f"image URL is not https: {url}")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        resp = await http.get(url)
    if resp.status_code != 200:
        raise PublishError(f"image download failed: HTTP {resp.status_code}")
    if not resp.headers.get("content-type", "").startswith("image/"):
        raise PublishError(f"not an image: {resp.headers.get('content-type')}")
    if len(resp.content) > MAX_IMAGE_BYTES:
        raise PublishError("image over 16 MB")
    return resp.content


async def publish_campaign(store: Any, wa: WhatsAppWeb, campaign_id: str, target: str, *,
                           enabled: bool, work_dir: Path, dry_run: bool = False,
                           fetch: Download = download) -> PostResult:
    if not dry_run and not enabled:
        raise PublishError("WA_ENABLED is not true — nothing is posted (use a dry run to test)")
    row = await store.campaign_for_publish(campaign_id)
    if row is None:
        raise NotApproved(f"{campaign_id} does not exist")
    if row["status"] != "approved":
        raise NotApproved(f"{campaign_id} is {row['status']}: only approved campaigns are published")
    caption, image_url = row.get("caption") or "", row.get("image_url") or ""
    if not caption or not image_url:
        raise PublishError(f"{campaign_id} has no caption or no image")
    key = effect_key(campaign_id, target)

    async def post() -> str:
        data = await fetch(image_url)
        path = Path(work_dir) / f"{campaign_id}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            result = await wa.post_image(target, path, caption, dry_run=dry_run)
        except NotConfirmed as e:
            result = PostResult("unconfirmed", target, "", "", (), str(e))
        log.info("%s → %s: %s %s", campaign_id, target, result.status, result.message_id or result.detail)
        return result.to_json()

    async with store.lease(BROWSER_LEASE, wait=LEASE_WAIT_S):     # one browser, one post at a time
        if dry_run:
            return PostResult.from_json(await post())
        status = await store.effect_status(key)
        if status not in (None, "done"):
            raise Unfinished(f"an earlier attempt for {campaign_id} → {target} did not finish ({status}). "
                             "Check the chat; if the post is not there: python -m publishing forget "
                             f"{campaign_id} --target \"{target}\"")
        # an error before Send means nothing went out: no record is left, so it can run again
        return PostResult.from_json(await store.once(key, post, forget_on=lambda e: True))


async def forget(store: Any, campaign_id: str, target: str) -> str | None:
    """Clear the record of one (campaign, chat) after a person checked the chat. Returns what it held."""
    key = effect_key(campaign_id, target)
    previous = await store.effect_status(key)
    await store.release(key)
    return previous
