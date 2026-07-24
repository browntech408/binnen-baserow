"""
For products of a brand with empty hero_images, copy the last product_images
file into hero_images.

  python fill_empty_hero_from_last_product_image.py --brand-id 19 --dry-run
  python fill_empty_hero_from_last_product_image.py --brand-id 19 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from baserow_client import BaserowClient
from config import load_settings

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def _files(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


def _belongs(row: dict, brand_id: int, settings) -> bool:
    for link in row.get(settings.field_brand_link) or []:
        if isinstance(link, dict) and int(link.get("id") or 0) == brand_id:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=OUTPUT_DIR / "fill_empty_hero_from_last_product_image_report.json",
    )
    args = parser.parse_args()

    apply = bool(args.apply)
    if not apply and not args.dry_run:
        print("Defaulting to --dry-run (pass --apply to update Baserow).")

    settings = load_settings()
    client = BaserowClient(settings)
    brand = client.get_row(settings.brands_table_id, args.brand_id)
    brand_name = str(brand.get(settings.field_brand_name) or args.brand_id)

    hero_field = settings.field_hero_images
    product_field = settings.field_product_images

    summary = {
        "brand_id": args.brand_id,
        "brand": brand_name,
        "apply": apply,
        "scanned": 0,
        "already_has_hero": 0,
        "empty_hero": 0,
        "filled": 0,
        "skipped_no_product_images": 0,
        "rows": [],
    }

    print(f"Brand: {brand_name} (id={args.brand_id})")
    print(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
    print()

    for row in client.list_table_rows(settings.products_table_id):
        if not _belongs(row, args.brand_id, settings):
            continue
        summary["scanned"] += 1
        row_id = int(row["id"])
        name = str(row.get(settings.field_product_name) or "")
        hero = _files(row.get(hero_field))
        products = _files(row.get(product_field))

        if hero:
            summary["already_has_hero"] += 1
            continue

        summary["empty_hero"] += 1
        if not products:
            summary["skipped_no_product_images"] += 1
            print(f"[skip] row {row_id}: {name} - no product_images")
            continue

        last = products[-1]
        file_name = str(last.get("name") or "").strip()
        if not file_name:
            summary["skipped_no_product_images"] += 1
            print(f"[skip] row {row_id}: {name} - last product image has no name")
            continue

        entry = {
            "row_id": row_id,
            "product_name": name,
            "product_images_count": len(products),
            "last_image_name": file_name,
            "last_image_url": last.get("url"),
        }
        summary["rows"].append(entry)

        action = "would set hero" if not apply else "set hero"
        print(
            f"[{action}] row {row_id}: {name} - "
            f"last of {len(products)} product_images -> hero"
        )

        if apply:
            client.update_row(
                settings.products_table_id,
                row_id,
                {hero_field: [{"name": file_name}]},
            )
            summary["filled"] += 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print()
    print(
        f"Scanned: {summary['scanned']}, already hero: {summary['already_has_hero']}, "
        f"empty: {summary['empty_hero']}, filled: {summary['filled']}, "
        f"skipped no product imgs: {summary['skipped_no_product_images']}"
    )
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
