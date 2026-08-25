"""Sync 3-Tier Recommendation Engine outputs to Shopify custom.similar_products metafield.

Usage:
  python sync_recommendations_to_shopify.py --dry-run --limit 5
  python sync_recommendations_to_shopify.py --apply --limit 10
  python sync_recommendations_to_shopify.py --apply --target binnen
  python sync_recommendations_to_shopify.py --apply --target sleepworld
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from baserow_client import BaserowClient
from config import load_settings
from recommendation_engine import RecommendationEngine, extract_linked_name
from shopify_client import ShopifyClient, load_shopify_config


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sync recommendation engine metafields to Shopify storefronts."
    )
    p.add_argument(
        "--target",
        choices=["woonbloq", "binnen", "sleepworld"],
        default="woonbloq",
        help="Target Shopify webstore (default: woonbloq).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes live to Shopify storefront & Baserow.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying Shopify or Baserow.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of products to sync (0 = unlimited).",
    )
    p.add_argument(
        "--top-k",
        type=int,
        default=4,
        help="Number of recommended products per item (default: 4).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.apply and not args.dry_run:
        print("[NOTICE] Running in --dry-run mode by default. Pass --apply to write changes.")
        args.dry_run = True

    settings = load_settings()
    baserow = BaserowClient(settings)
    shopify = ShopifyClient()

    print(f"Fetching catalog products from Baserow Table {settings.products_table_id}...", flush=True)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import requests

    def fetch_page(p_num):
        url = f"{settings.api_base}/database/rows/table/{settings.products_table_id}/?page={p_num}&size=200"
        r = requests.get(url, headers={"Authorization": f"Token {settings.baserow_token}"}, timeout=30)
        return r.json().get("results", []) if r.status_code == 200 else []

    r0 = requests.get(f"{settings.api_base}/database/rows/table/{settings.products_table_id}/?page=1&size=1", headers={"Authorization": f"Token {settings.baserow_token}"}, timeout=30)
    total_count = r0.json().get("count", 0) if r0.status_code == 200 else 0
    num_pages = (total_count + 199) // 200

    if args.limit > 0 and args.limit <= 20:
        max_fetch_pages = min(3, num_pages)
    else:
        max_fetch_pages = num_pages

    rows = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_page, p) for p in range(1, max_fetch_pages + 1)]
        for f in as_completed(futures):
            rows.extend(f.result())

    print(f"Total products fetched: {len(rows)}.", flush=True)

    engine = RecommendationEngine(rows)

    # Determine product GID field based on target store
    gid_field = "field_7425"  # WoonbloqProductID
    if args.target == "binnen":
        gid_field = "field_7407"  # BinnenProductID
    elif args.target == "sleepworld":
        gid_field = "field_7426"  # SleepworldProductID

    eligible_products = [
        p for p in rows
        if p.get(gid_field) and str(p.get(gid_field)).startswith("gid://shopify/Product/")
    ]
    print(f"Eligible products linked to {args.target.upper()} Shopify store: {len(eligible_products)}", flush=True)

    if args.limit > 0:
        eligible_products = eligible_products[:args.limit]
        print(f"Processing limited to top {args.limit} products.", flush=True)

    synced_count = 0
    failed_count = 0

    for idx, product in enumerate(eligible_products, start=1):
        pid = product["id"]
        pname = product.get("field_7347") or f"Product #{pid}"
        shopify_gid = str(product.get(gid_field))

        # Get top_k recommendations using 3-tier algorithm
        recs = engine.get_recommendations(product, top_k=args.top_k, store_target=args.target)
        if not recs:
            print(f"[{idx}/{len(eligible_products)}] '{pname}' -> No candidate recommendations found.", flush=True)
            continue

        rec_gids = [str(cand.get(gid_field)) for cand, _ in recs if cand.get(gid_field)]
        rec_names = [str(cand.get("field_7347") or "") for cand, _ in recs]

        # Format custom.similar_products metafield value as JSON array string
        metafield_json_val = json.dumps(rec_gids)

        print(f"\n[{idx}/{len(eligible_products)}] Item #{pid} '{pname}' -> Shopify GID: {shopify_gid}", flush=True)
        print(f"  Recommended Similar Products ({len(rec_gids)}): {rec_names}", flush=True)
        print(f"  Metafield Payload (custom.similar_products): {metafield_json_val}", flush=True)

        if args.dry_run:
            print("  [DRY-RUN] Skipped live Shopify update.", flush=True)
            synced_count += 1
            continue

        # Live Update Shopify Metafield
        try:
            ok, failed, errs = shopify.set_metafields_graphql(
                shopify_gid,
                [{
                    "namespace": "custom",
                    "key": "similar_products",
                    "value": metafield_json_val,
                    "type": "list.single_line_text_field",
                }]
            )

            if ok > 0:
                print(f"  [SUCCESS] Metafield custom.similar_products updated on Shopify ({shopify_gid}).")
                synced_count += 1

                # Update Baserow FBT subcategory link_row (field_7408) if candidate has subcategories
                fbt_sub_ids = []
                for cand, _ in recs:
                    sub_links = cand.get("field_7364") or cand.get("sub_category")
                    if isinstance(sub_links, list):
                        for sub_item in sub_links:
                            if isinstance(sub_item, dict) and "id" in sub_item:
                                fbt_sub_ids.append(sub_item["id"])

                if fbt_sub_ids:
                    try:
                        baserow.update_row(
                            settings.products_table_id,
                            pid,
                            {"field_7408": list(set(fbt_sub_ids))}
                        )
                    except Exception as e_row:
                        print(f"  [NOTICE] Baserow row update warning: {e_row}")
            else:
                print(f"  [FAILED] Shopify metafield error: {errs}")
                failed_count += 1
        except Exception as e:
            print(f"  [ERROR] Shopify sync failed for {shopify_gid}: {e}")
            failed_count += 1

        # Rate limit pause
        time.sleep(0.2)

    print(f"\n==================================================")
    print(f"RECOMMENDATION SYNC COMPLETE ({args.target.upper()})")
    print(f"  Total Processed : {len(eligible_products)}")
    print(f"  Successfully Synced: {synced_count}")
    print(f"  Failed / Skipped  : {failed_count}")
    print(f"==================================================")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
