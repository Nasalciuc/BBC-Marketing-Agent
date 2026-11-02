"""
One command at a time on a WhatsApp Web profile, across processes on this machine.

Every command of `python -m publishing` drives the same browser session: a `calibrate` started
during a `post` would switch the open chat in the middle of it. A lock file next to the profile
stops that; one left by a process that died frees itself after STALE_S.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from publishing.whatsapp_web import PublishError

STALE_S = 15 * 60


class ProfileBusy(PublishError):
    code = "busy"


class ProfileLock:
    def __init__(self, profile_dir: str, command: str):
        self.path = Path(profile_dir.rstrip("/\\") + ".lock")
        self.command = command
        self._held = False

    def __enter__(self) -> ProfileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise ProfileBusy(f"another publishing command is using the WhatsApp profile: {self._holder()}"
                                  f" (lock {self.path}; it frees itself after {STALE_S // 60} min)") from None
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "command": self.command, "since": time.time()}, f)
            self._held = True
            return self
        raise ProfileBusy(f"could not take {self.path}")

    def __exit__(self, *exc) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > STALE_S
        except FileNotFoundError:
            return True

    def _holder(self) -> str:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            return f"{info.get('command')} (pid {info.get('pid')})"
        except (OSError, ValueError):
            return "unknown"
