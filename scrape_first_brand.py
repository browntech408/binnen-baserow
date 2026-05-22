"""
Scrape only the FIRST brand (with a URL) from Baserow and save results to output/*.txt and output/*.pdf
Does not update Baserow — for testing only.
"""
from __future__ import annotations

import sys
from pathlib import Path

from baserow_client import BaserowClient
from brand_scraper import (
    extract_brand_name,
    extract_domain,
    normalize_url,
    scrape_brand_homepage,
    should_scrape_row,
)
from config import load_settings
from report_writer import build_context, save_reports

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def find_first_brand(client: BaserowClient, settings) -> tuple[dict, str] | None:
    """Return (row, url) for first scrapeable brand, or None."""
    for row in client.list_table_rows(settings.brands_table_id):
        name = extract_brand_name(row, settings.field_brand_name)
        do_scrape, skip_reason = should_scrape_row(row, settings)
        if not do_scrape:
            print(f"  [skip] {name} — {skip_reason}")
            continue
        domain_raw = extract_domain(row, settings)
        url = normalize_url(domain_raw) if domain_raw else None
        if not url:
            print(f"  [skip] {name} — no URL")
            continue
        return row, url
    return None


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    client = BaserowClient(settings)
    print(f"Baserow: {settings.baserow_url}")
    print(f"Table:   {settings.brands_table_id}")
    print("Finding first brand with URL...\n")

    found = find_first_brand(client, settings)
    if not found:
        print("No brand found to scrape (all skipped or missing URLs).")
        return 1

    row, url = found
    name = extract_brand_name(row, settings.field_brand_name)
    print(f"Scraping: {name}")
    print(f"URL:      {url}\n")

    result = scrape_brand_homepage(url, settings.http_timeout)
    ctx = build_context(row, settings, url)

    if result.ok:
        print(f"OK — title: {result.page_title or '(none)'}")
        print(f"     links found: {len(result.sample_links or [])}")
    else:
        print(f"FAILED — {result.error}")

    txt_path, pdf_path = save_reports(OUTPUT_DIR, ctx, result)
    print(f"\nSaved:")
    print(f"  Text: {txt_path}")
    print(f"  PDF:  {pdf_path}")
    print(f"\nOpen the .txt file to review full details.")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
