"""
Sites with product pages at /{slug} or /collectie/{slug}.
Examples: beekcollection.nl, castelijn.nl
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
from scrapers.taxonomy import normalize_product_categories

SITE_CONFIG: dict[str, dict] = {
    "beekcollection.nl": {
        "starts": ["/", "/collectie/"],
        "skip_slugs": {
            "contact",
            "materialen",
            "ontwerpers",
            "winkels",
            "dealers",
            "collectie",
            "over-ons",
            "privacy",
            "cookie",
        },
        "allow_collectie": True,
    },
    "castelijn.nl": {
        "starts": ["/", "/tafels/", "/kantoormeubels/", "/kasten/", "/klassiekers/"],
        "skip_slugs": {
            "contact",
            "designers",
            "downloads",
            "duurzaam",
            "feed",
            "hoogglans-lak",
            "houtfineer",
            "hpl",
            "melamine",
            "onderdelen",
            "pet-vilt",
            "privacy-policy",
            "projectdealers",
            "rondleiding",
            "thuiswerken",
            "toonkamer",
            "uitverkoop",
            "woondealers",
            "kantoormeubels",
            "tafels",
            "kasten",
            "klassiekers",
            "salontafels",
            "boardroomtafels",
        },
        "allow_collectie": False,
    },
}


def _host_key(site_url: str) -> str:
    host = urlparse(normalize_url(site_url) or site_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_links(html: str, page_url: str, host: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "lxml")
    base_host = host.lower().replace("www.", "")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        h = urlparse(href).netloc.lower().replace("www.", "")
        if h == base_host:
            yield href


def _is_product_url(url: str, cfg: dict) -> bool:
    path = urlparse(url).path.strip("/")
    if not path:
        return False
    parts = [p for p in path.split("/") if p]
    skip = cfg["skip_slugs"]

    if cfg.get("allow_collectie") and len(parts) == 2 and parts[0].lower() == "collectie":
        return parts[1].lower() not in skip and not parts[1].startswith("elementor")

    if len(parts) != 1:
        return False
    slug = parts[0].lower()
    if slug in skip or slug.startswith("elementor"):
        return False
    return True


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    base = normalize_url(site_url)
    if not base:
        return []

    key = _host_key(base)
    cfg = SITE_CONFIG.get(key)
    if not cfg:
        return []

    host = urlparse(base).netloc
    found: set[str] = set()

    for rel in cfg["starts"]:
        start = urljoin(base + "/", rel.lstrip("/"))
        try:
            resp = requests.get(
                start, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        except requests.RequestException:
            continue
        if resp.status_code >= 400:
            continue

        for href in _extract_links(resp.text, str(resp.url), host):
            if _is_product_url(href, cfg):
                found.add(href.rstrip("/"))

    return sorted(found)


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = scrape_product_page_common(product_url, brand_name, timeout)
    if product.scrape_ok:
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
