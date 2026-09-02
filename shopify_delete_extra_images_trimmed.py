"""Delete Shopify images beyond position 2 for report-trimmed white products.

Products where white_urls were reduced (3/4/5 -> 2 in the classify report) should
only keep live Shopify images at position 1 and 2.


  python shopify_delete_extra_images_trimmed.py --dry-run
  python shopify_delete_extra_images_trimmed.py --apply --checkpoint output/trimmed_extra_delete_checkpoint.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config

DEFAULT_REPORT = Path("output") / "shopify_before_june10_report.json"
DEFAULT_ORIGINAL = Path("output") / "shopify_before_june10_report.backup_20260626_114915.json"
DEFAULT_CHECKPOINT = Path("output") / "trimmed_extra_delete_checkpoint.json"
DEFAULT_REPORT_OUT = Path("output") / "shopify_delete_extra_trimmed_report.json"


@dataclass
class DeleteAction:
    product_id: int
    title: str
    shopify_image_id: int
    position: int
    src: str
    done: bool = False
    error: str = ""


def _load_checkpoint(path: Path) -> set[int]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {int(x) for x in (data.get("done_product_ids") or []) if x}


def _save_checkpoint(path: Path, done_product_ids: set[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "done_product_ids": sorted(done_product_ids),
                "total_done": len(done_product_ids),
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _white_rows_by_id(report: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in report.get("white") or []:
        pid = int(row.get("id") or 0)
        if pid:
            out[pid] = row
    return out


def find_trimmed_product_ids(original: dict, current: dict) -> list[dict]:
    """Products whose white_urls were shortened to 2 (had 3+ before)."""
    orig = _white_rows_by_id(original)
    cur = _white_rows_by_id(current)
    trimmed: list[dict] = []
    for pid, old_row in orig.items():
        new_row = cur.get(pid)
        if not new_row:
            continue
        old_n = len(old_row.get("white_urls") or [])
        new_n = len(new_row.get("white_urls") or [])
        if old_n >= 3 and new_n == 2 and old_n > new_n:
            trimmed.append(
                {
                    "id": pid,
                    "title": new_row.get("title") or old_row.get("title"),
                    "old_urls": old_n,
                    "new_urls": new_n,
                }
            )
    trimmed.sort(key=lambda r: str(r.get("title") or "").lower())
    return trimmed


def images_to_delete(product: dict, *, keep_positions: int = 2) -> list[dict]:
    images = list(product.get("images") or [])
    out = []
    for img in images:
        pos = int(img.get("position") or 0)
        if pos > keep_positions:
            out.append(img)
    out.sort(key=lambda i: int(i.get("position") or 0), reverse=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete Shopify images after position 2 for trimmed white products."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--original-report",
        type=Path,
        default=DEFAULT_ORIGINAL,
        help="Report backup from before URL trimming.",
    )
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--product-limit", type=int, default=0)
    parser.add_argument("--keep-positions", type=int, default=2)
    parser.add_argument(
        "--report-out",
        type=Path,
        default=DEFAULT_REPORT_OUT,
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if not args.report.exists():
        print(f"ERROR: report not found: {args.report}")
        return 1
    if not args.original_report.exists():
        print(f"ERROR: original backup not found: {args.original_report}")
        return 1

    original = json.loads(args.original_report.read_text(encoding="utf-8"))
    current = json.loads(args.report.read_text(encoding="utf-8"))
    trimmed = find_trimmed_product_ids(original, current)
    done_products = _load_checkpoint(args.checkpoint) if args.checkpoint else set()

    if args.product_id:
        trimmed = [t for t in trimmed if int(t["id"]) == args.product_id]
        if not trimmed:
            print(f"Product {args.product_id} is not in the trimmed set.")
            return 1

    trimmed = [t for t in trimmed if int(t["id"]) not in done_products]
    if args.product_limit > 0:
        trimmed = trimmed[: args.product_limit]

    client = ShopifyClient(load_shopify_config())
    print(f"Shop: {client.config.shop_host}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Trimmed products (3+ -> 2 urls in report): {len(trimmed) + len(done_products)} total")
    print(f"Already deleted extras (checkpoint): {len(done_products)}")
    print(f"Remaining this run: {len(trimmed)}")
    print(f"Rule: keep Shopify position 1-{args.keep_positions}, delete higher")
    print()

    if not trimmed:
        print("Nothing to do.")
        return 0

    all_actions: list[DeleteAction] = []
    products_ok = 0
    products_fail = 0
    done_ids = set(done_products)

    for i, row in enumerate(trimmed, 1):
        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        print(f"[{i}/{len(trimmed)}] {title} (id={pid}, was {row['old_urls']} urls)...", flush=True)

        try:
            product = client.get_product(pid)
        except Exception as exc:  # noqa: BLE001
            products_fail += 1
            print(f"  ERROR fetch: {exc}", flush=True)
            continue

        to_delete = images_to_delete(product, keep_positions=args.keep_positions)
        if not to_delete:
            print("  skip: already <= 2 images on Shopify", flush=True)
            products_ok += 1
            done_ids.add(pid)
            if not dry_run and args.checkpoint:
                _save_checkpoint(args.checkpoint, done_ids)
            continue

        jobs: list[DeleteAction] = []
        for img in to_delete:
            jobs.append(
                DeleteAction(
                    product_id=pid,
                    title=title,
                    shopify_image_id=int(img["id"]),
                    position=int(img.get("position") or 0),
                    src=str(img.get("src") or ""),
                )
            )

        for job in jobs:
            print(f"  delete pos={job.position} image_id={job.shopify_image_id}", flush=True)
            if dry_run:
                job.done = True
            else:
                try:
                    client.delete_product_image(job.product_id, job.shopify_image_id)
                    job.done = True
                except Exception as exc:  # noqa: BLE001
                    job.error = str(exc)
                    print(f"    ERROR: {exc}", flush=True)

        all_actions.extend(jobs)
        product_ok = all(j.done for j in jobs)
        if product_ok:
            products_ok += 1
            done_ids.add(pid)
            if not dry_run and args.checkpoint:
                _save_checkpoint(args.checkpoint, done_ids)
            print(f"  OK deleted {len(jobs)} image(s)", flush=True)
        else:
            products_fail += 1
            print("  PARTIAL FAIL — not checkpointed", flush=True)

    ok = sum(1 for a in all_actions if a.done)
    fail = sum(1 for a in all_actions if a.error)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(
        json.dumps(
            {
                "products_ok": products_ok,
                "products_fail": products_fail,
                "images_deleted": ok,
                "images_failed": fail,
                "actions": [
                    {
                        "product_id": a.product_id,
                        "title": a.title,
                        "position": a.position,
                        "shopify_image_id": a.shopify_image_id,
                        "src": a.src,
                        "done": a.done,
                        "error": a.error,
                    }
                    for a in all_actions
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Products OK: {products_ok}, failed: {products_fail}")
    print(f"Images deleted: {ok}, failed: {fail}")
    print(f"Report: {args.report_out}")
    if args.checkpoint:
        print(f"Checkpoint: {args.checkpoint} ({len(done_ids)} products)")
    if dry_run:
        print()
        print("Dry run. Add --apply to delete on Shopify.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
