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

  plan     --week 2026-W46 [--send]  research the week and write its plan (ANTHROPIC_API_KEY); --send:
           [--candidates 12]         store it and send it to Betty on Telegram          (railway run)
  plan     --week 2026-W46 --status  the week's plans and what Betty decided           (railway run)
  plan     --week 2026-W46 --resend <id>   finish sending a stored plan (after a failure) (railway run)
  build    --week 2026-W46           the plan Betty approved → weeks/2026-W46.toml, its photos, the render
           [--replace] [--no-photos] [--candidates 3]                                   (railway run)
           then `send weeks/2026-W46.toml`: each post still goes to Betty for its own ✅
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
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
    from content_system.visual.models import ModelSetupError, claude_judge, gemini_generator
    from content_system.visual.photos import make_photo

    week = _load(args.manifest)
    cs = get_settings()
    missing = [n for n, v in (("GEMINI_API_KEY", app.gemini_api_key), ("ANTHROPIC_API_KEY", app.anthropic_api_key))
               if not v]
    if missing:
        stop(", ".join(missing) + " not set — run it with: railway run python -m content_system photos …")
    wrong = [n for n in args.post or [] if not 1 <= n <= len(week.posts)]
    if wrong:
        stop(f"--post {' '.join(map(str, wrong))}: the week has posts 1 to {len(week.posts)}")
    n_candidates = args.candidates or cs.image_candidates
    if not 1 <= n_candidates <= 8:
        stop(f"--candidates {n_candidates}: between 1 and 8")
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
        try:
            r = await make_photo(p, folder, generate, judge, candidates=n_candidates, again=args.again)
        except ModelSetupError as e:
            stop(f"{e} — check the key and the model name (CONTENT_IMAGE_MODEL, CONTENT_VISION_MODEL); "
                 "nothing more was generated")
        if r.status == "kept":
            print(f"  = {p.day_label}  {p.spec.photo}: already there, kept (--again to make a new one)")
        elif r.status == "no_brief":
            print(f"  · {p.day_label}  {p.spec.photo}: no [post.image] brief — put the photo in by hand")
        elif r.status == "made":
            best = max((c for c in r.candidates if c.ok), key=lambda c: c.score)
            others = sum(c.ok for c in r.candidates) - 1
            print(f"  ✓ {p.day_label}  {Path(r.photo).name}  score {best.score}/10  "
                  f"(candidate {best.index} of {len(r.candidates)}; {others} other(s) passed; all in "
                  f"{Path(r.folder).relative_to(folder).as_posix()}/)")
            for k, v in best.notes.items():
                if k in ("size", "top", "aspect"):
                    print(f"      ! {v}")
        else:
            failed += 1
            print(f"  ✗ {p.day_label}  {p.spec.photo}: no candidate passed — " + "; ".join(r.problems), file=sys.stderr)
    print("\nLook at them (and at .candidates/<photo>/<time>/), then: python -m content_system render " + args.manifest)
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
        if cs.assistant_on and waiting:
            from content_system.assistant.posts import info
            from content_system.assistant.proposals import propose
            from content_system.engine import view

            posts = [p for c in week.posts if (p := info(await view(graph, c.campaign_id)))]

            async def approve_all() -> str:
                return await propose(store, services.telegram, services.chat_id, "approve", posts,
                                     intro="Le aprobi deodată? Sau una câte una, pe butoanele fiecărei postări.")

            await store.once(f"approve-all:{week.name}", approve_all)
            print("  + card “Le aprobi deodată?” (the assistant is on)")
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


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _today():
    from datetime import date

    return date.today()


def _span(week: str):
    from content_system.planning.research import week_span
    from content_system.settings import get_settings

    if not re.fullmatch(r"\d{4}-W\d{2}", week):
        stop(f"--week {week!r}: write it like 2026-W46")
    days = tuple(d.strip() for d in get_settings().plan_days.split(",") if d.strip())
    try:
        return week_span(week, days)
    except ValueError as e:
        stop(f"--week {week} with CONTENT_PLAN_DAYS={','.join(days)}: {e}")
        raise


async def _plans_status(span) -> None:
    from content_system.store import PgStore

    _, cs = _require()
    db = await _open(cs)
    try:
        problems, _ = await db.verify(planning=True)
        if problems:
            stop("database not ready: " + "; ".join(problems))
        rows = await PgStore(db.pool).week_plans(span.name)
    finally:
        await db.close()
    if not rows:
        print(f"{span.name}: no plan yet — python -m content_system plan --week {span.name} --send")
        return
    print(span.name)
    for r in rows:
        dropped = f", dropped {', '.join(map(str, r['dropped']))}" if r["dropped"] else ""
        when = f" → {r['decided_at']:%a %d %b %H:%M}" if r["decided_at"] else ""
        print(f"  {r['id']}  {r['status']:<9} {r['n']} posts{dropped}  {r['created_at']:%a %d %b %H:%M}{when}")
    if rows[-1]["status"] == "approved":
        print(f"\nNext: railway run python -m content_system build --week {span.name}")


async def cmd_plan(args: argparse.Namespace) -> None:
    import secrets

    from config import settings as app
    from content_system.assistant.usage import cost
    from content_system.planning.llm import PlanningError, anthropic_call
    from content_system.planning.plan import make_plan, priced_airports
    from content_system.settings import db_url_problem, get_settings
    from content_system.store import PgStore

    span = _span(args.week)
    if args.status:
        await _plans_status(span)
        return
    if args.resend:
        await _resend(span, args.resend)
        return
    cs = get_settings()
    if not app.anthropic_api_key:
        stop("ANTHROPIC_API_KEY not set — run it with: railway run python -m content_system plan …")
    if args.send:
        app, cs = _require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    db = store = None
    ideas: list[dict] = []
    if args.send or db_url_problem(cs.db_url) is None:
        db = await _open(cs)
    try:
        if db is not None:
            problems, _ = await db.verify(planning=True)
            if problems and args.send:
                stop("database not ready: " + "; ".join(problems))
            if problems:
                print("  · Betty's ideas not read: " + "; ".join(problems))
            else:
                store = PgStore(db.pool)
                ideas = await store.new_ideas()
        else:
            print("  · CONTENT_DB_URL not set: Betty's ideas not read, the plan is only written to a file")
        days = ", ".join(f"{d:%a %d %b}" for d in span.days)
        print(f"{span.name} · {len(span.days)} posts ({days}) · {len(ideas)} idea(s) from Betty · {cs.plan_model}, "
              f"up to {cs.plan_searches} web searches — this takes a few minutes")
        plan_id = secrets.token_hex(6)
        try:
            plan = await make_plan(anthropic_call(cs.plan_model, app.anthropic_api_key), span, today=_today(),
                                   ideas=ideas, airports=priced_airports(),
                                   candidates_n=args.candidates or cs.plan_candidates, max_searches=cs.plan_searches,
                                   photo_tag=plan_id[:6])
        except PlanningError as e:
            stop(f"no plan: {e}")
        for note in plan.notes:
            print(f"  · {note}")
        for i, (post, urls) in enumerate(zip(plan.posts, plan.sources, strict=True), 1):
            print(f"  {i}. {post['day']}  {post['design']['style']:<10} {post['label']}  ({len(urls)} source(s))")
        u = plan.usage
        usd = cost(cs.plan_model, u.get("input_tokens", 0), u.get("output_tokens", 0)) + u.get("searches", 0) * 0.01
        print(f"  cost ≈ ${usd:.2f} ({u.get('input_tokens', 0)} in, {u.get('output_tokens', 0)} out, "
              f"{u.get('searches', 0)} searches)")
        out = ROOT / cs.output_root / span.name / f"plan-{plan_id}.toml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(plan.manifest, encoding="utf-8")
        print(f"\nPlan {plan_id} → {_rel(out)}")
        if not args.send:
            print(f"Read it; to send it to Betty: railway run python -m content_system plan --week {span.name} --send"
                  " (a new plan, researched again)")
            return
        await store.add_plan(plan_id, span.name, plan.posts, plan.sources, plan.manifest, plan.idea_ids)
        await _send_stored(app, cs, store, span, plan_id)
    finally:
        if db is not None:
            await db.close()


async def _send_stored(app, cs, store, span, plan_id: str) -> None:
    """Send a stored plan (what hasn't gone out yet), then close the week's older open plans."""
    from content_system.planning.review import send_plan

    services = _services(app, cs, store)
    row = await store.get_plan(plan_id)
    try:
        await send_plan(store, services.telegram, services.chat_id, row)
    except Exception as e:  # noqa: BLE001 — the plan is stored: say how to finish sending it
        stop(f"plan {plan_id} is stored but not all of it reached Telegram ({type(e).__name__}: {e}). "
             f"Older plans are still open. Finish it with: python -m content_system plan --week {span.name} "
             f"--resend {plan_id}")
    closed = await store.replace_open_plans(span.name, plan_id)
    if closed:
        print(f"  · older plan(s) {', '.join(closed)} closed: their buttons no longer work")
    print(f"✓ plan {plan_id} sent to Betty on Telegram. Then: python -m content_system plan --week {span.name} --status")


async def _resend(span, plan_id: str) -> None:
    from content_system.store import PgStore

    app, cs = _require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    db = await _open(cs)
    try:
        problems, _ = await db.verify(planning=True)
        if problems:
            stop("database not ready: " + "; ".join(problems))
        store = PgStore(db.pool)
        row = await store.get_plan(plan_id)
        if row is None or row["week"] != span.name:
            stop(f"no plan {plan_id} for {span.name} — python -m content_system plan --week {span.name} --status")
        if row["status"] not in ("draft", "sent"):
            stop(f"plan {plan_id} is {row['status']}: nothing to send")
        await _send_stored(app, cs, store, span, plan_id)
    finally:
        await db.close()


async def cmd_build(args: argparse.Namespace) -> None:
    from content_system.planning.plan import check_week, kept_manifest
    from content_system.store import PgStore

    span = _span(args.week)
    _, cs = _require()
    db = await _open(cs)
    try:
        problems, _ = await db.verify(planning=True)
        if problems:
            stop("database not ready: " + "; ".join(problems))
        plan = await PgStore(db.pool).latest_plan(span.name, "approved")
    finally:
        await db.close()
    if plan is None:
        stop(f"no approved plan for {span.name} — python -m content_system plan --week {span.name} --status")
    from content_system.planning.review import kept_days

    past = [d for d in kept_days(plan) if d < _today()]
    if past:
        stop(f"the plan has posts for days that have passed ({', '.join(f'{d:%a %d %b}' for d in past)}) — "
             f"make a new plan: python -m content_system plan --week {span.name} --send")
    text = kept_manifest(span, plan["posts"], plan["dropped"],
                         header=f"{span.name} — plan {plan['id']}, approved by Betty on Telegram (content_system build)")
    problems = check_week(text)
    if problems:
        stop("the approved plan doesn't pass the manifest checks: " + "; ".join(problems))
    target = ROOT / "weeks" / f"{span.name}.toml"
    rel = target.relative_to(ROOT).as_posix()
    if target.exists() and target.read_text(encoding="utf-8") != text and not args.replace:
        stop(f"{rel} already exists and is different — keep it, or run again with --replace")
    target.write_text(text, encoding="utf-8")
    kept = len(plan["posts"]) - len(plan["dropped"])
    print(f"✓ {rel} · {kept} posts from plan {plan['id']}")
    if args.no_photos:
        print(f"Then: python -m content_system photos {rel}, render {rel}, send {rel}")
        return
    await cmd_photos(argparse.Namespace(manifest=str(target), post=None, again=False, candidates=args.candidates))
    await cmd_render(argparse.Namespace(manifest=str(target)))
    print(f"\nLook at the preview; then each post goes to Betty for its own ✅:\n"
          f"  railway run python -m content_system send {rel}")


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
    pl = sub.add_parser("plan")
    pl.add_argument("--week", required=True, help="ISO week, e.g. 2026-W46")
    pl.add_argument("--send", action="store_true", help="store the plan and send it to Betty on Telegram")
    pl.add_argument("--status", action="store_true", help="the week's plans and what Betty decided")
    pl.add_argument("--resend", metavar="PLAN_ID", default="", help="send what didn't go out of a stored plan")
    pl.add_argument("--candidates", type=int, default=0, help="candidates to research (default 12)")
    b = sub.add_parser("build")
    b.add_argument("--week", required=True)
    b.add_argument("--replace", action="store_true", help="overwrite weeks/<week>.toml if it is different")
    b.add_argument("--no-photos", action="store_true", help="only write the manifest")
    b.add_argument("--candidates", type=int, default=0, help="photo candidates per post (default 3)")
    args = ap.parse_args(argv)

    if args.command == "check":
        cmd_check(args)
        return
    runner = {"render": cmd_render, "send": cmd_send, "status": cmd_status, "photos": cmd_photos,
              "decide": cmd_decide, "db-setup": cmd_db_setup, "plan": cmd_plan, "build": cmd_build}[args.command]
    # psycopg's async mode can't run on Windows' default Proactor loop; Playwright renders in a
    # worker thread with its own (default) loop, so only the main loop changes.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(runner(args), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
