"""Builds docs/SUBMISSION_PACK.md: everything to paste into Devpost and YouTube, plus the list of files to upload, in one place.

    python scripts/make_submission_pack.py

The text comes straight from docs/DEVPOST.md (edit that file, then re-run this) and the file facts are measured from the files on disk.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_submission", ROOT / "scripts" / "check_submission.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

REPO = "https://github.com/ShlokVasandani/SubSweep"
VIDEO = ROOT / "docs" / "video" / "subsweep-demo.mp4"
SRT = ROOT / "docs" / "video" / "subsweep-demo.srt"
TESTING = ("Open https://subsweep-chi.vercel.app, click \"Try the sample data\" or drop in your own bank or PayPal CSV (up to 4 MB).\n"
           "To run it yourself: pip install -r requirements.txt, then PAYPAL_MODE=mock python -m subsweep, and open http://127.0.0.1:8000. No account, key or network needed.")
SHOTS = [("01-landing.jpg", "the landing page"), ("02-unravel.jpg", "the scroll-driven \"statement is noise\" scene"),
         ("03-overview.jpg", "the overview with the health score"), ("04-findings.jpg", "a PayPal mismatch finding with its plain-English explanation")]


def section(text: str, heading: str, until: str = "\n---") -> str:
    """Everything under a heading up to the next `until` marker (a '---' rule by default; the description has its own ## subheadings)."""
    start = text.index(heading) + len(heading)
    end = text.index(until, start)
    return text[start:end].strip()


def fenced(text: str) -> str:
    """A code fence that cannot be closed early by backticks inside the text."""
    fence = "````" if "```" in text else "```"
    return f"{fence}\n{text}\n{fence}"


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=width,height,codec_name,avg_frame_rate", "-of", "json", str(path)],
                       capture_output=True, text=True)
    info = json.loads(r.stdout)
    s = info["streams"][0]
    fps = s["avg_frame_rate"].split("/")
    return {"seconds": float(info["format"]["duration"]), "bytes": int(info["format"]["size"]), "size": f"{s['width']}x{s['height']}",
            "codec": s["codec_name"], "fps": round(int(fps[0]) / int(fps[1]))}


def main() -> int:
    devpost = (ROOT / "docs" / "DEVPOST.md").read_text(encoding="utf-8")
    name, pitch = cs.devpost_fields()
    description = section(devpost, '## Full description (paste into "About the project")')
    built_with = section(devpost, "## Built with", until="\n## ").strip("`\n ")
    v = probe(VIDEO)
    sha = hashlib.sha256(VIDEO.read_bytes()).hexdigest()
    ncaps = len([b for b in SRT.read_text(encoding='utf-8').split('\n\n') if b.strip()])
    mm, ss = int(v["seconds"] // 60), v["seconds"] % 60
    shot_rows = "\n".join(f"| {i}. | `docs/screenshots/{f}` | {d} |" for i, (f, d) in enumerate(SHOTS, 1))
    yt_title = name
    yt_description = (f"SubSweep finds forgotten, duplicate and price-hiked subscriptions from a bank or PayPal statement, explains each one in plain English, "
                      f"checks PayPal, and drafts the message. You decide; it never cancels anything or sends anything.\n\nCode: {REPO}\n\n"
                      "Built for the PayPal AI Hackathon.")

    md = f"""# Submission pack

Everything to paste or upload, in one place. **Generated** by `scripts/make_submission_pack.py` from `docs/DEVPOST.md` and the files on disk:
edit `docs/DEVPOST.md`, then re-run the script, rather than editing this file.

## Read this first: what is still open

| | Item | Why |
|---|---|---|
| 1 | **Use `subsweep-demo-voiced.mp4` (it has the voice-over).** | The silent `subsweep-demo.mp4` is kept as a fallback. The voice-over is your ElevenLabs take, placed sentence by sentence against the clicks; `subsweep-demo-voiced.srt` holds captions timed to it. A few sentences were trimmed or reordered from `docs/DEMO_SCRIPT.md` to match what is on screen (the demo-data sentence plays while the PayPal panel is showing). |
| 2 | **The video's closing card shows `github.com/ShlokVasandani/SubSweep`.** | The repository must be public before anyone watches it. Making it public is your call; I have not changed it. |
| 3 | **PayPal beat = demo data.** | Recorded in `PAYPAL_MODE=mock`, so the panel carries the "Demo data" badge, and the captions say so. Nothing in the video presents it as live PayPal. |
| 4 | **AI beat = built-in wording.** | No Anthropic key here, so the top bar reads "AI: built-in text" and the captions use the script's "AI is not on" sentence instead of claiming Claude wrote it. |
| 5 | **Two blanks to fill in the pasted text.** | `[Update this line after your live run.]` at the end of the "What's next" bullet in the description (delete the bracket if you do not run live; keep the sentence above it, which is true as written), and the YouTube link in "Try it out links". |
| 6 | **PayPal sandbox and the Anthropic API have never run live from here.** | The README and Devpost text say so truthfully. If you run them on your machine, update those lines (see `docs/MANUAL_STEPS.md`, section 1). |

## Files to upload

| File | Where it goes | Details |
|---|---|---|
| `docs/video/subsweep-demo-voiced.mp4` | **YouTube (visibility Public), then the Devpost video link** | 2:29.4, 1920x1080, H.264 + AAC voice-over, about 21 MB |
| `docs/video/subsweep-demo-voiced.srt` | YouTube captions for the voiced video | 41 captions timed to the voice-over |
| `docs/video/subsweep-demo.mp4` | Silent fallback | {mm}:{ss:04.1f} long, {v['size']}, {v['codec']}, {v['fps']} fps, {v['bytes'] / 1e6:.1f} MB, no audio. sha256 `{sha[:16]}...` |
| `docs/video/subsweep-demo.srt` | Captions for the silent version only | {ncaps} captions, matching the recorded variants |
| `docs/screenshots/*.jpg` | Devpost image gallery, in this order | see below |
| Repository | Devpost "Try it out" link | {REPO} (**must be public by the time you submit**) |

The video is under 3:00 (the hackathon limit) and follows the seven beats of `docs/DEMO_SCRIPT.md`.

**Image gallery order**

| # | File | Shows |
|---|---|---|
{shot_rows}

## YouTube

Title:

{fenced(yt_title)}

Description:

{fenced(yt_description)}

Visibility: **Public**. After uploading, open the link in a private window to confirm it plays without signing in.

## Devpost

### Project name ({len(name)} of 59 characters)

{fenced(name)}

### Elevator pitch ({len(pitch)} of 199 characters)

{fenced(pitch)}

### About the project (paste as Markdown)

{fenced(description)}

### Built with

{fenced(built_with)}

### Try it out links

{fenced(REPO + chr(10) + "https://subsweep-chi.vercel.app" + chr(10) + "[YouTube link: fill in after uploading]")}

### Testing instructions (if the form has a field for it)

{fenced(TESTING)}

## Before you press Submit

- [ ] `docs/MANUAL_STEPS.md` sections 1 to 4 done (live checks if you want them, video recorded and uploaded, repository public).
- [ ] No `[square bracket]` placeholder is left in the text you pasted.
- [ ] Deadline: Thursday 12 November 2026, 12:00 pm Pacific. The project must show as **Submitted**, not Draft.
"""
    dest = ROOT / "docs" / "SUBMISSION_PACK.md"
    dest.write_text(md, encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)} ({len(md)} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
