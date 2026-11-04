"""CLI (sandbox, read-only):
  python -m subsweep.paypal status                  live status of the seeded demo subscriptions
  python -m subsweep.paypal transactions --days 30  recent PayPal transactions (payer's point of view)

PAYPAL_MODE=mock uses the bundled demo data (no credentials, no network). In live mode, if a seeded subscription is
still awaiting buyer approval the bundled demo data is shown instead, and says so.
"""
import argparse
from datetime import date, timedelta

from . import PayPalClient, PayPalConfig, PayPalError
from .provider import current_mode, get_paypal_view
from .seed import REGISTRY_DEFAULT, registry_subscription_ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--registry", default=str(REGISTRY_DEFAULT))
    tx = sub.add_parser("transactions")
    tx.add_argument("--days", type=int, default=30)
    tx.add_argument("--perspective", choices=["payer", "merchant"], default="payer")
    a = ap.parse_args(argv)
    try:
        client = None if current_mode() == "mock" else PayPalClient(PayPalConfig.from_env())
        if a.cmd == "status":
            view = get_paypal_view(client, registry_ids=registry_subscription_ids(a.registry))
        else:
            end = date.today()
            view = get_paypal_view(client, end - timedelta(days=a.days), end, perspective=a.perspective)
        if view.is_mock:
            print(f"[DEMO DATA] {view.reason}\n            {view.notes[0]}")
        if a.cmd == "status":
            if not view.live:
                print("No PayPal subscriptions found yet. Run: python scripts/seed_paypal_sandbox.py")
                return 1
            for l in view.live:
                print(f"{(l.plan_name or l.plan_id):<26} {l.status:<18} next billing: {l.next_billing_time or '-'}  "
                      f"last payment: {l.last_payment_amount if l.last_payment_amount is not None else '-'}")
                print(f"{'':<26} manage it yourself at {l.manage_url}")
            for sid, msg in view.errors.items():
                print(f"{sid}: {msg}")
        else:
            df = view.transactions
            print(f"{len(df)} payments")
            for n in view.notes[1:] if view.is_mock else view.notes:
                print("note:", n)
            if len(df):
                print(df[["date", "merchant_clean", "amount", "currency", "source"]].to_string(index=False))
        return 0
    except PayPalError as exc:
        print(f"PayPal: {exc.user_message}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
