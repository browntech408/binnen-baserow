"""
Pick scraper by website domain. Same output: list[ScrapedProduct].
"""
from __future__ import annotations

from urllib.parse import urlparse

from brand_scraper import normalize_url
from product_schema import ScrapedProduct

# domain (no www) → module name
DOMAIN_MODULES: dict[str, str] = {
    "spectrumdesign.nl": "spectrum",
    "leolux.nl": "leolux",
    "designonstock.com": "category_paths",
    "sleepworldhelmond.nl": "woocommerce",
    "artifort.com": "collection_paths",
    "baenks.nl": "category_paths",
}

# category_paths = /banken/slug (designonstock) or /banken/*.html (baenks)
# collection_paths = /Collection/Category/Product (artifort)


def domain_key(site_url: str) -> str:
    host = urlparse(normalize_url(site_url) or site_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def get_scraper_module(site_url: str):
    key = domain_key(site_url)
    name = DOMAIN_MODULES.get(key, "spectrum")

    if name == "spectrum":
        from scrapers import spectrum as mod
    elif name == "leolux":
        from scrapers import leolux as mod
    elif name == "category_paths":
        from scrapers import category_paths as mod
    elif name == "woocommerce":
        from scrapers import woocommerce as mod
    elif name == "collection_paths":
        from scrapers import collection_paths as mod
    else:
        from scrapers import spectrum as mod

    return mod, name


def scrape_brand_products(
    site_url: str,
    brand_name: str,
    *,
    timeout: float = 30,
    max_products: int = 5,
    delay_seconds: float = 1.0,
) -> tuple[list[str], list[ScrapedProduct]]:
    mod, scraper_name = get_scraper_module(site_url)
    print(f"Scraper: {scraper_name} ({domain_key(site_url)})")
    return mod.scrape_brand_products(
        site_url,
        brand_name,
        timeout=timeout,
        max_products=max_products,
        delay_seconds=delay_seconds,
    )
