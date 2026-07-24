"""Classify Shopify products by image type: lifestyle / white bg / no bg (transparent).

Shopify-only logic in shopify_image_classify.py — scrapers/image_bg.py is not used.

Examples:
  python shopify_draft_bg_products.py --dry-run
  python shopify_draft_bg_products.py --apply          # draft lifestyle only
  python shopify_draft_bg_products.py --apply --workers 24
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config, shopify_preview_url
from shopify_image_classify import ImageKind, classify_image_url, product_category


@dataclass
class ImageCheck:
    product_id: int
    title: str
    image_url: str
    kind: ImageKind


@dataclass
class ProductResult:
    product_id: int
    title: str
    category: str = "unknown"
    white_urls: list[str] = field(default_factory=list)
    no_bg_urls: list[str] = field(default_factory=list)
    lifestyle_urls: list[str] = field(default_factory=list)
    unknown_urls: list[str] = field(default_factory=list)
    drafted: bool = False
    error: str = ""


def _check_image(
    product_id: int,
    title: str,
    image_url: str,
    timeout: float,
) -> ImageCheck:
    kind = classify_image_url(
        image_url,
        timeout,
        preview_url=shopify_preview_url(image_url),
    )
    return ImageCheck(product_id, title, image_url, kind)


def scan_products(
    products: list[dict],
    *,
    workers: int,
    timeout: float,
) -> dict[int, ProductResult]:
    tasks: list[tuple[int, str, str]] = []
    meta: dict[int, str] = {}

    for product in products:
        pid = int(product["id"])
        title = str(product.get("title") or "").strip()
        meta[pid] = title
        for image in product.get("images") or []:
            src = str(image.get("src") or "").strip()
            if src:
                tasks.append((pid, title, src))

    results: dict[int, ProductResult] = {
        pid: ProductResult(product_id=pid, title=meta[pid]) for pid in meta
    }

    if not tasks:
        for r in results.values():
            r.category = product_category(
                lifestyle=0,
                white=0,
                no_bg=0,
                unknown=0,
                image_count=0,
            )
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_check_image, pid, title, url, timeout): (pid, url)
            for pid, title, url in tasks
        }
        done = 0
        total = len(futures)
        for future in as_completed(futures):
            done += 1
            pid, url = futures[future]
            try:
                check = future.result()
            except Exception as exc:  # noqa: BLE001
                results[pid].unknown_urls.append(url)
                if not results[pid].error:
                    results[pid].error = str(exc)
                continue

            if check.kind == "lifestyle":
                results[pid].lifestyle_urls.append(url)
            elif check.kind == "white":
                results[pid].white_urls.append(url)
            elif check.kind == "no_bg":
                results[pid].no_bg_urls.append(url)
            else:
                results[pid].unknown_urls.append(url)

            if done % 50 == 0 or done == total:
                print(f"  checked {done}/{total} images...", flush=True)

    for r in results.values():
        image_count = (
            len(r.lifestyle_urls)
            + len(r.white_urls)
            + len(r.no_bg_urls)
            + len(r.unknown_urls)
        )
        r.category = product_category(
            lifestyle=len(r.lifestyle_urls),
            white=len(r.white_urls),
            no_bg=len(r.no_bg_urls),
            unknown=len(r.unknown_urls),
            image_count=image_count,
        )

    return results


CATEGORY_LABELS = {
    "lifestyle": "LIFESTYLE - room / scene background",
    "white": "WHITE BACKGROUND - white packshot",
    "no_bg": "NO BACKGROUND - transparent cutout",
    "unknown": "UNKNOWN - could not classify image",
    "no_images": "NO IMAGES",
}


def _product_sort_key(r: ProductResult) -> str:
    return r.title.lower()


def _detail_for(r: ProductResult) -> str:
    parts: list[str] = []
    if r.lifestyle_urls:
        parts.append(f"lifestyle={len(r.lifestyle_urls)}")
    if r.white_urls:
        parts.append(f"white={len(r.white_urls)}")
    if r.no_bg_urls:
        parts.append(f"no_bg={len(r.no_bg_urls)}")
    if r.unknown_urls:
        parts.append(f"unknown={len(r.unknown_urls)}")
    return ", ".join(parts) if parts else "no images"


def _print_categories(grouped: dict[str, list[ProductResult]]) -> None:
    order = ("lifestyle", "white", "no_bg", "unknown", "no_images")
    for key in order:
        items = grouped.get(key, [])
        print("=" * 60)
        print(f"{CATEGORY_LABELS[key]} ({len(items)})")
        print("=" * 60)
        if items:
            tag = {
                "lifestyle": "lifestyle",
                "white": "white",
                "no_bg": "no_bg",
                "unknown": "unknown",
                "no_images": "no_img",
            }[key]
            for item in items:
                print(
                    f"  [{tag}]  {item.title} "
                    f"(id={item.product_id}, {_detail_for(item)})"
                )
        else:
            print("  (none)")
        print()


def _result_to_dict(r: ProductResult) -> dict:
    return {
        "id": r.product_id,
        "title": r.title,
        "category": r.category,
        "white_urls": r.white_urls,
        "no_bg_urls": r.no_bg_urls,
        "lifestyle_urls": r.lifestyle_urls,
        "unknown_urls": r.unknown_urls,
        "drafted": r.drafted,
        "error": r.error,
    }


def draft_products(
    client: ShopifyClient,
    to_draft: list[ProductResult],
    *,
    workers: int,
) -> None:
    def _draft_one(item: ProductResult) -> ProductResult:
        try:
            client.set_product_status(item.product_id, "draft")
            item.drafted = True
        except Exception as exc:  # noqa: BLE001
            item.error = str(exc)
        return item

    with ThreadPoolExecutor(max_workers=min(workers, 4)) as pool:
        list(pool.map(_draft_one, to_draft))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify Shopify products: lifestyle (room/scene), white bg, or no bg. "
            "Use --apply to draft lifestyle products only."
        ),
        epilog=(
            "Quick commands:\n"
            "  python shopify_draft_lifestyle.py              preview\n"
            "  python shopify_draft_lifestyle.py --apply      draft lifestyle"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Set lifestyle products to draft (default is dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only (default unless --apply is passed).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("SHOPIFY_DRAFT_WORKERS", "20")),
        help="Parallel workers for image checks (default: 20).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("HTTP_TIMEOUT", "20")),
        help="HTTP timeout per image download.",
    )
    parser.add_argument(
        "--status",
        default="active",
        help="Only scan products with this Shopify status (default: active).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "shopify_image_categories_report.json",
        help="Write JSON report to this path.",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    config = load_shopify_config()
    client = ShopifyClient(config)

    print(f"Shop: {config.shop_host}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY (lifestyle -> draft)'}")
    print(f"Workers: {args.workers}")
    print()

    t0 = time.perf_counter()
    print(f"Fetching {args.status or 'all'} products...")
    products = client.iter_products(status=args.status)
    print(f"  {len(products)} products loaded")

    if not products:
        print("Nothing to do.")
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

    if not dry_run and lifestyle:
        print(f"Setting {len(lifestyle)} lifestyle products to draft...")
        draft_products(client, lifestyle, workers=args.workers)
        ok = sum(1 for r in lifestyle if r.drafted)
        fail = sum(1 for r in lifestyle if r.error)
        print(f"  drafted: {ok}, errors: {fail}")
        for item in lifestyle:
            if item.error:
                print(f"  ERROR id={item.product_id}: {item.error}")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "shop": config.shop_host,
        "dry_run": dry_run,
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
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {args.report}")
    print(f"Done in {report['elapsed_seconds']}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
