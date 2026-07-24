"""Leolux — /collectie/{category}/{product} discovery + shared extract."""
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
from scrapers.taxonomy import capture_source_categories, normalize_product_categories

COLLECTIE = "/collectie"


def _extract_links(html: str, page_url: str, host: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        if urlparse(href).netloc == host:
            yield href


def _classify(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    if COLLECTIE not in path:
        return None
    parts = [p for p in path.split("/") if p]
    try:
        idx = parts.index("collectie")
    except ValueError:
        return None
    after = parts[idx + 1 :]
    if not after:
        return None
    if after[0].lower() in ("categorie", "category"):
        return "category"
    if len(after) >= 2:
        return "product"
    return "category"


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    base = normalize_url(site_url)
    if not base:
        return []

    host = urlparse(base).netloc
    found: set[str] = set()
    category_pages: set[str] = set()

    for start in (urljoin(base + "/", "collectie/"), urljoin(base + "/", "collectie"), base + "/"):
        try:
            resp = requests.get(
                start, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        except requests.RequestException:
            continue
        if resp.status_code >= 400:
            continue

        for href in _extract_links(resp.text, str(resp.url), host):
            kind = _classify(href)
            if kind == "category":
                category_pages.add(href.rstrip("/"))
            elif kind == "product":
                found.add(href.rstrip("/"))

        for cat in sorted(category_pages)[:50]:
            try:
                cat_resp = requests.get(
                    cat, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
                )
            except requests.RequestException:
                continue
            if cat_resp.status_code >= 400:
                continue
            for href in _extract_links(cat_resp.text, str(cat_resp.url), host):
                if _classify(href) == "product":
                    found.add(href.rstrip("/"))

        if found:
            break

    def depth(u: str) -> int:
        return len([p for p in urlparse(u).path.split("/") if p])

    return sorted(found, key=lambda u: (-depth(u), u))


def _path_meta(url: str) -> tuple[str, str, str]:
    """Return (category_slug, product_slug, title) from /collectie/{cat}/{product}."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    low = [p.lower() for p in parts]
    if "collectie" not in low:
        return "", "", ""
    idx = low.index("collectie")
    after = parts[idx + 1 :]
    if len(after) < 2 or after[0].lower() in ("categorie", "category"):
        return "", "", ""
    slug = after[-1]
    return after[0], slug, _slug_to_title(slug)


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = scrape_product_page_common(product_url, brand_name, timeout)
    if not product.scrape_ok:
        return product
    category, _slug, title = _path_meta(product.product_url or product_url)
    if category:
        product.source_product_category = category.replace("-", " ")
        product.source_product_subcategory = ""
        product.product_category = product.source_product_category
        product.sub_category = ""
    if title and (
        not product.product_name
        or product.product_name.lower() == category.lower()
    ):
        product.product_name = title
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
