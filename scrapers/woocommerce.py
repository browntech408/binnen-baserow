"""
WooCommerce-style shops: /product-categorie/... and /product/slug
Example: sleepworldhelmond.nl
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


def _extract_links(html: str, page_url: str, host: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        if urlparse(href).netloc == host:
            yield href


def _is_product_url(url: str) -> bool:
    path = urlparse(url).path.strip("/").lower()
    parts = [p for p in path.split("/") if p]
    # /product/slug
    return len(parts) == 2 and parts[0] == "product"


def _is_category_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return "/product-categorie/" in path or "/product-category/" in path


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    base = normalize_url(site_url)
    if not base:
        return []

    host = urlparse(base).netloc
    found: set[str] = set()
    categories: set[str] = set()

    starts = [base + "/", base]
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
            elif _is_category_url(href):
                categories.add(href.rstrip("/"))

        for cat in sorted(categories)[:25]:
            try:
                cat_resp = requests.get(
                    cat, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
                )
            except requests.RequestException:
                continue
            if cat_resp.status_code >= 400:
                continue
            for href in _extract_links(cat_resp.text, str(cat_resp.url), host):
                if _is_product_url(href):
                    found.add(href.rstrip("/"))

        if found:
            break

    return sorted(found)


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    return scrape_product_page_common(product_url, brand_name, timeout)


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
