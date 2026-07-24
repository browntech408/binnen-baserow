"""One-off: print zero-image products live from Shopify (no file read)."""
from __future__ import annotations

import time
from collections import Counter

from shopify_client import ShopifyClient, load_shopify_config


def main() -> None:
    client = ShopifyClient(load_shopify_config())
    print("Shop:", client.config.shop_host)
    print("Source: LIVE Shopify Admin API")
    print("Fetching...")
    t0 = time.perf_counter()
    products = client.iter_products(
        status="",
        fields="id,title,status,images,vendor,created_at",
    )
    no_img = [p for p in products if not (p.get("images") or [])]
    no_img.sort(key=lambda x: (str(x.get("status") or ""), str(x.get("title") or "").lower()))

    print()
    print("=== LIVE ZERO-IMAGE PRODUCTS ===")
    print("Total catalog:", len(products))
    print("Zero images:  ", len(no_img))
    print("By status:   ", dict(Counter(p.get("status") for p in no_img)))
    print("By vendor:")
    for vendor, count in Counter(p.get("vendor") for p in no_img).most_common():
        print(f"  {vendor}: {count}")
    print()
    print("--- Full list ---")
    for p in no_img:
        print(
            f"[{p.get('status')}] {p.get('title')} "
            f"(id={p['id']}, vendor={p.get('vendor')})"
        )
    print()
    print(f"Done in {round(time.perf_counter() - t0, 1)}s")


if __name__ == "__main__":
    main()
