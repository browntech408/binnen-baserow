"""
Shared HTML → ScrapedProduct extraction (any site).
Missing fields stay empty strings / empty lists.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS
from product_schema import ScrapedProduct

# Reuse Spectrum-tuned helpers from product_scraper (breadcrumbs, designer, etc.)
from product_scraper import (
    _find_price,
    _main_paragraphs,
    _parse_categories,
    _parse_designer,
    _slug_to_title,
)


def _normalize_image_url(url: str) -> str:
    return re.sub(
        r"-\d+x\d+(?=\.(jpg|jpeg|png|webp)(\?|$))",
        "",
        (url or "").split("#")[0],
        flags=re.I,
    )


def _is_product_image_url(url: str) -> bool:
    low = url.lower()
    if not re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", low):
        return False
    if any(
        x in low
        for x in (
            "icon",
            "logo",
            "sprite",
            "favicon",
            "marker",
            "globe",
            "search",
            ".svg",
        )
    ):
        return False
    return True


def _dedupe_images(urls: list[str], *, max_images: int = 12) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        u = _normalize_image_url(url)
        if not u or not _is_product_image_url(u):
            continue
        key = urlparse(u).path.lower()
        if "storyblok.com" in u and "/m/" in key:
            key = key.split("/m/")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
        if len(out) >= max_images:
            break
    return out


def collect_product_images(
    soup: BeautifulSoup, h1: BeautifulSoup | None, page_url: str, *, max_images: int = 12
) -> list[str]:
    """All common CDNs + WordPress uploads."""
    candidates: list[str] = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        candidates.append(urljoin(page_url, og["content"]))

    scope = h1.find_all_next(["img", "picture"]) if h1 else soup.find_all("img")
    for img in scope[:40] if h1 else soup.find_all("img")[:30]:
        if h1 and img.find_previous("h1") is not h1:
            continue
        for attr in ("src", "data-src", "data-lazy-src"):
            raw = img.get(attr)
            if raw:
                candidates.append(urljoin(page_url, raw))
        for part in (img.get("srcset") or "").split(","):
            piece = part.strip().split()[0] if part.strip() else ""
            if piece:
                candidates.append(urljoin(page_url, piece))

    return _dedupe_images(candidates, max_images=max_images)


def scrape_product_page_common(
    product_url: str, brand_name: str, timeout: float
) -> ScrapedProduct:
    product = ScrapedProduct(product_url=product_url.rstrip("/"), Brand_table=brand_name)
    try:
        resp = requests.get(
            product_url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        product.scrape_ok = False
        product.scrape_error = str(exc)
        return product

    if resp.status_code >= 400:
        product.scrape_ok = False
        product.scrape_error = f"HTTP {resp.status_code}"
        return product

    final_url = str(resp.url).rstrip("/")
    product.product_url = final_url
    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")

    product.product_name = h1.get_text(strip=True) if h1 else _slug_to_title(final_url)
    product.product_description = _main_paragraphs(soup, h1)
    product.designer, product.designerDescription = _parse_designer(soup)
    product.price = _find_price(soup)
    product.product_category, product.sub_category = _parse_categories(soup, h1, final_url)
    product.source_product_category = product.product_category
    product.source_product_subcategory = product.sub_category

    images = collect_product_images(soup, h1, final_url)
    product.product_images = images
    if images:
        product.hero_images = [images[0]]
        product.lifestyle_images = images[1:4] if len(images) > 1 else []
        product.detail_image = images[-1] if len(images) > 2 else ""

    return product
