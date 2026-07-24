"""Audit scraped JSON category resolution against Baserow tables."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from baserow_client import BaserowClient
from config import load_settings
from product_baserow import CategoryLookup
from product_schema import ScrapedProduct


def main() -> int:
    if "--baserow" in sys.argv:
        return audit_baserow()

    settings = load_settings()
    client = BaserowClient(settings)
    lookup = CategoryLookup(client, settings)
    out = Path(__file__).resolve().parent / "output"
    issues: list[tuple] = []

    for jf in sorted(out.glob("products_*.json")):
        rows = json.loads(jf.read_text(encoding="utf-8"))
        for row in rows:
            product = ScrapedProduct(
                product_name=row.get("product_name", ""),
                product_category=row.get("product_category", ""),
                sub_category=row.get("sub_category", ""),
                product_url=row.get("product_url", ""),
            )
            cat_ids, sub_ids = lookup.resolve(
                product.product_category, product.sub_category, create=False
            )
            if not cat_ids:
                issues.append(
                    (
                        jf.name,
                        product.product_name,
                        product.product_category,
                        product.sub_category,
                    )
                )
            else:
                print(
                    f"{jf.stem}: {product.product_name!r} -> "
                    f"cat={product.product_category!r} sub={product.sub_category!r} "
                    f"ids={cat_ids},{sub_ids}"
                )


def audit_baserow() -> int:
    """Audit live productsDetails rows in Baserow."""
    settings = load_settings()
    client = BaserowClient(settings)
    lookup = CategoryLookup(client, settings)
    issues: list[tuple] = []

    cats = {
        r["id"]: r.get(settings.field_category_name)
        for r in client.list_table_rows(settings.category_table_id)
    }
    subs = {
        r["id"]: r.get(settings.field_subcategory_name)
        for r in client.list_table_rows(settings.subcategory_table_id)
    }

    for row in client.list_table_rows(settings.products_table_id):
        name = row.get(settings.field_product_name) or row.get(settings.field_product_url)
        src_c = row.get(settings.field_source_category) or ""
        src_s = row.get(settings.field_source_subcategory) or ""
        pc = [cats.get(x["id"]) for x in (row.get(settings.field_product_category) or [])]
        sc = [subs.get(x["id"]) for x in (row.get(settings.field_sub_category) or [])]
        if not pc:
            issues.append((row["id"], name, "no category link"))
        for x in row.get(settings.field_sub_category) or []:
            if subs.get(x["id"]) is None:
                issues.append((row["id"], name, "broken sub link"))
        print(f"[{row['id']}] {name} | src={src_c}/{src_s} | link={pc}/{sc}")

    print(f"\nISSUES: {len(issues)}")
    for item in issues:
        print(" | ".join(str(x) for x in item))
    return 1 if issues else 0

    print(f"\nISSUES (no category link): {len(issues)}")
    for item in issues:
        print(" | ".join(str(x) for x in item))
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
