"""Activate Shopify products listed in shopify_image_categories_report.json.

Mirrors shopify_draft_from_report.py — fast, no image re-scan.

Examples:
  python shopify_activate_from_report.py --dry-run
  python shopify_activate_from_report.py --apply
  python shopify_activate_from_report.py --apply --category lifestyle
  python shopify_activate_from_report.py --apply --only-drafted
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config
from shopify_draft_from_report import (
    DEFAULT_REPORT,
    VALID_CATEGORIES,
    items_from_report,
    load_report,
)

@dataclass
class ActivateItem:
    product_id: int
    title: str
    category: str
    activated: bool = False
    error: str = ""


def _items_from_report_filtered(
    report: dict,
    category: str,
    *,
    only_drafted: bool,
) -> list[ActivateItem]:
    if only_drafted:
        rows = report.get(category) or []
        if category == "all":
            rows = []
            for key in VALID_CATEGORIES:
                if key != "all":
                    rows.extend(report.get(key) or [])
        return [
            ActivateItem(
                product_id=int(row["id"]),
                title=str(row.get("title") or "").strip(),
                category=str(row.get("category") or category),
            )
            for row in rows
            if row.get("drafted") is True
        ]

    return [
        ActivateItem(
            product_id=i.product_id,
            title=i.title,
            category=i.category,
        )
        for i in items_from_report(report, category)
    ]


def activate_products(
    client: ShopifyClient,
    items: list[ActivateItem],
    *,
    workers: int,
) -> None:
    def _activate_one(item: ActivateItem) -> ActivateItem:
        try:
            client.set_product_status(item.product_id, "active")
            item.activated = True
        except Exception as exc:  # noqa: BLE001
            item.error = str(exc)
        return item

    with ThreadPoolExecutor(max_workers=min(workers, 4)) as pool:
        list(pool.map(_activate_one, items))


def _update_report_active(report_path: Path, items: list[ActivateItem]) -> None:
    report = load_report(report_path)
    activated_ids = {i.product_id for i in items if i.activated}
    errors = {i.product_id: i.error for i in items if i.error}

    for key in VALID_CATEGORIES:
        if key == "all":
            continue
        section = report.get(key)
        if not isinstance(section, list):
            continue
        for row in section:
            pid = int(row.get("id") or 0)
            if pid in activated_ids:
                row["drafted"] = False
                row["error"] = ""
            elif pid in errors:
                row["error"] = errors[pid]

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Activate products from saved image categories report (fast)."
    )
    parser.add_argument("--apply", action="store_true", help="Set products to active.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default).")
    parser.add_argument(
        "--category",
        default="lifestyle",
        choices=VALID_CATEGORIES,
        help="Report section (default: lifestyle).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"JSON report path (default: {DEFAULT_REPORT}).",
    )
    parser.add_argument(
        "--only-drafted",
        action="store_true",
        help="Only rows with drafted=true in the report.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max products (0 = all).")
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("SHOPIFY_DRAFT_WORKERS", "4")),
    )
    parser.add_argument(
        "--no-update-report",
        action="store_true",
        help="Do not set drafted=false in report after apply.",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    try:
        report = load_report(args.report)
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}")
        return 1

    items = _items_from_report_filtered(
        report, args.category, only_drafted=args.only_drafted
    )
    if args.limit > 0:
        items = items[: args.limit]

    print(f"Report: {args.report}")
    print(f"Shop (from report): {report.get('shop', '?')}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY -> active'}")
    print(f"Category: {args.category}")
    if args.only_drafted:
        print("Filter: only drafted=true in report")
    print(f"Products to activate: {len(items)}")
    print()

    for item in items[:30]:
        print(f"  [active] {item.title} (id={item.product_id})")
    if len(items) > 30:
        print(f"  ... and {len(items) - 30} more")

    if dry_run:
        print()
        print("Dry run. Add --apply to activate on Shopify.")
        return 0

    if not items:
        print("Nothing to activate.")
        return 0

    client = ShopifyClient(load_shopify_config())
    t0 = time.perf_counter()
    print()
    print(f"Setting {len(items)} products to active...")
    activate_products(client, items, workers=args.workers)

    ok = sum(1 for i in items if i.activated)
    fail = sum(1 for i in items if i.error)
    print(f"  activated: {ok}, errors: {fail}")
    for item in items:
        if item.error:
            print(f"  ERROR id={item.product_id}: {item.error}")

    if not args.no_update_report:
        _update_report_active(args.report, items)
        print(f"Updated report (drafted=false): {args.report}")

    print(f"Done in {time.perf_counter() - t0:.1f}s")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
