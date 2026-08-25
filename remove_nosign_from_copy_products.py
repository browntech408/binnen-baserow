"""
Remove placeholder images named like no-sign-png-... from all image columns
on products linked to the Copy brand.
"""
from __future__ import annotations

import argparse
import sys

from baserow_client import BaserowClient
from config import load_settings

TABLE_ID = 742
FIELD_PRODUCT_NAME = "field_7347"
FIELD_BRAND_LINK = "field_7376"
COPY_BRAND_ID = 39

# All file/image columns on products table
IMAGE_FIELDS = [
    ("field_7349", "product_images"),
    ("field_7350", "3d_models"),
    ("field_7351", "cinematic_video"),
    ("field_7355", "designerImage"),
    ("field_7358", "hero_images"),
    ("field_7359", "lifestyle_images"),
    ("field_7360", "detail_image"),
    ("field_7400", "bg_removed_hero"),
    ("field_7401", "Detailed_image_gen"),
    ("field_7402", "final_hero_image"),
    ("field_7403", "final_lifestyle_image"),
    ("field_7424", "qr_code"),
]

TARGET_NEEDLE = "no-sign-png-11553977065yhsowi0nim.png"


def _file_blob(item: dict) -> str:
    parts = [
        str(item.get("name") or ""),
        str(item.get("visible_name") or ""),
        str(item.get("url") or ""),
        str(item.get("original_name") or ""),
    ]
    return " ".join(parts).lower()


def _is_target_image(item: dict, needle: str) -> bool:
    return needle.lower() in _file_blob(item)


def _is_copy_brand(row: dict, brand_id: int) -> bool:
    links = row.get(FIELD_BRAND_LINK) or []
    if not isinstance(links, list):
        return False
    for link in links:
        if isinstance(link, dict) and link.get("id") == brand_id:
            return True
        if link == brand_id:
            return True
    return False


def _keep_files(files: list, needle: str) -> tuple[list, int]:
    """Return (kept file refs for API, removed_count)."""
    kept = []
    removed = 0
    for item in files or []:
        if isinstance(item, dict) and _is_target_image(item, needle):
            removed += 1
            continue
        if isinstance(item, dict) and item.get("name"):
            kept.append({"name": item["name"]})
        else:
            kept.append(item)
    return kept, removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--needle",
        default=TARGET_NEEDLE,
        help="Filename substring to remove",
    )
    parser.add_argument("--brand-id", type=int, default=COPY_BRAND_ID)
    args = parser.parse_args()

    settings = load_settings()
    baserow = BaserowClient(settings)
    table_id = settings.products_table_id
    needle = args.needle

    print("Scanning Copy brand products (brand_id=%d) for '%s'..." % (args.brand_id, needle))

    copy_products = []
    scanned = 0
    for row in baserow.list_table_rows(table_id):
        scanned += 1
        if _is_copy_brand(row, args.brand_id):
            copy_products.append(row)
        if scanned % 1000 == 0:
            print("  ...scanned %d, copy products so far: %d" % (scanned, len(copy_products)))

    print("Scanned %d rows. Copy brand products: %d" % (scanned, len(copy_products)))

    total_removed = 0
    updated_rows = 0
    touched = []

    for row in copy_products:
        row_id = row["id"]
        name = row.get(FIELD_PRODUCT_NAME) or ("#%s" % row_id)
        patch = {}
        row_removed = 0
        details = []

        for field_key, field_label in IMAGE_FIELDS:
            files = row.get(field_key) or []
            if not files:
                continue
            kept, removed = _keep_files(files, needle)
            if removed:
                patch[field_key] = kept
                row_removed += removed
                details.append("%s:-%d" % (field_label, removed))

        if not patch:
            continue

        print(
            "row %d | %s | remove %d | %s"
            % (row_id, name, row_removed, ", ".join(details))
        )
        touched.append((row_id, name, row_removed, details))
        total_removed += row_removed

        if not args.dry_run:
            baserow.update_row(table_id, row_id, patch)
            updated_rows += 1

    print("\n=== DONE ===")
    print("Products with target image: %d" % len(touched))
    print("Total images removed: %d" % total_removed)
    if args.dry_run:
        print("Dry-run only — no updates written.")
    else:
        print("Rows updated: %d" % updated_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
