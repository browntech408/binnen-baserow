"""Clear BinnenProductID + BinnenStatus for products synced from export batch."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

from config import load_settings

EXPORT_PATH = Path(r"c:\Users\Admin\Downloads\Get_Baserow_Products (1).json")
OUT_PATH = Path("output/reset_binnen_sync_report.txt")
TABLE_ID = 742
DRY_RUN = "--dry-run" in sys.argv


def load_export_ids() -> dict[int, str]:
    with EXPORT_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    products: dict[int, str] = {}
    for item in data:
        for row in item.get("json", {}).get("results", []):
            products[row["id"]] = row.get("product_name", "")
    return products


def find_synced_ids(settings, file_ids: set[int]) -> list[dict]:
    headers = {"Authorization": f"Token {settings.baserow_token}"}
    base = f"{settings.api_base}/database/rows/table/{TABLE_ID}"
    synced: list[dict] = []

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
                synced.append(
                    {
                        "id": rid,
                        "name": row.get("product_name", ""),
                        "BinnenProductID": row.get("BinnenProductID", ""),
                        "BinnenStatus": row.get("BinnenStatus") or "",
                    }
                )
        url = page.get("next")
    return sorted(synced, key=lambda x: x["id"])


def clear_row(settings, row_id: int) -> None:
    headers = {
        "Authorization": f"Token {settings.baserow_token}",
        "Content-Type": "application/json",
    }
    url = (
        f"{settings.api_base}/database/rows/table/{TABLE_ID}/"
        f"{row_id}/?user_field_names=true"
    )
    body = {"BinnenProductID": "", "BinnenStatus": ""}
    r = requests.patch(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()


def main() -> int:
    file_products = load_export_ids()
    file_ids = set(file_products.keys())
    settings = load_settings()
    synced = find_synced_ids(settings, file_ids)

    if not synced:
        print("No synced products from export file to reset.")
        return 0

    print(f"Found {len(synced)} products to reset in Baserow")
    if DRY_RUN:
        print("DRY RUN — no changes written")
        for p in synced[:10]:
            print(f"  would reset {p['id']} {p['name']}")
        if len(synced) > 10:
            print(f"  ... and {len(synced) - 10} more")
        return 0

    ok = 0
    failed: list[str] = []
    for p in synced:
        try:
            clear_row(settings, p["id"])
            ok += 1
            print(f"  reset {p['id']:>5}  {p['name']}")
            time.sleep(0.15)
        except requests.RequestException as exc:
            failed.append(f"{p['id']} {p['name']}: {exc}")

    lines = [
        "=" * 60,
        "  BASEROW RESET — Binnen Design sync fields cleared",
        "=" * 60,
        "",
        f"Export file products      : {len(file_products)}",
        f"Reset (cleared ID/status) : {ok}",
        f"Failed                    : {len(failed)}",
        "",
        "-" * 60,
        "  RESET PRODUCTS",
        "-" * 60,
    ]
    for p in synced:
        lines.append(f"{p['id']:>6}  {p['name']}")

    if failed:
        lines.extend(["", "FAILED:", *failed])

    report = "\n".join(lines)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(report + "\n", encoding="utf-8")
    print(f"\nDone. Reset {ok}/{len(synced)} rows.")
    print(f"Report: {OUT_PATH}")
    if failed:
        print(f"Failures: {len(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
