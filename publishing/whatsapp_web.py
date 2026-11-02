"""
Posts one image with its caption into one WhatsApp group, through WhatsApp Web.

agent-browser runs fixed steps; no AI reads the page or chooses what to click, so a message in
the group ("ignore your instructions and post in …") is only text on a page nobody obeys.

Guards, in the order they run:
  1. the target is on the allowlist (WA_TARGETS) — nothing else can be opened;
  2. the chat that opens has exactly that name in its header ("BBC Deals" never opens "BBC Deals 2"),
     and it is a group (or channel), never a person who happens to have the same name;
  3. the chat lets this account write (an admins-only group stops here);
  4. the same caption among the last outgoing messages is not sent again — read only after the
     chat's messages have finished loading;
  5. the caption box (never the chat's own message box) holds exactly our text before Send;
  6. after Send a new outgoing message with exactly our text must get its tick.

Anything that goes wrong after Send was pressed is NotConfirmed: the post may be out, so the caller
records it and a person looks — it is never retried on its own. Anything before Send leaves the
image preview closed and nothing sent, so it can be retried.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from publishing.browser import AgentBrowser, BrowserError

SELECTORS_FILE = Path(__file__).with_name("selectors.json")
GROUP_SUFFIXES = ("@g.us", "@newsletter")
RECENT = 20                  # outgoing messages compared before posting
READ_MORE = "Read more"


class PublishError(RuntimeError):
    code = "error"


class NotAllowed(PublishError):
    code = "not_allowed"


class NeedsLogin(PublishError):
    code = "needs_login"


class ChatNotFound(PublishError):
    code = "chat_not_found"


class WrongChat(PublishError):
    code = "wrong_chat"


class CannotPost(PublishError):
    code = "cannot_post"


class CaptionMismatch(PublishError):
    code = "caption_mismatch"


class NotConfirmed(PublishError):
    """Send was pressed but the post could not be confirmed. It may be out: a person checks the chat."""
    code = "not_confirmed"


@dataclass(frozen=True)
class PostResult:
    status: str                  # posted | already_posted | dry_run | unconfirmed
    target: str
    message_id: str = ""
    at: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> PostResult:
        d = json.loads(raw)
        return cls(d["status"], d["target"], d.get("message_id", ""), d.get("at", ""),
                   tuple(d.get("evidence") or ()), d.get("detail", ""))


# ── text ──────────────────────────────────────────────────────────────────────

_DROP = {"️", "︎", "‍", "​", "⁠", "﻿", "⃣"}
_EMOJI_NOT_SO = set("‼⁉ℹ〰")      # emoji whose base character is not a "symbol, other"
_EMOJI_RANGES = ((0x2190, 0x21FF), (0x2300, 0x23FF), (0x2B00, 0x2BFF), (0x1F000, 0x1FAFF))
_TIME_TAIL = re.compile(r"(\s*[Ee]dited)?\s*\d{1,2}:\d{2}(\s?[AaPp][Mm])?\s*$")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.replace(" ", " ").replace("﻿", "")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _is_emoji(ch: str) -> bool:
    if ch in _DROP or ch in _EMOJI_NOT_SO:
        return True
    if unicodedata.category(ch) in ("So", "Sk", "Cs", "Co", "Cn"):
        return True
    return any(lo <= ord(ch) <= hi for lo, hi in _EMOJI_RANGES)


_KEYCAP = re.compile("[0-9#*]\ufe0f?\u20e3")


def fingerprint(text: str) -> str:
    """The words in order. Emoji are left out on both sides (WhatsApp draws them as images, with
    or without alt text) and so is spacing; everything else must match."""
    text = _KEYCAP.sub("", normalize(text))
    return " ".join("".join(ch for ch in text if not _is_emoji(ch)).split())


def same_text(ours: str, shown: str) -> bool:
    """True when `shown` (a message in the chat) is our caption: equal, after its trailing time,
    or — when WhatsApp cut it with "Read more" — a long-enough start of it."""
    mine = fingerprint(ours)
    theirs = _TIME_TAIL.sub("", fingerprint(shown)).strip()
    if not mine:
        return False
    if theirs.endswith(READ_MORE):
        head = theirs[: -len(READ_MORE)].strip()
        return len(head) >= 40 and mine.startswith(head)
    return theirs == mine


def caption_commands(caption: str) -> list[list[str]]:
    """Insert the caption line by line; Shift+Enter makes a new line (Enter would send)."""
    lines = normalize(caption).split("\n")
    commands: list[list[str]] = []
    for i, line in enumerate(lines):
        if line:
            commands.append(["keyboard", "inserttext", line])
        if i < len(lines) - 1:
            commands.append(["press", "Shift+Enter"])
    return commands


def css_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40] or "chat"


def chat_jid(message_id: str) -> str:
    """"true_120363…@g.us_3EB0…" → "120363…@g.us" (WhatsApp Web's data-id: fromMe_chat_id[_author])."""
    parts = message_id.split("_")
    return parts[1] if len(parts) >= 3 and parts[0] in ("true", "false") else ""


class Selectors:
    def __init__(self, table: dict[str, list[str]]):
        self.table = table

    @classmethod
    def load(cls, path: str | Path | None = None) -> Selectors:
        data = json.loads(Path(path or SELECTORS_FILE).read_text(encoding="utf-8"))
        return cls({k: list(v) for k, v in data.items() if not k.startswith("_")})

    def get(self, key: str, **fmt: str) -> list[str]:
        return [s.replace("{name}", fmt["name"]) if "name" in fmt else s for s in self.table[key]]


# ── the page's own JavaScript (ours, passed base64-encoded) ───────────────────

# Text as a reader sees it: emoji images count as their alt text, hidden parts and icons
# (svg titles) don't, block boundaries are line breaks.
_JS_TEXT_OF = """
const textOf = (el) => {
  let out = '';
  const walk = (n) => {
    if (n.nodeType === 3) { out += n.nodeValue; return; }
    if (n.nodeType !== 1) return;
    const tag = n.tagName.toLowerCase();
    if (tag === 'svg' || tag === 'script' || tag === 'style') return;
    const cs = getComputedStyle(n);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (tag === 'img') { out += n.getAttribute('alt') || ''; return; }
    if (tag === 'br') { out += '\\n'; return; }
    const block = !cs.display.startsWith('inline');
    if (block) out += '\\n';
    for (const c of n.childNodes) walk(c);
    if (block) out += '\\n';
  };
  walk(el);
  return out;
};
const idOf = (m, i) => (m.closest('[data-id]') || m).getAttribute('data-id') || ('#' + i);
"""


def _js_outgoing(selector: str, text_selectors: list[str], limit: int) -> str:
    return ("(() => {" + _JS_TEXT_OF + " const T = " + json.dumps(text_selectors) + ";"
            " const n = [...document.querySelectorAll(" + json.dumps(selector) + ")];"
            " return JSON.stringify(n.map((m, i) => { let t = null;"
            "  for (const s of T) { t = m.querySelector(s); if (t) break; }"
            "  return {id: idOf(m, i), text: textOf(t || m).trim()}; }).slice(-" + str(int(limit)) + ")); })()")


def _js_state(selector: str, message_id: str, tick: list[str], clock: list[str], failed: list[str]) -> str:
    return ("(() => {" + _JS_TEXT_OF + " const all = [...document.querySelectorAll(" + json.dumps(selector)
            + ")]; const m = all.find((x, i) => idOf(x, i) === " + json.dumps(message_id) + ");"
            " if (!m) return JSON.stringify('missing');"
            " const h = m.closest('[data-id]') || m;"
            " const has = list => list.some(s => h.querySelector(s));"
            " return JSON.stringify(has(" + json.dumps(failed) + ") ? 'failed' : has(" + json.dumps(tick)
            + ") ? 'sent' : has(" + json.dumps(clock) + ") ? 'pending' : 'unknown'); })()")


def _js_text(selector: str) -> str:
    return ("(() => {" + _JS_TEXT_OF + " const e = document.querySelector(" + json.dumps(selector) + ");"
            " return JSON.stringify(e ? textOf(e) : null); })()")


def _js_in_composer(selector: str) -> str:
    return ("(() => { const e = document.querySelector(" + json.dumps(selector) + ");"
            " return JSON.stringify(!!(e && e.closest('#main footer'))); })()")


def _js_messages(selector: str) -> str:
    """How many messages the open chat shows, and the chat id taken from any of them."""
    return ("(() => { const n = [...document.querySelectorAll(" + json.dumps(selector) + ")];"
            " const ids = n.map(m => (m.closest('[data-id]') || m).getAttribute('data-id')).filter(Boolean);"
            " return JSON.stringify({count: n.length, id: ids[0] || ''}); })()")


class WhatsAppWeb:
    def __init__(self, browser: AgentBrowser, selectors: Selectors, *, url: str,
                 allowed_targets: frozenset[str], evidence_dir: Path,
                 ready_timeout_s: float = 90.0, sent_timeout_s: float = 60.0, poll_s: float = 1.0,
                 settle_s: float = 2.0, settle_max_s: float = 15.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.b = browser
        self.sel = selectors
        self.url = url
        self.allowed = allowed_targets
        self.evidence_dir = Path(evidence_dir)
        self.ready_timeout_s = ready_timeout_s
        self.sent_timeout_s = sent_timeout_s
        self.poll_s = poll_s
        self.settle_s = settle_s
        self.settle_max_s = settle_max_s
        self._sleep = sleep
        self._now = now

    # ── small helpers ──
    async def first(self, key: str, *, visible: bool = True, **fmt: str) -> str | None:
        """The first candidate selector present on the page — on screen, unless visible=False
        (a file input is hidden by design; messages may sit outside the viewport)."""
        candidates = self.sel.get(key, **fmt)
        if visible:
            hits = await self.b.visible(candidates)
        else:
            hits = [c > 0 for c in await self.b.counts(candidates)]
        for selector, hit in zip(candidates, hits, strict=True):
            if hit:
                return selector
        return None

    async def wait_first(self, key: str, seconds: float, *, visible: bool = True, **fmt: str) -> str | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while True:
            found = await self.first(key, visible=visible, **fmt)
            if found or loop.time() >= deadline:
                return found
            await self._sleep(self.poll_s)

    async def screenshot(self, name: str, target: str = "") -> str:
        stamp = self._now().strftime("%Y%m%dT%H%M%S")
        path = self.evidence_dir / f"{stamp}-{slug(target)}-{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.b.one("screenshot", str(path))
        return str(path)

    async def text_of(self, selector: str) -> str:
        return await self.b.eval_json(_js_text(selector)) or ""

    async def header(self) -> str:
        """The open chat's name; read even while the image preview covers it."""
        selector = await self.first("header_title", visible=False)
        if not selector:
            return ""
        result = await self.b.one("get", "attr", selector, "title")
        return str(result.get("value") or "") or normalize(await self.text_of(selector))

    async def outgoing(self, limit: int = RECENT) -> list[dict[str, str]]:
        selector = await self.first("outgoing", visible=False)
        if not selector:
            return []
        return await self.b.eval_json(_js_outgoing(selector, self.sel.get("message_text"), limit)) or []

    async def messages(self) -> dict[str, Any]:
        return await self.b.eval_json(_js_messages(self.sel.get("any_message")[0])) or {"count": 0, "id": ""}

    async def settled_outgoing(self) -> list[dict[str, str]]:
        """The last outgoing messages, read only once the chat has loaded: a fresh browser shows the
        header before the messages. Loaded = at least one message, and the count unchanged for
        `settle_s`. A chat that stays empty for `settle_max_s` is taken as empty."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        last, stable_since = -1, start
        while True:
            count = (await self.messages())["count"]
            now = loop.time()
            if count != last:
                last, stable_since = count, now
            elif count > 0 and now - stable_since >= self.settle_s:
                return await self.outgoing()
            if now - start >= self.settle_max_s:
                if count == 0:
                    return []
                raise PublishError("the chat's messages kept changing — not safe to check for an earlier post")
            await self._sleep(self.poll_s)

    # ── steps ──
    async def open(self) -> str:
        """Open WhatsApp Web and wait for it: "ready", "login" (QR on screen) or "unknown"."""
        current = str((await self.b.one("get", "url")).get("url") or "")
        if not current.startswith(self.url.rstrip("/")):
            await self.b.one("open", self.url)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.ready_timeout_s
        while loop.time() < deadline:
            if await self.first("app_ready"):
                return "ready"
            if await self.first("qr"):
                return "login"
            # "WhatsApp is open in another window" → take it over here
            await self.b.ok("find", "role", "button", "click", "--name", "Use here", "--exact")
            await self._sleep(self.poll_s)
        return "unknown"

    async def open_chat(self, target: str) -> None:
        if target not in self.allowed:
            raise NotAllowed(f"{target!r} is not in WA_TARGETS")
        state = await self.open()
        if state == "login":
            raise NeedsLogin("WhatsApp Web shows the QR code: run `python -m publishing login`")
        if state != "ready":
            raise PublishError("WhatsApp Web did not finish loading")
        if await self.header() != target:
            box = await self.first("search_box")
            if not box:
                raise PublishError("search box not found — run `python -m publishing calibrate`")
            await self.b.batch([["click", box], ["press", "Control+a"], ["press", "Backspace"],
                                ["keyboard", "type", target]])
            row = await self.wait_first("chat_in_list", 10, name=css_quote(target))
            if not row:
                raise ChatNotFound(f"no chat named exactly {target!r} — is this account in it?")
            await self.b.one("click", row)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            opened = await self.header()
            while opened != target and loop.time() < deadline:
                await self._sleep(self.poll_s)
                opened = await self.header()
            if opened != target:
                raise WrongChat(f"asked for {target!r}, the open chat is {opened!r}")
        jid = chat_jid((await self.messages())["id"])
        if jid and not jid.endswith(GROUP_SUFFIXES):
            raise WrongChat(f"{target!r} is a person ({jid}), not a group — another chat has this name")

    async def post_image(self, target: str, image: Path, caption: str, *,
                         dry_run: bool = False, allow_repeat: bool = False) -> PostResult:
        image = Path(image)
        if not image.is_file() or image.stat().st_size == 0:
            raise PublishError(f"image not found: {image}")
        if not fingerprint(caption):
            raise PublishError("empty caption")
        await self.open_chat(target)
        if not await self.first("composer"):
            raise CannotPost(f"{target!r} does not let this account write (admins only?)")
        before = await self.settled_outgoing()
        if not allow_repeat:
            for m in before:
                if same_text(caption, m["text"]):
                    return PostResult("already_posted", target, m["id"], self._now().isoformat())
        attach = await self.first("attach_button")
        if not attach:
            raise PublishError("attach button not found — run `python -m publishing calibrate`")

        evidence: list[str] = []
        send_pressed = False
        upload: Path | None = None
        try:
            await self.b.one("click", attach)
            file_input = await self.wait_first("image_input", 5, visible=False)
            if not file_input:
                raise PublishError("image input not found after Attach — run calibrate")
            # a fresh file name each time: re-picking the same file fires no "change" event
            upload = self.evidence_dir / f"upload-{uuid.uuid4().hex[:12]}{image.suffix.lower() or '.jpg'}"
            upload.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image, upload)
            await self.b.one("upload", file_input, str(upload.resolve()))
            box = await self.wait_first("caption_box", 15)
            if not box:
                raise PublishError("the image preview did not open (or its caption box changed — run calibrate)")
            if await self.b.eval_json(_js_in_composer(box)):
                # typing there would send the caption as plain text, without the image
                raise PublishError(f"caption selector {box!r} matches the chat's message box — fix selectors.json")
            await self.b.batch([["click", box], *caption_commands(caption)])
            typed = await self.text_of(box)
            if fingerprint(typed) != fingerprint(caption):
                raise CaptionMismatch(f"the caption box holds {typed[:80]!r}…, not our text")
            opened = await self.header()
            if opened != target:
                raise WrongChat(f"asked for {target!r}, the open chat is {opened!r}")
            evidence.append(await self.screenshot("before-send", target))
            if dry_run:
                await self.discard()
                return PostResult("dry_run", target, "", self._now().isoformat(), tuple(evidence))
            send = await self.first("send_button")
            send_pressed = True
            await self.b.one(*(("click", send) if send else ("press", "Enter")))
            message_id = await self._confirm(caption, {m["id"] for m in before})
        except BaseException as e:
            await self.discard()
            if send_pressed and not isinstance(e, NotConfirmed) and isinstance(e, Exception):
                raise NotConfirmed(f"after Send: {e}") from e
            raise
        finally:
            if upload is not None:
                upload.unlink(missing_ok=True)
        with contextlib.suppress(BrowserError):          # the post is confirmed; a missing screenshot is not
            evidence.append(await self.screenshot("after-send", target))
        return PostResult("posted", target, message_id, self._now().isoformat(), tuple(evidence))

    async def _confirm(self, caption: str, before_ids: set[str]) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.sent_timeout_s
        message_id, state = "", "missing"
        selector = await self.first("outgoing", visible=False) or self.sel.get("outgoing")[0]
        while loop.time() < deadline:
            if not message_id:
                for m in await self.outgoing():
                    if m["id"] not in before_ids and same_text(caption, m["text"]):
                        message_id = m["id"]
            if message_id:
                state = await self.b.eval_json(_js_state(
                    selector, message_id, self.sel.get("tick"), self.sel.get("clock"), self.sel.get("failed")))
                if state == "sent":
                    return message_id
                if state == "failed":
                    raise NotConfirmed("WhatsApp marked the message as not sent")
            await self._sleep(self.poll_s)
        if not message_id:
            raise NotConfirmed("Send was pressed but the message did not appear in the chat")
        raise NotConfirmed(f"the message is in the chat but still {state} after {self.sent_timeout_s:.0f} s")

    async def discard(self) -> None:
        """Close the image preview (or the attach menu) without sending. Best effort, never raises."""
        try:
            boxes = self.sel.get("caption_box")
            for _ in range(5):
                if not any(await self.b.visible(boxes)):
                    await self.b.ok("press", "Escape")        # closes the attach menu if it is open
                    return
                await self.b.ok("press", "Escape")
                await self._sleep(self.poll_s)
                if any(await self.b.visible(boxes)):           # "Discard unsent message?"
                    await self.b.ok("find", "role", "button", "click", "--name", "Discard", "--exact")
        except BrowserError:
            pass

    async def calibrate(self, target: str) -> list[dict[str, Any]]:
        """Which selector of each step matches on the real page, with a screenshot per step."""
        report: list[dict[str, Any]] = []

        async def note(step: str, key: str, *, visible: bool = True) -> str | None:
            found = await self.first(key, visible=visible)
            report.append({"step": step, "key": key, "selector": found,
                           "screenshot": await self.screenshot(step, target)})
            return found

        state = await self.open()
        report.append({"step": "open", "key": "state", "selector": state,
                       "screenshot": await self.screenshot("open", target)})
        if state != "ready":
            return report
        await note("search", "search_box")
        try:
            await self.open_chat(target)
        except PublishError as e:
            report.append({"step": "open_chat", "key": e.code, "selector": str(e), "screenshot": ""})
            return report
        await note("chat", "header_title", visible=False)
        await note("composer", "composer")
        await note("outgoing", "outgoing", visible=False)
        last = await self.outgoing(3)
        report.append({"step": "last_outgoing", "key": "message_text", "selector": last, "screenshot": ""})
        report.append({"step": "chat_id", "key": "any_message", "selector": (await self.messages())["id"],
                       "screenshot": ""})
        attach = await note("attach", "attach_button")
        if attach:
            await self.b.one("click", attach)
            found = await self.first("image_input", visible=False)
            report.append({"step": "attach_menu", "key": "image_input", "selector": found,
                           "screenshot": await self.screenshot("attach_menu", target)})
            await self.discard()
        return report
