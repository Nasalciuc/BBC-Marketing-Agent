"""Fakes for the content system tests: Telegram, renderer, storage. No network, no Playwright."""
from __future__ import annotations

import hashlib
from pathlib import Path

from langgraph.types import RetryPolicy
from PIL import Image

from content_system.graph import Services, is_transient
from content_system.manifest import load_week
from services.design_system import PRICED_STYLES

ROOT = Path(__file__).resolve().parent.parent
W42 = ROOT / "weeks" / "2026-W42.toml"
CHAT = -100123
FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.01, backoff_factor=1.0, jitter=False,
                         retry_on=is_transient)


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.fail: dict[str, list[Exception]] = {}
        self._next = 1000

    async def call(self, method: str, data: dict, *, files: dict | None = None):
        self.calls.append((method, dict(data), files))
        queue = self.fail.get(method)
        if queue:
            raise queue.pop(0)
        if method in ("sendPhoto", "sendMessage"):
            self._next += 1
            msg = {"message_id": self._next, "chat": {"id": CHAT}}
            if method == "sendPhoto":
                msg["photo"] = [{"file_id": f"thumb{self._next}"}, {"file_id": f"F{self._next}"}]
            return msg
        return True

    def sent(self, method: str) -> list[tuple[str, dict, dict | None]]:
        return [c for c in self.calls if c[0] == method]


def fake_render(design, out_dir: Path, stem: str, caption: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.jpg"
    Image.new("RGB", (108, 192), (sum(map(ord, stem)) % 255, 40, 80)).save(path, quality=90)
    (out_dir / f"{stem}.txt").write_text(caption, encoding="utf-8")
    data = path.read_bytes()
    return {"image_path": str(path), "image_sha256": hashlib.sha256(data).hexdigest(), "tone": "dark",
            "fare": 1234 if design.style in PRICED_STYLES else None, "text_bottom": 0.4}


class FakeStorage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail: list[Exception] = []

    def put(self, data: bytes, path: str) -> str:
        if self.fail:
            raise self.fail.pop(0)
        self.files[path] = data
        return f"https://example.supabase.co/storage/v1/object/public/marketing/{path}"

    def get(self, url: str) -> bytes:
        return self.files[url.split("/marketing/", 1)[1]]


def make_services(tmp_path: Path, store) -> tuple[Services, FakeTelegram, FakeStorage]:
    tg, storage = FakeTelegram(), FakeStorage()
    services = Services(store=store, render=fake_render, output_root=tmp_path, put_image=storage.put,
                        get_image=storage.get, telegram=tg, chat_id=CHAT)
    return services, tg, storage


def week42():
    return load_week(W42)
