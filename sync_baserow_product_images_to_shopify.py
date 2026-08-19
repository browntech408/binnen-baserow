"""
Sync Single or Multiple Baserow Product Images to Shopify
=========================================================

Given a Baserow Table ID and Row ID(s):
  1. Fetches the product record from Baserow.
  2. Extracts Hero images, Lifestyle images, and Detail images.
  3. Locates the matching product in Shopify (by title/handle or specified --shopify-id).
  4. Deletes ALL existing gallery images from the Shopify product.
  5. Uploads Hero images into the main Shopify product gallery.
  6. Uploads Lifestyle images to Shopify Files and updates the 'custom.lifestyle_images' metafield (list.file_reference).
  7. Uploads Detail images to Shopify Files and updates the 'custom.detail_images' metafield (list.file_reference).

USAGE:
  # Dry-run (inspect what will change without making changes):
  python sync_baserow_product_images_to_shopify.py --table-id 8224 --row-id 105

  # Apply changes to Shopify:
  python sync_baserow_product_images_to_shopify.py --table-id 8224 --row-id 105 --apply

  # Multiple rows:
  python sync_baserow_product_images_to_shopify.py --table-id 8224 --row-ids 105,106,107 --apply

  # Specify Shopify Product ID directly (if different title):
  python sync_baserow_product_images_to_shopify.py --table-id 8224 --row-id 105 --shopify-id 10528720912731 --apply
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
from typing import Any

import requests
from dotenv import load_dotenv

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config

load_dotenv()


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    t = re.sub(r"\s+", " ", str(title)).strip().lower()
    return t


def _extract_image_urls(val: Any) -> list[str]:
    """Extract list of public image URLs from Baserow file field value."""
    if not val:
        return []
    urls: list[str] = []
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                url = item.get("url") or item.get("image_url") or ""
                if url and isinstance(url, str) and url.startswith("http"):
                    if url not in urls:
                        urls.append(url)
            elif isinstance(item, str) and item.startswith("http"):
                if item not in urls:
                    urls.append(item)
    elif isinstance(val, dict):
        url = val.get("url") or val.get("image_url") or ""
        if url and isinstance(url, str) and url.startswith("http"):
            urls.append(url)
    elif isinstance(val, str) and val.startswith("http"):
        urls.append(val)
    return urls


def _find_shopify_product(
    shopify: ShopifyClient,
    product_title: str,
    *,
    explicit_id: int | None = None,
) -> dict[str, Any] | None:
    """Find Shopify product by explicit ID or by matching title."""
    if explicit_id:
        try:
            return shopify.get_product(explicit_id)
        except Exception as exc:
            print(f"  [ERROR] Could not fetch Shopify product ID {explicit_id}: {exc}")
            return None

    norm_target = _normalize_title(product_title)
    if not norm_target:
        return None

    # Fetch all products from Shopify and match title
    products = shopify.iter_products(status="", fields="id,title,handle,status,images")
    for prod in products:
        if _normalize_title(prod.get("title")) == norm_target:
            return prod

    # Try partial / stripped match
    for prod in products:
        p_title = _normalize_title(prod.get("title"))
        if norm_target in p_title or p_title in norm_target:
            return prod

    return None


@dataclass
class SyncResult:
    row_id: int
    product_title: str
    shopify_id: int | None = None
    deleted_images_count: int = 0
    hero_uploaded_count: int = 0
    lifestyle_uploaded_count: int = 0
    detail_uploaded_count: int = 0
    errors: list[str] = field(default_factory=list)
    success: bool = False


def sync_single_product(
    *,
    baserow: BaserowClient,
    shopify: ShopifyClient,
    settings: Any,
    table_id: int,
    row_id: int,
    explicit_shopify_id: int | None = None,
    dry_run: bool = True,
    metafield_namespace: str = "custom",
    lifestyle_metafield_key: str = "lifestyle_images",
    detail_metafield_key: str = "detail_images",
) -> SyncResult:
    res = SyncResult(row_id=row_id, product_title="")

    print("\n" + "=" * 70)
    print(f"Processing Baserow Row ID: {row_id} (Table: {table_id})")
    print("=" * 70)

    # 1. Fetch Baserow row
    try:
        row = baserow.get_row(table_id, row_id)
    except Exception as exc:
        err = f"Failed to fetch Baserow row {row_id} from table {table_id}: {exc}"
        print(f"  [ERROR] {err}")
        res.errors.append(err)
        return res

    title = str(row.get(settings.field_product_name) or row.get("Name") or row.get("title") or "").strip()
    res.product_title = title or f"Row #{row_id}"
    print(f"  Product Title: {res.product_title}")

    # Extract images from Baserow fields
    hero_urls = _extract_image_urls(row.get(settings.field_hero_images))
    lifestyle_urls = _extract_image_urls(row.get(settings.field_lifestyle_images))
    detail_urls = _extract_image_urls(row.get(settings.field_detail_image))

    # Fallback check if field IDs differ or named keys are present
    if not hero_urls:
        hero_urls = _extract_image_urls(row.get("hero_images") or row.get("Hero Images"))
    if not lifestyle_urls:
        lifestyle_urls = _extract_image_urls(row.get("lifestyle_images") or row.get("Lifestyle Images"))
    if not detail_urls:
        detail_urls = _extract_image_urls(row.get("detail_images") or row.get("Detail Images") or row.get("detail_image"))

    print(f"  Baserow Images Found:")
    print(f"    - Hero Images (Gallery):     {len(hero_urls)}")
    for u in hero_urls:
        print(f"        * {u}")
    print(f"    - Lifestyle Images (Meta):   {len(lifestyle_urls)}")
    for u in lifestyle_urls:
        print(f"        * {u}")
    print(f"    - Detail Images (Meta):      {len(detail_urls)}")
    for u in detail_urls:
        print(f"        * {u}")

    # 2. Locate Shopify product
    print(f"\n  Locating Shopify product...")
    shopify_prod = _find_shopify_product(
        shopify,
        res.product_title,
        explicit_id=explicit_shopify_id,
    )

    if not shopify_prod:
        err = f"Shopify product not found for title '{res.product_title}' (Row ID: {row_id}). Use --shopify-id if known."
        print(f"  [ERROR] {err}")
        res.errors.append(err)
        return res

    shopify_id = int(shopify_prod["id"])
    res.shopify_id = shopify_id
    existing_images = shopify_prod.get("images") or []
    print(f"  Found Shopify Product: ID={shopify_id}, Title='{shopify_prod.get('title')}'")
    print(f"  Existing Shopify Gallery Images: {len(existing_images)}")

    if dry_run:
        print("\n  [DRY-RUN SUMMARY - No changes made to Shopify]")
        print(f"    1. Would DELETE {len(existing_images)} existing Shopify gallery image(s):")
        for img in existing_images:
            print(f"       - Image ID: {img.get('id')} ({img.get('src')})")
        print(f"    2. Would UPLOAD {len(hero_urls)} Hero image(s) to Shopify Gallery.")
        print(f"    3. Would UPLOAD {len(lifestyle_urls)} Lifestyle image(s) to '{metafield_namespace}.{lifestyle_metafield_key}'.")
        print(f"    4. Would UPLOAD {len(detail_urls)} Detail image(s) to '{metafield_namespace}.{detail_metafield_key}'.")
        res.success = True
        return res

    # 3. APPLY: Delete ALL existing Shopify gallery images
    print(f"\n  [Step 1/3] Deleting {len(existing_images)} existing Shopify gallery images...")
    deleted_count = 0
    for img in existing_images:
        img_id = int(img["id"])
        try:
            shopify.delete_product_image(shopify_id, img_id)
            deleted_count += 1
            print(f"    [DELETED] Image ID {img_id}")
        except Exception as exc:
            err = f"Failed to delete image {img_id}: {exc}"
            print(f"    [ERR] {err}")
            res.errors.append(err)
    res.deleted_images_count = deleted_count

    # 4. APPLY: Upload Hero images to Shopify Gallery
    print(f"\n  [Step 2/3] Uploading {len(hero_urls)} Hero images to Shopify Gallery...")
    hero_uploaded = 0
    for pos, url in enumerate(hero_urls, start=1):
        try:
            img_res = shopify.create_product_image_from_src(shopify_id, url, position=pos)
            hero_uploaded += 1
            print(f"    [OK] Uploaded hero #{pos} (Image ID: {img_res.get('id')})")
        except Exception as exc:
            err = f"Failed to upload hero image ({url}): {exc}"
            print(f"    [ERR] {err}")
            res.errors.append(err)
    res.hero_uploaded_count = hero_uploaded

    # 5. APPLY: Sync Lifestyle Metafield
    print(f"\n  [Step 3/3] Uploading Lifestyle & Detail images to Shopify Metafields...")
    if lifestyle_urls:
        print(f"    Creating {len(lifestyle_urls)} lifestyle files in Shopify...")
        try:
            ls_gids = shopify.create_image_files_from_urls(lifestyle_urls)
            if ls_gids:
                shopify.set_product_list_file_reference_metafield(
                    shopify_id,
                    metafield_namespace,
                    lifestyle_metafield_key,
                    ls_gids,
                )
                res.lifestyle_uploaded_count = len(ls_gids)
                print(f"    [OK] Set '{metafield_namespace}.{lifestyle_metafield_key}' with {len(ls_gids)} files.")
            else:
                err = "No file GIDs generated for lifestyle images."
                print(f"    [WARN] {err}")
                res.errors.append(err)
        except Exception as exc:
            err = f"Failed to sync lifestyle metafield: {exc}"
            print(f"    [ERR] {err}")
            res.errors.append(err)
    else:
        print(f"    (No lifestyle images in Baserow)")

    # 6. APPLY: Sync Detail Metafield
    if detail_urls:
        print(f"    Creating {len(detail_urls)} detail files in Shopify...")
        try:
            dt_gids = shopify.create_image_files_from_urls(detail_urls)
            if dt_gids:
                shopify.set_product_list_file_reference_metafield(
                    shopify_id,
                    metafield_namespace,
                    detail_metafield_key,
                    dt_gids,
                )
                res.detail_uploaded_count = len(dt_gids)
                print(f"    [OK] Set '{metafield_namespace}.{detail_metafield_key}' with {len(dt_gids)} files.")
            else:
                err = "No file GIDs generated for detail images."
                print(f"    [WARN] {err}")
                res.errors.append(err)
        except Exception as exc:
            err = f"Failed to sync detail metafield: {exc}"
            print(f"    [ERR] {err}")
            res.errors.append(err)
    else:
        print(f"    (No detail images in Baserow)")

    res.success = (len(res.errors) == 0)
    return res


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Baserow Product Images to Shopify (Deletes existing, adds Hero to Gallery, Lifestyle & Detail to Metafields)"
    )
    parser.add_argument(
        "--table-id", "-t",
        type=int,
        default=0,
        help="Baserow Table ID (default: PRODUCTS_TABLE_ID from .env)",
    )
    parser.add_argument(
        "--row-id", "-r",
        type=int,
        default=0,
        help="Single Baserow Product Row ID",
    )
    parser.add_argument(
        "--row-ids",
        type=str,
        default="",
        help="Comma- or space-separated list of Baserow Product Row IDs (e.g. 101,102,103)",
    )
    parser.add_argument(
        "--shopify-id",
        type=int,
        default=None,
        help="Explicit Shopify Product ID (optional; matches title if omitted)",
    )
    parser.add_argument(
        "--metafield-namespace",
        default=os.getenv("SHOPIFY_METAFIELD_NAMESPACE", "custom"),
        help="Shopify metafield namespace (default: custom)",
    )
    parser.add_argument(
        "--lifestyle-key",
        default=os.getenv("SHOPIFY_METAFIELD_LIFESTYLE_IMAGES", "lifestyle_images"),
        help="Shopify lifestyle metafield key (default: lifestyle_images)",
    )
    parser.add_argument(
        "--detail-key",
        default="detail_images",
        help="Shopify detail metafield key (default: detail_images)",
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to Shopify (delete old images and upload new)",
    )
    grp.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Simulate actions without modifying Shopify (default)",
    )

    args = parser.parse_args()
    dry_run = not args.apply

    # Load settings and clients
    settings = load_settings()
    table_id = args.table_id or settings.products_table_id

    if not table_id:
        print("ERROR: --table-id is required or PRODUCTS_TABLE_ID must be set in .env", file=sys.stderr)
        return 1

    row_ids: list[int] = []
    if args.row_id:
        row_ids.append(args.row_id)
    if args.row_ids:
        for p in re.split(r"[\s,]+", args.row_ids.strip()):
            if p.isdigit():
                pid = int(p)
                if pid not in row_ids:
                    row_ids.append(pid)

    if not row_ids:
        print("ERROR: Please specify at least one Baserow row ID using --row-id <ID> or --row-ids <ID1,ID2>", file=sys.stderr)
        return 1

    try:
        baserow = BaserowClient(settings)
        shopify = ShopifyClient(load_shopify_config())
    except Exception as exc:
        print(f"ERROR Initializing clients: {exc}", file=sys.stderr)
        return 1

    print("=" * 70)
    print("Baserow -> Shopify Product Images Sync")
    print("=" * 70)
    print(f"  Baserow Table ID:   {table_id}")
    print(f"  Target Row ID(s):   {row_ids}")
    print(f"  Shopify Store:      {shopify.config.shop_host}")
    print(f"  Mode:               {'DRY RUN (Preview Only)' if dry_run else 'APPLY (Live Changes)'}")
    print(f"  Metafield Keys:     {args.metafield_namespace}.{args.lifestyle_key} & {args.metafield_namespace}.{args.detail_key}")

    results: list[SyncResult] = []
    t0 = time.perf_counter()

    for r_id in row_ids:
        # Pass explicit_shopify_id only if single row processed
        exp_id = args.shopify_id if len(row_ids) == 1 else None
        res = sync_single_product(
            baserow=baserow,
            shopify=shopify,
            settings=settings,
            table_id=table_id,
            row_id=r_id,
            explicit_shopify_id=exp_id,
            dry_run=dry_run,
            metafield_namespace=args.metafield_namespace,
            lifestyle_metafield_key=args.lifestyle_key,
            detail_metafield_key=args.detail_key,
        )
        results.append(res)

    elapsed = time.perf_counter() - t0

    # Summary
    print("\n" + "=" * 70)
    print("SYNC SUMMARY")
    print("=" * 70)
    print(f"  Total Processed:         {len(results)}")
    print(f"  Mode:                    {'DRY RUN' if dry_run else 'APPLIED'}")
    print(f"  Elapsed Time:            {elapsed:.2f}s")
    print(f"  Successful:              {sum(1 for r in results if r.success)}")
    print(f"  With Errors:             {sum(1 for r in results if r.errors)}")
    if not dry_run:
        print(f"  Total Deleted Images:    {sum(r.deleted_images_count for r in results)}")
        print(f"  Hero Gallery Uploaded:   {sum(r.hero_uploaded_count for r in results)}")
        print(f"  Lifestyle Meta Synced:   {sum(r.lifestyle_uploaded_count for r in results)}")
        print(f"  Detail Meta Synced:      {sum(r.detail_uploaded_count for r in results)}")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
