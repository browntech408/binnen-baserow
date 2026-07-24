"""
Remove Baserow file images whose name contains a junk pattern (Gealux default).

Scans product_images, hero_images, lifestyle_images, detail_image on all
products linked to a brand (default: Gealux).

  python gealux_remove_tmp_images.py --dry-run
  python gealux_remove_tmp_images.py --apply
  python gealux_remove_tmp_images.py --apply --pattern tmpe9f360wp --brand Gealux
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from baserow_client import BaserowClient
from config import load_settings
from product_baserow import find_brand_row_id

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

IMAGE_FIELD_KEYS = (
    "field_product_images",
    "field_hero_images",
    "field_lifestyle_images",
    "field_detail_image",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Remove matching junk images from Baserow product file fields."
    )
    p.add_argument("--brand", default="Gealux", help="Brand name in Baserow (default: Gealux)")
    p.add_argument(
        "--pattern",
        default="tmpe9f360wp",
        help='Substring to match in image file name (default: tmpe9f360wp)',
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only; do not update Baserow (default)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Update Baserow rows after removing matched images",
    )
    p.add_argument(
        "--report",
        default=str(OUTPUT_DIR / "gealux_remove_tmp_images_report.json"),
        help="JSON report path",
    )
    return p.parse_args()


def _normalize_file_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _filter_files(
    files: list[dict[str, Any]], pattern: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (kept, removed) file field objects."""
    pat = pattern.lower()
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for item in files:
        name = str(item.get("name") or "")
        visible = str(item.get("visible_name") or "")
        url = str(item.get("url") or "")
        blob = f"{name} {visible} {url}".lower()
        if pat in blob:
            removed.append(item)
        else:
            kept.append(item)
    return kept, removed


def _row_belongs_to_brand(row: dict[str, Any], brand_id: int, settings) -> bool:
    links = row.get(settings.field_brand_link) or []
    for link in links:
        if isinstance(link, dict) and int(link.get("id") or 0) == brand_id:
            return True
    return False


def process_brand(
    client: BaserowClient,
    settings,
    *,
    brand_name: str,
    pattern: str,
    apply: bool,
) -> dict[str, Any]:
    brand_id = find_brand_row_id(client, settings, brand_name)
    if brand_id is None:
        raise ValueError(f"Brand not found in Baserow: {brand_name!r}")

    field_map = {
        key: getattr(settings, key)
        for key in IMAGE_FIELD_KEYS
        if getattr(settings, key, "")
    }

    summary: dict[str, Any] = {
        "brand": brand_name,
        "brand_id": brand_id,
        "pattern": pattern,
        "apply": apply,
        "products_scanned": 0,
        "products_updated": 0,
        "images_removed": 0,
        "rows": [],
    }

    for row in client.list_table_rows(settings.products_table_id):
        if not _row_belongs_to_brand(row, brand_id, settings):
            continue

        summary["products_scanned"] += 1
        row_id = int(row["id"])
        product_name = str(row.get(settings.field_product_name) or "")
        updates: dict[str, list[dict[str, Any]]] = {}
        row_removed = 0
        row_details: list[dict[str, Any]] = []

        for key, field_name in field_map.items():
            original = _normalize_file_list(row.get(field_name))
            kept, removed = _filter_files(original, pattern)
            if not removed:
                continue
            updates[field_name] = kept
            row_removed += len(removed)
            row_details.append(
                {
                    "field": key,
                    "baserow_field": field_name,
                    "removed_count": len(removed),
                    "removed_names": [
                        str(x.get("visible_name") or x.get("name") or "")
                        for x in removed
                    ],
                }
            )

        if not updates:
            continue

        summary["images_removed"] += row_removed
        entry = {
            "row_id": row_id,
            "product_name": product_name,
            "product_url": row.get(settings.field_product_url),
            "images_removed": row_removed,
            "fields": row_details,
        }
        summary["rows"].append(entry)

        action = "would update" if not apply else "updated"
        print(
            f"[{action}] row {row_id}: {product_name} — "
            f"remove {row_removed} image(s)"
        )
        for detail in row_details:
            for name in detail["removed_names"]:
                print(f"    {detail['field']}: {name}")

        if apply:
            client.update_row(settings.products_table_id, row_id, updates)
            summary["products_updated"] += 1

    return summary


def main() -> int:
    args = parse_args()
    apply = bool(args.apply)
    if not apply and not args.dry_run:
        print("Defaulting to --dry-run (pass --apply to update Baserow).")

    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    client = BaserowClient(settings)
    try:
        summary = process_brand(
            client,
            settings,
            brand_name=args.brand,
            pattern=args.pattern,
            apply=apply,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"Scanned:  {summary['products_scanned']} {args.brand} product(s)")
    print(f"Matched:  {len(summary['rows'])} product(s)")
    print(f"Removed:  {summary['images_removed']} image(s)")
    if apply:
        print(f"Updated:  {summary['products_updated']} row(s)")
    print(f"Report:   {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
