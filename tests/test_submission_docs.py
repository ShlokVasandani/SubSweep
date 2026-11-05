"""The submission assets (README, Devpost text, demo script) must keep meeting the hackathon's stated limits.

scripts/check_submission.py does the checking; these tests run it, and also feed it deliberately broken documents to prove each rule
can actually fail.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_submission", ROOT / "scripts" / "check_submission.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def test_the_real_submission_assets_pass_every_check():
    assert cs.problems() == []


def test_the_demo_script_has_the_seven_beats_in_the_requested_order():
    labels = [b["label"].lower() for b in cs.script_beats()]
    assert len(labels) == 7
    for expected, label in zip(["problem", "upload", "detection", "duplicate", "paypal", "draft", "question"], labels):
        assert expected in label
    beats = cs.script_beats()
    assert beats[0]["end"] == 20 and beats[-1]["end"] == 150          # 20 seconds on the problem, 2:30 in total, under the 3:00 limit


def test_the_pitch_and_name_leave_room_under_the_limits():
    name, pitch = cs.devpost_fields()
    assert len(name) < 60 and len(pitch) < 200


def test_both_documents_give_the_24_month_reason():
    for rel in ("README.md", "docs/DEVPOST.md"):
        flat = " ".join((ROOT / rel).read_text().split()).lower()
        assert "24 months" in flat and "two yearly charges" in flat and "annual renewal" in flat


# ---------------------------------------------------------------- the checker really fails on broken input
def broken(monkeypatch, rel, transform):
    real = cs._read

    def fake(r):
        text = real(r)
        return transform(text) if r == rel else text
    monkeypatch.setattr(cs, "_read", fake)
    return cs.problems()


def test_checker_flags_an_overlong_pitch(monkeypatch):
    found = broken(monkeypatch, "docs/DEVPOST.md", lambda t: t.replace("You decide; it never cancels.", "You decide; it never cancels. " + "x" * 40))
    assert any("elevator pitch" in p for p in found)


def test_checker_flags_an_overlong_name(monkeypatch):
    found = broken(monkeypatch, "docs/DEVPOST.md", lambda t: t.replace("SubSweep: find the subscriptions quietly draining you", "SubSweep: " + "very " * 12 + "long"))
    assert any("project name" in p for p in found)


def test_checker_flags_a_script_that_overruns_or_has_a_gap(monkeypatch):
    assert any("totals" in p for p in broken(monkeypatch, "docs/DEMO_SCRIPT.md", lambda t: t.replace("2:15 to 2:30", "2:15 to 3:10")))
    assert any("gap or overlap" in p for p in broken(monkeypatch, "docs/DEMO_SCRIPT.md", lambda t: t.replace("1:30 to 1:55", "1:35 to 1:55")))


def test_checker_flags_narration_too_long_for_its_slot(monkeypatch):
    found = broken(monkeypatch, "docs/DEMO_SCRIPT.md", lambda t: t.replace("I decide what happens next.", "I decide what happens next. " + "and then more words " * 12))
    assert any("words" in p and "trim" in p for p in found)


@pytest.mark.parametrize("rel", ["README.md", "docs/DEVPOST.md"])
def test_checker_flags_a_missing_24_month_explanation(monkeypatch, rel):
    found = broken(monkeypatch, rel, lambda t: t.replace("two yearly charges", "some charges"))
    assert any("two yearly charges" in p for p in found)


def test_checker_flags_a_missing_diagram_and_missing_link(monkeypatch):
    assert any("Mermaid" in p for p in broken(monkeypatch, "README.md", lambda t: t.replace("```mermaid", "```text")))
    assert any("does not exist" in p for p in broken(monkeypatch, "README.md", lambda t: t + "\n[x](docs/NOPE.md)\n"))


def test_checker_flags_a_personal_email(monkeypatch):
    found = broken(monkeypatch, "README.md", lambda t: t + "\nContact someone.real@gmail.com\n")
    assert any("email address" in p for p in found)


def test_checker_allows_the_fictional_and_example_addresses(monkeypatch):
    found = broken(monkeypatch, "README.md", lambda t: t + "\nExample: john.doe@gmail.com or you@example.com\n")
    assert not any("email address" in p for p in found)


def test_the_first_commit_is_after_the_hackathon_start():
    first = cs.first_commit_date()
    assert first is None or first >= cs.HACKATHON_START
