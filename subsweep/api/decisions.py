"""Where a visitor's "dismissed / still using" decisions live in the hosted demo.

Locally they are kept in the SQLite `Store`. A serverless host has no durable disk and no memory shared between requests, and one shared
database would let every visitor change what everyone else sees, so the hosted demo keeps each visitor's decisions in their own cookie.
The cookie is validated on every read: anything that is not a known finding id with a known reason is dropped, and it never raises.
"""
from __future__ import annotations

import base64
import binascii
import json
import re

from starlette.requests import Request
from starlette.responses import Response

COOKIE = "subsweep_decisions"
MAX_DECISIONS = 40                  # a statement has a handful of findings; this only bounds a tampered cookie
MAX_COOKIE_CHARS = 2000
MAX_AGE = 6 * 3600
REASONS = ("dismissed", "still_using")
_FINDING_ID = re.compile(r"^fnd_[0-9a-f]{6,16}$")


def parse_cookie(raw: str | None) -> dict[str, str]:
    """Decode a decisions cookie; any malformed, oversized or unexpected content gives an empty (or filtered) result, never an error."""
    if not raw or len(raw) > MAX_COOKIE_CHARS:
        return {}
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {k: v for k, v in data.items() if isinstance(k, str) and _FINDING_ID.match(k) and v in REASONS}
    return dict(list(clean.items())[:MAX_DECISIONS])


class CookieDecisions:
    """Same interface as `subsweep.store.Store` for the parts the API uses, backed by one request's cookie."""

    def __init__(self, request: Request):
        self._d = parse_cookie(request.cookies.get(COOKIE))
        self._secure = request.url.scheme == "https"

    def dismissals(self) -> dict[str, str]:
        return dict(self._d)

    def dismiss(self, finding_id: str, reason: str = "dismissed") -> None:
        if reason not in REASONS:
            raise ValueError("reason must be 'dismissed' or 'still_using'")
        if not _FINDING_ID.match(finding_id):
            return                                                  # not an id this app could have produced
        if finding_id not in self._d and len(self._d) >= MAX_DECISIONS:
            return
        self._d[finding_id] = reason

    def restore(self, finding_id: str) -> None:
        self._d.pop(finding_id, None)

    def clear(self) -> None:
        self._d.clear()

    def write(self, response: Response) -> None:
        raw = base64.urlsafe_b64encode(json.dumps(self._d, separators=(",", ":")).encode()).decode().rstrip("=")
        response.set_cookie(COOKIE, raw, httponly=True, samesite="strict", secure=self._secure, path="/", max_age=MAX_AGE)
