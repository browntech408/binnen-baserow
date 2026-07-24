"""Generate brand-wise product count report from Baserow productsDetails."""
from __future__ import annotations

import sys
from collections import Counter

from baserow_client import BaserowClient
from config import load_settings

PRODUCTS_TABLE_ID = 742  # database 178 — productsDetails
STORE_REPORTS = ("Binnen Design", "WoonBloq")


def field_key(fields: list[dict], display_name: str) -> str | None:
    for f in fields:
        if f.get("name", "").strip().lower() == display_name.strip().lower():
            return f"field_{f['id']}"
    return None


def link_value_names(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v.get("value", "")).strip() for v in value if v and v.get("value")]
    return [str(value).strip()]


def row_has_store(row: dict, stores_key: str | None, store_name: str) -> bool:
    if not stores_key:
        return False
    store_names = link_value_names(row.get(stores_key))
    needle = store_name.lower()
    return any(needle in s.lower() for s in store_names)


def count_brands(rows: list[dict], brand_key: str) -> tuple[Counter[str], int, int]:
    brand_counts: Counter[str] = Counter()
    no_brand = 0
    for row in rows:
        brands = link_value_names(row.get(brand_key))
        if not brands:
            no_brand += 1
            brand_counts["(No Brand Linked)"] += 1
        else:
            for b in brands:
                brand_counts[b] += 1
    return brand_counts, len(rows), no_brand


def format_report(
    *,
    title_store: str,
    total: int,
    brand_linked: int,
    no_brand: int,
    brand_counts: Counter[str],
    shopify_label: str = "Exist in shopify",
) -> str:
    lines = [
        "============================================",
        "       PRODUCT & BRAND SUMMARY REPORT",
        "============================================",
        "",
        f"Store / Scope           : {title_store}",
        f"Total Products          : {total:,}",
        f"Brand Linked            : {brand_linked:,}",
        f"Product Without Brand   : {no_brand:,}",
        "",
        "--------------------------------------------",
        "         BRAND-WISE PRODUCT COUNT",
        "--------------------------------------------",
        "",
        f"{'Brand':<26}  {'Products':>8}      {shopify_label:>16}",
        f"{'-'*26}  {'-'*8}      {'-'*16}",
    ]

    for brand, count in brand_counts.most_common():
        shopify = "" if brand == "(No Brand Linked)" else "00"
        lines.append(f"{brand:<26}  {count:>8,}         {shopify:>2}")

    lines.extend([
        "",
        "--------------------------------------------",
        f"TOTAL                       {total:,}",
        "============================================",
    ])
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    client = BaserowClient(settings)
    fields = client.get_table_fields(PRODUCTS_TABLE_ID)

    brand_key = field_key(fields, "Brand_table")
    stores_key = field_key(fields, "stores")
    if not brand_key:
        print("ERROR: Brand_table field not found.", file=sys.stderr)
        return 1

    all_rows: list[dict] = []
    store_rows: dict[str, list[dict]] = {name: [] for name in STORE_REPORTS}

    for row in client.list_table_rows(PRODUCTS_TABLE_ID):
        all_rows.append(row)
        for store_name in STORE_REPORTS:
            if row_has_store(row, stores_key, store_name):
                store_rows[store_name].append(row)

    all_brands, all_total, all_no_brand = count_brands(all_rows, brand_key)

    sections = [
        format_report(
            title_store="All products (Baserow table 742)",
            total=all_total,
            brand_linked=all_total - all_no_brand,
            no_brand=all_no_brand,
            brand_counts=all_brands,
            shopify_label="Exist in shopify",
        )
    ]

    for store_name in STORE_REPORTS:
        rows = store_rows[store_name]
        brands, total, no_brand = count_brands(rows, brand_key)
        sections.append(
            format_report(
                title_store=f"{store_name} store",
                total=total,
                brand_linked=total - no_brand,
                no_brand=no_brand,
                brand_counts=brands,
                shopify_label="Exist in shopify",
            )
        )

    combined = "\n\n\n".join(sections)
    print(combined)

    out_path = "output/Brands_products_data.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(combined + "\n")
    print(f"\nSaved: {out_path}")
    for store_name in STORE_REPORTS:
        print(f"{store_name} store linked products: {len(store_rows[store_name]):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
