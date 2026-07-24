"""
Dummy: pretend to push Score from Baserow to Shopify.

  python update_score_baserow_to_shopify.py
  python update_score_baserow_to_shopify.py --wait 10
"""
from __future__ import annotations

import argparse
import sys
import time


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Dummy score update: Baserow → Shopify (no real API calls)."
    )
    p.add_argument(
        "--wait",
        type=float,
        default=10,
        help="Seconds to wait before the success message (default: 10).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    wait = max(0.0, float(args.wait))

    print("Updating score from Baserow to Shopify...")
    print(f"Please wait...")
    sys.stdout.flush()

    time.sleep(wait)

    print("Score has been updated from Baserow to Shopify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
