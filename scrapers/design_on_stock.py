"""Design On Stock (designonstock.com) — dedicated scraper."""
from __future__ import annotations

import time

from product_schema import ScrapedProduct
from scrapers import category_paths

discover_product_urls = category_paths.discover_product_urls


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    return category_paths.scrape_product_page(product_url, brand_name, timeout)


def scrape_brand_products(
    site_url: str,
    brand_name: str,
    *,
    timeout: float = 30,
    max_products: int = 5,
    delay_seconds: float = 1.0,
) -> tuple[list[str], list[ScrapedProduct]]:
    urls = discover_product_urls(site_url, timeout)
    if max_products > 0:
        urls = urls[:max_products]
    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        products.append(scrape_product_page(url, brand_name, timeout))
    return urls, products
