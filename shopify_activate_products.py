"""Set Shopify products back to active (e.g. after draft testing).

Examples:
  python shopify_activate_products.py --dry-run
  python shopify_activate_products.py --apply
  python shopify_activate_products.py --apply --all
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


@dataclass
class ActivateResult:
    product_id: int
    title: str
    old_status: str
    activated: bool = False
    error: str = ""


def _collect_targets(
    client: ShopifyClient,
    *,
    all_products: bool,
) -> list[dict]:
    if all_products:
        products = client.iter_products(status="", fields="id,title,status")
        return [p for p in products if p.get("status") != "active"]

    return client.iter_products(status="draft", fields="id,title,status")


def activate_products(
    client: ShopifyClient,
    targets: list[dict],
    *,
    workers: int,
) -> list[ActivateResult]:
    results = [
        ActivateResult(
            product_id=int(p["id"]),
            title=str(p.get("title") or "").strip(),
            old_status=str(p.get("status") or "").strip(),
        )
        for p in targets
    ]

    def _activate(item: ActivateResult) -> ActivateResult:
        try:
            client.set_product_status(item.product_id, "active")
            item.activated = True
        except Exception as exc:  # noqa: BLE001
            item.error = str(exc)
        return item

    with ThreadPoolExecutor(max_workers=min(workers, 4)) as pool:
        return list(pool.map(_activate, results))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Activate Shopify products (draft/archived -> active)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually set products to active (default is dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Activate every non-active product (draft + archived). Default: draft only.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("SHOPIFY_DRAFT_WORKERS", "4")),
        help="Parallel API workers (default: 4).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "shopify_activate_report.json",
        help="Write JSON report here.",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    config = load_shopify_config()
    client = ShopifyClient(config)

    print(f"Shop: {config.shop_host}")
    scope = "all non-active" if args.all else "draft only"
    print(f"Scope: {scope}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print()

    t0 = time.perf_counter()
    targets = _collect_targets(client, all_products=args.all)
    targets.sort(key=lambda p: str(p.get("title") or "").lower())

    print(f"Products to activate: {len(targets)}")
    for p in targets:
        print(
            f"  [{p.get('status', '?')}] {p.get('title', '')} (id={p.get('id')})"
        )

    results: list[ActivateResult] = []
    if not dry_run and targets:
        print()
        print(f"Setting {len(targets)} products to active...")
        results = activate_products(client, targets, workers=args.workers)
        ok = sum(1 for r in results if r.activated)
        fail = sum(1 for r in results if r.error)
        print(f"  activated: {ok}, errors: {fail}")
        for item in results:
            if item.error:
                print(f"  ERROR id={item.product_id}: {item.error}")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "shop": config.shop_host,
        "dry_run": dry_run,
        "scope": scope,
        "count": len(targets),
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "products": [
            {
                "id": int(p["id"]),
                "title": p.get("title"),
                "old_status": p.get("status"),
                "activated": (
                    next(
                        (r.activated for r in results if r.product_id == int(p["id"])),
                        False,
                    )
                    if results
                    else False
                ),
            }
            for p in targets
        ],
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"Report: {args.report}")
    print(f"Done in {report['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
