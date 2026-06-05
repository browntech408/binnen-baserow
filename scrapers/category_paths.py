"""
Sites with category + product paths (no /collectie/ required).
Example: designonstock.com → /banken/slug, /stoelen/slug
"""
from __future__ import annotations

import time
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers.extract_common import scrape_product_page_common

# First path segment = category listing; product = slug or .html (Magento)
LISTING_SEGMENTS = frozenset(
    {
        "banken",
        "stoelen",
        "tafels",
        "kasten",
        "collectie",
        "producten",
        "products",
        "sofas",
        "chairs",
        "tables",
        "accessoires",
        "accessories",
        "fauteuils",
        "poefs",
        "kussens",
    }
)

# Subcategory index pages (not product detail)
SUBCATEGORY_SLUGS = frozenset(
    {
        "hoekbanken",
        "leder",
        "stof",
        "fauteuil",
        "draaifauteuil",
        "3-zits-bank",
        "2-zits-bank",
        "modulaire-bank",
        "beige-hoekbank",
        "grijze-bank",
        "lederen-banken",
        "3-zitsbank",
    }
)


def _extract_links(html: str, page_url: str, host: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        if urlparse(href).netloc == host:
            yield href


def _is_product_url(url: str) -> bool:
    path = urlparse(url).path.strip("/")
    if not path:
        return False
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0].lower() not in LISTING_SEGMENTS:
        return False

    last = parts[-1].lower()
    skip = {"collectie", "producten", "products", "over-ons", "contact", "dealers"}

    # Magento-style product pages (e.g. baenks.nl)
    if last.endswith(".html"):
        return last not in ("banken.html",)

    if len(parts) >= 3:
        return False

    if len(parts) != 2:
        return False
    slug = parts[1].lower()
    if slug in skip or slug in SUBCATEGORY_SLUGS:
        return False
    return True


def _is_listing_page(url: str) -> bool:
    path = urlparse(url).path.strip("/")
    if not path:
        return False
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0].lower() not in LISTING_SEGMENTS:
        return False
    if _is_product_url(url):
        return False
    return len(parts) <= 2


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    base = normalize_url(site_url)
    if not base:
        return []

    host = urlparse(base).netloc
    found: set[str] = set()
    listing_pages: set[str] = set()

    starts = [base + "/", base]
    for seg in LISTING_SEGMENTS:
        starts.append(urljoin(base + "/", seg + "/"))

    for start in starts:
        try:
            resp = requests.get(
                start, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        except requests.RequestException:
            continue
        if resp.status_code >= 400:
            continue

        for href in _extract_links(resp.text, str(resp.url), host):
            if _is_product_url(href):
                found.add(href.rstrip("/"))
            elif _is_listing_page(href):
                listing_pages.add(href.rstrip("/"))

        for listing in sorted(listing_pages)[:40]:
            try:
                lr = requests.get(
                    listing, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
                )
            except requests.RequestException:
                continue
            if lr.status_code >= 400:
                continue
            for href in _extract_links(lr.text, str(lr.url), host):
                if _is_product_url(href):
                    found.add(href.rstrip("/"))

        if found:
            break

    return sorted(found)


def _category_from_path(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and parts[0].lower() in LISTING_SEGMENTS:
        return parts[0].replace("-", " ").title()
    return ""


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = scrape_product_page_common(product_url, brand_name, timeout)
    if not product.scrape_ok:
        return product
    from_path = _category_from_path(product.product_url or product_url)
    if from_path and (
        not product.product_category
        or product.product_category.lower() in ("home", "collectie", "collection")
    ):
        product.product_category = from_path
        product.source_product_category = from_path
    return product


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
