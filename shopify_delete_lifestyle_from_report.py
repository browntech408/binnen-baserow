"""Delete lifestyle_urls from Shopify using a saved classify report (no re-scan).

Updates the source report: cleared lifestyle_urls, recategorized products.

Examples:
  python shopify_delete_lifestyle_from_report.py --dry-run --report output/shopify_before_june10_report.json
  python shopify_delete_lifestyle_from_report.py --apply --report output/shopify_before_june10_report.json --product-limit 50
  python shopify_delete_lifestyle_from_report.py --apply --report output/shopify_before_june10_report.json --checkpoint output/lifestyle_delete_checkpoint.json
  python shopify_delete_lifestyle_from_report.py --dry-run --report output/shopify_before_june10_report.json --mixed-only
  python shopify_delete_lifestyle_from_report.py --apply --report output/shopify_before_june10_report.json --mixed-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config
from shopify_process_white_nobg_from_report import _map_shopify_images, _url_key
from shopify_recategorize_report import SECTIONS, recategorize_report


@dataclass
class DeleteJob:
    product_id: int
    product_title: str
    shopify_image_id: int
    source_url: str
    deleted: bool = False
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
        json.dumps({"done_product_ids": sorted(done_product_ids)}, indent=2),
        encoding="utf-8",
    )


def _is_mixed_lifestyle_row(row: dict) -> bool:
    """Has lifestyle images AND at least one white or no_bg image."""
    has_ls = bool(row.get("lifestyle_urls"))
    has_other = bool(row.get("white_urls") or row.get("no_bg_urls"))
    return has_ls and has_other


def _is_lifestyle_only_row(row: dict) -> bool:
    has_ls = bool(row.get("lifestyle_urls"))
    has_other = bool(row.get("white_urls") or row.get("no_bg_urls") or row.get("unknown_urls"))
    return has_ls and not has_other


def _rows_with_lifestyle(report: dict, *, mixed_only: bool = False) -> list[dict]:
    rows = list(report.get("products") or [])
    if not rows:
        for key in ("lifestyle", "white", "no_bg", "unknown", "no_images"):
            rows.extend(report.get(key) or [])
    seen: set[int] = set()
    out: list[dict] = []
    for row in rows:
        pid = int(row.get("id") or 0)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        if not row.get("lifestyle_urls"):
            continue
        if mixed_only:
            if not _is_mixed_lifestyle_row(row):
                continue
        out.append(row)
    return out


def _build_jobs(client: ShopifyClient, rows: list[dict]) -> tuple[list[DeleteJob], int]:
    jobs: list[DeleteJob] = []
    missing = 0
    for row in rows:
        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        product = client.get_product(pid)
        img_map = _map_shopify_images(product)
        for url in row.get("lifestyle_urls") or []:
            img = img_map.get(_url_key(url))
            if not img:
                missing += 1
                continue
            jobs.append(
                DeleteJob(
                    product_id=pid,
                    product_title=title,
                    shopify_image_id=int(img["id"]),
                    source_url=str(url),
                )
            )
    return jobs, missing


def _all_rows_by_id(report: dict) -> dict[int, list[dict]]:
    """Same product id may appear in products + category sections — update all copies."""
    refs: dict[int, list[dict]] = {}
    for key in ("products", *SECTIONS):
        for row in report.get(key) or []:
            pid = int(row.get("id") or 0)
            if not pid:
                continue
            refs.setdefault(pid, []).append(row)
    return refs


def _apply_report_updates(report: dict, jobs: list[DeleteJob]) -> tuple[dict, list[dict]]:
    deleted_keys: dict[int, set[str]] = {}
    for job in jobs:
        if not job.deleted:
            continue
        deleted_keys.setdefault(job.product_id, set()).add(_url_key(job.source_url))

    for pid, keys in deleted_keys.items():
        for row in _all_rows_by_id(report).get(pid, []):
            before = list(row.get("lifestyle_urls") or [])
            kept = [u for u in before if _url_key(u) not in keys]
            removed = [u for u in before if _url_key(u) in keys]
            row["lifestyle_urls"] = kept

    report, moved = recategorize_report(report)
    report["lifestyle_delete"] = {
        "deleted_images": sum(1 for j in jobs if j.deleted),
        "failed_images": sum(1 for j in jobs if j.error),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return report, moved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete report lifestyle_urls on Shopify and update the report JSON."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Classify report JSON (updated in place on --apply).",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Skip product IDs already fully processed (resume large runs).",
    )
    parser.add_argument(
        "--product-limit",
        type=int,
        default=0,
        help="Max products with lifestyle_urls to process (0 = all).",
    )
    parser.add_argument(
        "--mixed-only",
        action="store_true",
        help="Only products with lifestyle + white/no_bg images (skip lifestyle-only).",
    )
    parser.add_argument(
        "--run-report",
        type=Path,
        default=Path("output") / "shopify_delete_lifestyle_report.json",
        help="Execution log for this run.",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if not args.report.is_file():
        print(f"ERROR: report not found: {args.report}")
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = _rows_with_lifestyle(report, mixed_only=args.mixed_only)
    done_products = _load_checkpoint(args.checkpoint) if args.checkpoint else set()
    if done_products:
        rows = [r for r in rows if int(r["id"]) not in done_products]
    if args.product_limit > 0:
        rows = rows[: args.product_limit]

    lifestyle_url_count = sum(len(r.get("lifestyle_urls") or []) for r in rows)
    print(f"Report: {args.report}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    if args.mixed_only:
        print("Filter: mixed only (lifestyle + white/no_bg; lifestyle-only skipped)")
    print(f"Products with lifestyle_urls: {len(rows)}")
    print(f"Lifestyle URLs in report: {lifestyle_url_count}")
    if args.checkpoint:
        print(f"Checkpoint skip: {len(done_products)} products")
    print()

    if not rows:
        print("Nothing to delete.")
        return 0

    client = ShopifyClient(load_shopify_config())
    t0 = time.perf_counter()
    jobs, missing = _build_jobs(client, rows)

    print(f"Delete jobs (matched on Shopify): {len(jobs)}")
    if missing:
        print(f"WARN: {missing} lifestyle URLs not found on live Shopify (skipped)")
    print()

    for job in jobs[:30]:
        print(f"  delete {job.product_title} (product={job.product_id}, image={job.shopify_image_id})")
    if len(jobs) > 30:
        print(f"  ... and {len(jobs) - 30} more")

    if dry_run:
        print()
        print("Dry run. Add --apply to delete on Shopify and update the report.")
        return 0

    if not jobs:
        print("No matched images to delete.")
        return 0

    print()
    print("Deleting lifestyle images on Shopify...")
    for i, job in enumerate(jobs, 1):
        try:
            print(
                f"  [{i}/{len(jobs)}] {job.product_title} image_id={job.shopify_image_id}",
                flush=True,
            )
            client.delete_product_image(job.product_id, job.shopify_image_id)
            job.deleted = True
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
            print(f"    ERROR: {exc}")

    print()
    print("Updating report...")
    report, moved = _apply_report_updates(report, jobs)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated report: {args.report}")
    print(f"Recategorized products: {len(moved)}")
    print("New counts: " + ", ".join(f"{k}={v}" for k, v in sorted((report.get("counts") or {}).items())))

    if args.checkpoint:
        from collections import defaultdict

        by_product: dict[int, list[DeleteJob]] = defaultdict(list)
        for job in jobs:
            by_product[job.product_id].append(job)
        fully_done = {
            pid for pid, product_jobs in by_product.items() if all(j.deleted for j in product_jobs)
        }
        done_products |= fully_done
        _save_checkpoint(args.checkpoint, done_products)
        print(f"Checkpoint: {args.checkpoint} ({len(done_products)} products fully done)")

    ok = sum(1 for j in jobs if j.deleted)
    fail = sum(1 for j in jobs if j.error)
    args.run_report.parent.mkdir(parents=True, exist_ok=True)
    args.run_report.write_text(
        json.dumps(
            {
                "source_report": str(args.report),
                "products_processed": len(rows),
                "delete_jobs": len(jobs),
                "deleted": ok,
                "failed": fail,
                "missing_on_shopify": missing,
                "recategorized": len(moved),
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
                "jobs": [
                    {
                        "product_id": j.product_id,
                        "title": j.product_title,
                        "shopify_image_id": j.shopify_image_id,
                        "source_url": j.source_url,
                        "deleted": j.deleted,
                        "error": j.error,
                    }
                    for j in jobs
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Run log: {args.run_report}")
    print(f"Done: {ok} deleted, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
