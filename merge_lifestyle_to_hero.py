"""
Merge Baserow lifestyle_images into hero_images (keep existing hero, append lifestyle).

Default brand: Janssens Oriënt (CarpetRebel scrape).

  python merge_lifestyle_to_hero.py --dry-run --brand "Janssens Oriënt"
  python merge_lifestyle_to_hero.py --apply --brand "Janssens Oriënt"
  python merge_lifestyle_to_hero.py --apply --brand-id 21
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from baserow_client import BaserowClient
from config import load_settings
from product_baserow import find_brand_row_id

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def _normalize_file_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _file_key(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    if name:
        return name.lower()
    url = str(item.get("url") or "").strip()
    if url:
        return urlparse(url).path.rstrip("/").lower()
    visible = str(item.get("visible_name") or "").strip()
    return visible.lower()


def _merge_lifestyle_into_hero(
    hero_files: list[dict[str, Any]],
    lifestyle_files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return (new_hero, new_lifestyle, moved_count)."""
    if not lifestyle_files:
        return hero_files, lifestyle_files, 0

    seen = {_file_key(img) for img in hero_files if _file_key(img)}
    merged = list(hero_files)
    moved = 0

    for img in lifestyle_files:
        key = _file_key(img)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(img)
        moved += 1

    return merged, [], moved


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
    brand_id: int,
    brand_name: str,
    apply: bool,
) -> dict[str, Any]:
    hero_field = settings.field_hero_images
    lifestyle_field = settings.field_lifestyle_images
    if not hero_field or not lifestyle_field:
        raise ValueError("FIELD_HERO_IMAGES and FIELD_LIFESTYLE_IMAGES must be set.")

    summary: dict[str, Any] = {
        "brand": brand_name,
        "brand_id": brand_id,
        "apply": apply,
        "products_scanned": 0,
        "products_with_lifestyle": 0,
        "products_updated": 0,
        "images_moved_to_hero": 0,
        "rows": [],
    }

    for row in client.list_table_rows(settings.products_table_id):
        if not _row_belongs_to_brand(row, brand_id, settings):
            continue

        summary["products_scanned"] += 1
        row_id = int(row["id"])
        product_name = str(row.get(settings.field_product_name) or "")

        hero_files = _normalize_file_list(row.get(hero_field))
        lifestyle_files = _normalize_file_list(row.get(lifestyle_field))
        if not lifestyle_files:
            continue

        summary["products_with_lifestyle"] += 1
        new_hero, new_lifestyle, moved = _merge_lifestyle_into_hero(
            hero_files, lifestyle_files
        )
        if moved == 0 and len(new_lifestyle) == len(lifestyle_files):
            continue

        summary["images_moved_to_hero"] += moved
        entry = {
            "row_id": row_id,
            "product_name": product_name,
            "product_url": row.get(settings.field_product_url),
            "hero_before": len(hero_files),
            "hero_after": len(new_hero),
            "lifestyle_before": len(lifestyle_files),
            "lifestyle_after": len(new_lifestyle),
            "moved": moved,
        }
        summary["rows"].append(entry)

        action = "would update" if not apply else "updated"
        print(
            f"[{action}] row {row_id}: {product_name} - "
            f"hero {len(hero_files)}->{len(new_hero)}, "
            f"lifestyle {len(lifestyle_files)}->{len(new_lifestyle)} "
            f"(+{moved} to hero)"
        )

        if apply:
            client.update_row(
                settings.products_table_id,
                row_id,
                {
                    hero_field: new_hero,
                    lifestyle_field: new_lifestyle,
                },
            )
            summary["products_updated"] += 1

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move lifestyle_images into hero_images in Baserow."
    )
    parser.add_argument(
        "--brand",
        default="Janssens Oriënt",
        help='Brand name in Baserow (default: "Janssens Oriënt")',
    )
    parser.add_argument("--brand-id", type=int, help="Brand row id (table 805).")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=OUTPUT_DIR / "merge_lifestyle_to_hero_report.json",
    )
    args = parser.parse_args()

    apply = bool(args.apply)
    if not apply and not args.dry_run:
        print("Defaulting to --dry-run (pass --apply to update Baserow).")

    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    client = BaserowClient(settings)
    brand_id = args.brand_id
    if brand_id is None:
        brand_id = find_brand_row_id(client, settings, args.brand)
    if brand_id is None:
        print(f"Brand not found: {args.brand!r}", file=sys.stderr)
        return 1

    print(f"Brand: {args.brand} (id={brand_id})")
    print(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
    print()

    summary = process_brand(
        client,
        settings,
        brand_id=brand_id,
        brand_name=args.brand,
        apply=apply,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print()
    print(
        f"Scanned: {summary['products_scanned']}, "
        f"with lifestyle: {summary['products_with_lifestyle']}, "
        f"updated: {summary['products_updated']}, "
        f"images moved: {summary['images_moved_to_hero']}"
    )
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
