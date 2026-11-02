"""
python -m publishing <command>      WhatsApp Web through agent-browser, fixed steps, no AI at the wheel

  status                                   is WhatsApp Web logged in on this profile?
  login [--telegram]                       show the QR code (a PNG, or sent to WA_LOGIN_CHAT_ID) until scanned
  calibrate --target NAME                  which selectors match on the real page, a screenshot per step
  send --target NAME --image F --caption-file T [--dry-run] [--allow-repeat]
                                           post one image by hand (tests, calibration)
  post CAMPAIGN_ID [--target NAME] [--dry-run]
                                           post an approved campaign, at most once per chat  (railway run)
  forget CAMPAIGN_ID --target NAME --checked
                                           clear an "unconfirmed" or unfinished record after you looked
                                           at the chat and the post is not there                (railway run)

Nothing is posted unless WA_ENABLED=true; --dry-run stops right before Send and needs no switch.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from publishing.browser import AgentBrowser, BrowserError
from publishing.lock import ProfileLock
from publishing.settings import PublishSettings, get_settings
from publishing.whatsapp_web import PostResult, PublishError, Selectors, WhatsAppWeb

ROOT = Path(__file__).resolve().parent.parent
LOGIN_ROUNDS = 9          # a QR code lives ~20 s; 9 rounds of 20 s ≈ 3 minutes to scan
BROWSER_COMMANDS = ("status", "login", "calibrate", "send", "post")


def stop(message: str) -> None:
    print(f"STOP — {message}", file=sys.stderr)
    raise SystemExit(1)


def show(result: PostResult) -> None:
    print(result.to_json())
    if result.status == "unconfirmed":
        stop("Send was pressed but the post could not be confirmed. Look at the chat: if the post is not "
             "there, run `python -m publishing forget …`; it is not retried on its own.")


def read_caption(path: str) -> str:
    """UTF-8 (with or without BOM) or UTF-16 — what Windows PowerShell writes by default."""
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def build(ws: PublishSettings) -> WhatsAppWeb:
    evidence = Path(ws.evidence_dir)
    if not evidence.is_absolute():
        evidence = ROOT / evidence
    return WhatsAppWeb(AgentBrowser(ws.browser_config()), Selectors.load(ws.selectors_file or None),
                       url=ws.url, allowed_targets=ws.allowed_targets(), evidence_dir=evidence,
                       ready_timeout_s=ws.ready_timeout_s, sent_timeout_s=ws.sent_timeout_s)


async def login(wa: WhatsAppWeb, notify: Callable[[str], Awaitable[None]] | None) -> str:
    state = await wa.open()
    for _ in range(LOGIN_ROUNDS):
        if state != "login":
            return state
        shot = await wa.screenshot("qr")
        if notify:
            await notify(shot)
        else:
            print(f"  scan the QR code (window, or {shot}): WhatsApp → Linked devices → Link a device")
        state = "ready" if await wa.wait_first("app_ready", 20) else ("login" if await wa.first("qr") else "unknown")
    return state


async def cmd_status(args, ws, wa) -> None:
    state = await wa.open()
    print(json.dumps({"state": state, "screenshot": await wa.screenshot("status")}))
    if state != "ready":
        raise SystemExit(1)


async def cmd_login(args, ws, wa) -> None:
    notify, sent, tg = None, [], None
    if args.telegram:
        from config import settings as app
        from content_system.telegram import TelegramAPI

        if not ws.login_chat_id:
            stop("WA_LOGIN_CHAT_ID is not set")
        tg = TelegramAPI(app.telegram_bot_token)

        async def notify(path: str) -> None:
            photo = await asyncio.to_thread(Path(path).read_bytes)
            msg = await tg.call("sendPhoto", {"chat_id": ws.login_chat_id,
                                              "caption": "WhatsApp → Linked devices → Link a device. "
                                                         "The code changes every ~20 s."},
                                files={"photo": ("qr.png", photo, "image/png")})
            sent.append(msg["message_id"])

    try:
        state = await login(wa, notify)
    finally:
        for message_id in sent:      # a QR code links a device: never leave one lying in a chat
            try:
                await tg.call("deleteMessage", {"chat_id": ws.login_chat_id, "message_id": message_id})
            except Exception:  # noqa: BLE001
                logging.getLogger("bbc.publishing").warning("could not delete QR message %s", message_id)
    print("✓ WhatsApp Web is logged in" if state == "ready" else f"STOP — still {state}")
    if state != "ready":
        raise SystemExit(1)


async def cmd_calibrate(args, ws, wa) -> None:
    for row in await wa.calibrate(args.target):
        print(json.dumps(row))


async def cmd_send(args, ws, wa) -> None:
    if not args.dry_run and not ws.enabled:
        stop("WA_ENABLED is not true — use --dry-run to test")
    caption = await asyncio.to_thread(read_caption, args.caption_file)
    show(await wa.post_image(args.target, Path(args.image), caption,
                             dry_run=args.dry_run, allow_repeat=args.allow_repeat))


async def _with_store(fn):
    from content_system.__main__ import _open, _require
    from content_system.store import PgStore

    _, cs = _require()
    db = await _open(cs)
    try:
        return await fn(PgStore(db.pool))
    finally:
        await db.close()


def _target(args, ws) -> str:
    target = args.target or ws.default_target
    if not target:
        stop("no --target and no WA_DEFAULT_TARGET")
    return target


async def cmd_post(args, ws, wa) -> None:
    from publishing.publish import publish_campaign

    target = _target(args, ws)
    show(await _with_store(lambda store: publish_campaign(
        store, wa, args.campaign_id, target, enabled=ws.enabled, work_dir=wa.evidence_dir,
        dry_run=args.dry_run)))


async def cmd_forget(args, ws, wa) -> None:
    from publishing.publish import forget

    if not args.checked:
        stop("look at the chat first; add --checked to confirm the post is not there")
    previous = await _with_store(lambda store: forget(store, args.campaign_id, _target(args, ws)))
    print(f"✓ cleared (was: {previous or 'nothing recorded'})")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m publishing", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    lg = sub.add_parser("login")
    lg.add_argument("--telegram", action="store_true", help="send the QR code to WA_LOGIN_CHAT_ID")
    cal = sub.add_parser("calibrate")
    cal.add_argument("--target", required=True)
    sd = sub.add_parser("send")
    sd.add_argument("--target", required=True)
    sd.add_argument("--image", required=True)
    sd.add_argument("--caption-file", required=True)
    sd.add_argument("--dry-run", action="store_true")
    sd.add_argument("--allow-repeat", action="store_true")
    po = sub.add_parser("post")
    po.add_argument("campaign_id")
    po.add_argument("--target", default="")
    po.add_argument("--dry-run", action="store_true")
    fg = sub.add_parser("forget")
    fg.add_argument("campaign_id")
    fg.add_argument("--target", default="")
    fg.add_argument("--checked", action="store_true")
    args = ap.parse_args(argv)

    ws = get_settings()
    problems = ws.problems(posting=args.command in ("send", "post", "calibrate"))
    if problems and args.command in BROWSER_COMMANDS:
        stop("; ".join(problems))
    runner = {"status": cmd_status, "login": cmd_login, "calibrate": cmd_calibrate,
              "send": cmd_send, "post": cmd_post, "forget": cmd_forget}[args.command]

    async def go() -> None:
        if args.command not in BROWSER_COMMANDS:
            await runner(args, ws, None)
            return
        wa = build(ws)
        try:
            with ProfileLock(ws.profile_dir, args.command):
                await runner(args, ws, wa)
        finally:
            await wa.b.close()      # the login stays in the profile; nothing is left open

    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(go(), loop_factory=loop_factory)
    except PublishError as e:
        stop(f"{e.code}: {e}")
    except BrowserError as e:
        stop(f"browser: {e}")
    except Exception as e:  # noqa: BLE001 — LeaseBusy, EffectBusy, a database error: one line, no traceback
        stop(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
