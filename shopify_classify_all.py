"""Classify all Shopify products by image category and include product status.

Examples:
  python shopify_classify_all.py
  python shopify_classify_all.py --status active
  python shopify_classify_all.py --workers 40 --report output/shopify_all_products_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config
from shopify_draft_bg_products import (
    ProductResult,
    _print_categories,
    _product_sort_key,
    scan_products,
)


def _result_to_dict(r: ProductResult, *, meta: dict[int, dict]) -> dict:
    m = meta.get(r.product_id, {})
    return {
        "id": r.product_id,
        "title": r.title,
        "status": str(m.get("status") or "").strip(),
        "created_at": str(m.get("created_at") or "").strip(),
        "category": r.category,
        "white_urls": r.white_urls,
        "no_bg_urls": r.no_bg_urls,
        "lifestyle_urls": r.lifestyle_urls,
        "unknown_urls": r.unknown_urls,
        "error": r.error,
    }


def _status_counts(rows: list[dict]) -> dict[str, int]:
    return dict(Counter(str(r.get("status") or "unknown") for r in rows))


def _normalize_shopify_status(raw: str) -> str:
    """Map CLI status to Shopify API param ('' = all statuses)."""
    value = str(raw or "").strip().lower()
    if value in ("", "all", "any", "*"):
        return ""
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify all store products (category + Shopify status)."
    )
    parser.add_argument(
        "--status",
        default="",
        help='Shopify status: active, draft, archived, or all for every status (default: all).',
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
        default=Path("output") / "shopify_all_products_report.json",
    )
    args = parser.parse_args()
    args.status = _normalize_shopify_status(args.status)

    config = load_shopify_config()
    client = ShopifyClient(config)

    label = args.status or "all"
    print(f"Shop: {config.shop_host}")
    print(f"Status filter: {label}")
    print(f"Workers: {args.workers}")
    print()

    t0 = time.perf_counter()
    print(f"Fetching {label} products...")
    products = client.iter_products(
        status=args.status,
        fields="id,title,status,images,created_at",
    )
    print(f"  {len(products)} products loaded")
    if not products:
        print("Nothing to classify.")
        return 0

    meta = {int(p["id"]): p for p in products}

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

    all_rows = [
        _result_to_dict(r, meta=meta)
        for r in sorted(results.values(), key=_product_sort_key)
    ]

    print()
    print(
        "Summary: "
        f"{len(lifestyle)} lifestyle, "
        f"{len(white)} white, "
        f"{len(no_bg)} no_bg, "
        f"{len(unknown)} unknown, "
        f"{len(no_images)} no_images"
    )
    print("Status totals: " + ", ".join(f"{k}={v}" for k, v in sorted(_status_counts(all_rows).items())))
    print()
    _print_categories(grouped)

    by_category = {
        "lifestyle": [_result_to_dict(r, meta=meta) for r in lifestyle],
        "white": [_result_to_dict(r, meta=meta) for r in white],
        "no_bg": [_result_to_dict(r, meta=meta) for r in no_bg],
        "unknown": [_result_to_dict(r, meta=meta) for r in unknown],
        "no_images": [_result_to_dict(r, meta=meta) for r in no_images],
    }

    report = {
        "shop": config.shop_host,
        "status_filter": label,
        "scanned_products": len(products),
        "counts": {
            "lifestyle": len(lifestyle),
            "white": len(white),
            "no_bg": len(no_bg),
            "unknown": len(unknown),
            "no_images": len(no_images),
        },
        "status_counts": _status_counts(all_rows),
        "status_by_category": {
            cat: _status_counts(rows) for cat, rows in by_category.items()
        },
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "products": all_rows,
        "lifestyle": by_category["lifestyle"],
        "white": by_category["white"],
        "no_bg": by_category["no_bg"],
        "unknown": by_category["unknown"],
        "no_images": by_category["no_images"],
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {args.report}")
    print(f"Done in {report['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
