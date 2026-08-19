"""Update Score in Baserow & Sync to Shopify Metafields.

Usage:
  # Update score for a single row in Baserow & push to Shopify
  python update_score_baserow_to_shopify.py --row-id 2 --score 95 --sync-shopify

  # Batch sync existing Baserow scores to Shopify
  python update_score_baserow_to_shopify.py --all
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Update Baserow product Score and sync to Shopify metafields."
    )
    p.add_argument("--row-id", type=int, help="Baserow product row ID to update.")
    p.add_argument("--score", type=int, help="Score value (0-100).")
    p.add_argument(
        "--sync-shopify",
        action="store_true",
        help="Push updated score metafield directly to Shopify.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Sync all Baserow product scores to Shopify storefront.",
    )
    p.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="Optional delay seconds before execution.",
    )
    return p.parse_args()


def update_single_score(row_id: int, score: int, sync_shopify: bool = True) -> bool:
    """Update score in Baserow table 742 and optionally push to Shopify."""
    settings = load_settings()
    baserow = BaserowClient(settings)
    score_field = "field_7394"
    woonbloq_field = "field_7425"

    print(f"Updating Baserow Item #{row_id} score -> {score}...")
    updated = baserow.update_row(settings.products_table_id, row_id, {score_field: str(score)})
    print(f"[OK] Baserow Item #{row_id} updated successfully.")

    if sync_shopify:
        shopify_gid = updated.get(woonbloq_field) or updated.get("WoonbloqProductID")
        if shopify_gid and str(shopify_gid).startswith("gid://shopify/Product/"):
            try:
                pid = int(str(shopify_gid).split("/")[-1])
                print(f"Syncing Score {score} to Shopify Product #{pid}...")
                shopify = ShopifyClient()
                ok, failed, errs = shopify.set_metafields_graphql(
                    shopify_gid,
                    [{
                        "namespace": "custom",
                        "key": "score",
                        "value": str(score),
                        "type": "number_integer",
                    }]
                )
                if ok > 0:
                    print(f"[OK] Shopify Product #{pid} score metafield updated.")
                    return True
                else:
                    print(f"[NOTICE] Shopify metafield set error: {errs}")
            except Exception as e:
                print(f"[NOTICE] Shopify sync error: {e}")
        else:
            print(f"[NOTICE] Item #{row_id} is not yet linked to a live Shopify Product ID.")

    return True


def sync_all_scores_to_shopify() -> None:
    """Batch update Shopify score metafields for all linked Baserow products."""
    settings = load_settings()
    baserow = BaserowClient(settings)
    shopify = ShopifyClient()

    print("Fetching linked products from Baserow...")
    rows = baserow.list_table_rows(settings.products_table_id)
    count = 0
    synced = 0

    for r in rows:
        count += 1
        score = r.get("field_7394") or r.get("Score")
        woonbloq_id = r.get("field_7425") or r.get("WoonbloqProductID")

        if score and woonbloq_id and str(woonbloq_id).startswith("gid://shopify/Product/"):
            try:
                score_int = int(float(score))
                ok, failed, errs = shopify.set_metafields_graphql(
                    str(woonbloq_id),
                    [{
                        "namespace": "custom",
                        "key": "score",
                        "value": str(score_int),
                        "type": "number_integer",
                    }]
                )
                if ok > 0:
                    synced += 1
            except Exception:
                pass

    print(f"Finished. Total products scanned: {count}, Shopify score metafields synced: {synced}.")


def main() -> int:
    args = parse_args()
    if args.wait > 0:
        print(f"Waiting {args.wait} seconds...")
        time.sleep(args.wait)

    if args.all:
        sync_all_scores_to_shopify()
        return 0

    if args.row_id is not None and args.score is not None:
        update_single_score(args.row_id, args.score, sync_shopify=args.sync_shopify)
        return 0

    print("Usage example:")
    print("  python update_score_baserow_to_shopify.py --row-id 2 --score 95 --sync-shopify")
    print("  python update_score_baserow_to_shopify.py --all")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
