"""
Scrape products from ONE brand website; optional save to Baserow productsDetails.

Usage:
  python scrape_brand_products.py
  python scrape_brand_products.py --max 4 --save
  python scrape_brand_products.py --brand "Baenks" --url https://www.baenks.nl --max 3 --save
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from baserow_client import BaserowClient
from brand_scraper import extract_brand_name, extract_domain, normalize_url, row_field
from config import load_settings
from product_schema import GENERATED_LATER, SCRAPE_FIELDS
from product_baserow import save_products
from product_scraper import scrape_brand_products

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

DEFAULT_BRAND = "Spectrum Design"
DEFAULT_URL = "https://www.spectrumdesign.nl"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape one brand's products to text file.")
    p.add_argument("--brand", default=DEFAULT_BRAND, help="Brand name for Brand_table field")
    p.add_argument("--url", default=DEFAULT_URL, help="Brand website URL")
    p.add_argument(
        "--max",
        type=int,
        default=5,
        help="Max products to scrape (0 = all discovered). Default 5 for testing.",
    )
    p.add_argument(
        "--from-baserow",
        action="store_true",
        help="Pick first brand with URL from Baserow brands table instead of --url",
    )
    p.add_argument(
        "--save",
        action="store_true",
        help="Create or update rows in Baserow productsDetails table (by product_url)",
    )
    return p.parse_args()


def format_product_block(index: int, product) -> str:
    lines = [f"{'=' * 60}", f"PRODUCT #{index}", f"{'=' * 60}"]
    d = product.to_report_dict()

    lines.append(f"product_name: {product.product_name or '(empty)'}")
    lines.append(f"product_url: {product.product_url or '(empty)'}")
    lines.append(f"Status: {product.Status}")
    lines.append(f"Brand_table: {product.Brand_table or '(empty)'}")
    lines.append(f"designer: {product.designer or '(empty)'}")
    lines.append(f"designerDescription: {product.designerDescription or '(empty)'}")
    lines.append(f"price: {product.price or '(empty)'}")
    lines.append(f"product_category: {product.product_category or '(empty)'}")
    lines.append(f"sub_category: {product.sub_category or '(empty)'}")
    lines.append(f"source_product_category: {product.source_product_category or '(empty)'}")
    lines.append(f"source_product_subcategory: {product.source_product_subcategory or '(empty)'}")
    lines.append(f"product_description:\n  {d.get('product_description') or '(empty)'}")
    lines.append(f"product_images ({len(product.product_images)}):")
    for u in product.product_images[:10]:
        lines.append(f"  - {u}")
    lines.append(f"hero_images: {', '.join(product.hero_images[:3]) or '(none)'}")
    lines.append(f"lifestyle_images: {', '.join(product.lifestyle_images[:3]) or '(none)'}")
    lines.append(f"detail_image: {product.detail_image or '(none)'}")

    if not product.scrape_ok:
        lines.append(f"scrape_error: {product.scrape_error}")
    lines.append("")
    return "\n".join(lines)


def build_report(
    brand: str, site_url: str, discovered: list[str], products: list
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = [
        "=" * 70,
        "BRAND PRODUCT SCRAPE — REPORT",
        "=" * 70,
        f"Scraped at: {now}",
        f"Brand_table: {brand}",
        f"Website:     {site_url}",
        f"Discovered:  {len(discovered)} product URLs",
        f"Scraped:     {len(products)} product pages",
        "",
        "--- productsDetails columns ---",
        "FROM WEBSITE (this script tries to fill):",
        ", ".join(SCRAPE_FIELDS),
        "",
        "GENERATED LATER in Baserow/n8n/AI (not scraped now):",
        ", ".join(GENERATED_LATER[:12]) + ", ...",
        "",
        "Discovered URLs:",
    ]
    for u in discovered:
        header.append(f"  - {u}")
    header.append("")

    blocks = [format_product_block(i + 1, p) for i, p in enumerate(products)]
    ok = sum(1 for p in products if p.scrape_ok)
    footer = [
        "=" * 70,
        f"Summary: {ok}/{len(products)} products scraped OK",
        "=" * 70,
    ]
    return "\n".join(header + blocks + footer)


def resolve_brand_from_baserow(settings, client) -> tuple[str, str] | None:
    for row in client.list_table_rows(settings.brands_table_id):
        name = extract_brand_name(row, settings.field_brand_name)
        raw = extract_domain(row, settings)
        url = normalize_url(raw) if raw else None
        if url:
            return name, url
    return None


def main() -> int:
    args = parse_args()
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    brand = args.brand
    site_url = normalize_url(args.url) or args.url

    if args.from_baserow:
        client = BaserowClient(settings)
        found = resolve_brand_from_baserow(settings, client)
        if not found:
            print("No brand with URL in Baserow.", file=sys.stderr)
            return 1
        brand, site_url = found
        print(f"From Baserow: {brand} -> {site_url}")

    print(f"Brand: {brand}")
    print(f"URL:   {site_url}")
    print(f"Max products: {args.max or 'all'}\n")

    discovered, products = scrape_brand_products(
        site_url,
        brand,
        timeout=settings.http_timeout,
        max_products=args.max,
        delay_seconds=settings.scrape_delay_seconds,
    )

    if not discovered:
        print("No product URLs found. Check site structure (/collectie/ etc.).")
        return 1

    report = build_report(brand, site_url, discovered, products)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in brand).strip("_")
    txt_path = OUTPUT_DIR / f"products_{safe}.txt"
    json_path = OUTPUT_DIR / f"products_{safe}.json"
    txt_path.write_text(report, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            [p.to_report_dict() for p in products],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(report[:2500])
    if len(report) > 2500:
        print(f"\n... ({len(report)} chars total, see file)")
    print(f"\nSaved:\n  {txt_path}\n  {json_path}")

    if args.save:
        client = BaserowClient(settings)
        print(f"\nSaving to Baserow productsDetails (table {settings.products_table_id})...")
        try:
            db_results = save_products(client, settings, products, brand)
        except ValueError as exc:
            print(f"Baserow save failed: {exc}", file=sys.stderr)
            return 1

        created = sum(1 for r in db_results if r.get("action") == "created")
        updated = sum(1 for r in db_results if r.get("action") == "updated")
        failed = sum(1 for r in db_results if not r.get("ok"))
        print(f"Baserow: {created} created, {updated} updated, {failed} failed")
        for r in db_results:
            if r.get("ok"):
                imgs = r.get("images_uploaded", 0)
                extra = f" ({imgs} images)" if imgs else ""
                print(f"  [{r['action']}] row {r['row_id']}: {r['product_name']}{extra}")
            else:
                print(f"  [error] {r.get('product_name')}: {r.get('error')}", file=sys.stderr)
        if failed:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
