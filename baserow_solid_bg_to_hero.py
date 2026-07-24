"""
Move solid-color-background images into Baserow hero_images for a brand.

Checks product / lifestyle / hero images. Any flat studio backdrop
(white, grey, beige, black, etc.) goes to hero_images; the rest stay in
lifestyle_images. product_images is left unchanged.

Examples:
  python baserow_solid_bg_to_hero.py --brand-id 15 --dry-run
  python baserow_solid_bg_to_hero.py --brand-id 15 --apply
  python baserow_solid_bg_to_hero.py --brand-id 15 --clear-hero --apply
  python baserow_solid_bg_to_hero.py --brand "Pastoe" --clear-hero --apply
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
from scrapers.image_bg import classify_solid_background_url

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


def _file_url(item: dict[str, Any]) -> str:
    return str(item.get("url") or "").strip()


def _baserow_file_ref(item: dict[str, Any]) -> dict[str, str]:
    """Baserow file field write format."""
    name = str(item.get("name") or "").strip()
    if not name:
        raise ValueError(f"File entry missing name: {item!r}")
    return {"name": name}


def _row_belongs_to_brand(row: dict[str, Any], brand_id: int, settings) -> bool:
    links = row.get(settings.field_brand_link) or []
    for link in links:
        if isinstance(link, dict) and int(link.get("id") or 0) == brand_id:
            return True
    return False


def _brand_name(client: BaserowClient, settings, brand_id: int) -> str:
    row = client.get_row(settings.brands_table_id, brand_id)
    return str(row.get(settings.field_brand_name) or f"brand-{brand_id}")


def _collect_unique_files(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        for item in group:
            key = _file_key(item)
            if not key or key in seen:
                continue
            if not _file_url(item):
                continue
            seen.add(key)
            out.append(item)
    return out


def clear_hero_for_brand(
    client: BaserowClient,
    settings,
    *,
    brand_id: int,
    apply: bool,
) -> int:
    hero_field = settings.field_hero_images
    cleared = 0
    for row in client.list_table_rows(settings.products_table_id):
        if not _row_belongs_to_brand(row, brand_id, settings):
            continue
        hero = _normalize_file_list(row.get(hero_field))
        if not hero:
            continue
        row_id = int(row["id"])
        name = str(row.get(settings.field_product_name) or "")
        action = "would clear hero" if not apply else "cleared hero"
        print(f"[{action}] row {row_id}: {name} ({len(hero)} images)")
        if apply:
            client.update_row(settings.products_table_id, row_id, {hero_field: []})
        cleared += 1
    return cleared


def classify_row_images(
    row: dict[str, Any],
    settings,
    *,
    timeout: float,
    include_existing_hero: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    hero_field = settings.field_hero_images
    lifestyle_field = settings.field_lifestyle_images
    product_field = settings.field_product_images

    product_files = _normalize_file_list(row.get(product_field))
    lifestyle_files = _normalize_file_list(row.get(lifestyle_field))
    hero_files = _normalize_file_list(row.get(hero_field)) if include_existing_hero else []

    candidates = _collect_unique_files(product_files, lifestyle_files, hero_files)
    solid: list[dict[str, Any]] = []
    not_solid: list[dict[str, Any]] = []
    unknown: list[str] = []

    for item in candidates:
        url = _file_url(item)
        result = classify_solid_background_url(url, timeout)
        if result is True:
            solid.append(item)
        elif result is False:
            not_solid.append(item)
        else:
            unknown.append(url)
            # keep unknowns out of hero — safer as lifestyle
            not_solid.append(item)

    stats = {
        "candidates": len(candidates),
        "solid": len(solid),
        "not_solid": len(not_solid),
        "unknown": len(unknown),
        "solid_urls": [_file_url(x) for x in solid],
        "unknown_urls": unknown,
    }
    return solid, not_solid, stats


def process_brand(
    client: BaserowClient,
    settings,
    *,
    brand_id: int,
    brand_name: str,
    apply: bool,
    clear_hero: bool,
    timeout: float,
) -> dict[str, Any]:
    hero_field = settings.field_hero_images
    lifestyle_field = settings.field_lifestyle_images
    if not hero_field or not lifestyle_field:
        raise ValueError("FIELD_HERO_IMAGES and FIELD_LIFESTYLE_IMAGES must be set.")

    summary: dict[str, Any] = {
        "brand": brand_name,
        "brand_id": brand_id,
        "apply": apply,
        "clear_hero": clear_hero,
        "products_scanned": 0,
        "products_updated": 0,
        "hero_cleared_rows": 0,
        "hero_images_set": 0,
        "rows": [],
    }

    if clear_hero:
        print("--- CLEAR HERO ---")
        summary["hero_cleared_rows"] = clear_hero_for_brand(
            client, settings, brand_id=brand_id, apply=apply
        )
        print()

    print("--- CLASSIFY SOLID BG → HERO ---")
    # After clear+apply, re-list so hero is empty. For dry-run with clear-hero,
    # still ignore existing hero when classifying.
    include_existing_hero = not clear_hero

    for row in client.list_table_rows(settings.products_table_id):
        if not _row_belongs_to_brand(row, brand_id, settings):
            continue

        summary["products_scanned"] += 1
        row_id = int(row["id"])
        product_name = str(row.get(settings.field_product_name) or "")

        current_hero = _normalize_file_list(row.get(hero_field))
        current_lifestyle = _normalize_file_list(row.get(lifestyle_field))

        solid, not_solid, stats = classify_row_images(
            row,
            settings,
            timeout=timeout,
            include_existing_hero=include_existing_hero,
        )

        new_hero = [_baserow_file_ref(x) for x in solid]
        new_lifestyle = [_baserow_file_ref(x) for x in not_solid]

        # Skip no-op updates
        current_hero_keys = {_file_key(x) for x in current_hero}
        current_life_keys = {_file_key(x) for x in current_lifestyle}
        new_hero_keys = {_file_key(x) for x in solid}
        new_life_keys = {_file_key(x) for x in not_solid}
        changed = current_hero_keys != new_hero_keys or current_life_keys != new_life_keys

        entry = {
            "row_id": row_id,
            "product_name": product_name,
            "hero_before": len(current_hero),
            "hero_after": len(solid),
            "lifestyle_before": len(current_lifestyle),
            "lifestyle_after": len(not_solid),
            "changed": changed,
            **stats,
        }
        summary["rows"].append(entry)

        if not changed:
            print(
                f"[skip] row {row_id}: {product_name} "
                f"(hero={len(solid)}, lifestyle={len(not_solid)})"
            )
            continue

        action = "would update" if not apply else "updated"
        print(
            f"[{action}] row {row_id}: {product_name} - "
            f"hero {len(current_hero)}->{len(solid)}, "
            f"lifestyle {len(current_lifestyle)}->{len(not_solid)} "
            f"(solid={stats['solid']}, unknown={stats['unknown']})"
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
            summary["hero_images_set"] += len(solid)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Put solid-color-background images into Baserow hero_images "
            "for one brand (white or any flat studio color)."
        )
    )
    parser.add_argument("--brand-id", type=int, help="Baserow brand row id.")
    parser.add_argument("--brand", help="Brand name (if --brand-id not given).")
    parser.add_argument(
        "--clear-hero",
        action="store_true",
        help="First empty hero_images for all products of this brand, then reclassify.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="HTTP timeout per image download (default 45).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=OUTPUT_DIR / "baserow_solid_bg_to_hero_report.json",
    )
    args = parser.parse_args()

    if not args.brand_id and not args.brand:
        print("Pass --brand-id or --brand", file=sys.stderr)
        return 1

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
    brand_name = args.brand or ""
    if brand_id is None:
        brand_id = find_brand_row_id(client, settings, args.brand)
        if brand_id is None:
            print(f"Brand not found: {args.brand!r}", file=sys.stderr)
            return 1
        brand_name = args.brand
    else:
        brand_name = brand_name or _brand_name(client, settings, brand_id)

    print(f"Brand: {brand_name} (id={brand_id})")
    print(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
    print(f"Clear hero first: {bool(args.clear_hero)}")
    print()

    summary = process_brand(
        client,
        settings,
        brand_id=brand_id,
        brand_name=brand_name,
        apply=apply,
        clear_hero=bool(args.clear_hero),
        timeout=args.timeout,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    # Drop long URL lists from disk report size? keep them — useful.
    args.report.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print()
    print(
        f"Scanned: {summary['products_scanned']}, "
        f"hero cleared rows: {summary['hero_cleared_rows']}, "
        f"updated: {summary['products_updated']}, "
        f"hero images set: {summary['hero_images_set']}"
    )
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
