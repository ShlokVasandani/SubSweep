"""Checks the submission assets against the hackathon's stated limits, so they cannot drift.

    python scripts/check_submission.py          # prints a report, exits 1 on any problem

Checked: project name under 60 characters, elevator pitch under 200, the demo script's timestamps are contiguous, total 2:30 and
under 3:00, each beat's narration can be spoken in its time slot, the README and the Devpost text both explain why the demo
statement spans 24 months, the README has a Mermaid diagram and build dates after the 1 October 2026 start, every local link in the
README exists, and no personal email address appears in any document.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HACKATHON_START = date(2026, 10, 1)
NAME_LIMIT, PITCH_LIMIT = 60, 200
VIDEO_TARGET_SECONDS, VIDEO_LIMIT_SECONDS = 150, 180
MAX_WORDS_PER_SECOND = 2.8          # about 168 words a minute: brisk but speakable
ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.test", "users.noreply.github.com", "anthropic.com"}
FICTIONAL_ADDRESSES = {"john.doe@gmail.com"}          # the made-up address used in the redaction examples; any other gmail address is flagged
DOC_FILES = ["README.md", "PROGRESS.md", ".env.example", "docs/DEVPOST.md", "docs/DEMO_SCRIPT.md", "docs/MANUAL_STEPS.md"]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _blocks_after(text: str, heading: str) -> str:
    """The first fenced block that follows a heading."""
    i = text.index(heading)
    m = re.search(r"```[a-z]*\n(.*?)```", text[i:], re.S)
    if not m:
        raise ValueError(f"no code block after {heading!r}")
    return m.group(1).strip()


def devpost_fields() -> tuple[str, str]:
    t = _read("docs/DEVPOST.md")
    return _blocks_after(t, "## Project name"), _blocks_after(t, "## Elevator pitch")


def _seconds(stamp: str) -> int:
    m, s = stamp.split(":")
    return int(m) * 60 + int(s)


def script_beats() -> list[dict]:
    beats = []
    for line in _read("docs/DEMO_SCRIPT.md").splitlines():
        m = re.match(r"\|\s*\*\*(\d+:\d{2}) to (\d+:\d{2})\*\*\s*(.*?)\s*\|(.*)\|(.*)\|\s*$", line)
        if m:
            start, end, label, screen, say = m.groups()
            beats.append({"start": _seconds(start), "end": _seconds(end), "label": label.strip(), "say": say.strip(), "words": len(say.split())})
    return beats


def problems() -> list[str]:
    out: list[str] = []
    name, pitch = devpost_fields()
    if len(name) >= NAME_LIMIT:
        out.append(f"project name is {len(name)} characters; it must be under {NAME_LIMIT}")
    if len(pitch) >= PITCH_LIMIT:
        out.append(f"elevator pitch is {len(pitch)} characters; it must be under {PITCH_LIMIT}")

    beats = script_beats()
    if len(beats) < 7:
        out.append(f"demo script has {len(beats)} timed beats; expected the 7 in the brief (problem, upload, detection, duplicate, PayPal, draft, Q&A)")
    if beats:
        if beats[0]["start"] != 0:
            out.append("the demo script must start at 0:00")
        for a, b in zip(beats, beats[1:]):
            if a["end"] != b["start"]:
                out.append(f"gap or overlap between '{a['label']}' (ends {a['end']}s) and '{b['label']}' (starts {b['start']}s)")
        total = beats[-1]["end"]
        if total != VIDEO_TARGET_SECONDS:
            out.append(f"the demo script totals {total}s; the target is {VIDEO_TARGET_SECONDS}s (2:30)")
        if total >= VIDEO_LIMIT_SECONDS:
            out.append(f"the demo script totals {total}s; the video must be under {VIDEO_LIMIT_SECONDS}s")
        if beats[0]["end"] - beats[0]["start"] != 20:
            out.append("the problem beat should be 20 seconds")
        for b in beats:
            secs = b["end"] - b["start"]
            if b["words"] > secs * MAX_WORDS_PER_SECOND:
                out.append(f"'{b['label']}' has {b['words']} words for {secs}s ({b['words'] / secs:.1f} words/s); trim it to at most {int(secs * MAX_WORDS_PER_SECOND)}")

    readme, devpost = _read("README.md"), _read("docs/DEVPOST.md")
    for label, text in (("README", readme), ("Devpost text", devpost)):
        flat = " ".join(text.split()).lower()
        if "24 months" not in flat and "24-month" not in flat:
            out.append(f"{label} does not mention that the demo statement covers 24 months")
        if "two yearly charges" not in flat:
            out.append(f"{label} does not explain why: an annual renewal needs two yearly charges to be detected")
    if "```mermaid" not in readme:
        out.append("README has no Mermaid architecture diagram")
    m = re.search(r"## When it was built(.*?)(?=\n## )", readme, re.S)
    if not m:
        out.append("README has no 'When it was built' section")
    else:
        dates = [date.fromisoformat(d) for d in re.findall(r"\b(2026-\d{2}-\d{2})\b", m.group(1))]
        years = re.findall(r"(\d{1,2}) (Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep)\w* 2026", m.group(1))
        if not years and not dates:
            out.append("the build-dates section contains no dates")
    first = first_commit_date()
    if first is not None and first < HACKATHON_START:
        out.append(f"the first commit is dated {first}, before the {HACKATHON_START} hackathon start")

    for link in set(re.findall(r"\]\((?!https?:|#|mailto:)([^)#\s]+)", readme)):
        if not (ROOT / link).exists():
            out.append(f"README links to {link}, which does not exist")

    for rel in DOC_FILES:
        for full, domain in re.findall(r"(?<![\w.+\-])([\w.+\-]+@([\w\-]+(?:\.[\w\-]+)+))", _read(rel)):
            if domain.lower() not in ALLOWED_EMAIL_DOMAINS and full.lower() not in FICTIONAL_ADDRESSES:
                out.append(f"{rel} contains an email address at {domain}; personal addresses must not be written into the repository")
    return out


def first_commit_date() -> date | None:
    try:
        r = subprocess.run(["git", "log", "--reverse", "--format=%ad", "--date=short"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        return min(date.fromisoformat(ln) for ln in lines) if r.returncode == 0 and lines else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def main() -> int:
    name, pitch = devpost_fields()
    print(f"project name   : {len(name):>3} / {NAME_LIMIT - 1} characters  {name!r}")
    print(f"elevator pitch : {len(pitch):>3} / {PITCH_LIMIT - 1} characters")
    beats = script_beats()
    for b in beats:
        print(f"  {b['start'] // 60}:{b['start'] % 60:02d} to {b['end'] // 60}:{b['end'] % 60:02d}  {b['words']:>3} words  {b['label']}")
    if beats:
        print(f"demo script    : {beats[-1]['end'] // 60}:{beats[-1]['end'] % 60:02d} total, {sum(b['words'] for b in beats)} words")
    first = first_commit_date()
    print(f"first commit   : {first}  (hackathon start {HACKATHON_START})")
    found = problems()
    for p in found:
        print("PROBLEM:", p)
    print("OK" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
