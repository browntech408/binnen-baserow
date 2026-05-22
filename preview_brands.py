"""Dry-run: show which brands would be scraped (no HTTP, no Baserow writes)."""
from __future__ import annotations

import sys

from baserow_client import BaserowClient
from brand_scraper import extract_brand_name, extract_domain, normalize_url, should_scrape_row
from config import load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    client = BaserowClient(settings)
    scrape_n = skip_n = no_url_n = 0

    print(f"Table {settings.brands_table_id} @ {settings.baserow_url}\n")

    for row in client.list_table_rows(settings.brands_table_id):
        name = extract_brand_name(row, settings.field_brand_name)
        url_raw = extract_domain(row, settings)
        url = normalize_url(url_raw) if url_raw else None

        do_scrape, reason = should_scrape_row(row, settings)
        if not url:
            print(f"  [no URL]  {name}")
            no_url_n += 1
            continue
        if not do_scrape:
            print(f"  [skip]    {name} — {reason}")
            skip_n += 1
            continue
        print(f"  [scrape]  {name} — {url}")
        scrape_n += 1

    print(f"\nWould scrape: {scrape_n}, skip: {skip_n}, no URL: {no_url_n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
