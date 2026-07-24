"""Draft Shopify products from a saved shopify_image_categories_report.json.

No image re-scan — reads lifestyle/white/no_bg lists from the report file.

Examples:
  python shopify_draft_from_report.py --dry-run
  python shopify_draft_from_report.py --apply
  python shopify_draft_from_report.py --apply --category lifestyle
  python shopify_draft_from_report.py --dry-run --report output/shopify_before_june10_report.json --category lifestyle
  python shopify_draft_from_report.py --apply --report output/shopify_before_june10_report.json --category lifestyle
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config

VALID_CATEGORIES = ("lifestyle", "white", "no_bg", "unknown", "no_images", "all")
DEFAULT_REPORT = Path("output") / "shopify_image_categories_report.json"


@dataclass
class DraftItem:
    product_id: int
    title: str
    category: str
    drafted: bool = False
    error: str = ""


def load_report(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Report not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _is_lifestyle_only_row(row: dict) -> bool:
    """True when product has lifestyle images only (no white / no_bg / unknown)."""
    ls = len(row.get("lifestyle_urls") or [])
    wh = len(row.get("white_urls") or [])
    nb = len(row.get("no_bg_urls") or [])
    un = len(row.get("unknown_urls") or [])
    return ls > 0 and wh == 0 and nb == 0 and un == 0


def _product_rows(report: dict) -> list[dict]:
    return list(report.get("products") or [])


def _rows_for_category(report: dict, category: str) -> list[dict]:
    section = report.get(category) or []
    if section:
        return list(section)
    products = _product_rows(report)
    if not products:
        return []
    if category == "lifestyle":
        return [r for r in products if _is_lifestyle_only_row(r)]
    if category == "no_images":
        return [
            r
            for r in products
            if not (
                (r.get("lifestyle_urls") or [])
                or (r.get("white_urls") or [])
                or (r.get("no_bg_urls") or [])
                or (r.get("unknown_urls") or [])
            )
        ]
    return [r for r in products if str(r.get("category") or "") == category]


def items_from_report(report: dict, category: str) -> list[DraftItem]:
    if category == "all":
        keys = [k for k in VALID_CATEGORIES if k != "all"]
        seen: set[int] = set()
        out: list[DraftItem] = []
        for key in keys:
            for row in _rows_for_category(report, key):
                pid = int(row["id"])
                if pid in seen:
                    continue
                seen.add(pid)
                out.append(
                    DraftItem(
                        product_id=pid,
                        title=str(row.get("title") or "").strip(),
                        category=str(row.get("category") or key),
                    )
                )
        out.sort(key=lambda x: x.title.lower())
        return out

    rows = _rows_for_category(report, category)
    return [
        DraftItem(
            product_id=int(row["id"]),
            title=str(row.get("title") or "").strip(),
            category=str(row.get("category") or category),
        )
        for row in rows
    ]


def draft_products(
    client: ShopifyClient,
    items: list[DraftItem],
    *,
    workers: int,
) -> None:
    def _draft_one(item: DraftItem) -> DraftItem:
        try:
            client.set_product_status(item.product_id, "draft")
            item.drafted = True
        except Exception as exc:  # noqa: BLE001
            item.error = str(exc)
        return item

    with ThreadPoolExecutor(max_workers=min(workers, 4)) as pool:
        list(pool.map(_draft_one, items))


def _update_report_drafted(report_path: Path, items: list[DraftItem]) -> None:
    report = load_report(report_path)
    drafted_ids = {i.product_id for i in items if i.drafted}
    errors = {i.product_id: i.error for i in items if i.error}

    for key in VALID_CATEGORIES:
        if key == "all":
            continue
        section = report.get(key)
        if not isinstance(section, list):
            continue
        for row in section:
            pid = int(row.get("id") or 0)
            if pid in drafted_ids:
                row["drafted"] = True
                row["error"] = ""
            elif pid in errors:
                row["error"] = errors[pid]

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Draft Shopify products using a saved image categories report (fast, no re-scan)."
    )
    parser.add_argument("--apply", action="store_true", help="Set products to draft.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default).")
    parser.add_argument(
        "--category",
        default="lifestyle",
        choices=VALID_CATEGORIES,
        help="Report section to draft. lifestyle = only lifestyle images (no white/no_bg).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"JSON report path (default: {DEFAULT_REPORT}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max products to draft (0 = all in section).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("SHOPIFY_DRAFT_WORKERS", "4")),
        help="Parallel Shopify API workers (default: 4).",
    )
    parser.add_argument(
        "--update-report",
        action="store_true",
        default=True,
        help="Mark drafted=true in report JSON after --apply (default: on).",
    )
    parser.add_argument(
        "--no-update-report",
        action="store_true",
        help="Do not modify the report file after apply.",
    )
    args = parser.parse_args()
    dry_run = not args.apply
    update_report = args.update_report and not args.no_update_report

    try:
        report = load_report(args.report)
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}")
        return 1

    shop = report.get("shop", "?")
    counts = report.get("counts") or {}
    items = items_from_report(report, args.category)
    if args.limit > 0:
        items = items[: args.limit]

    print(f"Report: {args.report}")
    print(f"Shop (from report): {shop}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY -> draft'}")
    print(f"Category: {args.category}")
    if counts:
        print(f"Report counts: {counts}")
    print(f"Products to draft: {len(items)}")
    print()

    for item in items[:30]:
        print(f"  [draft] {item.title} (id={item.product_id})")
    if len(items) > 30:
        print(f"  ... and {len(items) - 30} more")

    if dry_run:
        print()
        print("Dry run. Add --apply to draft these products on Shopify.")
        return 0

    if not items:
        print("Nothing to draft.")
        return 0

    config = load_shopify_config()
    client = ShopifyClient(config)
    print(f"Live shop: {config.shop_host}")
    if shop and config.shop_host not in str(shop) and str(shop) not in config.shop_host:
        print(f"WARNING: report shop ({shop}) != .env shop ({config.shop_host})")

    t0 = time.perf_counter()
    print()
    print(f"Setting {len(items)} products to draft...")
    draft_products(client, items, workers=args.workers)

    ok = sum(1 for i in items if i.drafted)
    fail = sum(1 for i in items if i.error)
    print(f"  drafted: {ok}, errors: {fail}")
    for item in items:
        if item.error:
            print(f"  ERROR id={item.product_id}: {item.error}")

    if update_report:
        _update_report_drafted(args.report, items)
        print(f"Updated report: {args.report}")

    print(f"Done in {time.perf_counter() - t0:.1f}s")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
