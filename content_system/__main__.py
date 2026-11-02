"""
python -m content_system <command>

  check    weeks/2026-W43.toml       manifest, captions, week mix, photos — nothing rendered
  photos   weeks/2026-W43.toml       the missing photos, from each post's [post.image] brief
           [--post N …] [--again] [--candidates 3]    (GEMINI_API_KEY, ANTHROPIC_API_KEY)
  render   weeks/2026-W43.toml       finished posts + captions + preview in output/content/<week>/
  send     weeks/2026-W43.toml       render, upload, campaigns, Telegram review      (railway run)
  status   weeks/2026-W43.toml       where each post is                               (railway run)
  decide   <campaign_id> approve|reject [--version N]   same as the Telegram buttons  (railway run)
  db-setup                           LangGraph checkpoint tables + checks             (railway run)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from content_system.manifest import ManifestError, Week, find_photo, load_week, photo_report

ROOT = Path(__file__).resolve().parent.parent


def stop(message: str) -> None:
    print(f"STOP — {message}", file=sys.stderr)
    raise SystemExit(1)


def _load(path: str) -> Week:
    try:
        return load_week(path)
    except ManifestError as e:
        print(f"{path}: {len(e.problems)} problem(s)", file=sys.stderr)
        for p in e.problems:
            print(f"  ✗ {p}", file=sys.stderr)
        raise SystemExit(1) from None
    except FileNotFoundError:
        stop(f"{path} not found")
        raise


def cmd_check(args: argparse.Namespace) -> Week:
    week = _load(args.manifest)
    errors, warnings = photo_report(week)
    priced = sum(1 for p in week.posts if p.design.style.value in ("offer", "price_hero", "multi"))
    print(f"{week.name} · {Path(args.manifest).name} · {len(week.posts)} posts · {priced} with a fare")
    for p in week.posts:
        photo = find_photo(week.photos_dir, p.spec.photo)
        mark = "✗" if photo is None else "✓"
        print(f"  {mark} {p.day_label}  {p.design.style.value:<10} {p.campaign_id}  "
              f"caption {len(p.caption):>4}  {photo.name if photo else p.spec.photo + '.jpg MISSING'}")
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        stop(f"{len(errors)} photo(s) missing — put them in {week.manifest.photos}/ with exactly the names above")
    return week


def _require(*names: str) -> tuple:
    """Stop unless CONTENT_DB_URL and the named app settings are set (they come from railway run)."""
    from config import settings as app
    from content_system.settings import db_url_problem, get_settings

    cs = get_settings()
    missing = [n for n in names if not getattr(app, n.lower())]
    if missing:
        stop(", ".join(missing) + " not set — run it with: railway run python -m content_system …")
    problem = db_url_problem(cs.db_url)
    if problem:
        stop(problem)
    return app, cs


async def _open(cs):
    from content_system.db import Database

    db = Database(cs.db_url, schema=cs.db_schema, max_size=cs.db_pool_max)
    try:
        await db.open()
    except Exception as e:  # noqa: BLE001
        stop(f"can't connect with CONTENT_DB_URL ({type(e).__name__}: {e})")
    return db


def _services(app, cs, store):
    from content_system.adapters import get_image, put_image, render_files
    from content_system.graph import Services
    from content_system.telegram import TelegramAPI

    return Services(store=store, render=render_files, output_root=ROOT / cs.output_root,
                    put_image=put_image, get_image=get_image,
                    telegram=TelegramAPI(app.telegram_bot_token), chat_id=app.telegram_chat_id)


async def cmd_render(args: argparse.Namespace) -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from content_system.adapters import preview_sheet, render_files
    from content_system.engine import start
    from content_system.graph import Services, build_graph
    from content_system.settings import get_settings
    from content_system.store import MemoryStore
    from services.design_system import DesignQAError
    from services.pricing_engine import format_price

    week = cmd_check(args)
    out_root = ROOT / get_settings().output_root
    services = Services(store=MemoryStore(), render=render_files, output_root=out_root)
    graph = build_graph(InMemorySaver())
    print("\nRender — design v4")
    sheet = []
    for p in week.posts:
        try:
            v = await start(graph, services, week, p, "render")
        except DesignQAError as e:
            stop(f"design QA on {p.spec.photo}: {e}")
        s = v.values
        fare = format_price(s["fare"]) if s.get("fare") else "—"
        rel = Path(s["image_path"]).relative_to(ROOT)
        print(f"  ✓ {p.day_label}  {p.design.style.value:<10} tone {s['tone']:<5} fare {fare:<7} "
              f"text ends at {s['text_bottom']:.0%}  caption {len(p.caption)}  → {rel}")
        sheet.append((f"{p.day_label} · {p.spec.label}", Path(s["image_path"])))
    out = preview_sheet(sheet, out_root / week.name / "_preview.jpg")
    print(f"\nPreview of the week → {out.relative_to(ROOT)}")


async def cmd_photos(args: argparse.Namespace) -> None:
    from config import settings as app
    from content_system.settings import get_settings
    from content_system.visual.models import claude_judge, gemini_generator
    from content_system.visual.photos import make_photo

    week = _load(args.manifest)
    cs = get_settings()
    missing = [n for n, v in (("GEMINI_API_KEY", app.gemini_api_key), ("ANTHROPIC_API_KEY", app.anthropic_api_key))
               if not v]
    if missing:
        stop(", ".join(missing) + " not set — run it with: railway run python -m content_system photos …")
    posts = [p for p in week.posts if not args.post or p.index in args.post]
    if not any(p.spec.image for p in posts):
        stop("no post has an [post.image] brief — add one under the post in the manifest")
    generate = gemini_generator(cs.image_model, app.gemini_api_key, cs.image_size)
    judge = claude_judge(cs.vision_model, app.anthropic_api_key)
    folder = week.photos_dir
    folder.mkdir(parents=True, exist_ok=True)
    print(f"{week.name} · photos → {week.manifest.photos}/  ({cs.image_model}, checked by {cs.vision_model})")
    failed = 0
    for p in posts:
        r = await make_photo(p, folder, generate, judge, candidates=args.candidates or cs.image_candidates,
                             again=args.again)
        if r.status == "kept":
            print(f"  = {p.day_label}  {p.spec.photo}: already there, kept (--again to make a new one)")
        elif r.status == "no_brief":
            print(f"  · {p.day_label}  {p.spec.photo}: no [post.image] brief — put the photo in by hand")
        elif r.status == "made":
            best = max((c for c in r.candidates if c.ok), key=lambda c: c.score)
            others = sum(c.ok for c in r.candidates) - 1
            print(f"  ✓ {p.day_label}  {Path(r.photo).name}  score {best.score}/10  "
                  f"(candidate {best.index} of {len(r.candidates)}; {others} other(s) passed, in .candidates/)")
            for k, v in best.notes.items():
                if k in ("size", "top"):
                    print(f"      ! {v}")
        else:
            failed += 1
            print(f"  ✗ {p.day_label}  {p.spec.photo}: no candidate passed — " + "; ".join(r.problems), file=sys.stderr)
    print("\nLook at them (and at .candidates/), then: python -m content_system render " + args.manifest)
    if failed:
        stop(f"{failed} photo(s) not made — change the brief, or run again (each run tries new candidates)")


async def cmd_send(args: argparse.Namespace) -> None:
    from content_system.engine import start
    from content_system.graph import build_graph
    from content_system.store import PgStore

    week = cmd_check(args)
    _, warnings = photo_report(week)
    if warnings and not args.accept_warnings:
        stop("photo warnings above — show them to the person; re-run with --accept-warnings if they say yes")
    app, cs = _require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_KEY")
    db = await _open(cs)
    try:
        problems, notes = await db.verify()
        for n in notes:
            print(f"  · {n}")
        if problems:
            stop("database not ready: " + "; ".join(problems))
        store = PgStore(db.pool)
        foreign = await store.foreign_campaigns([p.campaign_id for p in week.posts])
        if foreign:
            stop("these campaigns already exist and were not created by the content system: "
                 + ", ".join(foreign) + " — change id_tag in the manifest")
        services = _services(app, cs, store)
        graph = build_graph(db.checkpointer())

        lines = "\n".join(f"{p.day_label} · {p.spec.label} · {p.campaign_id}" for p in week.posts)

        async def header() -> int:
            msg = await services.telegram.call("sendMessage", {
                "chat_id": services.chat_id,
                "text": f"📰 {week.name} · {len(week.posts)} posts for review\n\n{lines}\n\n"
                        "Approve, reject or edit each post below. Nothing reaches clients from here."})
            return msg["message_id"]

        await store.once(f"header:{week.name}", header)
        print("\nSend — render, upload, campaign, Telegram review")
        waiting, failed = 0, []
        for p in week.posts:
            try:
                v = await start(graph, services, week, p, "send")
            except Exception as e:  # noqa: BLE001 — report it, go on with the next post
                failed.append(p.campaign_id)
                print(f"  ✗ {p.day_label}  {p.campaign_id}  {type(e).__name__}: {e}", file=sys.stderr)
                continue
            waiting += v.waiting
            extra = f" — {v.last_error}" if v.last_error else ""
            if v.plan_changed:
                extra += (" — the manifest changed after this post was sent: edit the caption on Telegram,"
                          " or reject it and add it again with a new id")
            print(f"  {'→' if v.waiting else '='} {p.day_label}  {p.campaign_id}  v{v.version}  {v.status}{extra}")
        print(f"\n{waiting}/{len(week.posts)} waiting for review on Telegram.")
        if failed:
            stop(f"{len(failed)} post(s) failed: {', '.join(failed)} — fix the cause above, then run send again "
                 "(finished posts are skipped, failed ones continue where they stopped)")
    finally:
        await db.close()


async def cmd_status(args: argparse.Namespace) -> None:
    from content_system.engine import view
    from content_system.graph import build_graph

    week = _load(args.manifest)
    _, cs = _require()
    db = await _open(cs)
    try:
        graph = build_graph(db.checkpointer())
        print(f"{week.name}")
        for p in week.posts:
            v = await view(graph, p.campaign_id)
            state = f"v{v.version}  {v.status}" if v.exists else "not sent"
            hint = "  (waiting for a decision)" if v.waiting else ""
            hint += f"  — {v.last_error}" if v.last_error else ""
            hint += "  → run send again to finish it" if v.status == "failed" else ""
            print(f"  {p.day_label}  {p.campaign_id}  {state}{hint}")
    finally:
        await db.close()


async def cmd_decide(args: argparse.Namespace) -> None:
    from content_system.engine import decide
    from content_system.graph import build_graph
    from content_system.store import LeaseBusy, PgStore

    app, cs = _require("TELEGRAM_BOT_TOKEN")
    db = await _open(cs)
    try:
        services = _services(app, cs, PgStore(db.pool))
        graph = build_graph(db.checkpointer())
        try:
            out = await decide(graph, services, args.campaign_id, args.action, version=args.version,
                               lock_timeout=cs.lock_timeout_s)
        except LeaseBusy:
            stop("another decision on this post is running — try again in a few seconds")
        print(("✓ " if out.ok else "= ") + out.message)
        if not out.ok:
            raise SystemExit(1)
    finally:
        await db.close()


async def cmd_db_setup(args: argparse.Namespace) -> None:
    _, cs = _require()
    db = await _open(cs)
    try:
        try:
            await db.setup()
        except Exception as e:  # noqa: BLE001
            stop(f"checkpoint tables: {type(e).__name__}: {e} (does content_app have CREATE on schema content?)")
        problems, notes = await db.verify()
        for n in notes:
            print(f"  · {n}")
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        if problems:
            raise SystemExit(1)
        print("✓ database ready for the content system")
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="python -m content_system", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("check", "render", "send", "status"):
        p = sub.add_parser(name)
        p.add_argument("manifest")
        if name == "send":
            p.add_argument("--accept-warnings", action="store_true", help="go on despite photo size/ratio warnings")
    ph = sub.add_parser("photos")
    ph.add_argument("manifest")
    ph.add_argument("--post", type=int, nargs="*", help="only these posts (1-based, publishing order)")
    ph.add_argument("--again", action="store_true", help="make a new photo even where one exists")
    ph.add_argument("--candidates", type=int, default=0, help="candidates per post (default 3)")
    d = sub.add_parser("decide")
    d.add_argument("campaign_id")
    d.add_argument("action", choices=["approve", "reject"])
    d.add_argument("--version", type=int, default=None)
    sub.add_parser("db-setup")
    args = ap.parse_args(argv)

    if args.command == "check":
        cmd_check(args)
        return
    runner = {"render": cmd_render, "send": cmd_send, "status": cmd_status, "photos": cmd_photos,
              "decide": cmd_decide, "db-setup": cmd_db_setup}[args.command]
    # psycopg's async mode can't run on Windows' default Proactor loop; Playwright renders in a
    # worker thread with its own (default) loop, so only the main loop changes.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(runner(args), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
