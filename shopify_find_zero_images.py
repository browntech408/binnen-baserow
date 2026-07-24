"""Find Shopify products with zero images (live API).

Examples:
  python shopify_find_zero_images.py
  python shopify_find_zero_images.py --status active
  python shopify_find_zero_images.py --output output/shopify_zero_image_products.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config


def main() -> int:
    parser = argparse.ArgumentParser(description="List Shopify products with no images.")
    parser.add_argument(
        "--status",
        default="",
        help='Shopify status filter: active, draft, archived, or "" for all.',
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "shopify_zero_image_products.json",
    )
    args = parser.parse_args()

    client = ShopifyClient(load_shopify_config())
    label = args.status or "all"

    print(f"Shop: {client.config.shop_host}")
    print(f"Status filter: {label}")
    t0 = time.perf_counter()

    print("Fetching products from Shopify...")
    products = client.iter_products(
        status=args.status,
        fields="id,title,status,images,created_at,vendor,product_type",
    )
    print(f"  {len(products)} products loaded")

    no_images = []
    for p in products:
        if not (p.get("images") or []):
            no_images.append(
                {
                    "id": p["id"],
                    "title": p.get("title"),
                    "status": p.get("status"),
                    "vendor": p.get("vendor"),
                    "product_type": p.get("product_type"),
                    "created_at": p.get("created_at"),
                }
            )

    no_images.sort(key=lambda x: str(x.get("title") or "").lower())

    by_status: dict[str, int] = {}
    for p in no_images:
        s = str(p.get("status") or "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    payload = {
        "shop": client.config.shop_host,
        "status_filter": label,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_products": len(products),
        "zero_image_count": len(no_images),
        "by_status": by_status,
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "products": no_images,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=== ZERO IMAGE PRODUCTS ===")
    print(f"Total products: {len(products)}")
    print(f"Zero images:    {len(no_images)}")
    print("By status:", by_status)
    print()
    for p in no_images[:30]:
        print(f"  [{p.get('status')}] {p.get('title')} (id={p['id']})")
    if len(no_images) > 30:
        print(f"  ... and {len(no_images) - 30} more")
    print(f"\nSaved: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
