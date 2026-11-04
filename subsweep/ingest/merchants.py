"""Merchant description cleaning.

`clean_merchant(raw)` returns a short, human-readable merchant name suitable for
fuzzy grouping later. The raw text is never discarded by callers (the pipeline
keeps it in `merchant_raw`). Each rule is a small function so it can be tested
and explained on its own.
"""
from __future__ import annotations

import re

# --- noise vocabularies -------------------------------------------------------
_PREFIXES = [
    r"purchase authorized on \d{1,2}/\d{1,2}",
    r"recurring (?:payment|purchase|debit)(?: authorized on \d{1,2}/\d{1,2})?",
    r"debit card purchase",
    r"card purchase(?: with pin)?",
    r"check ?card",
    r"visa purchase",
    r"pos(?: purchase| debit)?",
    r"ach (?:debit|withdrawal|pmt|payment)",
    r"online (?:payment|transfer|purchase) (?:to|at)",
    r"autopay",
    r"payment to",
    r"pymt to",
    r"payment for",
    r"ecom",
    r"ecs",
    r"nach",
    r"si",  # standing instruction (only stripped as whole leading token)
]
_STAR_PREFIX_RE = re.compile(r"^(?:sq|tst|dd|paypal|pp|gpay|google pay)\s*\*\s*", re.I)
_PREFIX_RE = re.compile(r"^(?:" + "|".join(_PREFIXES) + r")(?=[\s\-/*:]|$)[\s\-/*:]*", re.I)
_PAYPAL_RE = re.compile(r"^\s*(?:paypal|pp)\s*\*", re.I)

_UPI_RE = re.compile(r"^\s*upi[\s/\-:]", re.I)
_UPI_NOISE = {
    "payment", "upi", "pay", "paid", "from", "to", "ref", "refno", "txn", "transfer",
    "mandate", "autopay", "collect", "request", "sent", "received", "via", "on", "for",
}
_VPA_RE = re.compile(r"\S+@\S+")

_CARD_MASK = re.compile(
    r"(?:\b(?:card|crd|acct|account|a/c|ac)\s*(?:no\.?|number|#|ending(?: in)?)?\s*[:#]?\s*)?"
    r"(?:[x*•]{2,}[\s-]*){1,4}\d{2,4}\b|\bending(?: in)?\s*\d{4}\b|\bxx+\d{3,4}\b",
    re.I,
)
_REF_LABELLED = re.compile(
    r"\b(?:ref(?:erence)?|txn|trans(?:action)?|trn|rrn|utr|order|inv(?:oice)?|id|auth|conf(?:irmation)?)(?![A-Za-z])"
    r"[\s.#:/\-]*(?:no\.?|number|id)?[\s.#:/\-]*(?=[A-Za-z0-9\-]*\d)[A-Za-z0-9\-]{3,}",
    re.I,
)
_HASH_REF = re.compile(r"#\s*[A-Za-z0-9\-]+")
_LONG_DIGITS = re.compile(r"\b\d{4,}\b")
_ALNUM_TOKEN = re.compile(r"\b[A-Za-z0-9]{5,}\b")
_PHONE = re.compile(r"\b(?:\+?\d{1,2}[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b|\b1?-?8[0-9]{2}-[A-Za-z0-9\-]{3,}\b")
_DATE_IN_DESC = re.compile(r"\b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b")
_STAR_ID = re.compile(r"\*(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,}")

_TLD_RE = re.compile(r"\.(?:com|net|org|in|co\.in|co\.uk|io|tv|app|ai|co|us)\b(?:/\S*)?", re.I)
_LEADING_WWW = re.compile(r"^(?:www|help|billing|support|pay)\.", re.I)

# Trailing location noise: "<city> <state/country code>" and bare codes.
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
_COUNTRIES = "US USA GB UK IN IND SE DE FR NL IE SG AE CA AU NZ ES IT LU CH MH KA DL TN GJ WB KL RJ HR".split()
_LOC_CODES = set(_US_STATES) | set(_COUNTRIES)
_CITIES = {
    "los gatos", "san francisco", "new york", "mumbai", "bangalore", "bengaluru", "delhi",
    "new delhi", "gurgaon", "gurugram", "hyderabad", "chennai", "pune", "kolkata", "noida",
    "stockholm", "london", "dublin", "amsterdam", "singapore", "seattle", "mountain view",
    "cupertino", "austin", "houston", "dallas", "san jose", "atlanta", "miami", "denver", "portland",
    "san diego", "los angeles", "phoenix", "brooklyn", "toronto", "vancouver", "sydney", "melbourne",
    "berlin", "paris", "madrid", "jaipur", "ahmedabad", "kochi", "indore", "lucknow", "dubai", "chicago", "boston", "luxembourg", "redwood city", "palo alto",
}
_TRAILING_LOC_RE = re.compile(
    r"(?:\s+(?:" + "|".join(sorted((re.escape(c) for c in _CITIES), key=len, reverse=True)) + r"))?"
    r"(?:\s+(?:" + "|".join(sorted(_LOC_CODES)) + r"))?\s*$",
    re.I,
)
_NOISE_WORDS = re.compile(r"\b(?:inc|llc|ltd|pvt|pte|limited|corp|co|gmbh|ab|bv|sa)\b\.?$", re.I)
_PUNCT_EDGE = re.compile(r"^[\W_]+|[\W_]+$")


# --- individual rules ---------------------------------------------------------
def strip_card_mask(s: str) -> str:
    return _CARD_MASK.sub(" ", s)


def extract_upi(s: str) -> str | None:
    """UPI/123456789012/Spotify/spotify@okaxis/Payment -> 'Spotify'."""
    if not _UPI_RE.match(s):
        return None
    parts = [p.strip() for p in re.split(r"[/]|(?<=[A-Za-z])-(?=[A-Za-z@])", s) if p.strip()]
    candidates = []
    for p in parts[1:]:
        if _VPA_RE.search(p) or re.fullmatch(r"[\d\-]{6,}", p):
            continue
        if p.lower() in _UPI_NOISE:
            continue
        candidates.append(p)
    if candidates:
        return candidates[0]
    # Fall back to the VPA handle: spotify@okaxis -> spotify
    m = _VPA_RE.search(s)
    return m.group(0).split("@")[0] if m else None


def strip_prefixes(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _STAR_PREFIX_RE.sub("", s, count=1).strip()
        s = _PREFIX_RE.sub("", s, count=1).strip()
    return s


def _transitions(tok: str) -> int:
    kinds = [c.isdigit() for c in tok]
    return sum(a != b for a, b in zip(kinds, kinds[1:]))


def _strip_mixed_ids(s: str) -> str:
    """Drop opaque IDs like 'P3A8E1' or 'TXN8A7F6E5D4C' (many letter/digit switches).

    Brand names such as 'Office365' or '1Password' switch only once, so survive.
    """
    def repl(m):
        tok = m.group(0)
        return " " if (any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok) and _transitions(tok) >= 3) else tok
    return _ALNUM_TOKEN.sub(repl, s)


def strip_reference_noise(s: str) -> str:
    s = _REF_LABELLED.sub(" ", s)
    s = _HASH_REF.sub(" ", s)
    s = _PHONE.sub(" ", s)
    s = _DATE_IN_DESC.sub(" ", s)
    s = _LONG_DIGITS.sub(" ", s)
    s = _STAR_ID.sub(" ", s)
    s = _strip_mixed_ids(s)
    return s


_DOMAIN_TOKEN = re.compile(r"\b[\w\-]+(?:\.[\w\-]+)*\.(?:com|net|org|in|co\.in|co\.uk|io|tv|app|ai|co|us)\b(/\S*)?#?", re.I)


def strip_domain(s: str) -> str:
    """'NETFLIX.COM' -> 'NETFLIX'; a URL with a path after other words is dropped entirely."""
    s = _LEADING_WWW.sub("", s.strip())

    def repl(m):
        if m.group(1) and s[:m.start()].strip():  # e.g. 'Google *YouTube g.co/helppay#'
            return " "
        tok = re.sub(r"^(?:www|help|billing|support|pay)\.", "", m.group(0), flags=re.I)
        tok = re.sub(r"/\S*#?$", "", tok)
        return " " + _TLD_RE.sub("", tok) + " "
    return _DOMAIN_TOKEN.sub(repl, s)


def _drop_repeated_words(s: str) -> str:
    seen, out = set(), []
    for w in s.split():
        k = w.lower()
        if k in seen and len(k) > 2:
            continue
        seen.add(k)
        out.append(w)
    return " ".join(out)


def strip_trailing_location(s: str) -> str:
    """Remove a trailing '<city> <code>' / bare city / bare code, keeping >=1 word."""
    words = s.split()
    if len(words) <= 1:
        return s
    stripped = _TRAILING_LOC_RE.sub("", s).strip()
    return stripped if stripped else s


def strip_company_suffix(s: str) -> str:
    prev = None
    while prev != s and len(s.split()) > 1:
        prev = s
        s = _NOISE_WORDS.sub("", s).strip() or prev
    return s


def _strip_edges(s: str) -> str:
    """Trim punctuation at both ends, but keep a closing ')' that has a matching '(' ("Plus (Annual)")."""
    s = re.sub(r"^[\W_]+", "", s)
    while s and not s[-1].isalnum():
        if s[-1] == ")" and s.count("(") >= s.count(")"):
            break
        s = s[:-1]
    return s.strip()


def _tidy(s: str) -> str:
    s = re.sub(r"[\s_]+", " ", s)
    s = re.sub(r"\s*[*/|,;:]+\s*", " ", s)
    s = re.sub(r"\s-\s|\s-$|^-\s", " ", s)
    return _strip_edges(s)


_ACRONYMS = {
    "hbo", "aws", "ibm", "bbc", "gcp", "kfc", "bp", "nyt", "cnn", "espn", "tv", "uk", "us", "ai",
    "hd", "vpn", "hdfc", "icici", "sbi", "upi", "amc", "dstv",
}


def _smart_case(s: str) -> str:
    """Title-case ALL-CAPS / all-lower text; leave mixed case (YouTube, iCloud) alone."""
    if not (s.isupper() or s.islower()):
        return s
    def cap(w: str) -> str:      # capitalise the first letter, even after a leading "("
        i = next((k for k, ch in enumerate(w) if ch.isalpha()), None)
        return w if i is None else w[:i] + w[i].upper() + w[i + 1:].lower()

    return " ".join(w.upper() if w.strip("()").lower() in _ACRONYMS else cap(w) for w in s.split())


def is_paypal_description(raw: str) -> bool:
    return bool(_PAYPAL_RE.match(str(raw or "")))


# --- public entry point -------------------------------------------------------
def clean_merchant(raw) -> str:
    """Return a cleaned merchant name; falls back to a tidied raw string."""
    if raw is None:
        return ""
    original = str(raw).strip()
    if not original or original.lower() == "nan":
        return ""

    upi = extract_upi(original)
    s = upi if upi else original

    s = strip_card_mask(s)
    s = strip_prefixes(s)
    s = strip_reference_noise(s)
    s = strip_domain(s)
    s = _tidy(s)
    s = strip_trailing_location(s)
    s = strip_company_suffix(s)
    s = _drop_repeated_words(_tidy(s))

    if not s:  # everything was noise; fall back so we never return nothing
        s = _tidy(re.sub(r"\d{4,}", " ", original)) or original
    return _smart_case(s)
