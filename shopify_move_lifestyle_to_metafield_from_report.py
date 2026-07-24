"""Move report lifestyle_urls from Shopify product images to lifestyle_images metafield.

Flow per product:
  1) fileCreate from lifestyle CDN URLs -> MediaImage GIDs
  2) metafieldsSet custom.lifestyle_images (list.file_reference), merged with existing
  3) delete lifestyle images from product.images

Examples:
  python shopify_move_lifestyle_to_metafield_from_report.py --dry-run \\
    --report output/shopify_after_june10_report.json
  python shopify_move_lifestyle_to_metafield_from_report.py --apply \\
    --report output/shopify_after_june10_report.json \\
    --checkpoint output/lifestyle_to_metafield_checkpoint.json
  python shopify_move_lifestyle_to_metafield_from_report.py --apply \\
    --report output/shopify_after_june10_report.json --product-limit 20
  # Default: only products with >=1 white/no_bg image (lifestyle-only skipped).
  # All lifestyle products (incl. lifestyle-only): --include-lifestyle-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config
from shopify_delete_lifestyle_from_report import (
    _apply_report_updates,
    _load_checkpoint,
    _rows_with_lifestyle,
    _save_checkpoint,
)
from shopify_process_white_nobg_from_report import _map_shopify_images, _url_key


def _hero_image_count(row: dict) -> int:
    return len(row.get("white_urls") or []) + len(row.get("no_bg_urls") or [])


def _filter_rows_by_hero(rows: list[dict], *, min_hero_images: int) -> list[dict]:
    if min_hero_images <= 0:
        return rows
    return [r for r in rows if _hero_image_count(r) >= min_hero_images]


@dataclass
class MoveJob:
    product_id: int
    product_title: str
    shopify_image_id: int
    source_url: str
    deleted: bool = False
    error: str = ""


@dataclass
class ProductMove:
    product_id: int
    product_title: str
    lifestyle_urls: list[str] = field(default_factory=list)
    jobs: list[MoveJob] = field(default_factory=list)
    metafield_set: bool = False
    file_gids: list[str] = field(default_factory=list)
    error: str = ""


def _get_existing_lifestyle_gids(
    client: ShopifyClient,
    product_id: int,
    *,
    namespace: str,
    key: str,
) -> list[str]:
    query = (
        "query productMetafield($id: ID!, $namespace: String!, $key: String!) {"
        "  product(id: $id) {"
        "    metafield(namespace: $namespace, key: $key) { value }"
        "  }"
        "}"
    )
    data = client.graphql(
        query,
        {
            "id": f"gid://shopify/Product/{product_id}",
            "namespace": namespace,
            "key": key,
        },
    )
    mf = (data.get("product") or {}).get("metafield")
    if not mf:
        return []
    raw = str(mf.get("value") or "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x).strip() for x in parsed if str(x).strip()]


def _merge_gids(existing: list[str], new: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for gid in [*existing, *new]:
        g = str(gid).strip()
        if not g or g in seen:
            continue
        seen.add(g)
        out.append(g)
    return out


def _build_product_moves(client: ShopifyClient, rows: list[dict]) -> tuple[list[ProductMove], int]:
    moves: list[ProductMove] = []
    missing = 0
    for row in rows:
        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        product = client.get_product(pid)
        img_map = _map_shopify_images(product)
        pm = ProductMove(product_id=pid, product_title=title)
        seen_urls: set[str] = set()
        for url in row.get("lifestyle_urls") or []:
            u = str(url).strip()
            if not u:
                continue
            key = _url_key(u)
            if key in seen_urls:
                continue
            img = img_map.get(key)
            if not img:
                missing += 1
                continue
            seen_urls.add(key)
            pm.lifestyle_urls.append(u)
            pm.jobs.append(
                MoveJob(
                    product_id=pid,
                    product_title=title,
                    shopify_image_id=int(img["id"]),
                    source_url=u,
                )
            )
        if pm.jobs:
            moves.append(pm)
    return moves, missing


def _apply_report_after_moves(report: dict, moves: list[ProductMove]) -> tuple[dict, list[dict]]:
    delete_jobs = [job for pm in moves for job in pm.jobs]
    report, moved = _apply_report_updates(report, delete_jobs)
    report["lifestyle_to_metafield"] = {
        "products": sum(1 for pm in moves if pm.metafield_set),
        "metafield_images": sum(len(pm.file_gids) for pm in moves if pm.metafield_set),
        "deleted_images": sum(1 for j in delete_jobs if j.deleted),
        "failed_products": sum(1 for pm in moves if pm.error),
        "failed_deletes": sum(1 for j in delete_jobs if j.error),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return report, moved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move lifestyle_urls from Shopify images to lifestyle_images metafield."
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
        help="Skip product IDs already fully processed.",
    )
    parser.add_argument(
        "--product-limit",
        type=int,
        default=0,
        help="Max products with lifestyle_urls to process (0 = all).",
    )
    parser.add_argument(
        "--min-hero-images",
        type=int,
        default=1,
        help="Require at least N white/no_bg images (default: 1). Lifestyle-only skipped.",
    )
    parser.add_argument(
        "--include-lifestyle-only",
        action="store_true",
        help="Also move lifestyle for products with zero white/no_bg images.",
    )
    parser.add_argument(
        "--mixed-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--run-report",
        type=Path,
        default=Path("output") / "shopify_move_lifestyle_to_metafield_report.json",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if not args.report.is_file():
        print(f"ERROR: report not found: {args.report}")
        return 1

    settings = load_settings()
    namespace = settings.shopify_metafield_namespace
    mf_key = settings.shopify_metafield_lifestyle_images
    if not mf_key:
        print("ERROR: SHOPIFY_METAFIELD_LIFESTYLE_IMAGES is not set.")
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = _rows_with_lifestyle(report, mixed_only=False)
    min_hero = 0 if args.include_lifestyle_only else max(1, args.min_hero_images)
    if args.mixed_only and not args.include_lifestyle_only:
        min_hero = max(min_hero, 1)
    before_filter = len(rows)
    rows = _filter_rows_by_hero(rows, min_hero_images=min_hero)
    skipped = before_filter - len(rows)
    done_products = _load_checkpoint(args.checkpoint) if args.checkpoint else set()
    if done_products:
        rows = [r for r in rows if int(r["id"]) not in done_products]
    if args.product_limit > 0:
        rows = rows[: args.product_limit]

    lifestyle_url_count = sum(len(r.get("lifestyle_urls") or []) for r in rows)
    print(f"Report: {args.report}")
    print(f"Metafield: {namespace}.{mf_key}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    if min_hero > 0:
        print(f"Filter: require >= {min_hero} white/no_bg image(s) per product")
        print(f"Skipped (lifestyle-only / insufficient hero): {skipped} products")
    print(f"Products with lifestyle_urls: {len(rows)}")
    print(f"Lifestyle URLs in report: {lifestyle_url_count}")
    if args.checkpoint:
        print(f"Checkpoint skip: {len(done_products)} products")
    print()

    if not rows:
        print("Nothing to move.")
        return 0

    client = ShopifyClient(load_shopify_config())
    t0 = time.perf_counter()
    moves, missing = _build_product_moves(client, rows)
    job_count = sum(len(pm.jobs) for pm in moves)

    print(f"Products matched on Shopify: {len(moves)}")
    print(f"Move jobs (lifestyle images): {job_count}")
    if missing:
        print(f"WARN: {missing} lifestyle URLs not found on live Shopify (skipped)")
    print()

    for pm in moves[:20]:
        print(
            f"  {pm.product_title} (product={pm.product_id}): "
            f"{len(pm.jobs)} lifestyle image(s)"
        )
    if len(moves) > 20:
        print(f"  ... and {len(moves) - 20} more products")

    if dry_run:
        print()
        print("Dry run. Add --apply to move images to metafield and delete from product images.")
        return 0

    if not moves:
        print("No matched images to move.")
        return 0

    print()
    print("Moving lifestyle images to metafield...")
    for i, pm in enumerate(moves, 1):
        try:
            print(
                f"  [{i}/{len(moves)}] {pm.product_title} "
                f"({len(pm.lifestyle_urls)} url(s))",
                flush=True,
            )
            new_gids = client.create_image_files_from_urls(pm.lifestyle_urls)
            if not new_gids:
                raise RuntimeError("fileCreate returned no GIDs")
            existing = _get_existing_lifestyle_gids(
                client, pm.product_id, namespace=namespace, key=mf_key
            )
            merged = _merge_gids(existing, new_gids)
            client.set_product_list_file_reference_metafield(
                pm.product_id, namespace, mf_key, merged
            )
            pm.metafield_set = True
            pm.file_gids = merged
            for job in pm.jobs:
                client.delete_product_image(job.product_id, job.shopify_image_id)
                job.deleted = True
        except Exception as exc:  # noqa: BLE001
            pm.error = str(exc)
            print(f"    ERROR: {exc}")

    print()
    print("Updating report...")
    report, moved = _apply_report_after_moves(report, moves)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated report: {args.report}")
    print(f"Recategorized products: {len(moved)}")
    print("New counts: " + ", ".join(f"{k}={v}" for k, v in sorted((report.get("counts") or {}).items())))

    if args.checkpoint:
        fully_done = {
            pm.product_id
            for pm in moves
            if pm.metafield_set and all(j.deleted for j in pm.jobs)
        }
        done_products |= fully_done
        _save_checkpoint(args.checkpoint, done_products)
        print(f"Checkpoint: {args.checkpoint} ({len(done_products)} products fully done)")

    ok_products = sum(1 for pm in moves if pm.metafield_set)
    fail_products = sum(1 for pm in moves if pm.error)
    deleted = sum(1 for pm in moves for j in pm.jobs if j.deleted)
    args.run_report.parent.mkdir(parents=True, exist_ok=True)
    args.run_report.write_text(
        json.dumps(
            {
                "source_report": str(args.report),
                "metafield": f"{namespace}.{mf_key}",
                "products_processed": len(moves),
                "products_ok": ok_products,
                "products_failed": fail_products,
                "images_deleted": deleted,
                "missing_on_shopify": missing,
                "recategorized": len(moved),
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
                "products": [
                    {
                        "product_id": pm.product_id,
                        "title": pm.product_title,
                        "lifestyle_urls": pm.lifestyle_urls,
                        "metafield_set": pm.metafield_set,
                        "file_gids": pm.file_gids,
                        "deleted_images": sum(1 for j in pm.jobs if j.deleted),
                        "error": pm.error,
                    }
                    for pm in moves
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Run log: {args.run_report}")
    print(f"Done: {ok_products} products moved, {fail_products} failed, {deleted} images deleted")
    return 0 if fail_products == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
