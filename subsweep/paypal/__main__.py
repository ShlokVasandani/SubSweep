"""CLI (sandbox, read-only):
  python -m subsweep.paypal status                 live status of the seeded demo subscriptions
  python -m subsweep.paypal transactions --days 30 recent PayPal transactions (payer's point of view)
"""
import argparse
from datetime import date, timedelta

from . import PayPalClient, PayPalConfig, PayPalError
from .seed import REGISTRY_DEFAULT, registry_subscription_ids
from .subscriptions import live_status
from .transactions import fetch_transactions, to_transactions


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
        client = PayPalClient(PayPalConfig.from_env())
        if a.cmd == "status":
            ids = registry_subscription_ids(a.registry)
            if not ids:
                print("No seeded subscriptions yet. Run: python scripts/seed_paypal_sandbox.py")
                return 1
            live, errors = live_status(client, ids)
            for l in live:
                print(f"{(l.plan_name or l.plan_id):<26} {l.status:<18} next billing: {l.next_billing_time or '-'}  "
                      f"last payment: {l.last_payment_amount if l.last_payment_amount is not None else '-'}")
                print(f"{'':<26} manage it yourself at {l.manage_url}")
            for sid, msg in errors.items():
                print(f"{sid}: {msg}")
        else:
            batch = fetch_transactions(client, date.today() - timedelta(days=a.days), date.today())
            df, dropped = to_transactions(batch.details, a.perspective)
            print(f"{len(df)} payments ({sum(dropped.values())} non-payment rows skipped); PayPal data refreshed {batch.last_refreshed}")
            for n in batch.notes:
                print("note:", n)
            if len(df):
                print(df[["date", "merchant_clean", "amount", "currency", "paypal_subscription_id"]].to_string(index=False))
        return 0
    except PayPalError as exc:
        print(f"PayPal: {exc.user_message}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
