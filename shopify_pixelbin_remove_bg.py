"""Remove Shopify product image backgrounds via Pixelbin erase.bg.

Replaces each processed image on Shopify (same image id, old file gone).

Fast path for large catalogs (5000+ products):
  1) Classify once (hours, parallel): shopify_classify_all.py → report JSON
  2) Pixelbin in batches from report (no re-classify):
     python shopify_pixelbin_remove_bg.py --from-report output/shopify_all_products_report.json --dry-run
     python shopify_pixelbin_remove_bg.py --apply --from-report output/shopify_all_products_report.json --product-limit 50 --checkpoint output/pixelbin_checkpoint.json

Examples:
  python shopify_pixelbin_remove_bg.py --dry-run --category lifestyle
  python shopify_pixelbin_remove_bg.py --apply --category lifestyle
  python shopify_pixelbin_remove_bg.py --apply --product-id 16139840422223
  python pixelbin_bg.py --test-url https://example.com/image.jpg
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pixelbin_bg import load_pixelbin_settings, remove_background_to_bytes
from shopify_client import ShopifyClient, load_shopify_config
from shopify_draft_bg_products import ProductResult, scan_products

VALID_CATEGORIES = ("lifestyle", "white", "no_bg", "all")


@dataclass
class ImageJob:
    product_id: int
    product_title: str
    category: str
    shopify_image_id: int
    image_index: int
    position: int
    source_url: str
    output_path: Path = field(default_factory=Path)
    output_url: str = ""
    credits: int = 0
    shopify_replaced: bool = False
    error: str = ""


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text.strip())[:80].strip("_") or "product"


def _image_jobs_for_product(
    product: dict,
    *,
    category: str,
    result: ProductResult | None,
) -> list[ImageJob]:
    pid = int(product["id"])
    title = str(product.get("title") or "").strip()
    cat = result.category if result else "unknown"
    if category != "all" and cat != category:
        return []

    jobs: list[ImageJob] = []
    for idx, img in enumerate(product.get("images") or [], 1):
        src = str(img.get("src") or "").strip()
        image_id = img.get("id")
        if not src or not image_id:
            continue
        jobs.append(
            ImageJob(
                product_id=pid,
                product_title=title,
                category=cat,
                shopify_image_id=int(image_id),
                image_index=idx,
                position=int(img.get("position") or idx),
                source_url=src,
            )
        )
    return jobs


def _collect_jobs(
    products: list[dict],
    results: dict[int, ProductResult],
    *,
    category: str,
) -> list[ImageJob]:
    jobs: list[ImageJob] = []
    for product in products:
        pid = int(product["id"])
        jobs.extend(
            _image_jobs_for_product(
                product,
                category=category,
                result=results.get(pid),
            )
        )
    return jobs


def image_jobs_from_product(product: dict, *, category: str = "all") -> list[ImageJob]:
    """All Shopify images on a product as Pixelbin jobs (no lifestyle/white filter)."""
    return _image_jobs_for_product(product, category=category, result=None)


def run_pixelbin_image_jobs(
    client: ShopifyClient,
    jobs: list[ImageJob],
    *,
    output_dir: Path,
) -> tuple[int, int]:
    """Pixelbin erase.bg + replace each image on Shopify (same image id)."""
    load_pixelbin_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0

    for i, job in enumerate(jobs, 1):
        out_dir = output_dir / str(job.product_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{job.position:02d}_{_safe_filename(job.product_title)}.png"
        job.output_path = out_dir / fname
        try:
            print(
                f"  [{i}/{len(jobs)}] {job.product_title} "
                f"image_id={job.shopify_image_id}...",
                flush=True,
            )
            png_bytes, px = remove_background_to_bytes(job.source_url)
            job.output_path.write_bytes(png_bytes)
            job.output_url = px.output_url
            job.credits = px.consumed_credits

            client.replace_product_image(
                job.product_id,
                job.shopify_image_id,
                image_bytes=png_bytes,
                filename=fname,
                position=job.position,
            )
            job.shopify_replaced = True
            ok += 1
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
            fail += 1
            print(f"    ERROR: {exc}")

    return ok, fail


def pixelbin_replace_all_product_images(
    client: ShopifyClient,
    product: dict,
    *,
    output_dir: Path,
    report_category: str = "unknown",
) -> tuple[int, int, list[ImageJob]]:
    """Remove BG on every image of one Shopify product and replace in place."""
    jobs = image_jobs_from_product(product, category="all")
    for job in jobs:
        job.category = report_category
    if not jobs:
        return 0, 0, jobs
    ok, fail = run_pixelbin_image_jobs(client, jobs, output_dir=output_dir)
    return ok, fail, jobs


def _load_classify_report_rows(report_path: Path) -> list[dict]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = list(report.get("products") or [])
    if rows:
        return rows
    seen: set[int] = set()
    out: list[dict] = []
    for key in ("lifestyle", "white", "no_bg", "unknown", "no_images"):
        for row in report.get(key) or []:
            pid = int(row.get("id") or 0)
            if pid and pid not in seen:
                seen.add(pid)
                out.append(row)
    return out


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


def run_pixelbin_report_batch(
    client: ShopifyClient,
    report_path: Path,
    *,
    output_dir: Path,
    category: str | None = None,
    checkpoint_path: Path | None = None,
    product_limit: int = 0,
    dry_run: bool = True,
) -> tuple[list[ImageJob], int, int, set[int]]:
    """
    Process products from classify report one-by-one (checkpoint-friendly).
    Returns (all_jobs, ok_images, failed_images, done_product_ids).
    """
    skip = _load_checkpoint(checkpoint_path) if checkpoint_path else set()
    rows = _load_classify_report_rows(report_path)
    if category and category != "all":
        rows = [r for r in rows if str(r.get("category") or "") == category]
    rows = [r for r in rows if int(r.get("id") or 0) not in skip]

    if product_limit > 0:
        rows = rows[:product_limit]

    all_jobs: list[ImageJob] = []
    ok_total = 0
    fail_total = 0
    done = set(skip)

    if dry_run:
        for row in rows:
            pid = int(row["id"])
            product = client.get_product(pid)
            cat = str(row.get("category") or "unknown")
            product_jobs = image_jobs_from_product(product, category="all")
            for job in product_jobs:
                job.category = cat
            all_jobs.extend(product_jobs)
        return all_jobs, 0, 0, done

    load_pixelbin_settings()
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, row in enumerate(rows, 1):
        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        cat = str(row.get("category") or "unknown")
        print(f"[{i}/{len(rows)}] {title} (id={pid}, report_cat={cat})...", flush=True)
        product = client.get_product(pid)
        px_ok, px_fail, product_jobs = pixelbin_replace_all_product_images(
            client,
            product,
            output_dir=output_dir,
            report_category=cat,
        )
        all_jobs.extend(product_jobs)
        ok_total += px_ok
        fail_total += px_fail
        if px_fail == 0 and product_jobs:
            done.add(pid)
            if checkpoint_path:
                _save_checkpoint(checkpoint_path, done)

    return all_jobs, ok_total, fail_total, done


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove backgrounds with Pixelbin and replace images on Shopify "
            "(same slot, old image removed)."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run Pixelbin and replace images on Shopify.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only (default).",
    )
    parser.add_argument(
        "--category",
        choices=VALID_CATEGORIES,
        help="Process products in this category (all their images).",
    )
    parser.add_argument(
        "--status",
        default="active",
        help="Shopify status when listing products (default: active).",
    )
    parser.add_argument(
        "--product-id",
        type=int,
        help="Process one product: all images, any status, no category filter.",
    )
    parser.add_argument(
        "--from-report",
        type=Path,
        metavar="REPORT.json",
        help="Use product list from shopify_classify_all report (no image re-scan).",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Skip product IDs already listed in this JSON (resume large runs).",
    )
    parser.add_argument(
        "--product-limit",
        type=int,
        default=0,
        help="Max products to process (with --from-report). 0 = all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max images to process (0 = no limit).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "pixelbin",
        help="Local backup of PNG cutouts.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "shopify_pixelbin_report.json",
        help="JSON report path.",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if args.apply and not args.category and not args.product_id and not args.from_report:
        print("ERROR: --apply needs --category, --product-id, or --from-report.")
        return 1

    shop_cfg = load_shopify_config()
    client = ShopifyClient(shop_cfg)

    print(f"Shop: {shop_cfg.shop_host}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY (replace on Shopify)'}")
    if args.from_report:
        print(f"From report: {args.from_report} (no re-classify)")
        if args.checkpoint:
            done = _load_checkpoint(args.checkpoint)
            print(f"Checkpoint: {args.checkpoint} ({len(done)} products already done)")
        if args.category:
            print(f"Report category filter: {args.category}")
        if args.product_limit:
            print(f"Product limit: {args.product_limit}")
    elif args.product_id:
        print(f"Product ID: {args.product_id} (all images)")
    elif args.category:
        print(f"Category: {args.category}")
    print()

    t0 = time.perf_counter()

    if args.from_report:
        if not args.from_report.is_file():
            print(f"ERROR: report not found: {args.from_report}")
            return 1
        jobs, ok, fail = [], 0, 0
        if dry_run:
            jobs, ok, fail, _done = run_pixelbin_report_batch(
                client,
                args.from_report,
                output_dir=args.output_dir,
                category=args.category,
                checkpoint_path=args.checkpoint,
                product_limit=args.product_limit,
                dry_run=True,
            )
    elif args.product_id:
        product = client.get_product(args.product_id)
        products = [product]
        print(f"Product: {product.get('title')} (status={product.get('status')})")
        print("Classifying images...")
        results = scan_products(
            products,
            workers=int(os.getenv("SHOPIFY_DRAFT_WORKERS", "20")),
            timeout=float(os.getenv("HTTP_TIMEOUT", "30")),
        )
        r = results.get(args.product_id)
        if r:
            print(
                f"  category={r.category} "
                f"(lifestyle={len(r.lifestyle_urls)}, white={len(r.white_urls)}, "
                f"no_bg={len(r.no_bg_urls)})"
            )
        jobs = _image_jobs_for_product(
            product,
            category="all",
            result=r,
        )
    else:
        products = client.iter_products(status=args.status)
        print(f"Loaded {len(products)} products. Classifying images...")
        results = scan_products(
            products,
            workers=int(os.getenv("SHOPIFY_DRAFT_WORKERS", "20")),
            timeout=float(os.getenv("HTTP_TIMEOUT", "30")),
        )
        counts: dict[str, int] = {}
        for r in results.values():
            counts[r.category] = counts.get(r.category, 0) + 1
        print("Products by category: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

        if not args.category:
            print()
            print("Pick a category, e.g.:")
            print("  python shopify_pixelbin_remove_bg.py --dry-run --category lifestyle")
            print("  python shopify_pixelbin_remove_bg.py --apply --category lifestyle")
            return 0

        jobs = _collect_jobs(products, results, category=args.category)

    if args.limit > 0:
        jobs = jobs[: args.limit]

    print()
    print(f"Images to process: {len(jobs)}")
    for job in jobs[:25]:
        print(
            f"  [{job.category}] {job.product_title} "
            f"(product={job.product_id}, image_id={job.shopify_image_id}, pos={job.position})"
        )
    if len(jobs) > 25:
        print(f"  ... and {len(jobs) - 25} more")

    if dry_run:
        print()
        print("Dry run. Add --apply to remove BG and replace on Shopify.")
        return 0

    if args.from_report:
        print()
        print("Pixelbin + Shopify replace (from report)...")
        jobs, ok, fail, _done = run_pixelbin_report_batch(
            client,
            args.from_report,
            output_dir=args.output_dir,
            category=args.category,
            checkpoint_path=args.checkpoint,
            product_limit=args.product_limit,
            dry_run=False,
        )
        print(f"Done: {ok} images replaced, {fail} failed")
        if args.checkpoint:
            print(f"Checkpoint updated: {args.checkpoint}")
    else:
        print()
        print("Pixelbin + Shopify replace...")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        ok, fail = run_pixelbin_image_jobs(client, jobs, output_dir=args.output_dir)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "shop": shop_cfg.shop_host,
        "category": args.category
        or ("report:" + str(args.from_report) if args.from_report else "")
        or ("product:" + str(args.product_id)),
        "processed": len(jobs),
        "ok": ok,
        "failed": fail,
        "replace_mode": "shopify_image_put",
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "jobs": [
            {
                "product_id": j.product_id,
                "title": j.product_title,
                "category": j.category,
                "shopify_image_id": j.shopify_image_id,
                "position": j.position,
                "source_url": j.source_url,
                "output_path": str(j.output_path) if j.output_path else "",
                "output_url": j.output_url,
                "credits": j.credits,
                "shopify_replaced": j.shopify_replaced,
                "error": j.error,
            }
            for j in jobs
        ],
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"Done: {ok} replaced, {fail} failed")
    print(f"Backup PNGs: {args.output_dir}")
    print(f"Report: {args.report}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
