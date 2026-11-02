"""
The posts' photos (content_system/visual): the brief and its prompt, the checks without AI (on the
approved photos in assets/curated and on broken images), the pass rule over the vision verdict, and
choosing the photo — with a fake image model and a fake vision model. No API is called.
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from content_system.graph import plan_sha
from content_system.manifest import ManifestError, load_week, photo_report
from content_system.visual.brief import ImageBrief, prompt, week_problems
from content_system.visual.models import VERDICT_TOOL, judge_prompt
from content_system.visual.photos import make_photo, verdict_problems
from content_system.visual.qc import fit, inspect
from tests.content_fakes import ROOT, W42

CURATED = sorted((ROOT / "assets" / "curated").rglob("*.jpg"))
BRIEF = ImageBrief(scene="Table Mountain at golden hour over Clifton, the Atlantic below",
                   vantage="a villa terrace above Clifton beach",
                   device="the edge of a teak sun lounger with a folded towel", time="golden hour")
GOOD = {"one_photograph": True, "crowd": False, "people": "none", "text_or_logo": False, "matches_brief": True,
        "calm_top": True, "artifacts": False, "score": 8, "problems": []}


def run(coro):
    return asyncio.run(coro)


def _manifest_with_briefs(tmp_path: Path, devices: list[str]) -> Path:
    text = W42.read_text(encoding="utf-8")
    parts = text.split("\n[[post]]\n")
    out = [parts[0]]
    for part, device in zip(parts[1:], devices, strict=True):
        out.append(part.rstrip() + "\n\n[post.image]\n"
                   f'scene = "The defining moment of this place, seen at its best"\n'
                   f'vantage = "a private terrace above it"\ndevice = "{device}"\n')
    photos = tmp_path / "photos"
    photos.mkdir()
    path = tmp_path / "week.toml"
    path.write_text("\n[[post]]\n".join(out).replace('photos = "assets/curated/week_2026_10_12"',
                                                      f'photos = "{photos.as_posix()}"'), encoding="utf-8")
    return path


def _jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


# ── the brief and the prompt ─────────────────────────────────────────────────

def test_the_prompt_carries_the_brief_and_the_rules():
    text = prompt(BRIEF)
    assert "Table Mountain at golden hour over Clifton" in text
    assert "Seen from a villa terrace above Clifton beach." in text
    assert "In the near foreground, from that same place: the edge of a teak sun lounger" in text
    for rule in ("not a composite, not a collage", "never a crowd", "upper 45% of the frame is calm",
                 "No text anywhere", "No people at all."):
        assert rule in text
    few = prompt(BRIEF.model_copy(update={"people": "few"}), attempt_note="a crowd in the frame")
    assert "on the left third only, out of focus" in few and "rejected because: a crowd in the frame" in few


def test_the_foreground_device_must_change_within_a_week(tmp_path):
    assert week_problems([("a", BRIEF), ("b", BRIEF.model_copy(update={"device": "The edge of a  teak sun lounger, "
                                                                           "with a folded towel"}))]) != []
    devices = ["a teak sun lounger", "a terrace balustrade", "the rail of a private box", "a car door window",
               "A teak sun lounger"]
    with pytest.raises(ManifestError) as e:
        load_week(_manifest_with_briefs(tmp_path, devices))
    assert any("repeats post 1" in p for p in e.value.problems)


def test_a_week_with_briefs_loads_and_says_how_to_make_the_photos(tmp_path):
    week = load_week(_manifest_with_briefs(tmp_path, ["a teak sun lounger", "a terrace balustrade",
                                                      "the rail of a private box", "a car door window",
                                                      "a linen napkin on a balcony table"]))
    assert all(p.spec.image is not None for p in week.posts)
    errors, _ = photo_report(week)
    assert len(errors) == 5 and all("python -m content_system photos" in e for e in errors)


def test_posts_without_a_brief_keep_their_fingerprint():
    """PR-1 threads compare plan_sha: adding the optional field must not make every post 'changed'."""
    import hashlib

    week = load_week(W42)
    p = week.posts[0]
    assert p.spec.image is None
    spec = p.spec.model_dump(mode="json")
    spec.pop("image")
    photo = Path(p.design.background)
    plan = {"spec": spec, "caption": p.caption, "design": p.design.model_dump(mode="json", exclude={"background"}),
            "photo": hashlib.sha256(photo.read_bytes()).hexdigest() if photo.exists() else None}
    assert plan_sha(p) == hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ── checks without AI ────────────────────────────────────────────────────────

def test_every_approved_photo_passes_the_checks():
    assert CURATED, "assets/curated has the approved photos"
    refused = {f.name: inspect(*fit(f.read_bytes())).problems for f in CURATED}
    assert {k: v for k, v in refused.items() if v} == {}


@pytest.mark.parametrize(("image", "problem"), [
    (Image.new("RGB", (1536, 2752), (120, 120, 120)), "almost uniform"),
    (Image.effect_noise((1536, 2752), 6).convert("RGB").point(lambda v: v // 12), "far too dark"),
    (Image.effect_noise((400, 711), 60).convert("RGB"), "too small"),
])
def test_broken_images_are_refused(image, problem):
    c = inspect(*fit(_jpeg(image)))
    assert not c.ok and any(problem in p for p in c.problems)


def test_a_small_image_is_noted_as_soft():
    img = Image.open(CURATED[-1]).convert("RGB")
    small = img.resize((720, 1280))
    c = inspect(*fit(_jpeg(small)))
    assert c.ok and "upscaled from 720 px wide" in c.notes["size"]


def test_fit_makes_1080x1920():
    img, size = fit(_jpeg(Image.effect_noise((1600, 2400), 50).convert("RGB")))
    assert img.size == (1080, 1920) and size == (1600, 2400)


# ── the pass rule over the vision verdict ────────────────────────────────────

@pytest.mark.parametrize(("change", "problem"), [
    ({"one_photograph": False}, "not one single photograph"),
    ({"crowd": True}, "a crowd"),
    ({"people": "elsewhere"}, "people where they must not be"),
    ({"people": "few_left_small_blurred"}, "the brief wants none"),
    ({"text_or_logo": True}, "text or a logo"),
    ({"matches_brief": False}, "doesn't show the brief"),
    ({"artifacts": True}, "generation errors"),
    ({"score": 6}, "scored 6/10"),
])
def test_each_rule_refuses_on_its_own(change, problem):
    assert verdict_problems(GOOD, BRIEF) == []
    found = verdict_problems(GOOD | change, BRIEF)
    assert len(found) == 1 and problem in found[0]


def test_few_people_on_the_left_pass_when_the_brief_allows_them():
    assert verdict_problems(GOOD | {"people": "few_left_small_blurred"}, BRIEF.model_copy(update={"people": "few"})) == []


def test_a_verdict_missing_answers_fails_closed():
    assert len(verdict_problems({}, BRIEF)) >= 6


def test_the_judge_is_asked_every_rule():
    text = judge_prompt(BRIEF)
    assert "foreground element: the edge of a teak sun lounger" in text and "No text anywhere" in text
    assert set(VERDICT_TOOL["input_schema"]["required"]) == set(GOOD)


# ── choosing the photo ───────────────────────────────────────────────────────

class FakeModels:
    def __init__(self, verdicts: list, *, fail_first: bool = False):
        self.verdicts = list(verdicts)
        self.prompts: list[str] = []
        self.fail_first = fail_first
        self.n = 0

    async def generate(self, text: str) -> bytes:
        self.prompts.append(text)
        self.n += 1
        if self.fail_first and self.n == 1:
            raise RuntimeError("model overloaded")
        return _jpeg(Image.open(CURATED[self.n % len(CURATED)]).convert("RGB").resize((1536, 2730)))

    async def judge(self, jpeg: bytes, brief: ImageBrief) -> dict:
        return self.verdicts.pop(0)


@pytest.fixture
def post(tmp_path):
    week = load_week(_manifest_with_briefs(tmp_path, ["a teak sun lounger", "a terrace balustrade",
                                                      "the rail of a private box", "a car door window",
                                                      "a linen napkin on a balcony table"]))
    return week.posts[2], week.photos_dir


def test_the_best_passing_candidate_becomes_the_photo(post):
    p, folder = post
    m = FakeModels([GOOD | {"one_photograph": False, "score": 9}, GOOD | {"score": 8}, GOOD | {"score": 9}])
    r = run(make_photo(p, folder, m.generate, m.judge))
    assert r.status == "made" and Path(r.photo) == folder / f"{p.spec.photo}.jpg"
    assert [c.ok for c in r.candidates] == [False, True, True]
    assert Image.open(r.photo).size == (1080, 1920)
    assert Path(r.photo).read_bytes() == Path(r.candidates[2].path).read_bytes()        # the 9, not the 8
    assert "rejected because: not one single photograph" in m.prompts[1]                # told why
    report = json.loads((folder / ".candidates" / f"{p.spec.photo}.qc.json").read_text())
    assert report["chosen"] == 3 and len(report["candidates"]) == 3


def test_no_passing_candidate_writes_no_photo(post):
    p, folder = post
    m = FakeModels([GOOD | {"crowd": True}, GOOD | {"text_or_logo": True}, GOOD | {"score": 5}])
    r = run(make_photo(p, folder, m.generate, m.judge))
    assert r.status == "failed" and not (folder / f"{p.spec.photo}.jpg").exists()
    assert r.problems == ["a crowd in the frame", "scored 5/10 (needs 7)", "text or a logo in the picture"]


def test_a_failed_generation_is_reported_and_the_next_one_tried(post):
    p, folder = post
    m = FakeModels([GOOD, GOOD], fail_first=True)
    r = run(make_photo(p, folder, m.generate, m.judge))
    assert r.status == "made" and r.candidates[0].problems == ["generation failed: RuntimeError: model overloaded"]


def test_an_existing_photo_is_kept_unless_again(post):
    p, folder = post
    old = folder / f"{p.spec.photo}.jpg"
    old.write_bytes(_jpeg(Image.open(CURATED[0]).convert("RGB")))
    m = FakeModels([GOOD])
    assert run(make_photo(p, folder, m.generate, m.judge)).status == "kept" and m.prompts == []
    before = old.read_bytes()
    r = run(make_photo(p, folder, m.generate, m.judge, candidates=1, again=True))
    assert r.status == "made" and old.read_bytes() != before
    assert (folder / ".candidates" / f"{p.spec.photo}.previous.jpg").read_bytes() == before     # not lost


def test_a_post_without_a_brief_is_left_to_a_person(tmp_path):
    p = load_week(W42).posts[0]
    m = FakeModels([GOOD])
    assert run(make_photo(p, tmp_path, m.generate, m.judge)).status == "no_brief" and m.prompts == []


def test_the_photos_command_needs_both_keys(monkeypatch, capsys):
    from config import settings
    from content_system.__main__ import main

    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "x")
    with pytest.raises(SystemExit):
        main(["photos", str(W42)])
    assert "GEMINI_API_KEY not set" in capsys.readouterr().err


def test_the_photos_command_with_fake_models(tmp_path, monkeypatch, capsys):
    import content_system.visual.models as models
    from config import settings
    from content_system.__main__ import main

    path = _manifest_with_briefs(tmp_path, ["a teak sun lounger", "a terrace balustrade", "the rail of a private box",
                                            "a car door window", "a linen napkin on a balcony table"])
    m = FakeModels([GOOD | {"score": 9}] * 15)
    monkeypatch.setattr(settings, "gemini_api_key", "g")
    monkeypatch.setattr(settings, "anthropic_api_key", "a")
    monkeypatch.setattr(models, "gemini_generator", lambda *a, **k: m.generate)
    monkeypatch.setattr(models, "claude_judge", lambda *a, **k: m.judge)
    main(["photos", str(path), "--post", "1", "3", "--candidates", "2"])
    out = capsys.readouterr().out
    assert out.count("✓ ") == 2 and "score 9/10" in out and len(m.prompts) == 4
    assert sorted(f.name for f in (tmp_path / "photos").glob("*.jpg")) == ["cape_town.jpg", "singapore_airlines.jpg"]


# ── the real clients, with their network call replaced ───────────────────────

def test_gemini_generator_asks_for_9_16_and_reads_the_image(monkeypatch):
    from google.genai import types

    from content_system.visual import models

    seen = {}

    async def fake_generate_content(*, model, contents, config):
        seen.update(model=model, contents=contents, config=config)
        part = types.Part(inline_data=types.Blob(data=b"\xff\xd8jpeg", mime_type="image/jpeg"))
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[part]))])

    generate = models.gemini_generator("gemini-3.1-flash-image", "test-key", "2K")
    client = next(c.cell_contents for c in generate.__closure__ if c.cell_contents.__class__.__name__ == "Client")
    monkeypatch.setattr(client.aio.models, "generate_content", fake_generate_content)
    assert run(generate("a photo")) == b"\xff\xd8jpeg"
    assert seen["model"] == "gemini-3.1-flash-image" and seen["contents"] == "a photo"
    assert seen["config"].image_config.aspect_ratio == "9:16" and seen["config"].image_config.image_size == "2K"
    assert seen["config"].response_modalities == ["IMAGE"]


def test_gemini_generator_without_an_image_raises(monkeypatch):
    from google.genai import types

    from content_system.visual import models

    async def empty(**kwargs):
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[]),
                                                                         finish_reason="SAFETY")])

    generate = models.gemini_generator("gemini-3.1-flash-image", "test-key")
    client = next(c.cell_contents for c in generate.__closure__ if c.cell_contents.__class__.__name__ == "Client")
    monkeypatch.setattr(client.aio.models, "generate_content", empty)
    with pytest.raises(models.ImageModelError, match="returned no image"):
        run(generate("a photo"))


def test_claude_judge_sends_the_photo_and_forces_the_verdict(monkeypatch):
    from types import SimpleNamespace

    import anthropic

    from content_system.visual import models

    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="verdict", input=dict(GOOD))])

    real = anthropic.AsyncAnthropic

    def client(**kw):
        c = real(**kw)
        monkeypatch.setattr(c.messages, "create", create)
        return c

    monkeypatch.setattr(anthropic, "AsyncAnthropic", client)
    judge = models.claude_judge("claude-sonnet-5-5", "test-key")
    verdict = run(judge(_jpeg(Image.open(CURATED[0]).convert("RGB")), BRIEF))
    assert verdict == GOOD
    assert seen["model"] == "claude-sonnet-5-5" and seen["tool_choice"] == {"type": "tool", "name": "verdict"}
    image, text = seen["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert "teak sun lounger" in text["text"]
