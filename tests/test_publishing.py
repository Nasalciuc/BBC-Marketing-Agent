"""Publishing without a browser: text rules, the agent-browser wrapper, settings, the approval gate."""
from __future__ import annotations

import json
import os
import time

import pytest

from content_system.store import MemoryStore
from publishing.__main__ import read_caption
from publishing.browser import AgentBrowser, BrowserConfig, BrowserError, resolve_binary
from publishing.lock import ProfileBusy, ProfileLock
from publishing.publish import NotApproved, Unfinished, effect_key, forget, publish_campaign
from publishing.settings import PublishSettings
from publishing.whatsapp_web import (
    NotConfirmed,
    PostResult,
    PublishError,
    Selectors,
    caption_commands,
    chat_jid,
    css_quote,
    fingerprint,
    normalize,
    same_text,
)

# ── text ──


def test_fingerprint_leaves_out_emoji_on_both_sides_whatever_their_category():
    ours = "Last seats‼️ 1️⃣ JFK ↔ CDG ℹ️ ✈️\r\n\n  Call +1 888-322-7999 "
    shown_without_images = "Last seats  JFK  CDG   Call +1 888-322-7999"
    shown_with_alt_text = "Last seats‼ 1⃣ JFK ↔ CDG ℹ ✈ Call +1 888-322-7999"
    assert fingerprint(ours) == fingerprint(shown_without_images) == fingerprint(shown_with_alt_text)
    assert "+1 888-322-7999" in fingerprint(ours)


def test_same_text_is_exact_apart_from_the_time_and_read_more():
    caption = "Not every trip needs a return date.\nCall +1 888-322-7999"
    assert same_text(caption, "Not every trip needs a return date. Call +1 888-322-7999 10:42")
    assert same_text(caption, "Not every trip needs a return date.\nCall +1 888-322-7999\nEdited 9:05 PM")
    long = "Beaune. " + "The cellars pour all weekend. " * 40
    assert same_text(long, long[:400] + "Read more 10:42")


def test_same_text_does_not_match_a_corrected_or_a_longer_post():
    original = "One-way business class to Europe. " * 4 + "The fare above is the lowest of the three."
    corrected = original.replace("lowest of the three", "lowest of the four")
    assert not same_text(corrected, original)
    assert not same_text("Paris in November.", "Lisbon, Rome and Paris in November. Call us. 09:14")
    assert not same_text("Paris", "Paris" + " x" * 10 + " Read more")          # a cut start too short to trust
    assert not same_text("", "anything")


def test_normalize_keeps_lines_and_romanian_letters():
    assert normalize("﻿ș ț ă\r\nline two  \n") == "ș ț ă\nline two"


def test_caption_commands_use_shift_enter_and_keep_dashes_as_text():
    cmds = caption_commands("--headed\n\n@e1 line\nlast")
    assert cmds == [
        ["keyboard", "inserttext", "--headed"],
        ["press", "Shift+Enter"],
        ["press", "Shift+Enter"],
        ["keyboard", "inserttext", "@e1 line"],
        ["press", "Shift+Enter"],
        ["keyboard", "inserttext", "last"],
    ]


def test_css_quote_escapes_quotes_and_backslashes():
    assert css_quote('Q&A "VIP" \\ Lounge') == 'Q&A \\"VIP\\" \\\\ Lounge'


def test_chat_jid_reads_whatsapp_message_ids():
    assert chat_jid("true_120363000000000010@newsletter_3EB0AB12") == "120363000000000010@newsletter"
    assert chat_jid("true_120363000000000001@g.us_3EB0AB12") == "120363000000000001@g.us"
    assert chat_jid("false_120363000000000001@g.us_3EB0AB12_37360000001@c.us") == "120363000000000001@g.us"
    assert chat_jid("false_37360000001@c.us_3EB0") == "37360000001@c.us"
    assert chat_jid("#3") == "" and chat_jid("") == ""


def test_default_selectors_cover_every_step_the_code_uses():
    sel = Selectors.load()
    for key in ("app_ready", "qr", "channels_tab", "channel_in_list", "header_title", "composer",
                "attach_button", "image_input", "caption_box", "send_button", "outgoing", "message_text",
                "any_message", "tick", "clock", "failed"):
        assert sel.get(key, name="X"), key
    assert all("{name}" not in s for s in sel.get("channel_in_list", name="X"))
    assert "search_box" not in sel.table and "chat_in_list" not in sel.table, "only channels are opened"
    assert not any("data-tab" in s for s in sel.get("caption_box")), "would also match the chat's message box"


def test_a_caption_file_from_windows_powershell_is_read_right(tmp_path):
    text = "Cape Town ✈️\nșț"
    for name, data in (("utf8.txt", text.encode()), ("bom.txt", "﻿".encode() + text.encode()),
                       ("utf16.txt", text.encode("utf-16"))):
        (tmp_path / name).write_bytes(data)
        assert read_caption(str(tmp_path / name)) == text, name


# ── the wrapper ──


class FakeRunner:
    def __init__(self, out: bytes, code: int = 0, err: bytes = b""):
        self.out, self.code, self.err = out, code, err
        self.calls: list[tuple[list[str], bytes]] = []

    async def __call__(self, argv, stdin, env, wait_s):
        self.calls.append((argv, stdin))
        return self.code, self.out, self.err


@pytest.mark.asyncio
async def test_text_goes_on_stdin_never_on_the_command_line():
    ok = json.dumps([{"command": ["keyboard", "inserttext", "--headed"], "success": True,
                      "result": {"inserted": True}, "error": None}]).encode()
    run = FakeRunner(ok)
    b = AgentBrowser(BrowserConfig(binary="ab", session="s1", profile_dir="/data/p"), run)
    await b.batch([["keyboard", "inserttext", "--headed"]])
    argv, stdin = run.calls[0]
    assert argv == ["ab", "--session", "s1", "--json", "--idle-timeout", "10m", "--profile", "/data/p",
                    "batch", "--json", "--bail"]
    assert "--headed" not in argv
    assert json.loads(stdin) == [["keyboard", "inserttext", "--headed"]]


@pytest.mark.asyncio
async def test_a_failed_command_raises_with_the_command():
    out = json.dumps([{"command": ["click", "#x"], "success": False, "result": None,
                       "error": "Element not found"}]).encode()
    b = AgentBrowser(BrowserConfig(binary="ab"), FakeRunner(out, code=1))
    with pytest.raises(BrowserError) as e:
        await b.batch([["click", "#x"]])
    assert e.value.command == ["click", "#x"]
    assert not await AgentBrowser(BrowserConfig(binary="ab"), FakeRunner(out, code=1)).ok("click", "#x")


@pytest.mark.asyncio
@pytest.mark.parametrize("out", [b"", b"not json", b'{"success": false, "error": "bad flag"}'])
async def test_unreadable_output_is_an_error(out):
    b = AgentBrowser(BrowserConfig(binary="ab"), FakeRunner(out, code=2, err=b"boom"))
    with pytest.raises(BrowserError):
        await b.batch([["get", "url"]])


@pytest.mark.asyncio
async def test_a_missing_binary_is_a_browser_error():
    b = AgentBrowser(BrowserConfig(binary="/nonexistent/agent-browser"))
    with pytest.raises(BrowserError, match="can't run"):
        await b.batch([["get", "url"]])


def test_on_windows_the_npm_shim_is_replaced_by_the_native_exe(tmp_path, monkeypatch):
    shim = tmp_path / "agent-browser.cmd"
    shim.write_text("@echo off\n")
    exe = tmp_path / "node_modules" / "agent-browser" / "bin" / "agent-browser-win32-x64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr("publishing.browser.shutil.which", lambda name: str(shim))
    assert resolve_binary("agent-browser") == str(exe)
    assert resolve_binary(str(exe)) == str(exe)


# ── settings and the profile lock ──


def test_targets_are_exact_names_separated_by_bars():
    ws = PublishSettings(targets=" BBC Deals | Q&A, VIP ", profile_dir=os.path.abspath("wa"), _env_file=None)
    assert ws.allowed_targets() == frozenset({"BBC Deals", "Q&A, VIP"})
    assert ws.problems(posting=True) == []
    assert ws.enabled is False


def test_settings_problems_are_reported():
    ws = PublishSettings(profile_dir="relative/path", default_target="X", _env_file=None)
    problems = " ".join(ws.problems(posting=True))
    assert "absolute" in problems and "WA_TARGETS is empty" in problems and "WA_DEFAULT_TARGET" in problems


def test_one_command_at_a_time_on_a_profile(tmp_path):
    profile = str(tmp_path / "wa-profile")
    with ProfileLock(profile, "post"):
        with pytest.raises(ProfileBusy, match="post"), ProfileLock(profile, "calibrate"):
            pass
    with ProfileLock(profile, "calibrate"):                 # released after the first
        pass
    lock = tmp_path / "wa-profile.lock"
    lock.write_text("{}")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with ProfileLock(profile, "send"):                      # a lock left by a dead process frees itself
        pass


# ── the approval gate ──


class FakeWA:
    def __init__(self, outcome: str = "posted"):
        self.calls = []
        self.outcome = outcome

    async def post_image(self, target, image, caption, *, dry_run=False, allow_repeat=False):
        self.calls.append((target, image.read_bytes(), caption, dry_run))
        if self.outcome == "not_confirmed":
            raise NotConfirmed("the message is in the chat but still pending after 60 s")
        if self.outcome == "before_send":
            raise PublishError("search box not found")
        return PostResult("dry_run" if dry_run else "posted", target, "" if dry_run else "true_1@g.us_A",
                          "2026-11-02T10:00:00+00:00", ())


CID = "2026-W45-V4-001"


def _store(status: str) -> MemoryStore:
    s = MemoryStore()
    s.campaigns[CID] = {"campaign_id": CID, "status": status, "caption": "Approved text",
                        "image_url": "https://x/img.jpg", "scheduled_day": "Tue"}
    return s


async def _fetch(url):
    return b"\xff\xd8jpeg"


async def _publish(store, wa, tmp_path, **kw):
    kw.setdefault("enabled", True)
    return await publish_campaign(store, wa, CID, "BBC Deals", work_dir=tmp_path, fetch=_fetch, **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "rejected", "editing", "sent"])
async def test_only_approved_campaigns_are_published(tmp_path, status):
    wa = FakeWA()
    with pytest.raises(NotApproved):
        await _publish(_store(status), wa, tmp_path)
    assert wa.calls == []


@pytest.mark.asyncio
async def test_nothing_is_posted_while_the_switch_is_off(tmp_path):
    wa = FakeWA()
    with pytest.raises(PublishError, match="WA_ENABLED"):
        await _publish(_store("approved"), wa, tmp_path, enabled=False)
    assert wa.calls == []
    assert (await _publish(_store("approved"), wa, tmp_path, enabled=False, dry_run=True)).status == "dry_run"


@pytest.mark.asyncio
async def test_an_approved_campaign_is_posted_once_per_chat(tmp_path):
    store, wa = _store("approved"), FakeWA()
    first = await _publish(store, wa, tmp_path)
    again = await _publish(store, wa, tmp_path)
    assert first == again and first.status == "posted" and first.message_id == "true_1@g.us_A"
    assert len(wa.calls) == 1
    assert wa.calls[0][1:3] == (b"\xff\xd8jpeg", "Approved text")
    assert store.effects[effect_key(CID, "BBC Deals")]["status"] == "done"


@pytest.mark.asyncio
async def test_an_unconfirmed_post_is_recorded_and_never_retried_on_its_own(tmp_path):
    store, wa = _store("approved"), FakeWA("not_confirmed")
    first = await _publish(store, wa, tmp_path)
    again = await _publish(store, wa, tmp_path)
    assert first.status == again.status == "unconfirmed" and "pending" in first.detail
    assert len(wa.calls) == 1
    assert await forget(store, CID, "BBC Deals") == "done"   # a person looked: the post is not there
    wa.outcome = "posted"
    assert (await _publish(store, wa, tmp_path)).status == "posted"
    assert len(wa.calls) == 2


@pytest.mark.asyncio
async def test_a_failure_before_send_leaves_no_record(tmp_path):
    store, wa = _store("approved"), FakeWA("before_send")
    with pytest.raises(PublishError, match="search box"):
        await _publish(store, wa, tmp_path)
    assert effect_key(CID, "BBC Deals") not in store.effects
    wa.outcome = "posted"
    assert (await _publish(store, wa, tmp_path)).status == "posted"


@pytest.mark.asyncio
async def test_an_attempt_that_never_finished_is_refused_until_cleared(tmp_path):
    store, wa = _store("approved"), FakeWA()
    store.effects[effect_key(CID, "BBC Deals")] = {"status": "running", "external_id": None, "attempts": 1,
                                                   "t": 0.0}          # the process died after Send
    with pytest.raises(Unfinished, match="forget"):
        await _publish(store, wa, tmp_path)
    assert wa.calls == []
    assert await forget(store, CID, "BBC Deals") == "running"
    assert (await _publish(store, wa, tmp_path)).status == "posted"


@pytest.mark.asyncio
async def test_a_dry_run_leaves_no_record(tmp_path):
    store, wa = _store("approved"), FakeWA()
    out = await _publish(store, wa, tmp_path, dry_run=True)
    assert out.status == "dry_run" and store.effects == {}
