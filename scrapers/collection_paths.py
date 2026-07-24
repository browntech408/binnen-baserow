"""
Sites with /Collection/{category}/{product} paths.
Examples: artifort.com, pode.eu
"""
from __future__ import annotations

import time
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from product_scraper import _slug_to_title
from scrapers.extract_common import scrape_product_page_common
from scrapers.taxonomy import normalize_product_categories

COLLECTION_MARKERS = ("/collection/", "/collectie/")


def _path_parts(url: str) -> list[str]:
    return [p for p in urlparse(url).path.split("/") if p]


def _has_collection_path(url: str) -> bool:
    low = urlparse(url).path.lower()
    return any(m in low for m in COLLECTION_MARKERS)


def _is_product_url(url: str) -> bool:
    parts = _path_parts(url)
    low = [p.lower() for p in parts]
    if "collection" not in low and "collectie" not in low:
        return False
    try:
        idx = low.index("collection") if "collection" in low else low.index("collectie")
    except ValueError:
        return False
    after = parts[idx + 1 :]
    # /Collection/Category/Product
    if len(after) >= 2:
        return True
    return False


def _is_category_url(url: str) -> bool:
    parts = _path_parts(url)
    low = [p.lower() for p in parts]
    if "collection" not in low and "collectie" not in low:
        return False
    try:
        idx = low.index("collection") if "collection" in low else low.index("collectie")
    except ValueError:
        return False
    after = parts[idx + 1 :]
    return len(after) == 1


def _extract_links(html: str, page_url: str, host: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "lxml")
    base_host = host.lower().replace("www.", "")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        h = urlparse(href).netloc.lower().replace("www.", "")
        if h == base_host:
            yield href


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    base = normalize_url(site_url)
    if not base:
        return []

    host = urlparse(base).netloc
    found: set[str] = set()
    categories: set[str] = set()

    starts = [
        base + "/",
        urljoin(base + "/", "Collection"),
        urljoin(base + "/", "collection"),
        urljoin(base + "/", "collectie"),
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
            if not _has_collection_path(href):
                continue
            if _is_product_url(href):
                found.add(href.rstrip("/"))
            elif _is_category_url(href):
                categories.add(href.rstrip("/"))

        for cat in sorted(categories)[:40]:
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


def _path_meta(url: str) -> tuple[str, str, str]:
    """Return (category_label, product_slug, product_title) from /Collection/.../slug."""
    parts = _path_parts(url)
    low = [p.lower() for p in parts]
    try:
        idx = low.index("collection") if "collection" in low else low.index("collectie")
    except ValueError:
        return "", "", ""
    after = parts[idx + 1 :]
    if len(after) < 2:
        return "", "", ""
    category = after[0].replace("-", " ")
    slug = after[-1]
    return category, slug, _slug_to_title(slug)


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = scrape_product_page_common(product_url, brand_name, timeout)
    if not product.scrape_ok:
        return product
    category, _slug, title = _path_meta(product.product_url or product_url)
    if category:
        product.source_product_category = category
        product.source_product_subcategory = ""
        product.product_category = category
        product.sub_category = ""
    if title and (
        not product.product_name
        or product.product_name.lower() == category.lower()
    ):
        product.product_name = title
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
