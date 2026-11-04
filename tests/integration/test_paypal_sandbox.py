"""Live PayPal SANDBOX checks. Skipped unless PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are set.

Read-only: it never creates, changes or cancels anything. Run with:  python -m pytest tests/integration -v
Failures print PayPal's friendly message and debug id so a mismatch with the docs can be reported exactly.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from subsweep.paypal import PayPalClient, PayPalConfig, PayPalError
from subsweep.paypal.config import load_dotenv
from subsweep.paypal.seed import REGISTRY_DEFAULT, registry_subscription_ids
from subsweep.paypal.subscriptions import SCOPE_SUBSCRIPTIONS, list_plans, live_status
from subsweep.paypal.transactions import SCOPE_SEARCH, fetch_transactions, to_transactions

load_dotenv()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET")),
                       reason="PayPal sandbox credentials not set (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)"),
]


@pytest.fixture(scope="module")
def client():
    c = PayPalClient(PayPalConfig.from_env())
    yield c
    c.close()


def test_oauth_client_credentials_returns_a_token_and_scopes(client):
    try:
        token = client.get_token()
    except PayPalError as exc:
        pytest.fail(f"OAuth failed: {exc.user_message} (status {exc.status}, debug {exc.debug_id})")
    assert token and SCOPE_SUBSCRIPTIONS in client.scopes, f"scopes granted: {sorted(client.scopes)}"


def test_subscriptions_api_lists_plans(client):
    try:
        plans = list_plans(client)
    except PayPalError as exc:
        pytest.fail(f"Subscriptions API failed: {exc.user_message} (status {exc.status}, debug {exc.debug_id})")
    assert isinstance(plans, list)


def test_transaction_search_last_week(client):
    client.get_token()
    if SCOPE_SEARCH not in client.scopes:
        pytest.fail(f"The app's token does not include {SCOPE_SEARCH}. Granted scopes: {sorted(client.scopes)}")
    try:
        batch = fetch_transactions(client, date.today() - timedelta(days=7), date.today())
    except PayPalError as exc:
        pytest.fail(f"Transaction Search failed: {exc.user_message} (status {exc.status}, debug {exc.debug_id})")
    df, _ = to_transactions(batch.details)
    assert df.empty or (df.amount < 0).all()       # payer perspective: payments are debits


def test_seeded_subscriptions_report_live_status(client):
    ids = registry_subscription_ids(Path(os.environ.get("SUBSWEEP_SEED_REGISTRY", REGISTRY_DEFAULT)))
    if not ids:
        pytest.skip("No seeded subscriptions yet (run scripts/seed_paypal_sandbox.py)")
    live, errors = live_status(client, ids)
    assert not errors, errors
    assert {l.status for l in live} <= {"APPROVAL_PENDING", "APPROVED", "ACTIVE", "SUSPENDED", "CANCELLED", "EXPIRED"}
