"""Classify all Shopify draft products by image type (live fetch + scan).

Report format matches shopify_before_june10_report.backup_*.json:
  shop, status, created_after, created_before, scanned_products, counts,
  elapsed_seconds, lifestyle, white, no_bg, unknown, no_images

Examples:
  python shopify_classify_draft.py
  python shopify_classify_draft.py --workers 40 --report output/shopify_draft_products_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config
from shopify_draft_bg_products import (
    ProductResult,
    _print_categories,
    _product_sort_key,
    _result_to_dict,
    scan_products,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify all draft Shopify products (live API, backup report format)."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("SHOPIFY_DRAFT_WORKERS", "20")),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("HTTP_TIMEOUT", "20")),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "shopify_draft_products_report.json",
    )
    args = parser.parse_args()

    config = load_shopify_config()
    client = ShopifyClient(config)

    print(f"Shop: {config.shop_host}")
    print("Status filter: draft")
    print(f"Workers: {args.workers}")
    print()

    t0 = time.perf_counter()
    print("Fetching draft products from Shopify...")
    products = client.iter_products(
        status="draft",
        fields="id,title,status,images,created_at",
    )
    print(f"  {len(products)} draft products loaded")
    if not products:
        print("Nothing to classify.")
        return 0

    print("Classifying images: lifestyle / white / no_bg...")
    results = scan_products(products, workers=args.workers, timeout=args.timeout)

    grouped: dict[str, list[ProductResult]] = defaultdict(list)
    for r in results.values():
        grouped[r.category].append(r)
    for key in grouped:
        grouped[key] = sorted(grouped[key], key=_product_sort_key)

    lifestyle = grouped.get("lifestyle", [])
    white = grouped.get("white", [])
    no_bg = grouped.get("no_bg", [])
    unknown = grouped.get("unknown", [])
    no_images = grouped.get("no_images", [])

    print()
    print(
        "Summary: "
        f"{len(lifestyle)} lifestyle, "
        f"{len(white)} white, "
        f"{len(no_bg)} no_bg, "
        f"{len(unknown)} unknown, "
        f"{len(no_images)} no_images"
    )
    print()
    _print_categories(grouped)

    report = {
        "shop": config.shop_host,
        "status": "draft",
        "created_after": None,
        "created_before": None,
        "scanned_products": len(products),
        "counts": {
            "lifestyle": len(lifestyle),
            "white": len(white),
            "no_bg": len(no_bg),
            "unknown": len(unknown),
            "no_images": len(no_images),
        },
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "lifestyle": [_result_to_dict(r) for r in lifestyle],
        "white": [_result_to_dict(r) for r in white],
        "no_bg": [_result_to_dict(r) for r in no_bg],
        "unknown": [_result_to_dict(r) for r in unknown],
        "no_images": [_result_to_dict(r) for r in no_images],
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {args.report}")
    print(f"Done in {report['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
