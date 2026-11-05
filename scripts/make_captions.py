"""Writes docs/video/subsweep-demo.srt from the narration in docs/DEMO_SCRIPT.md.

    python scripts/make_captions.py [--live-paypal] [--claude]

The recording in docs/video was made with the bundled PayPal demo data and no Anthropic key, so by default the captions use the script's
"variant B" PayPal sentence and its "AI is not on" replacement. Pass --live-paypal / --claude when you re-record with those switched on.
Each beat's caption text is spread over that beat's time slot in proportion to its length; retime in a video editor if you re-pace the clicks.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_submission", ROOT / "scripts" / "check_submission.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

CLAUDE_SENTENCE = "The wording comes from Claude, and every figure is checked against the data before I see it."
BUILTIN_SENTENCE = "Here the wording is built in; with an Anthropic key, Claude writes it, and every figure is still checked against the data."
PAYPAL_DEMO_SENTENCE = "This panel is labelled demo data; with sandbox credentials the same screen reads live from PayPal."
PAYPAL_LIVE_SENTENCE = "This status is read live from the PayPal sandbox."


def narration(live_paypal: bool, claude: bool) -> list[dict]:
    beats = cs.script_beats()
    for b in beats:
        text = b["say"]
        if "duplicate" in b["label"].lower() and not claude:
            if CLAUDE_SENTENCE not in text:
                raise SystemExit("the duplicate beat no longer contains the Claude sentence; update make_captions.py")
            text = text.replace(CLAUDE_SENTENCE, BUILTIN_SENTENCE)
        if "paypal" in b["label"].lower():
            text = f"{text} {PAYPAL_LIVE_SENTENCE if live_paypal else PAYPAL_DEMO_SENTENCE}"
        b["text"] = text
        b["words_spoken"] = len(text.split())
    return beats


def stamp(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def captions(beats: list[dict]) -> list[tuple[float, float, str]]:
    out = []
    for b in beats:
        lo, hi = b["start"] + 0.4, b["end"] - 0.4
        chunks: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", b["text"]):
            lines = textwrap.wrap(sentence, width=44)
            chunks += ["\n".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]
        total = sum(len(c) for c in chunks)
        t = lo
        for c in chunks:
            d = (hi - lo) * len(c) / total
            out.append((t, t + d, c))
            t += d
    return out


def main() -> int:
    live, claude = "--live-paypal" in sys.argv, "--claude" in sys.argv
    beats = narration(live, claude)
    caps = captions(beats)
    dest = ROOT / "docs" / "video" / "subsweep-demo.srt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{t}\n" for i, (a, b, t) in enumerate(caps, 1)), encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}: {len(caps)} captions; PayPal variant {'A (live)' if live else 'B (demo data)'}, AI {'Claude' if claude else 'built-in wording'}")
    fast = False
    for b in beats:
        slot = b["end"] - b["start"]
        rate = b["words_spoken"] / slot
        flag = "" if rate <= cs.MAX_WORDS_PER_SECOND else "   <-- too fast to speak comfortably"
        fast = fast or bool(flag)
        print(f"  {b['start'] // 60}:{b['start'] % 60:02d}  {b['words_spoken']:>3} words in {slot}s = {rate:.2f} words/s  {b['label']}{flag}")
    print(f"total narration: {sum(b['words_spoken'] for b in beats)} words")
    return 1 if fast else 0


if __name__ == "__main__":
    sys.exit(main())
