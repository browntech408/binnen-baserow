"""Classify Shopify products by image type, filtered by created_at date.

Uses the same image rules as shopify_draft_bg_products.py (lifestyle / white / no_bg).

Examples:
  python shopify_classify_since.py --since 2026-06-10
  python shopify_classify_since.py --since 2026-07-01 --status all --report output/shopify_since_july1_2026_report.json
  python shopify_classify_since.py --before 2026-06-10 --status all --report output/shopify_before_june10_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config
from shopify_draft_bg_products import (
    ProductResult,
    _print_categories,
    _product_sort_key,
    _result_to_dict,
    scan_products,
)


def _parse_created_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _filter_since(products: list[dict], since: date) -> list[dict]:
    out: list[dict] = []
    for product in products:
        raw = str(product.get("created_at") or "").strip()
        if not raw:
            continue
        if _parse_created_at(raw).date() > since:
            out.append(product)
    return out


def _filter_before(products: list[dict], before: date) -> list[dict]:
    """Products strictly before `before` (created_at date < before)."""
    out: list[dict] = []
    for product in products:
        raw = str(product.get("created_at") or "").strip()
        if not raw:
            continue
        if _parse_created_at(raw).date() < before:
            out.append(product)
    return out


def _normalize_shopify_status(raw: str) -> str:
    """Map CLI status to Shopify API param ('' = all statuses)."""
    value = str(raw or "").strip().lower()
    if value in ("", "all", "any", "*"):
        return ""
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify products filtered by Shopify created_at date."
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Include products created after this date (exclusive of same day logic: date > since).",
    )
    parser.add_argument(
        "--before",
        default=None,
        help="Include products created before this date (date < before). E.g. --before 2026-06-10",
    )
    parser.add_argument(
        "--status",
        default="active",
        help='Shopify status: active, draft, archived, or all for every status (default: active).',
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
        default=None,
        help="JSON report path (default depends on --since / --before).",
    )
    args = parser.parse_args()
    args.status = _normalize_shopify_status(args.status)

    if args.since and args.before:
        print("ERROR: use only one of --since or --before.")
        return 1
    if not args.since and not args.before:
        args.since = "2026-06-10"

    if args.report is None:
        if args.before:
            args.report = Path("output") / "shopify_before_june10_report.json"
        else:
            args.report = Path("output") / "shopify_new_since_june10_report.json"

    since: date | None = date.fromisoformat(args.since) if args.since else None
    before: date | None = date.fromisoformat(args.before) if args.before else None
    config = load_shopify_config()
    client = ShopifyClient(config)

    status_label = args.status or "all"
    print(f"Shop: {config.shop_host}")
    print(f"Status: {status_label}")
    if before:
        print(f"Created before: {before.isoformat()}")
    else:
        print(f"Created after: {since.isoformat()}")
    print(f"Workers: {args.workers}")
    print()

    t0 = time.perf_counter()
    print(f"Fetching {status_label} products...")
    products = client.iter_products(
        status=args.status,
        fields="id,title,status,images,created_at",
    )
    print(f"  {len(products)} products loaded")

    if before:
        filtered = _filter_before(products, before)
        print(f"  {len(filtered)} created before {before.isoformat()}")
    else:
        filtered = _filter_since(products, since)  # type: ignore[arg-type]
        print(f"  {len(filtered)} created after {since.isoformat()}")
    if not filtered:
        print("Nothing to classify.")
        return 0

    print("Classifying images: lifestyle / white / no_bg...")
    results = scan_products(filtered, workers=args.workers, timeout=args.timeout)

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

    meta = {int(p["id"]): p for p in filtered}
    all_rows = [
        {
            "id": r.product_id,
            "title": r.title,
            "status": str(meta.get(r.product_id, {}).get("status") or "").strip(),
            "created_at": str(meta.get(r.product_id, {}).get("created_at") or "").strip(),
            "category": r.category,
            "white_urls": r.white_urls,
            "no_bg_urls": r.no_bg_urls,
            "lifestyle_urls": r.lifestyle_urls,
            "unknown_urls": r.unknown_urls,
            "error": r.error,
        }
        for r in sorted(results.values(), key=_product_sort_key)
    ]

    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "shop": config.shop_host,
        "status": status_label,
        "created_after": since.isoformat() if since else None,
        "created_before": before.isoformat() if before else None,
        "scanned_products": len(filtered),
        "counts": {
            "lifestyle": len(lifestyle),
            "white": len(white),
            "no_bg": len(no_bg),
            "unknown": len(unknown),
            "no_images": len(no_images),
        },
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "products": all_rows,
        "lifestyle": [_result_to_dict(r) for r in lifestyle],
        "white": [_result_to_dict(r) for r in white],
        "no_bg": [_result_to_dict(r) for r in no_bg],
        "unknown": [_result_to_dict(r) for r in unknown],
        "no_images": [_result_to_dict(r) for r in no_images],
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {args.report}")
    print(f"Done in {report['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
