"""
Find ~30 products in Baserow Table 742 whose Detailed_image_gen (field_7401)
has more than 2 images, clone them, and link the copies to the Copy brand.
"""
from __future__ import annotations

import argparse
import sys

from baserow_client import BaserowClient
from config import load_settings

TABLE_ID = 742
FIELD_PRODUCT_NAME = "field_7347"
FIELD_DETAILED_IMAGE_GEN = "field_7401"
FIELD_BRAND_LINK = "field_7376"


def get_or_create_copy_brand(baserow, brand_table_id: int) -> int:
    print("Checking for 'Copy' brand in Brands table (%d)..." % brand_table_id)
    for row in baserow.list_table_rows(brand_table_id):
        if str(row.get("field_7446") or "").strip().lower() == "copy":
            brand_id = row["id"]
            print("Found existing 'Copy' brand (ID=%d)" % brand_id)
            return brand_id

    new_brand = baserow.create_row(
        brand_table_id, {"field_7446": "Copy", "field_7447": "Copy"}
    )
    brand_id = new_brand["id"]
    print("Created new 'Copy' brand (ID=%d)" % brand_id)
    return brand_id


def get_table_read_only_fields(baserow, table_id: int) -> set[str]:
    try:
        resp = baserow.session.get(
            baserow._url("/database/fields/table/%d/" % table_id), timeout=60
        )
        if resp.ok:
            return {
                "field_%s" % f["id"]
                for f in resp.json()
                if f.get("read_only", False)
            }
    except Exception as exc:
        print("WARN: could not load read-only fields: %s" % exc)
    return {"field_created_on", "field_updated_on", "id", "order"}


def _clean_field_value(value):
    """Normalize link / file / select field values for Baserow create_row."""
    # Single select: {"id": 3025, "value": "Populair", "color": "blue"}
    if isinstance(value, dict):
        if "name" in value and "url" in value:
            return {"name": value["name"]}
        if "id" in value:
            return value["id"]
        return value

    if not isinstance(value, list):
        return value

    cleaned = []
    for item in value:
        if isinstance(item, dict):
            if "name" in item and ("url" in item or "size" in item or "mime_type" in item):
                cleaned.append({"name": item["name"]})
            elif "id" in item:
                cleaned.append(item["id"])
            else:
                cleaned.append(item)
        else:
            cleaned.append(item)
    return cleaned


def clone_product_row(row: dict, copy_brand_id: int, baserow, read_only_fields: set) -> dict:
    orig_name = str(row.get(FIELD_PRODUCT_NAME) or ("Product %s" % row["id"]))
    new_name = "%s - COPY" % orig_name

    new_row_data = {}
    for k, v in row.items():
        if k.startswith("field_") and k not in read_only_fields:
            new_row_data[k] = _clean_field_value(v)

    new_row_data[FIELD_PRODUCT_NAME] = new_name
    new_row_data[FIELD_BRAND_LINK] = [copy_brand_id]
    # Keep Detailed_image_gen and other image fields as-is (already copied above).

    print("  Cloning: '%s' -> '%s' (Copy brand %d)..." % (orig_name, new_name, copy_brand_id))
    new_row = baserow.create_row(TABLE_ID, new_row_data)
    print("  Created copy row ID=%d" % new_row["id"])
    return new_row


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clone products with >2 Detailed_image_gen images onto Copy brand"
    )
    parser.add_argument("--limit", type=int, default=30, help="Max products to clone (default 30)")
    parser.add_argument(
        "--min-images",
        type=int,
        default=3,
        help="Minimum Detailed_image_gen count (default 3 = more than 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list matching products, do not create copies",
    )
    args = parser.parse_args()

    settings = load_settings()
    baserow = BaserowClient(settings)
    table_id = settings.products_table_id

    print("Scanning products table %d for Detailed_image_gen > %d images..." % (
        table_id, args.min_images - 1
    ))

    candidates = []
    scanned = 0
    for row in baserow.list_table_rows(table_id):
        scanned += 1
        name = str(row.get(FIELD_PRODUCT_NAME) or "")
        if " - COPY" in name.upper() or name.upper().endswith("- COPY"):
            continue

        gen_images = row.get(FIELD_DETAILED_IMAGE_GEN) or []
        if not isinstance(gen_images, list):
            continue
        if len(gen_images) < args.min_images:
            continue

        candidates.append(row)
        if len(candidates) >= args.limit:
            break

        if scanned % 500 == 0:
            print("  ...scanned %d rows, found %d candidates" % (scanned, len(candidates)))

    print(
        "Scanned %d rows. Found %d products with Detailed_image_gen >= %d "
        "(taking up to %d)."
        % (scanned, len(candidates), args.min_images, args.limit)
    )

    if not candidates:
        print("No matching products found.")
        return 1

    for i, row in enumerate(candidates, 1):
        name = row.get(FIELD_PRODUCT_NAME) or ("#%s" % row["id"])
        n_img = len(row.get(FIELD_DETAILED_IMAGE_GEN) or [])
        print("[%d] id=%d | %d gen images | %s" % (i, row["id"], n_img, name))

    if args.dry_run:
        print("Dry-run only — no rows created.")
        return 0

    read_only = get_table_read_only_fields(baserow, table_id)
    copy_brand_id = get_or_create_copy_brand(baserow, settings.brands_table_id)

    created = []
    errors = []
    for i, row in enumerate(candidates, 1):
        print("\n[%d/%d]" % (i, len(candidates)))
        try:
            new_row = clone_product_row(row, copy_brand_id, baserow, read_only)
            created.append(
                {
                    "source_id": row["id"],
                    "copy_id": new_row["id"],
                    "name": new_row.get(FIELD_PRODUCT_NAME),
                    "gen_images": len(row.get(FIELD_DETAILED_IMAGE_GEN) or []),
                }
            )
        except Exception as exc:
            print("  ERROR cloning row %d: %s" % (row["id"], exc))
            errors.append({"source_id": row["id"], "error": str(exc)})

    print("\n=== DONE ===")
    print("Created %d copy products linked to Copy brand (ID=%d)" % (
        len(created), copy_brand_id
    ))
    for item in created:
        print(
            "  source=%d -> copy=%d | %d gen imgs | %s"
            % (item["source_id"], item["copy_id"], item["gen_images"], item["name"])
        )
    if errors:
        print("%d errors:" % len(errors))
        for err in errors:
            print("  source=%d: %s" % (err["source_id"], err["error"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
