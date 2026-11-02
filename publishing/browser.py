"""
A thin wrapper over the agent-browser CLI (vercel-labs/agent-browser 0.41.x).

Every call is one `agent-browser batch --json --bail` process with the commands on stdin as JSON.
Text never goes on the command line: the CLI reads global flags anywhere in argv, so a caption line
such as "--headed" would be taken as a flag. Inside a stdin batch the same text is inserted as is
(tests/test_publishing_browser.py checks this).

No AI drives the browser: the commands are fixed, the page is never shown to a model.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("bbc.publishing.browser")

# (argv, stdin, env, timeout_s) -> (exit code, stdout, stderr)
Runner = Callable[[list[str], bytes, dict[str, str], float], Awaitable[tuple[int, bytes, bytes]]]


class BrowserError(RuntimeError):
    """A command failed (element missing, timeout, browser down). `command` is the failing one."""

    def __init__(self, message: str, *, command: Sequence[str] | None = None):
        super().__init__(message)
        self.command = list(command) if command else None


@dataclass(frozen=True)
class BrowserConfig:
    binary: str = "agent-browser"
    session: str = "bbc-wa"
    profile_dir: str = ""                # persistent Chrome profile: holds the WhatsApp Web login
    headed: bool = False                 # on a server without a display agent-browser starts Xvfb
    executable_path: str = ""
    step_timeout_ms: int = 25_000
    idle_timeout: str = "10m"
    extra_env: dict[str, str] = field(default_factory=dict)

    def global_args(self) -> list[str]:
        args = ["--session", self.session, "--json", "--idle-timeout", self.idle_timeout]
        if self.profile_dir:
            args += ["--profile", self.profile_dir]
        if self.headed:
            args.append("--headed")
        if self.executable_path:
            args += ["--executable-path", self.executable_path]
        return args


async def subprocess_runner(argv: list[str], stdin: bytes, env: dict[str, str],
                            wait_s: float) -> tuple[int, bytes, bytes]:
    """In a worker thread: asyncio subprocesses need the Proactor loop on Windows, while psycopg
    (python -m publishing post) needs the Selector loop there."""
    try:
        done = await asyncio.to_thread(subprocess.run, argv, input=stdin, capture_output=True,
                                       env=env, timeout=wait_s, check=False)
    except subprocess.TimeoutExpired:
        raise BrowserError(f"agent-browser did not answer in {wait_s:.0f} s") from None
    except OSError as e:
        raise BrowserError(f"can't run {argv[0]}: {e}") from None
    return done.returncode, done.stdout, done.stderr


def resolve_binary(name: str) -> str:
    """The agent-browser executable to start. On Windows a global npm install leaves only
    agent-browser.cmd/.ps1 shims, which subprocess can't run safely: use the native .exe behind them."""
    if os.path.isfile(name):
        return name
    found = shutil.which(name)
    if found and found.lower().endswith((".cmd", ".ps1", ".bat")):
        exe = Path(found).parent / "node_modules" / "agent-browser" / "bin" / "agent-browser-win32-x64.exe"
        if exe.is_file():
            return str(exe)
    return found or name


def js_b64(script: str) -> str:
    return base64.b64encode(script.encode()).decode()


class AgentBrowser:
    def __init__(self, cfg: BrowserConfig, run: Runner | None = None):
        self.cfg = cfg
        self._run = run or subprocess_runner

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["AGENT_BROWSER_DEFAULT_TIMEOUT"] = str(self.cfg.step_timeout_ms)
        env.update(self.cfg.extra_env)
        return env

    async def batch(self, commands: Sequence[Sequence[str]], *, bail: bool = True) -> list[dict[str, Any]]:
        """Run commands in order; returns one entry per command that ran:
        {"command": [...], "success": bool, "result": {...} | None, "error": str | None}.
        With bail=True the first failure raises BrowserError."""
        if not commands:
            return []
        argv = [self.cfg.binary, *self.cfg.global_args(), "batch", "--json"]
        if bail:
            argv.append("--bail")
        payload = json.dumps([list(c) for c in commands], ensure_ascii=False).encode()
        wait_s = len(commands) * self.cfg.step_timeout_ms / 1000 + 30
        code, out, err = await self._run(argv, payload, self._env(), wait_s)
        results = _parse(out, err, code)
        if bail:
            for r in results:
                if not r.get("success"):
                    raise BrowserError(str(r.get("error") or "command failed"), command=r.get("command"))
        return results

    async def one(self, *command: str) -> dict[str, Any]:
        return (await self.batch([command]))[0].get("result") or {}

    async def ok(self, *command: str) -> bool:
        """True when the command succeeds; failures are an answer here, not an error."""
        results = await self.batch([command], bail=False)
        return bool(results and results[0].get("success"))

    async def counts(self, selectors: Sequence[str]) -> list[int]:
        results = await self.batch([["get", "count", s] for s in selectors], bail=False)
        found = {tuple(r.get("command") or ()): r for r in results}
        counts = []
        for s in selectors:
            r = found.get(("get", "count", s)) or {}
            counts.append(int((r.get("result") or {}).get("count") or 0) if r.get("success") else 0)
        return counts

    async def visible(self, selectors: Sequence[str]) -> list[bool]:
        """Whether the first match of each selector is on screen (a missing element is False)."""
        results = await self.batch([["is", "visible", s] for s in selectors], bail=False)
        found = {tuple(r.get("command") or ()): r for r in results}
        out = []
        for s in selectors:
            r = found.get(("is", "visible", s)) or {}
            out.append(bool(r.get("success") and (r.get("result") or {}).get("visible")))
        return out

    async def eval_json(self, script: str) -> Any:
        """Run our own JavaScript (passed base64-encoded) that returns JSON.stringify(...)."""
        result = await self.one("eval", "-b", js_b64(script))
        raw = result.get("result")
        return json.loads(raw) if isinstance(raw, str) else raw

    async def close(self) -> None:
        try:
            await self.batch([["close"]], bail=False)
        except BrowserError:
            log.warning("agent-browser close failed", exc_info=True)


def _parse(out: bytes, err: bytes, code: int) -> list[dict[str, Any]]:
    text = out.decode("utf-8", "replace").strip()
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):           # the CLI failed before running the batch
        raise BrowserError(str(data.get("error") or "agent-browser failed"))
    detail = (err.decode("utf-8", "replace").strip() or text)[-500:]
    raise BrowserError(f"agent-browser exited {code}: {detail or 'no output'}")
