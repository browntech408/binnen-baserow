"""Inspect first 5 rows of Baserow table 742 (database 178)."""
from __future__ import annotations

import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

TABLE_ID = 742


def main() -> None:
    url = os.getenv("BASEROW_URL", "").rstrip("/")
    token = os.getenv("BASEROW_TOKEN", "").strip()
    headers = {"Authorization": f"Token {token}"}

    fr = requests.get(
        f"{url}/api/database/fields/table/{TABLE_ID}/",
        headers=headers,
        timeout=60,
    )
    fr.raise_for_status()
    fields = fr.json()
    fid_name = {f"field_{f['id']}": f.get("name", "") for f in fields}

    print("=== ALL FIELDS ===")
    for f in fields:
        print(f"  {f.get('name')!r} -> field_{f['id']} ({f.get('type')})")

    rr = requests.get(
        f"{url}/api/database/rows/table/{TABLE_ID}/",
        headers=headers,
        params={"size": 5, "page": 1},
        timeout=60,
    )
    rr.raise_for_status()
    rows = rr.json().get("results", [])
    print(f"\n=== FIRST {len(rows)} ROWS ===")

    for row in rows:
        print("\n" + "=" * 60)
        print(f"ROW ID: {row.get('id')}")
        for k, v in row.items():
            if k == "id" or not v:
                continue
            name = fid_name.get(k, k)
            low = name.lower()
            if any(x in low for x in ("name", "title", "brand", "url", "status")):
                print(f"  {name}: {str(v)[:150]}")

        for k, v in row.items():
            name = fid_name.get(k, k)
            if "desc" not in name.lower() or not v:
                continue
            text = str(v).strip()
            print(f"\n  --- {name} (len={len(text)}) ---")
            print(text[:1200])
            if len(text) > 1200:
                print("  ...[truncated]")


if __name__ == "__main__":
    main()
