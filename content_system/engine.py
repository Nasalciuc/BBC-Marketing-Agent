"""
Operations on post threads, shared by the CLI and the Telegram webhook:

  start(...)    start a post (or finish one whose last run failed half-way); idempotent
  decide(...)   approve / reject / edit — under the post's lease, after checking that the post is
                still waiting and that the tap was on the newest version
  view(...)     what a post is doing right now

A decision whose run failed half-way (database or Telegram down through every retry) is treated
as not taken: Betty was told it didn't go through. The same tap again completes it; a different tap
starts again from the pause before it. A partial write the failed run did make is never hidden:
the next step reports it ("Already approved — nothing changed").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from langgraph.types import Command

from content_system.graph import Mode, Services, initial_state, plan_sha, thread_config, thread_id
from content_system.manifest import PlannedPost, Week, caption_from_edit, caption_problems
from services.design_system import Style

log = logging.getLogger("bbc.content.engine")
CLOSES = {"approve": "approved", "reject": "rejected"}


@dataclass(frozen=True)
class PostView:
    campaign_id: str
    exists: bool
    status: str               # not started | in_review | approved | rejected | locked | rendered | failed
    version: int | None
    waiting: bool             # paused at review, waiting for a decision
    next_nodes: tuple[str, ...]
    last_error: str
    values: dict[str, Any]
    plan_changed: bool = False  # the manifest no longer matches what was sent (set by start)


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str              # short: it is shown as the Telegram toast (max 200 characters)
    view: PostView | None = None


def _view_of(campaign_id: str, snap: Any) -> PostView:
    values = dict(snap.values or {})
    if not values:
        return PostView(campaign_id, False, "not started", None, False, (), "", {})
    waiting = bool(snap.interrupts)
    status = values.get("status", "?")
    if not waiting and snap.next:
        status = "failed"     # stopped by an error before reaching the next pause
    return PostView(campaign_id, True, status, values.get("version"), waiting, tuple(snap.next),
                    values.get("last_error", ""), values)


async def view(graph: Any, campaign_id: str) -> PostView:
    return _view_of(campaign_id, await graph.aget_state(thread_config(campaign_id)))


async def _last_pause(graph: Any, campaign_id: str) -> tuple[PostView, dict] | None:
    """The most recent checkpoint where the post waited at review, and its config to resume from."""
    async for snap in graph.aget_state_history(thread_config(campaign_id)):
        if snap.interrupts:
            return _view_of(campaign_id, snap), snap.config
    return None


async def start(graph: Any, services: Services, week: Week, post: PlannedPost, mode: Mode,
                *, lock_timeout: float = 15.0) -> PostView:
    """Start the post's thread. A thread that exists is never started twice: it continues where a
    failed run stopped, or — when the manifest changed and nothing reached Telegram yet — starts over.
    After Telegram, a changed manifest is only reported (plan_changed): edits go through Telegram."""
    cid = post.campaign_id
    cfg = thread_config(cid)
    plan = plan_sha(post)
    async with services.store.lease(thread_id(cid), wait=lock_timeout):
        current = await view(graph, cid)
        if (current.exists and current.values.get("plan_sha") != plan
                and not await services.store.reached_telegram(cid)):
            log.info("%s changed in the manifest before reaching Telegram: starting it over", cid)
            await graph.checkpointer.adelete_thread(thread_id(cid))
            current = await view(graph, cid)
        if not current.exists:
            await graph.ainvoke(initial_state(week, post, mode), cfg, context=services, durability="sync")
        elif current.status == "failed":
            log.info("%s: continuing the run that failed at %s", cid, current.next_nodes)
            await graph.ainvoke(None, cfg, context=services, durability="sync")
        after = await view(graph, cid)
    return replace(after, plan_changed=after.values.get("plan_sha") != plan)


async def decide(graph: Any, services: Services, campaign_id: str, action: str, *,
                 version: int | None = None, caption: str | None = None, lock_timeout: float = 8.0) -> Outcome:
    """Apply one review decision. Raises store.LeaseBusy when another decision holds the post."""
    if action not in ("approve", "reject", "edit"):
        return Outcome(False, f"unknown action {action}")
    cfg = thread_config(campaign_id)
    async with services.store.lease(thread_id(campaign_id), wait=lock_timeout):
        current = await view(graph, campaign_id)
        if not current.exists:
            return Outcome(False, f"{campaign_id} was not sent by the content system")
        resume_cfg, next_version = cfg, 0
        if current.status == "failed":
            failed = (current.values.get("decision") or {}).get("action")
            pause = None if (failed == action and action != "edit") else await _last_pause(graph, campaign_id)
            if pause is None:   # the same tap again (or never paused yet): finish what failed
                log.info("%s: completing the %s that failed at %s", campaign_id, failed, current.next_nodes)
                await graph.ainvoke(None, cfg, context=services, durability="sync")
                current = await view(graph, campaign_id)
                if failed == action and current.status == CLOSES.get(action):
                    return Outcome(True, "✅ Approved" if action == "approve" else "❌ Rejected", current)
            else:               # a different tap: go back to the pause before the failed decision
                log.info("%s: the %s that failed is dropped for this %s", campaign_id, failed, action)
                next_version = (current.version or 0) + 1      # a version number never gets a second text
                current, resume_cfg = pause
        if not current.waiting:
            return Outcome(False, f"Already {current.status} — nothing changed", current)
        if action != "edit" and version is not None and version != current.version:
            return Outcome(False, f"That was v{version}; the latest is v{current.version} — use the newest message",
                           current)
        decision: dict[str, Any] = {"action": action}
        if action == "edit":
            vals = current.values
            new = caption_from_edit(caption or "", Style(vals["design"]["style"]), vals["spec"]["caption_city"])
            problems = caption_problems(new, campaign_id)
            if new.strip() == vals["caption"].strip():
                problems.append("same text as the current version")
            if problems:
                return Outcome(False, "Not applied: " + "; ".join(problems), current)
            decision |= {"caption": caption, "next_version": next_version}
        if resume_cfg is cfg:
            await graph.ainvoke(Command(resume=decision), cfg, context=services, durability="sync")
        else:   # branch off the old pause as if review had returned this decision, then run on from there
            branch = await graph.aupdate_state(resume_cfg, {"decision": decision}, as_node="review")
            await graph.ainvoke(None, branch, context=services, durability="sync")
        after = await view(graph, campaign_id)
    if action == "edit":
        if after.waiting and after.version and after.version > (current.version or 0):
            return Outcome(True, f"✏️ v{after.version} sent for review", after)
        return Outcome(False, after.last_error or "the edit was not applied", after)
    if after.status == CLOSES[action]:
        return Outcome(True, "✅ Approved" if action == "approve" else "❌ Rejected", after)
    return Outcome(False, f"Already {after.status} — nothing changed" if after.status in CLOSES.values()
                   else after.last_error or f"now {after.status}", after)
