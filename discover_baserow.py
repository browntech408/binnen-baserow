"""
Helper: list fields on your brands table so you can copy names into .env
Run after BASEROW_URL and BASEROW_TOKEN are set in .env
"""
from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    url = os.getenv("BASEROW_URL", "").strip().rstrip("/")
    token = os.getenv("BASEROW_TOKEN", "").strip()
    table_id = os.getenv("BRANDS_TABLE_ID", "").strip()

    if not url or not token:
        print("Set BASEROW_URL and BASEROW_TOKEN in .env first.", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Token {token}"}

    if not table_id:
        print("BRANDS_TABLE_ID not set. Fetching first page of all tables is not")
        print("available with database token only on all instances.")
        print("\nTo find table ID manually:")
        print("  1. Open your brands table in the browser")
        print("  2. URL often contains: .../table/123/...  -> use 123 as BRANDS_TABLE_ID")
        return 1

    # Quick token check (works even if table ID is wrong)
    check = requests.get(
        f"{url}/api/database/tokens/check/",
        headers=headers,
        timeout=60,
    )
    if check.status_code == 401:
        print("Token rejected. Create a Database token in Baserow (not your login password).")
        return 1
    if check.status_code == 200:
        print("Token: OK (valid database token)\n")

    print(f"Table ID: {table_id}\nFields:")
    fields_url = f"{url}/api/database/fields/table/{table_id}/"
    response = requests.get(fields_url, headers=headers, timeout=60)
    if response.status_code == 401:
        try:
            detail = response.json()
        except Exception:
            detail = {}
        code = detail.get("error", "")
        if code == "ERROR_NO_PERMISSION_TO_TABLE":
            print("401 — Token has NO permission for this table.")
            print("  1. Open brands table in browser; copy real ID from URL (.../table/456/...).")
            print("  2. Baserow -> Database API -> your token -> enable Read (and Update) for that table.")
            print(f"  Server detail: {detail.get('detail', detail)}")
        else:
            print("401 Unauthorized:", detail or response.text[:200])
        return 1
    if response.status_code == 404:
        print("404 — Table ID not found. Fix BRANDS_TABLE_ID in .env (see URL in browser).")
        return 1
    response.raise_for_status()

    for field in response.json():
        name = field.get("name", "?")
        ftype = field.get("type", "?")
        fid = field.get("id", "?")
        # API row keys are usually field_<id>
        api_key = f"field_{fid}"
        print(f"  - {name!r}  type={ftype}  -> use in .env: {api_key}  (id={fid})")

    print("\nSample rows (first 3):")
    rows_url = f"{url}/api/database/rows/table/{table_id}/"
    rows_resp = requests.get(
        rows_url, headers=headers, params={"size": 3}, timeout=60
    )
    rows_resp.raise_for_status()
    for row in rows_resp.json().get("results", []):
        keys = [k for k in row.keys() if k.startswith("field_") or k == "id"]
        print(f"  row id={row.get('id')}: { {k: row[k] for k in keys} }")

    print("\nCopy the API keys (field_XXXX) into .env for FIELD_* variables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
