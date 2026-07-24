"""Fetch products from Baserow (by brand or single product row).

Examples:
  python baserow_fetch_products.py --brand-id 16
  python baserow_fetch_products.py --product-id 8009
  python baserow_fetch_products.py --brand-id 16 --output output/pode_products.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from baserow_client import BaserowClient
from config import load_settings


def _file_urls(value: Any) -> list[str]:
    if not value:
        return []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            if url:
                out.append(url)
    return out


def _link_names(value: Any) -> list[str]:
    if not value:
        return []
    if not isinstance(value, list):
        return [str(value)]
    return [str(v.get("value") or v.get("name") or "").strip() for v in value if v]


def _row_to_product(row: dict[str, Any], settings) -> dict[str, Any]:
    return {
        "baserow_row_id": row["id"],
        "product_name": row.get(settings.field_product_name),
        "product_url": row.get(settings.field_product_url),
        "product_status": row.get(settings.field_product_status),
        "description": row.get(settings.field_product_description),
        "price": row.get(settings.field_price),
        "designer": row.get(settings.field_designer),
        "brands": _link_names(row.get(settings.field_brand_link)),
        "categories": _link_names(row.get(settings.field_product_category)),
        "subcategories": _link_names(row.get(settings.field_sub_category)),
        "source_category": row.get(settings.field_source_category),
        "source_subcategory": row.get(settings.field_source_subcategory),
        "product_images": _file_urls(row.get(settings.field_product_images)),
        "hero_images": _file_urls(row.get(settings.field_hero_images)),
        "lifestyle_images": _file_urls(row.get(settings.field_lifestyle_images)),
        "detail_image": _file_urls(row.get(settings.field_detail_image)),
    }


def fetch_by_brand_id(client: BaserowClient, settings, brand_id: int) -> tuple[dict, list[dict]]:
    brand_row = client.get_row(settings.brands_table_id, brand_id)
    linked = brand_row.get(settings.field_products) or []
    linked_ids = [int(x["id"]) for x in linked if isinstance(x, dict) and x.get("id")]

    products: list[dict] = []
    seen: set[int] = set()

    for row in client.list_table_rows(settings.products_table_id):
        rid = int(row["id"])
        if rid in linked_ids:
            products.append(_row_to_product(row, settings))
            seen.add(rid)
            continue
        for link in row.get(settings.field_brand_link) or []:
            if int(link.get("id") or 0) == brand_id:
                products.append(_row_to_product(row, settings))
                seen.add(rid)
                break

    products.sort(key=lambda p: str(p.get("product_name") or "").lower())
    brand_info = {
        "baserow_row_id": brand_id,
        "brand_name": brand_row.get(settings.field_brand_name),
        "domain": brand_row.get(settings.field_domain),
        "linked_product_ids_on_brand_row": linked_ids,
        "product_count": len(products),
    }
    return brand_info, products


def fetch_by_product_id(client: BaserowClient, settings, product_id: int) -> dict:
    row = client.get_row(settings.products_table_id, product_id)
    return _row_to_product(row, settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Baserow products to JSON.")
    parser.add_argument("--brand-id", type=int, help="Brand row id (table 805).")
    parser.add_argument("--product-id", type=int, help="Single product row id (table 802).")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "baserow_products_export.json",
    )
    args = parser.parse_args()

    if not args.brand_id and not args.product_id:
        print("ERROR: pass --brand-id OR --product-id")
        print()
        print("URL guide:")
        print("  .../table/805/<id>  -> brand row  (--brand-id)")
        print("  .../table/802/<id>  -> product row (--product-id)")
        return 1
    if args.brand_id and args.product_id:
        print("ERROR: use only one of --brand-id or --product-id")
        return 1

    settings = load_settings()
    client = BaserowClient(settings)

    if args.product_id:
        product = fetch_by_product_id(client, settings, args.product_id)
        payload = {
            "source": "baserow",
            "baserow_url": settings.baserow_url,
            "products_table_id": settings.products_table_id,
            "mode": "single_product",
            "product": product,
        }
        print(f"Product: {product.get('product_name')} (row {product['baserow_row_id']})")
    else:
        brand_info, products = fetch_by_brand_id(client, settings, args.brand_id)
        payload = {
            "source": "baserow",
            "baserow_url": settings.baserow_url,
            "products_table_id": settings.products_table_id,
            "brands_table_id": settings.brands_table_id,
            "mode": "brand_products",
            "brand": brand_info,
            "products": products,
        }
        print(
            f"Brand: {brand_info.get('brand_name')} "
            f"(row {brand_info['baserow_row_id']}) — {len(products)} products"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
