#!/usr/bin/env python3
"""Seed the PayPal SANDBOX with demo plans and subscriptions.

  python scripts/seed_paypal_sandbox.py --dry-run      # show what would be created (no network)
  python scripts/seed_paypal_sandbox.py                # asks you to type 'yes', then creates what is missing
  python scripts/seed_paypal_sandbox.py --status       # read-only: live status of the seeded subscriptions
  python scripts/seed_paypal_sandbox.py --wait         # poll until you have approved them as the sandbox buyer

Needs PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (sandbox app) in .env. PAYPAL_BUYER_EMAIL is optional.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from subsweep.paypal import ConfirmationRequired, PayPalClient, PayPalConfig, PayPalError  # noqa: E402
from subsweep.paypal.seed import (  # noqa: E402
    REGISTRY_DEFAULT, load_registry, plan_actions, seed, wait_until_active,
)
from subsweep.paypal.subscriptions import live_status  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=str(REGISTRY_DEFAULT))
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--buyer-email", default=None, help="sandbox personal (buyer) account email; or PAYPAL_BUYER_EMAIL")
    a = ap.parse_args(argv)

    reg = load_registry(a.registry)
    if a.dry_run:
        acts = plan_actions(reg)
        print("\n".join(f"  - {x}" for x in acts) if acts else "Nothing to create: everything is already seeded.")
        return 0
    try:
        cfg = PayPalConfig.from_env()
        client = PayPalClient(cfg)
        if a.status or a.wait:
            reg = load_registry(a.registry)
            if not reg.get("subscriptions"):
                print("Nothing seeded yet. Run this script without --status first.")
                return 1
            if a.wait:
                print("Approve each link as your sandbox buyer; polling every 10s (Ctrl+C to stop).")
                for s in reg["subscriptions"].values():
                    print(f"  {s['name']}: {s['approve_url']}")
                wait_until_active(client, reg, on_update=lambda st: print("  ", st))
            live, errors = live_status(client, [s["id"] for s in reg["subscriptions"].values()])
            for l in live:
                print(f"{l.plan_name or l.plan_id:<28} {l.status:<18} next: {l.next_billing_time}  last: {l.last_payment_amount}")
            for sid, msg in errors.items():
                print(f"{sid}: {msg}")
            return 0

        def confirm(actions):
            print("This will create the following in your PayPal SANDBOX:")
            print("\n".join(f"  - {x}" for x in actions))
            return a.yes or input("Type 'yes' to continue: ").strip().lower() == "yes"

        reg = seed(client, registry_path=a.registry, buyer_email=a.buyer_email or os.environ.get("PAYPAL_BUYER_EMAIL"),
                   confirm=confirm)
    except ConfirmationRequired:
        print("Nothing was created.")
        return 1
    except PayPalError as exc:
        print(f"PayPal said no: {exc.user_message}")
        return 2
    print("\nSeeded. Now open each link while signed in as your sandbox BUYER (personal) account and approve it:")
    for s in reg["subscriptions"].values():
        print(f"  {s['name']}: {s['approve_url'] or '(no approval link returned)'}")
    print(f"\nSaved to {a.registry}. Check progress with: python scripts/seed_paypal_sandbox.py --status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
