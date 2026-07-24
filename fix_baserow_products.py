"""
Re-scrape products in Baserow and fix category / subcategory links.
Also verifies product URLs respond with HTTP 200.
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse

import requests

from baserow_client import BaserowClient
from brand_scraper import DEFAULT_HEADERS, extract_brand_name
from config import load_settings
from baserow_images import BaserowImageUploader
from product_baserow import CategoryLookup, find_brand_row_id, scraped_product_to_fields
from product_schema import ScrapedProduct
from scrapers.router import get_scraper_module, domain_key
from scrapers.taxonomy import capture_source_categories, normalize_product_categories


def _brand_url_for_row(client: BaserowClient, settings, brand_row_id: int) -> str:
    from brand_scraper import extract_domain, normalize_url

    row = client.get_row(settings.brands_table_id, brand_row_id)
    raw = extract_domain(row, settings)
    return normalize_url(raw) or ""


def _check_url(url: str, timeout: float) -> tuple[bool, int | str]:
    try:
        r = requests.head(
            url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
        )
        if r.status_code >= 400:
            r = requests.get(
                url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        return r.status_code < 400, r.status_code
    except requests.RequestException as exc:
        return False, str(exc)


def fix_products(
    *,
    dry_run: bool = False,
    skip_url_check: bool = False,
    brand_filter: str | None = None,
    with_images: bool = False,
) -> int:
    settings = load_settings()
    client = BaserowClient(settings)
    lookup = CategoryLookup(client, settings)
    uploader = (
        BaserowImageUploader(client, settings) if with_images and settings.upload_product_images else None
    )
    filter_id = None
    if brand_filter:
        filter_id = find_brand_row_id(client, settings, brand_filter)
        if filter_id is None:
            print(f"Brand not found: {brand_filter!r}", file=sys.stderr)
            return 1

    brands: dict[int, str] = {}
    for row in client.list_table_rows(settings.brands_table_id):
        brands[row["id"]] = extract_brand_name(row, settings.field_brand_name)

    errors = 0
    fixed = 0
    url_bad = 0
    warmed_sites: set[str] = set()

    for row in client.list_table_rows(settings.products_table_id):
        row_id = row["id"]
        url = (row.get(settings.field_product_url) or "").strip().rstrip("/")
        name = row.get(settings.field_product_name) or url
        brand_links = row.get(settings.field_brand_link) or []
        brand_id = brand_links[0]["id"] if brand_links else None
        if filter_id is not None and brand_id != filter_id:
            continue
        brand_name = brands.get(brand_id, "?")
        site_url = _brand_url_for_row(client, settings, brand_id) if brand_id else ""

        print(f"\n[{row_id}] {brand_name} — {name}")
        print(f"  URL: {url}")

        if not url:
            print("  SKIP: no URL")
            errors += 1
            continue

        if not skip_url_check:
            ok, status = _check_url(url, settings.http_timeout)
            if not ok:
                print(f"  URL FAIL: {status}")
                url_bad += 1
            else:
                print(f"  URL OK ({status})")

        if not site_url:
            site_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

        mod, scraper_name = get_scraper_module(url or site_url)
        if scraper_name == "woocommerce" and site_url not in warmed_sites:
            print("  Warming WooCommerce category cache…")
            mod.discover_product_urls(site_url, settings.http_timeout)
            warmed_sites.add(site_url)

        product = mod.scrape_product_page(url, brand_name, settings.http_timeout)
        if not product.scrape_ok:
            print(f"  SCRAPE FAIL: {product.scrape_error}")
            errors += 1
            continue

        capture_source_categories(product)
        normalize_product_categories(product, site_url)

        cat_ids, sub_ids = lookup.resolve(
            product.product_category, product.sub_category, create=True
        )
        print(
            f"  src={product.source_product_category!r}/{product.source_product_subcategory!r}"
        )
        print(
            f"  map={product.product_category!r}/{product.sub_category!r} "
            f"-> cat_ids={cat_ids} sub_ids={sub_ids}"
        )
        print(f"  images={len(product.product_images)} (flags filtered)")

        if not cat_ids:
            print("  WARN: no category link")
            errors += 1

        if dry_run:
            continue

        fields = scraped_product_to_fields(
            product,
            settings,
            brand_row_id=brand_id,
            category_lookup=lookup,
            image_uploader=uploader,
        )
        client.update_row(settings.products_table_id, row_id, fields)
        fixed += 1
        print("  UPDATED")

    print(f"\nDone: updated={fixed}, url_failures={url_bad}, errors={errors}")
    return 1 if errors or url_bad else 0


def main() -> None:
    p = argparse.ArgumentParser(description="Fix Baserow product categories from live URLs")
    p.add_argument("--dry-run", action="store_true", help="Show changes only")
    p.add_argument("--skip-url-check", action="store_true")
    p.add_argument("--brand", default=None, help="Only fix products for this brand name")
    p.add_argument("--with-images", action="store_true", help="Re-upload product images")
    args = p.parse_args()
    sys.exit(
        fix_products(
            dry_run=args.dry_run,
            skip_url_check=args.skip_url_check,
            brand_filter=args.brand,
            with_images=args.with_images,
        )
    )


if __name__ == "__main__":
    main()
