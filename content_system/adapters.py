"""The real services behind the graph: Playwright renderer, Supabase Storage, the preview sheet."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from services.design_system import H, PostDesign

log = logging.getLogger("bbc.content.adapters")
ROOT = Path(__file__).resolve().parent.parent


class ImageConflict(RuntimeError):
    """The storage path already holds a different image; nothing was overwritten."""


def render_files(design: PostDesign, out_dir: Path, stem: str, caption: str) -> dict:
    """Render the post (design QA strict) and write <stem>.jpg + <stem>.txt."""
    from services.design_system import render_post

    res = render_post(design, strict=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    jpg = out_dir / f"{stem}.jpg"
    jpg.write_bytes(res.jpeg)
    (out_dir / f"{stem}.txt").write_text(caption, encoding="utf-8")
    return {
        "image_path": str(jpg),
        "image_sha256": hashlib.sha256(res.jpeg).hexdigest(),
        "tone": res.tone.value,
        "fare": res.fare,
        "text_bottom": round(res.metrics["textBottom"] / H, 3),
    }


def _already_exists(exc: Exception) -> bool:
    status = str(getattr(exc, "status", ""))
    return status == "409" or "already exists" in str(exc).lower() or "duplicate" in str(exc).lower()


def put_image(data: bytes, path: str) -> str:
    """Upload a new JPEG to the marketing bucket without overwriting anything; returns the public URL.

    A file already at `path` is accepted only when it is byte-for-byte this image (a retry)."""
    from config import settings
    from services.supabase_client import _get_client

    client = _get_client()
    if client is None:
        raise RuntimeError("Supabase is not configured (SUPABASE_URL / SUPABASE_KEY) — run with railway run")
    bucket = settings.supabase_storage_bucket
    storage = client.storage.from_(bucket)
    try:
        storage.upload(path=path, file=data, file_options={"content-type": "image/jpeg", "upsert": "false"})
    except Exception as e:
        if not _already_exists(e):
            raise
        existing = storage.download(path)
        if hashlib.sha256(existing).digest() != hashlib.sha256(data).digest():
            raise ImageConflict(f"{bucket}/{path} already holds a different image — nothing was overwritten") from None
        log.info("%s/%s already uploaded (same bytes)", bucket, path)
    return f"{settings.supabase_url}/storage/v1/object/public/{bucket}/{path}"


def get_image(url: str) -> bytes:
    import httpx

    r = httpx.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content


def preview_sheet(items: list[tuple[str, Path]], out: Path) -> Path:
    """All the posts side by side, in publishing order: (label, jpeg path) → one JPEG."""
    from PIL import Image, ImageDraw, ImageFont

    tw, th, gap, label_h = 432, 768, 24, 60
    sheet = Image.new("RGB", (gap + len(items) * (tw + gap), gap + th + label_h), (11, 24, 41))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(str(ROOT / "assets" / "fonts" / "PlayfairDisplay.ttf"), 26)
    except OSError:
        font = ImageFont.load_default()
    for k, (label, path) in enumerate(items):
        x = gap + k * (tw + gap)
        with Image.open(path) as im:
            sheet.paste(im.convert("RGB").resize((tw, th), Image.LANCZOS), (x, gap))
        draw.text((x, gap + th + 16), label, fill=(201, 165, 78), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=90)
    return out
