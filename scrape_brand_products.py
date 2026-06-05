"""
Scrape products from brand websites; optional save to Baserow productsDetails.

Default: first 5 brands from Baserow → scrape ALL products per brand → create/update in DB.
Spectrum Design uses the original product_scraper (unchanged).

Usage:
  python scrape_brand_products.py
  python scrape_brand_products.py --save
  python scrape_brand_products.py --limit-brands 2 --max 0
  python scrape_brand_products.py --max 10
  python scrape_brand_products.py --brand "Leolux" --url https://www.leolux.nl --no-save
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from baserow_client import BaserowClient
from brand_scraper import (
    extract_brand_name,
    extract_domain,
    normalize_url,
    should_scrape_row,
)
from config import load_settings
from product_schema import GENERATED_LATER, SCRAPE_FIELDS
from product_baserow import save_products
from scrapers.router import scrape_brand_products as route_scrape_brand

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape brand products (domain-specific scraper, same JSON output)."
    )
    p.add_argument(
        "--brand",
        default=None,
        help="Single brand name (optional; default = batch from Baserow)",
    )
    p.add_argument("--url", default=None, help="Brand website URL (with --brand)")
    p.add_argument(
        "--max",
        type=int,
        default=0,
        help="Max products per brand (0 = all discovered, default). Use e.g. --max 5 for a test run.",
    )
    p.add_argument(
        "--limit-brands",
        type=int,
        default=5,
        help="How many brands to process from Baserow when --brand is not set. Default 5.",
    )
    p.add_argument(
        "--save",
        action="store_true",
        default=True,
        help="Save to Baserow productsDetails (default: on)",
    )
    p.add_argument(
        "--no-save",
        dest="save",
        action="store_false",
        help="Only write output/*.json and *.txt, do not update Baserow",
    )
    p.add_argument(
        "--brand-delay",
        type=float,
        default=1.0,
        help="Seconds to wait between brands (default 1)",
    )
    p.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit code 1 if any brand fails",
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


def iter_brands_from_baserow(settings, client, limit: int) -> list[tuple[int, str, str]]:
    """First N brands with a website URL, in Baserow table order."""
    brands: list[tuple[int, str, str]] = []
    for row in client.list_table_rows(settings.brands_table_id):
        do_scrape, _ = should_scrape_row(row, settings)
        if not do_scrape:
            continue
        name = extract_brand_name(row, settings.field_brand_name)
        raw = extract_domain(row, settings)
        url = normalize_url(raw) if raw else None
        if not url:
            continue
        brands.append((row["id"], name, url))
        if limit > 0 and len(brands) >= limit:
            break
    return brands


def run_one_brand(
    settings,
    client: BaserowClient | None,
    brand: str,
    site_url: str,
    *,
    max_products: int,
    save: bool,
) -> dict:
    print(f"Brand: {brand}")
    print(f"URL:   {site_url}")
    print(f"Max products: {max_products or 'all'}\n")

    discovered, products = route_scrape_brand(
        site_url,
        brand,
        timeout=settings.http_timeout,
        max_products=max_products,
        delay_seconds=settings.scrape_delay_seconds,
    )

    safe = "".join(c if c.isalnum() else "_" for c in brand).strip("_") or "brand"
    txt_path = OUTPUT_DIR / f"products_{safe}.txt"
    json_path = OUTPUT_DIR / f"products_{safe}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    result: dict = {
        "brand": brand,
        "url": site_url,
        "saved_txt": str(txt_path),
        "saved_json": str(json_path),
    }

    if not discovered:
        result["ok"] = False
        result["error"] = "No product URLs found."
        result["discovered"] = 0
        result["scraped"] = 0
        print(result["error"], file=sys.stderr)
        return result

    report = build_report(brand, site_url, discovered, products)
    txt_path.write_text(report, encoding="utf-8")
    json_path.write_text(
        json.dumps([p.to_report_dict() for p in products], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved:\n  {txt_path}\n  {json_path}\n")

    result["ok"] = True
    result["discovered"] = len(discovered)
    result["scraped"] = len(products)

    if save and client:
        print(f"Saving to Baserow productsDetails (table {settings.products_table_id})...")
        try:
            db_results = save_products(client, settings, products, brand)
            created = sum(1 for r in db_results if r.get("action") == "created")
            updated = sum(1 for r in db_results if r.get("action") == "updated")
            failed = sum(1 for r in db_results if not r.get("ok"))
            result["created"] = created
            result["updated"] = updated
            result["failed"] = failed
            print(f"Baserow: {created} created, {updated} updated, {failed} failed")
            for r in db_results:
                if r.get("ok"):
                    imgs = r.get("images_uploaded", 0)
                    extra = f" ({imgs} images)" if imgs else ""
                    print(f"  [{r['action']}] row {r['row_id']}: {r['product_name']}{extra}")
                else:
                    print(
                        f"  [error] {r.get('product_name')}: {r.get('error')}",
                        file=sys.stderr,
                    )
            if failed:
                result["ok"] = False
                result["error"] = f"{failed} product(s) failed to save"
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)
            print(f"Baserow save failed: {exc}", file=sys.stderr)

    return result


def main() -> int:
    args = parse_args()
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    client = BaserowClient(settings) if args.save else None

    if args.brand and args.url:
        targets = [(0, args.brand, normalize_url(args.url) or args.url)]
    elif args.brand:
        if not client:
            client = BaserowClient(settings)
        found_url = None
        for row in client.list_table_rows(settings.brands_table_id):
            name = extract_brand_name(row, settings.field_brand_name)
            if name.lower() == args.brand.strip().lower():
                raw = extract_domain(row, settings)
                found_url = normalize_url(raw) if raw else None
                break
        if not found_url:
            print(f"Brand not found or no URL in Baserow: {args.brand}", file=sys.stderr)
            return 1
        targets = [(0, args.brand, found_url)]
    else:
        if not client:
            client = BaserowClient(settings)
        targets = iter_brands_from_baserow(settings, client, args.limit_brands)
        if not targets:
            print("No brands with URLs in Baserow.", file=sys.stderr)
            return 1
        print(
            f"Brands from Baserow (table {settings.brands_table_id}), "
            f"limit {args.limit_brands}:\n"
        )
        for _, name, url in targets:
            print(f"  - {name}: {url}")
        print()

    summary: list[dict] = []
    had_errors = False

    for i, (_, brand, site_url) in enumerate(targets, start=1):
        if len(targets) > 1:
            print("=" * 70)
            print(f"[{i}/{len(targets)}]")
        summary.append(
            run_one_brand(
                settings,
                client,
                brand,
                site_url,
                max_products=args.max,
                save=args.save,
            )
        )
        if not summary[-1].get("ok"):
            had_errors = True
        if args.brand_delay and i < len(targets):
            time.sleep(args.brand_delay)

    summary_path = OUTPUT_DIR / "_brands_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nSummary: {summary_path}")

    return 1 if (had_errors and args.fail_on_error) else (1 if had_errors and len(targets) == 1 else 0)


if __name__ == "__main__":
    raise SystemExit(main())
