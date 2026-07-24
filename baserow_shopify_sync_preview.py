"""Print skip / active / draft breakdown for baserow_shopify_sync."""
from __future__ import annotations

import argparse
import sys

from baserow_client import BaserowClient
from baserow_shopify_sync import (
    STORE_TARGETS,
    _iter_candidate_rows,
    _normalize_title,
    _parse_brand_ids,
    _preview_job,
)
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand-id", type=int, help="Single brand row id.")
    parser.add_argument(
        "--brand-ids",
        type=str,
        help="Comma-separated brand row ids (e.g. 11,14,15,19,37).",
    )
    parser.add_argument("--target", choices=tuple(STORE_TARGETS), default="woonbloq")
    args = parser.parse_args()

    brand_ids = _parse_brand_ids(args.brand_id, args.brand_ids)
    if not brand_ids:
        print("Pass --brand-id or --brand-ids", file=sys.stderr)
        return 1

    settings = load_settings()
    target = STORE_TARGETS[args.target]
    baserow = BaserowClient(settings)
    shopify = ShopifyClient(load_shopify_config())

    existing: dict[str, list[int]] = {}
    for p in shopify.iter_products(status="", fields="id,title"):
        t = _normalize_title(p.get("title"))
        if t:
            existing.setdefault(t, []).append(int(p["id"]))

    candidates = _iter_candidate_rows(
        baserow,
        settings,
        target,
        brand_ids=brand_ids,
        store_id=None,
        skip_synced=True,
    )

    skipped_dup: list[dict] = []
    skipped_batch_dup: list[dict] = []
    to_push: list[dict] = []
    queued: set[str] = set()

    print(f"Brand filter: {', '.join(str(i) for i in sorted(brand_ids))}")
    print()

    for row in candidates:
        title = str(row.get(settings.field_product_name) or "").strip()
        tnorm = _normalize_title(title)
        if not tnorm:
            continue
        if tnorm in existing:
            skipped_dup.append(
                {
                    "baserow_id": row["id"],
                    "title": title,
                    "shopify_product_ids": existing[tnorm],
                }
            )
            continue
        if tnorm in queued:
            skipped_batch_dup.append({"baserow_id": row["id"], "title": title})
            continue
        queued.add(tnorm)
        job = _preview_job(row, settings)
        to_push.append(
            {
                "baserow_id": row["id"],
                "title": title,
                "status": job.shopify_status,
                "missing": job.missing_fields,
                "hero_images": job.hero_image_count,
                "lifestyle_images": job.lifestyle_image_count,
                "metafields": len(job.metafields),
            }
        )

    print(f"=== SKIP Shopify duplicate ({len(skipped_dup)}) ===")
    for x in skipped_dup:
        ids = ", ".join(str(i) for i in x["shopify_product_ids"])
        print(f"  {x['title']} (row {x['baserow_id']}) -> Shopify id: {ids}")

    print()
    print(f"=== SKIP same name twice in Baserow batch ({len(skipped_batch_dup)}) ===")
    for x in skipped_batch_dup:
        print(f"  {x['title']} (row {x['baserow_id']}) -> pehli row push list mein hai")

    active = [x for x in to_push if x["status"] == "active"]
    draft = [x for x in to_push if x["status"] == "draft"]
    print()
    print(f"=== WILL PUSH: {len(to_push)} (active {len(active)}, draft {len(draft)}) ===")
    print()
    print("--- ACTIVE (title + AI description + hero + category + subcategory + brand) ---")
    for x in active:
        print(
            f"  {x['title']} (row {x['baserow_id']}, hero={x['hero_images']}, "
            f"lifestyle={x['lifestyle_images']}, metafields={x['metafields']})"
        )

    print()
    print("--- DRAFT (missing fields) ---")
    for x in draft:
        miss = ", ".join(x["missing"]) if x["missing"] else "none"
        print(
            f"  {x['title']} (row {x['baserow_id']}, hero={x['hero_images']}, "
            f"lifestyle={x['lifestyle_images']})"
        )
        print(f"    missing: {miss}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
