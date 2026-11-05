"""PayPal settings, read from environment variables only (an optional local .env is loaded, never overriding)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .errors import PayPalNotConfigured

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"
SANDBOX_MANAGE_URL = "https://www.sandbox.paypal.com/myaccount/autopay/"


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines). Real environment variables always win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class PayPalConfig:
    client_id: str
    client_secret: str
    api_base: str = SANDBOX_BASE
    manage_url: str | None = None

    def __repr__(self) -> str:      # never print secrets
        return f"PayPalConfig(client_id={self.client_id[:4]}***, client_secret=***, api_base={self.api_base!r})"

    @property
    def is_sandbox(self) -> bool:
        return urlparse(self.api_base).hostname == urlparse(SANDBOX_BASE).hostname

    @property
    def subscriber_manage_url(self) -> str:
        """Where the BUYER manages automatic payments (we never cancel for them).

        Live URL is PayPal's documented page. The sandbox form swaps the host and is not confirmed in
        PayPal's docs, so PAYPAL_MANAGE_URL can override it.
        """
        if self.manage_url:
            return self.manage_url
        host = "www.sandbox.paypal.com" if self.is_sandbox else "www.paypal.com"
        return f"https://{host}/myaccount/autopay/"

    @classmethod
    def from_env(cls, *, require: bool = True) -> "PayPalConfig | None":
        load_dotenv()
        cid, sec = os.environ.get("PAYPAL_CLIENT_ID", "").strip(), os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
        base = (os.environ.get("PAYPAL_API_BASE") or SANDBOX_BASE).rstrip("/")
        if not cid or not sec:
            if require:
                raise PayPalNotConfigured(
                    "PayPal isn't connected yet. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET from a sandbox app "
                    "to your .env file (see .env.example).")
            return None
        if not base.startswith("https://"):
            raise PayPalNotConfigured("PAYPAL_API_BASE must be an https URL.")
        if urlparse(base).hostname != urlparse(SANDBOX_BASE).hostname and os.environ.get("SUBSWEEP_ALLOW_LIVE") != "1":
            raise PayPalNotConfigured(
                "SubSweep is a sandbox-only demo. PAYPAL_API_BASE must be https://api-m.sandbox.paypal.com "
                "(set SUBSWEEP_ALLOW_LIVE=1 only if you really mean to use a live account).")
        manage = (os.environ.get("PAYPAL_MANAGE_URL") or "").strip()
        # This ends up in an href in the browser: accept https links only (never javascript:, data: or plain http).
        return cls(cid, sec, base, manage if manage.lower().startswith("https://") else None)
