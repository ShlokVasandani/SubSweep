"""Keeps the test suite hermetic: nothing outside tests/integration may reach live PayPal, the Anthropic API or a developer's .env.

PayPalConfig.from_env() calls load_dotenv(), which reads .env and fills in any variable a test just deleted, so removing variables is not
enough. For every test outside tests/integration this file therefore (a) removes the PayPal/Anthropic variables, (b) sets PAYPAL_MODE=mock and
(c) turns load_dotenv into a no-op. The values are only moved around in memory; they are never printed, logged or written anywhere.
Tests under tests/integration get the real environment (including .env) back, because they exist to talk to the PayPal sandbox.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from subsweep.paypal import config as paypal_config

SECRET_KEYS = ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_API_BASE", "PAYPAL_MANAGE_URL", "SUBSWEEP_ALLOW_LIVE",
               "ANTHROPIC_API_KEY", "SUBSWEEP_AI", "SUBSWEEP_SEED_REGISTRY")
_REAL_LOAD_DOTENV = paypal_config.load_dotenv
_INTEGRATION = Path(__file__).resolve().parent / "integration"


def _is_integration(path) -> bool:
    return _INTEGRATION in Path(str(path)).resolve().parents


@pytest.fixture(scope="session", autouse=True)
def _hermetic_session():
    """Collection has already run (the integration module reads .env at import). Take every sensitive variable out of os.environ for the
    session, keep it in memory so integration modules can have it back, and put everything back at the end."""
    saved = {k: os.environ.pop(k) for k in (*SECRET_KEYS, "PAYPAL_MODE") if k in os.environ}
    os.environ["PAYPAL_MODE"] = "mock"
    paypal_config.load_dotenv = lambda *a, **k: None
    try:
        yield saved
    finally:
        paypal_config.load_dotenv = _REAL_LOAD_DOTENV
        os.environ.pop("PAYPAL_MODE", None)
        os.environ.update(saved)


@pytest.fixture(scope="module", autouse=True)
def _live_environment_for_integration(request, _hermetic_session):
    """Module-scoped so it is in place before the integration modules' own module-scoped fixtures build their PayPal client."""
    if not _is_integration(request.module.__file__):
        yield
        return
    mp = pytest.MonkeyPatch()
    mp.setattr(paypal_config, "load_dotenv", _REAL_LOAD_DOTENV)
    mp.delenv("PAYPAL_MODE", raising=False)
    for k, v in _hermetic_session.items():
        if k != "PAYPAL_MODE":
            mp.setenv(k, v)
    try:
        yield
    finally:
        mp.undo()
