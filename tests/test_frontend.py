"""Static checks on the frontend: contrast of every theme/mood, icon references, CSP-safe markup, no external requests."""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
TOKENS = (WEB / "css" / "tokens.css").read_text()
MOODS = (WEB / "js" / "moods.js").read_text()


# ---------------------------------------------------------------- colour maths (WCAG 2.x)
def lin(c):
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb):
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg):
    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def over(fg, alpha, bg):
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))


def parse_color(text):
    text = text.strip()
    if m := re.fullmatch(r"#([0-9a-fA-F]{6})", text):
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    if m := re.fullmatch(r"rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:/\s*([\d.]+))?\s*\)", text):
        return tuple(int(m.group(i)) for i in (1, 2, 3)), float(m.group(4) or 1)
    raise ValueError(text)


def theme_block(name):
    pat = r":root\s*\{(.*?)\n\}" if name == "night" else r':root\[data-theme="day"\]\s*\{(.*?)\n\}'
    body = re.search(pat, TOKENS, re.S).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def moods(table):
    block = re.search(rf"export const {table} = \{{(.*?)\n\}};", MOODS, re.S).group(1)
    out = {}
    for line in block.splitlines():
        m = re.match(r"\s*(\w+):\s*\{(.*)\}", line)
        if m:
            out[m.group(1)] = {k: tuple(int(x) for x in v.split(",")) for k, v in re.findall(r"(\w+):\s*\[([^\]]+)\]", m.group(2))}
    return out


NIGHT, DAY = moods("NIGHT"), moods("DAY")
def aurora_worst(theme, mood):
    """Worst plausible background behind text: the lighter gradient end, a blob's glow at full strength on top (night: the
    brightest glow; day: the darkest), then the scrim. Real pages are always better than this."""
    t = NIGHT if theme == "night" else DAY
    tok = theme_block(theme)
    m = t[mood]
    base = m["b"] if theme == "night" else m["a"]
    glow = max((m["g1"], m["g2"], m["g3"]), key=lum if theme == "night" else lambda c: -lum(c))
    bare = over(glow, float(tok["--blob-opacity"]), base)
    return over((4, 6, 18), float(tok["--scrim"]), bare)


def glass_backing(theme, mood):
    tok = theme_block(theme)
    backing = aurora_worst(theme, mood)
    tint, ta = parse_color(tok["--glass-tint"])
    backing = over(tint, ta, backing)
    wash, wa = parse_color(tok["--glass-bg"])
    return over(wash, wa, backing)


# ---------------------------------------------------------------- tests
@pytest.mark.parametrize("theme", ["night", "day"])
def test_moods_are_parsed(theme):
    t = NIGHT if theme == "night" else DAY
    assert set(t) == {"midnight", "deepsea", "dusk", "ember", "dawn"}
    assert all(set(v) == {"a", "b", "g1", "g2", "g3"} for v in t.values())


@pytest.mark.parametrize("theme", ["night", "day"])
@pytest.mark.parametrize("mood", ["midnight", "deepsea", "dusk", "ember", "dawn"])
@pytest.mark.parametrize("token,minimum", [("--ink", 7.0), ("--ink-2", 7.0), ("--ink-3", 4.5)])
def test_text_on_glass_meets_wcag_on_every_mood(theme, mood, token, minimum):
    fg, _ = parse_color(theme_block(theme)[token])
    r = ratio(fg, glass_backing(theme, mood))
    assert r >= minimum, f"{theme}/{mood} {token}: {r:.2f} < {minimum}"


@pytest.mark.parametrize("theme", ["night", "day"])
@pytest.mark.parametrize("mood", ["midnight", "deepsea", "dusk", "ember", "dawn"])
def test_text_sitting_directly_on_the_aurora_is_readable(theme, mood):
    tok = theme_block(theme)
    bare = aurora_worst(theme, mood)
    assert ratio(parse_color(tok["--ink"])[0], bare) >= 7.0                # hero headline and paragraph
    assert ratio(parse_color(tok["--ink-3"])[0], bare) >= 4.5              # eyebrows, captions and small labels


@pytest.mark.parametrize("theme", ["night", "day"])
@pytest.mark.parametrize("mood", ["midnight", "deepsea", "dusk", "ember", "dawn"])
def test_alert_colour_is_readable_for_text_and_chips(theme, mood):
    tok = theme_block(theme)
    backing = glass_backing(theme, mood)
    alert, _ = parse_color(tok["--alert"])
    assert ratio(alert, backing) >= 4.5, f"{theme}/{mood} --alert on glass"
    chip_bg, ca = parse_color(tok["--alert-bg"])
    chip_ink, _ = parse_color(tok["--alert-ink"])
    assert ratio(chip_ink, over(chip_bg, ca, backing)) >= 4.5, f"{theme}/{mood} --alert-ink on alert chip"


@pytest.mark.parametrize("theme", ["night", "day"])
def test_good_colour_and_buttons(theme):
    tok = theme_block(theme)
    for mood in NIGHT:
        good, _ = parse_color(tok["--good"])
        assert ratio(good, glass_backing(theme, mood)) >= 4.5, f"{theme}/{mood} --good"
    btn_bg, _ = parse_color(tok["--btn-bg"])
    btn_ink, _ = parse_color(tok["--btn-ink"])
    assert ratio(btn_ink, btn_bg) >= 7.0


@pytest.mark.parametrize("theme", ["night", "day"])
def test_landing_chips_and_focus_ring(theme):
    tok = theme_block(theme)
    chip, ca = parse_color(tok["--chip-bg"])
    ink, _ = parse_color(tok["--ink"])
    ink3, _ = parse_color(tok["--ink-3"])
    backing = over(chip, ca, aurora_worst(theme, "deepsea"))
    assert ratio(ink, backing) >= 7.0 and ratio(ink3, backing) >= 4.5       # the scattered statement lines in the unravel scene
    focus, _ = parse_color(tok["--focus"])
    for mood in NIGHT:
        assert ratio(focus, aurora_worst(theme, mood)) >= 3.0               # WCAG 1.4.11 non-text contrast


# ---------------------------------------------------------------- markup / safety
HTML = (WEB / "index.html").read_text()
JS = {p.name: p.read_text() for p in (WEB / "js").glob("*.js")}
CSS = {p.name: p.read_text() for p in (WEB / "css").glob("*.css")}


def test_document_basics():
    assert '<html lang="en"' in HTML and 'name="viewport"' in HTML and "<title>" in HTML
    assert 'class="skip"' in HTML and '<main id="main"' in HTML and "<header" in HTML and "<nav" in HTML
    assert HTML.count("<h1") == 1                              # the landing hero; each app view renders its own


def test_no_inline_scripts_or_handlers_so_the_csp_can_stay_strict():
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", HTML)
    assert not re.search(r"\son[a-z]+\s*=", HTML)
    assert "javascript:" not in HTML


def test_no_external_requests_anywhere_in_our_code():
    for name, text in {**{f"js/{k}": v for k, v in JS.items() if k != "chart.umd.js"}, **{f"css/{k}": v for k, v in CSS.items()}, "index.html": HTML}.items():
        urls = [u for u in re.findall(r"https?://[^\s\"'`)<>]+", text) if not u.startswith(("http://www.w3.org", "http://127.0.0.1", "http://localhost"))]
        assert not urls, f"{name} loads or links to {urls}"


def test_every_icon_used_is_defined_in_the_sprite():
    defined = set(re.findall(r'<symbol id="i-([\w-]+)"', HTML))
    used = set(re.findall(r'href="#i-([\w-]+)"', HTML))
    for text in JS.values():
        used |= set(re.findall(r'\bicon\("([\w-]+)"', text))
        used |= set(re.findall(r'icon: "([\w-]+)"', text)) | set(re.findall(r'\bicon: *"([\w-]+)"', text))
    used |= set(re.findall(r"TYPE_ICON = \{([^}]*)\}", JS["util.js"])[0].replace('"', "").replace(" ", "").split(",")[0:0])
    for m in re.findall(r"TYPE_ICON = \{([^}]*)\}", JS["util.js"]):
        used |= {pair.split(":")[1].strip().strip('"') for pair in m.split(",") if ":" in pair}
    assert used <= defined, f"undefined icons: {used - defined}"


def test_server_data_is_never_inserted_as_html():
    hits = {k: len(re.findall(r"\.innerHTML\s*=|insertAdjacentHTML|outerHTML\s*=|document\.write", v)) for k, v in JS.items() if k != "chart.umd.js"}
    assert {k: v for k, v in hits.items() if v} == {"overview.js": 1}        # only the constant SVG for the health ring
    ring = re.search(r"svg\.innerHTML = `(.*?)`;", JS["overview.js"], re.S).group(1)
    assert "${" not in ring                                                 # no interpolation: it cannot carry data


def test_reduced_motion_and_focus_styles_exist():
    base = CSS["base.css"]
    assert "prefers-reduced-motion: reduce" in base and ":focus-visible" in base
    assert "prefers-reduced-motion: reduce" in CSS["landing.css"]
    assert "reduceMotion()" in JS["scenes.js"] and "prefers-reduced-motion" in JS["theme.js"]


def test_vendored_assets_and_licences_are_present():
    for f in ["vendor/chart.umd.js", "vendor/LICENSE-chartjs.md", "vendor/THIRD_PARTY.md", "vendor/fonts/fraunces-latin-opsz-normal.woff2",
              "vendor/fonts/hanken-grotesk-latin-wght-normal.woff2", "vendor/fonts/LICENSE-fraunces-OFL.txt"]:
        assert (WEB / f).is_file(), f
    assert "Chart.js v4.4.1" in (WEB / "vendor/chart.umd.js").read_text()[:200]
