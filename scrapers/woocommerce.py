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
from scrapers.taxonomy import (
    capture_source_categories,
    categories_from_listing_url,
    normalize_product_categories,
)

# product_url → (category, sub_category) from listing page where URL was found
_listing_categories: dict[str, tuple[str, str]] = {}


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
    if _is_product_url(url):
        return False
    if "/product-categorie/" in path or "/product-category/" in path:
        return True
    # Bert Plantagie and similar: /collections/collecties/...
    return "/collections/collecties/" in path


def _parse_woocommerce_breadcrumb(soup: BeautifulSoup) -> tuple[str, str]:
    crumb = soup.select_one(".woocommerce-breadcrumb")
    if not crumb:
        return "", ""
    parts: list[str] = []
    for a in crumb.find_all("a"):
        text = a.get_text(strip=True)
        if text and text.lower() not in ("home",):
            parts.append(text)
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    global _listing_categories
    _listing_categories = {}

    base = normalize_url(site_url)
    if not base:
        return []

    host = urlparse(base).netloc
    found: set[str] = set()
    categories: set[str] = set()

    starts = [
        base + "/",
        base,
        urljoin(base + "/", "collections/collecties/"),
        urljoin(base + "/", "product-categorie/"),
    ]
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

    queue = sorted(categories)
    visited: set[str] = set()
    while queue and len(visited) < 60:
        cat = queue.pop(0)
        if cat in visited:
            continue
        visited.add(cat)
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
                key = href.rstrip("/")
                found.add(key)
                if key not in _listing_categories:
                    _listing_categories[key] = categories_from_listing_url(cat)
            elif _is_category_url(href) and href.rstrip("/") not in visited:
                queue.append(href.rstrip("/"))

    return sorted(found)


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = scrape_product_page_common(product_url, brand_name, timeout)
    if not product.scrape_ok:
        return product

    key = (product.product_url or product_url).rstrip("/")
    listing = _listing_categories.get(key)
    if listing and listing[0]:
        product.product_category, product.sub_category = listing
        product.source_product_category = listing[0]
        product.source_product_subcategory = listing[1]

    try:
        import requests
        from brand_scraper import DEFAULT_HEADERS

        resp = requests.get(
            key, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
        )
        if resp.status_code < 400:
            soup = BeautifulSoup(resp.text, "lxml")
            bcat, bsub = _parse_woocommerce_breadcrumb(soup)
            if bcat or bsub:
                crumbs: list[str] = []
                crumb = soup.select_one(".woocommerce-breadcrumb")
                if crumb:
                    for a in crumb.find_all("a"):
                        t = a.get_text(strip=True)
                        if t:
                            crumbs.append(t)
                if len(crumbs) >= 2:
                    product.source_product_category = crumbs[0]
                    product.source_product_subcategory = crumbs[1]
                elif bcat:
                    product.source_product_category = bcat
                    product.source_product_subcategory = bsub
            if bcat and not (listing and listing[0]):
                product.product_category = bcat
            if bsub and not (listing and listing[1]):
                product.sub_category = bsub
    except Exception:
        pass

    capture_source_categories(product)
    normalize_product_categories(product)
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
