"""Move custom.lifestyle_images metafield files into product.images, then clear metafield.

Reads product IDs from a report JSON (products[]), loads live metafield from Shopify.

Examples:
  python shopify_move_metafield_lifestyle_to_images.py --dry-run \\
    --report output/shopify_products_after_july9_2026.json
  python shopify_move_metafield_lifestyle_to_images.py --apply \\
    --report output/shopify_products_after_july9_2026.json \\
    --checkpoint output/lifestyle_mf_to_images_checkpoint.json
  python shopify_move_metafield_lifestyle_to_images.py --apply \\
    --report output/shopify_products_after_july9_2026.json --product-limit 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config


def _url_key(url: str) -> str:
    return urlparse(url.strip()).path.rstrip("/").lower()


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


def _load_product_rows(report: dict) -> list[dict]:
    rows = list(report.get("products") or [])
    if rows:
        return rows
    out: list[dict] = []
    seen: set[int] = set()
    for key in ("lifestyle", "white", "no_bg", "unknown", "no_images"):
        for row in report.get(key) or []:
            pid = int(row.get("id") or 0)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            out.append(row)
    return out


@dataclass
class ProductMove:
    product_id: int
    product_title: str
    metafield_id: str = ""
    lifestyle_urls: list[str] = field(default_factory=list)
    urls_to_add: list[str] = field(default_factory=list)
    added: int = 0
    skipped_existing: int = 0
    metafield_cleared: bool = False
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move lifestyle_images metafield -> product images, then clear metafield."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--product-limit", type=int, default=0)
    parser.add_argument(
        "--run-report",
        type=Path,
        default=Path("output") / "shopify_move_metafield_lifestyle_to_images_report.json",
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
    rows = _load_product_rows(report)
    done = _load_checkpoint(args.checkpoint) if args.checkpoint else set()
    if done:
        rows = [r for r in rows if int(r["id"]) not in done]
    if args.product_limit > 0:
        rows = rows[: args.product_limit]

    print(f"Report: {args.report}")
    print(f"Metafield: {namespace}.{mf_key}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Products in run: {len(rows)}")
    if args.checkpoint:
        print(f"Checkpoint skip: {len(done)} products")
    print()

    if not rows:
        print("Nothing to process.")
        return 0

    client = ShopifyClient(load_shopify_config())
    t0 = time.perf_counter()
    moves: list[ProductMove] = []

    print("Reading lifestyle_images metafield from Shopify...")
    for i, row in enumerate(rows, 1):
        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        try:
            info = client.get_product_list_file_reference_metafield(
                pid, namespace, mf_key
            )
            urls = list(info.get("urls") or [])
            pm = ProductMove(
                product_id=pid,
                product_title=title,
                metafield_id=str(info.get("metafield_id") or ""),
                lifestyle_urls=urls,
            )
            if not urls:
                moves.append(pm)
                continue

            product = client.get_product(pid)
            existing = {
                _url_key(str(img.get("src") or ""))
                for img in (product.get("images") or [])
                if img.get("src")
            }
            to_add = [u for u in urls if _url_key(u) not in existing]
            pm.urls_to_add = to_add
            pm.skipped_existing = len(urls) - len(to_add)
            moves.append(pm)
        except Exception as exc:  # noqa: BLE001
            moves.append(
                ProductMove(
                    product_id=pid,
                    product_title=title,
                    error=f"read: {exc}",
                )
            )
        if i % 50 == 0 or i == len(rows):
            print(f"  scanned {i}/{len(rows)}", flush=True)

    with_mf = [m for m in moves if m.lifestyle_urls and not m.error]
    to_add_total = sum(len(m.urls_to_add) for m in with_mf)
    print()
    print(f"Products with lifestyle metafield images: {len(with_mf)}")
    print(f"Lifestyle image URLs: {sum(len(m.lifestyle_urls) for m in with_mf)}")
    print(f"URLs to add to product.images: {to_add_total}")
    print(f"Already on product (skip): {sum(m.skipped_existing for m in with_mf)}")
    print()

    for m in with_mf[:20]:
        print(
            f"  {m.product_title} (id={m.product_id}): "
            f"{len(m.lifestyle_urls)} mf -> add {len(m.urls_to_add)}"
        )
    if len(with_mf) > 20:
        print(f"  ... and {len(with_mf) - 20} more")

    if dry_run:
        print()
        print("Dry run. Add --apply to move images and clear metafield.")
        return 0

    targets = [m for m in moves if (m.lifestyle_urls or m.metafield_id) and not m.error]
    if not targets:
        print("No metafield images to move.")
        return 0

    print()
    print("Moving metafield images -> product.images, then clearing metafield...")
    for i, pm in enumerate(targets, 1):
        try:
            print(
                f"  [{i}/{len(targets)}] {pm.product_title} "
                f"(add {len(pm.urls_to_add)}, clear mf)",
                flush=True,
            )
            for url in pm.urls_to_add:
                client.create_product_image_from_src(pm.product_id, url)
                pm.added += 1
            if pm.metafield_id or pm.lifestyle_urls:
                client.clear_product_list_file_reference_metafield(
                    pm.product_id,
                    namespace,
                    mf_key,
                    metafield_gid=pm.metafield_id,
                )
                pm.metafield_cleared = True
            if args.checkpoint:
                done.add(pm.product_id)
                _save_checkpoint(args.checkpoint, done)
        except Exception as exc:  # noqa: BLE001
            pm.error = str(exc)
            print(f"    ERROR: {exc}")

    ok = sum(1 for m in targets if m.metafield_cleared and not m.error)
    fail = sum(1 for m in targets if m.error)
    added = sum(m.added for m in targets)

    args.run_report.parent.mkdir(parents=True, exist_ok=True)
    args.run_report.write_text(
        json.dumps(
            {
                "source_report": str(args.report),
                "metafield": f"{namespace}.{mf_key}",
                "products_with_metafield": len(with_mf),
                "products_ok": ok,
                "products_failed": fail,
                "images_added": added,
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
                "products": [
                    {
                        "product_id": m.product_id,
                        "title": m.product_title,
                        "lifestyle_urls": m.lifestyle_urls,
                        "urls_added": m.urls_to_add[: m.added] if m.added else [],
                        "added": m.added,
                        "skipped_existing": m.skipped_existing,
                        "metafield_cleared": m.metafield_cleared,
                        "error": m.error,
                    }
                    for m in moves
                    if m.lifestyle_urls or m.error or m.metafield_cleared
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print()
    print(f"Done: {ok} products ok, {fail} failed, {added} images added")
    print(f"Run log: {args.run_report}")
    if args.checkpoint:
        print(f"Checkpoint: {args.checkpoint} ({len(done)} done)")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
