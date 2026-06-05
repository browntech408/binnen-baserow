"""
Delete ALL rows from the productsDetails table (PRODUCTS_TABLE_ID, default 802).

DESTRUCTIVE — requires --confirm.

Usage:
  python clear_products_table.py              # dry-run: show count only
  python clear_products_table.py --confirm    # delete all product rows
  python clear_products_table.py --confirm --clear-brand-links  # also empty brands.productsDetails links
"""
from __future__ import annotations

import argparse
import sys

from baserow_client import BaserowClient
from config import load_settings

BATCH_SIZE = 200


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Empty productsDetails table in Baserow (all rows deleted)."
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete rows. Without this flag, only shows how many rows would be deleted.",
    )
    p.add_argument(
        "--clear-brand-links",
        action="store_true",
        help="After delete, clear productsDetails link field on all brand rows (table 805).",
    )
    p.add_argument(
        "--table-id",
        type=int,
        default=0,
        help="Override PRODUCTS_TABLE_ID (default: from .env)",
    )
    return p.parse_args()


def collect_row_ids(client: BaserowClient, table_id: int) -> list[int]:
    return [row["id"] for row in client.list_table_rows(table_id)]


def delete_all_rows(client: BaserowClient, table_id: int, row_ids: list[int]) -> int:
    deleted = 0
    for i in range(0, len(row_ids), BATCH_SIZE):
        chunk = row_ids[i : i + BATCH_SIZE]
        client.batch_delete_rows(table_id, chunk)
        deleted += len(chunk)
        print(f"  Deleted {deleted}/{len(row_ids)} rows...")
    return deleted


def clear_brand_product_links(client: BaserowClient, settings) -> int:
    field = settings.field_products
    if not field:
        print("FIELD_PRODUCTS not set — skipping brand link cleanup.")
        return 0

    cleared = 0
    for row in client.list_table_rows(settings.brands_table_id):
        linked = row.get(field) or []
        if not linked:
            continue
        client.update_row(settings.brands_table_id, row["id"], {field: []})
        cleared += 1
    return cleared


def main() -> int:
    args = parse_args()
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    table_id = args.table_id or settings.products_table_id
    client = BaserowClient(settings)

    # Safety: must be productsDetails (has product_name field)
    fields = client.get_table_fields(table_id)
    names = {f.get("name") for f in fields}
    if "product_name" not in names:
        print(
            f"Table {table_id} does not look like productsDetails "
            f"(columns: {sorted(names)}). Aborting.",
            file=sys.stderr,
        )
        return 1

    print(f"Baserow: {settings.baserow_url}")
    print(f"Table:  productsDetails (id {table_id})")
    print()

    row_ids = collect_row_ids(client, table_id)
    count = len(row_ids)

    if count == 0:
        print("Table is already empty.")
        return 0

    if not args.confirm:
        print(f"DRY RUN — would delete {count} row(s).")
        print("Sample row IDs:", row_ids[:10], "..." if count > 10 else "")
        print()
        print("To delete everything, run:")
        print("  python clear_products_table.py --confirm")
        return 0

    print(f"Deleting {count} row(s) from table {table_id}...")
    try:
        deleted = delete_all_rows(client, table_id, row_ids)
    except Exception as exc:
        print(f"Delete failed: {exc}", file=sys.stderr)
        print(
            "Check API token has Delete permission on productsDetails table.",
            file=sys.stderr,
        )
        return 1

    print(f"Done. Deleted {deleted} row(s).")

    remaining = sum(1 for _ in client.list_table_rows(table_id))
    print(f"Remaining rows: {remaining}")

    if args.clear_brand_links:
        print("Clearing productsDetails links on brands table...")
        n = clear_brand_product_links(client, settings)
        print(f"Cleared links on {n} brand row(s).")

    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
