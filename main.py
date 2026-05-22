"""
Scrape each brand from the Baserow brands table, one by one, and write results back.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Any

from baserow_client import BaserowClient
from brand_scraper import (
    extract_brand_name,
    extract_domain,
    normalize_url,
    scrape_brand_homepage,
    should_scrape_row,
)
from config import load_settings


def build_update_payload(settings: Any, result: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {}

    if settings.field_scrape_status:
        payload[settings.field_scrape_status] = "done" if result.ok else "error"
    if settings.field_scrape_error:
        payload[settings.field_scrape_error] = result.error or ""
    if settings.field_last_scraped:
        payload[settings.field_last_scraped] = now
    if settings.field_page_title and result.page_title:
        payload[settings.field_page_title] = result.page_title
    if settings.field_meta_description and result.meta_description:
        payload[settings.field_meta_description] = result.meta_description

    return payload


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    client = BaserowClient(settings)
    table_id = settings.brands_table_id

    print(f"Connecting to Baserow: {settings.baserow_url}")
    print(f"Brands table ID: {table_id}")
    print("Loading brands...\n")

    processed = 0
    skipped = 0
    errors = 0

    for row in client.list_table_rows(table_id):
        row_id = row["id"]
        brand_name = extract_brand_name(row, settings.field_brand_name)

        do_scrape, skip_reason = should_scrape_row(row, settings)
        if not do_scrape:
            print(f"[skip] {brand_name} ({skip_reason})")
            skipped += 1
            continue

        domain_raw = extract_domain(row, settings)
        url = normalize_url(domain_raw) if domain_raw else None
        if not url:
            print(f"[skip] {brand_name} — no domain/URL in row")
            skipped += 1
            continue

        print(f"[scrape] {brand_name} -> {url}")
        result = scrape_brand_homepage(url, settings.http_timeout)

        if result.ok:
            print(f"  OK: {result.page_title or '(no title)'}")
        else:
            print(f"  ERROR: {result.error}")
            errors += 1

        update_fields = build_update_payload(settings, result)
        if update_fields:
            try:
                client.update_row(table_id, row_id, update_fields)
                print("  Updated Baserow row")
            except Exception as exc:
                print(f"  Failed to update Baserow: {exc}", file=sys.stderr)
                errors += 1

        processed += 1
        if settings.scrape_delay_seconds > 0:
            time.sleep(settings.scrape_delay_seconds)

    print(
        f"\nFinished. Scraped: {processed}, skipped: {skipped}, errors: {errors}"
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
