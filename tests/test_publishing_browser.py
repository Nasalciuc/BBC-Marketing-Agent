"""
The channel publisher against a stand-in WhatsApp Web page (tests/fixtures/whatsapp_web), with the real
agent-browser binary and a real Chromium. Skipped when agent-browser is not installed; set
AGENT_BROWSER_BIN to point at it. These tests prove the steps and the guards, not WhatsApp's
current markup: that is checked on the real page with `python -m publishing calibrate`.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import os
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest

from publishing.browser import AgentBrowser, BrowserConfig
from publishing.whatsapp_web import (
    CannotPost,
    ChatNotFound,
    NeedsLogin,
    NotAllowed,
    NotConfirmed,
    PublishError,
    Selectors,
    WhatsAppWeb,
    WrongChat,
    normalize,
)

BIN = os.environ.get("AGENT_BROWSER_BIN") or shutil.which("agent-browser")
FIXTURE = Path(__file__).parent / "fixtures" / "whatsapp_web"
pytestmark = [pytest.mark.skipif(not BIN, reason="agent-browser is not installed"), pytest.mark.asyncio]

CHANNEL = "BBC Business Class"           # a group in the chat list has this name too
ALLOWED = frozenset({CHANNEL, "Travel News", 'Q&A "VIP" Lounge', "BBC Deals Test"})
CAPTION = ("While New York turns cold, Cape Town turns to summer ✈️\n"
           "\n"
           "--headed is text here, not a flag\n"
           "@e1 São Paulo · Brașov · 6–8 Nov\n"
           "Call +1 888-322-7999 or reply here.")
EDITOR = '#editor [aria-label="Add a caption"]'


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(_QuietHandler, directory=str(FIXTURE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture()
def image(tmp_path):
    from PIL import Image

    path = tmp_path / "post.jpg"
    Image.new("RGB", (1080, 1920), (11, 24, 41)).save(path, quality=80)
    return path


@pytest.fixture()
def make_wa(site, tmp_path):
    made: list[WhatsAppWeb] = []

    def make(query: str = "", sent_timeout_s: float = 8.0) -> WhatsAppWeb:
        cfg = BrowserConfig(binary=BIN, session=f"t{uuid.uuid4().hex[:10]}",
                            profile_dir=str(tmp_path / f"profile{len(made)}"), step_timeout_ms=8000)
        wa = WhatsAppWeb(AgentBrowser(cfg), Selectors.load(), url=site + query, allowed_targets=ALLOWED,
                         evidence_dir=tmp_path / "evidence", ready_timeout_s=10,
                         sent_timeout_s=sent_timeout_s, poll_s=0.3, settle_s=1.0, settle_max_s=4)
        made.append(wa)
        return wa

    yield make
    for wa in made:
        asyncio.run(wa.b.close())


async def sent(wa: WhatsAppWeb) -> list[dict]:
    return await wa.b.eval_json("JSON.stringify(window.__sent)")


async def editor_open(wa: WhatsAppWeb) -> bool:
    return (await wa.b.visible([EDITOR]))[0]


async def test_posts_once_into_the_channel_not_the_group_with_its_name(make_wa, image, tmp_path):
    wa = make_wa()
    result = await wa.post_image(CHANNEL, image, CAPTION)
    assert result.status == "posted"
    assert result.message_id.startswith("true_120363000000000010@newsletter_")
    out = await sent(wa)
    assert len(out) == 1
    assert out[0]["chat"] == CHANNEL and out[0]["jid"].endswith("@newsletter")   # not the group, not "… 2"
    assert out[0]["file"].startswith("upload-") and out[0]["size"] == image.stat().st_size
    assert normalize(out[0]["caption"]) == normalize(CAPTION)
    assert all(Path(p).is_file() for p in result.evidence) and len(result.evidence) == 2
    assert not list((tmp_path / "evidence").glob("upload-*"))   # the temporary copy is gone


async def test_a_group_is_never_posted_to(make_wa, image):
    """Only channels: a group on the allowlist is not under Channels, so it is not found at all."""
    wa = make_wa()
    with pytest.raises(ChatNotFound, match="under Channels"):
        await wa.post_image("BBC Deals Test", image, CAPTION)
    assert await sent(wa) == []


async def test_a_selector_that_lands_on_a_group_is_refused(make_wa, image):
    """The id check holds even when the selectors are wrong and open the group with the channel's name."""
    wa = make_wa()
    wa.sel = Selectors({**wa.sel.table, "channels_tab": ['[aria-label="Chats"]'],
                        "channel_in_list": ['#pane-side span[title="{name}"]']})
    with pytest.raises(WrongChat, match="group"):
        await wa.post_image(CHANNEL, image, CAPTION)
    assert await sent(wa) == []


async def test_the_same_caption_is_not_posted_twice(make_wa, image):
    wa = make_wa()
    first = await wa.post_image(CHANNEL, image, CAPTION)
    again = await wa.post_image(CHANNEL, image, CAPTION)
    assert (first.status, again.status) == ("posted", "already_posted")
    assert again.message_id == first.message_id
    assert len(await sent(wa)) == 1
    forced = await wa.post_image(CHANNEL, image, CAPTION, allow_repeat=True)
    assert forced.status == "posted" and len(await sent(wa)) == 2


async def test_two_channels_one_after_the_other(make_wa, image):
    """After the first post the Channels list stays open; the second post starts from there."""
    wa = make_wa()
    assert (await wa.post_image(CHANNEL, image, CAPTION)).status == "posted"
    assert (await wa.post_image('Q&A "VIP" Lounge', image, "Short caption")).status == "posted"
    assert [m["chat"] for m in await sent(wa)] == [CHANNEL, 'Q&A "VIP" Lounge']


async def test_emoji_of_every_kind_are_confirmed_and_not_reposted(make_wa, image):
    """Found in review: ‼ ⁉ ℹ ↔ and keycaps are not 'symbols, other'; WhatsApp draws them as images."""
    wa = make_wa()
    caption = "Last seats‼️ 1️⃣ JFK ↔ CDG ℹ️ ⁉️\nBusiness class, New York to Paris."
    assert (await wa.post_image(CHANNEL, image, caption)).status == "posted"
    assert (await wa.post_image(CHANNEL, image, caption)).status == "already_posted"
    assert len(await sent(wa)) == 1


async def test_a_post_already_in_a_slowly_loading_channel_is_not_sent_again(make_wa, image):
    """Found in review: after a crash a fresh browser shows the header before the posts."""
    caption = "Prior post: Cape Town, summer a night away. Call +1 888-322-7999."
    wa = make_wa("?late=1500&preposted=" + quote(caption))
    result = await wa.post_image(CHANNEL, image, caption)
    assert result.status == "already_posted" and result.message_id.endswith("_3EB0OLD1")
    assert await sent(wa) == []


async def test_a_corrected_post_is_not_mistaken_for_the_old_one(make_wa, image):
    old = "One-way business class to Europe. The fare above is the lowest of the three."
    wa = make_wa("?preposted=" + quote(old))
    result = await wa.post_image(CHANNEL, image, old.replace("three", "four"))
    assert result.status == "posted" and len(await sent(wa)) == 1


async def test_a_dry_run_stops_before_send_and_closes_the_preview(make_wa, image):
    wa = make_wa()
    result = await wa.post_image(CHANNEL, image, CAPTION, dry_run=True)
    assert result.status == "dry_run" and len(result.evidence) == 1
    assert await sent(wa) == []
    assert not await editor_open(wa)          # Escape asked "Discard unsent message?" and Discard was clicked


async def test_a_failure_after_attaching_closes_the_preview(make_wa, image):
    """Found in review: errors after the upload left the image preview open for the next run."""
    wa = make_wa()
    wa.sel = Selectors({**wa.sel.table, "caption_box": ["#no-such-caption-box"]})
    with pytest.raises(PublishError, match="preview did not open"):
        await wa.post_image(CHANNEL, image, CAPTION)
    assert not await editor_open(wa) and await sent(wa) == []
    wa.sel = Selectors.load()
    assert (await wa.post_image(CHANNEL, image, CAPTION)).status == "posted"


async def test_a_channel_outside_the_allowlist_is_refused(make_wa, image):
    wa = make_wa()
    with pytest.raises(NotAllowed):
        await wa.post_image("BBC Business Class 2", image, CAPTION)


async def test_a_channel_this_account_only_follows_stops_before_attaching(make_wa, image):
    wa = make_wa()
    with pytest.raises(CannotPost, match="not its admin"):
        await wa.post_image("Travel News", image, CAPTION)
    assert await sent(wa) == []


async def test_quotes_in_the_name_of_a_channel_with_no_posts_yet(make_wa, image):
    wa = make_wa()
    result = await wa.post_image('Q&A "VIP" Lounge', image, "Short caption")
    assert result.status == "posted"
    assert (await sent(wa))[0]["chat"] == 'Q&A "VIP" Lounge'


async def test_a_qr_code_means_login(make_wa, image):
    wa = make_wa("?state=qr")
    assert await wa.open() == "login"
    with pytest.raises(NeedsLogin):
        await wa.post_image(CHANNEL, image, CAPTION)


async def test_open_in_another_window_is_taken_over(make_wa):
    wa = make_wa("?state=elsewhere")
    assert await wa.open() == "ready"


async def test_a_long_caption_cut_by_read_more_is_confirmed(make_wa, image):
    wa = make_wa()
    long = "Beaune, one weekend, all of Burgundy. " * 25
    assert (await wa.post_image(CHANNEL, image, long)).status == "posted"
    assert (await wa.post_image(CHANNEL, image, long)).status == "already_posted"


async def test_a_post_stuck_on_the_clock_is_not_confirmed(make_wa, image):
    wa = make_wa("?stuck=1", sent_timeout_s=3)
    with pytest.raises(NotConfirmed):
        await wa.post_image(CHANNEL, image, CAPTION)
    assert len(await sent(wa)) == 1                       # it did go out once; nothing more without a person
    again = await wa.post_image(CHANNEL, image, CAPTION)
    assert again.status == "already_posted" and len(await sent(wa)) == 1


async def test_a_caption_selector_that_hits_the_message_box_is_refused(make_wa, image):
    """Found by these tests: a loose fallback ([data-tab="10"]) also matches the channel's own message
    box, so the caption would go out as plain text without the image. The guard stops it."""
    wa = make_wa()
    wa.sel = Selectors({**wa.sel.table, "caption_box": ['#main footer div[contenteditable="true"]']})
    with pytest.raises(PublishError, match="message box"):
        await wa.post_image(CHANNEL, image, CAPTION)
    assert await sent(wa) == [] and not await editor_open(wa)


async def test_calibrate_reports_every_step_on_the_channel(make_wa):
    wa = make_wa()
    report = {r["step"]: r for r in await wa.calibrate(CHANNEL)}
    assert report["open"]["selector"] == "ready"
    for step in ("channels_tab", "channel", "composer", "outgoing", "attach", "attach_menu"):
        assert report[step]["selector"], step
    assert report["channel_id"]["selector"] == "120363000000000010@newsletter"
    assert not await editor_open(wa)
