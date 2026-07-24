"""Check which products from n8n Get_Baserow_Products export are now synced in Baserow."""
from __future__ import annotations

import json
from pathlib import Path

import requests

from config import load_settings

EXPORT_PATH = Path(r"c:\Users\Admin\Downloads\Get_Baserow_Products (1).json")
OUT_PATH = Path("output/synced_from_export.txt")


def main() -> int:
    with EXPORT_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    file_products: dict[int, str] = {}
    for item in data:
        for row in item.get("json", {}).get("results", []):
            file_products[row["id"]] = row.get("product_name", "")

    file_ids = set(file_products.keys())
    settings = load_settings()
    headers = {"Authorization": f"Token {settings.baserow_token}"}
    base = f"{settings.api_base}/database/rows/table/742"

    # Fetch current state for export IDs only (rows that now have BinnenProductID)
    current: dict[int, dict] = {}
    url = (
        f"{base}/?user_field_names=true&size=200"
        "&filter__stores__link_row_has=3"
        "&filter__BinnenProductID__not_empty"
    )
    while url:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        page = r.json()
        for row in page.get("results", []):
            rid = row["id"]
            if rid in file_ids:
                current[rid] = row
        url = page.get("next")

    synced = []
    for rid in sorted(current.keys()):
        row = current[rid]
        synced.append(
            {
                "id": rid,
                "name": row.get("product_name") or file_products[rid],
                "BinnenProductID": row.get("BinnenProductID", ""),
                "BinnenStatus": row.get("BinnenStatus") or "",
            }
        )

    synced_ids = set(current.keys())
    not_synced = [
        (rid, file_products[rid])
        for rid in sorted(file_products.keys())
        if rid not in synced_ids
    ]

    lines = [
        "=" * 60,
        "  SYNC CHECK — Get_Baserow_Products export vs Baserow now",
        "=" * 60,
        "",
        f"Products in export file     : {len(file_products)}",
        f"Now synced (BinnenProductID): {len(synced)}",
        f"Still not synced            : {len(not_synced)}",
        "",
        "-" * 60,
        f"  SYNCED ({len(synced)} products)",
        "-" * 60,
        f"{'ID':>6}  {'Status':<10}  Product",
        f"{'-'*6}  {'-'*10}  {'-'*40}",
    ]
    for p in synced:
        lines.append(f"{p['id']:>6}  {p['BinnenStatus']:<10}  {p['name']}")

    lines.extend(
        [
            "",
            "-" * 60,
            f"  NOT YET SYNCED ({len(not_synced)} products)",
            "-" * 60,
        ]
    )
    for row_id, name in not_synced[:50]:
        lines.append(f"{row_id:>6}  {name}")
    if len(not_synced) > 50:
        lines.append(f"  ... and {len(not_synced) - 50} more")

    report = "\n".join(lines)
    print(report)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(report + "\n", encoding="utf-8")
    print(f"\nSaved: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
