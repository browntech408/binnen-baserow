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
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description, is_junk_paragraph


def _normalize_image_url(url: str) -> str:
    return re.sub(
        r"-\d+x\d+(?=\.(jpg|jpeg|png|webp)(\?|$))",
        "",
        (url or "").split("#")[0],
        flags=re.I,
    )


def _is_nav_or_flag_img(img, url: str) -> bool:
    low = url.lower()
    classes = " ".join(img.get("class") or []).lower()
    if any(x in classes for x in ("wpml", "iclflag", "language", "lang-switch")):
        return True
    if any(
        x in low
        for x in (
            "/flags/",
            "sitepress-multilingual",
            "wpml-ls",
            "label-logo",
            "logo-opt",
        )
    ):
        return True
    return False


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
            "/flags/",
            "sitepress-multilingual",
            "wpml",
        )
    ):
        return False
    return True


def _urls_from_img(img, page_url: str) -> list[str]:
    urls: list[str] = []
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        raw = img.get(attr)
        if not raw or str(raw).startswith("data:"):
            continue
        urls.append(urljoin(page_url, raw))
    for attr in ("data-srcset", "srcset"):
        raw = img.get(attr) or ""
        for part in raw.split(","):
            piece = part.strip().split()[0] if part.strip() else ""
            if piece and not piece.startswith("data:"):
                urls.append(urljoin(page_url, piece))
    return urls


def _product_image_scope(soup: BeautifulSoup, h1: BeautifulSoup | None):
    gallery = soup.select_one(".woocommerce-product-gallery")
    if gallery:
        return gallery
    if h1:
        for sel in (".product", "main article", "main", "article"):
            el = soup.select_one(sel)
            if el and h1 in el.descendants:
                return el
    return soup


def _dedupe_images(urls: list[str], *, max_images: int = 0) -> list[str]:
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
        if max_images > 0 and len(out) >= max_images:
            break
    return out


def collect_product_images(
    soup: BeautifulSoup, h1: BeautifulSoup | None, page_url: str, *, max_images: int = 0
) -> list[str]:
    """WordPress/WooCommerce galleries + common CDNs; skips flags/logos."""
    candidates: list[str] = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        candidates.append(urljoin(page_url, og["content"]))

    scope = _product_image_scope(soup, h1)
    in_gallery = scope.select_one(".woocommerce-product-gallery") is not None or (
        scope.get("class") and "product" in " ".join(scope.get("class", [])).lower()
    )

    for img in scope.find_all("img"):
        if _is_nav_or_flag_img(img, ""):
            continue
        if h1 and not in_gallery:
            if img.find_previous("h1") is not h1:
                continue
            prev_h2 = img.find_previous("h2")
            if prev_h2 and "meer van" in prev_h2.get_text(strip=True).lower():
                break
        for url in _urls_from_img(img, page_url):
            if _is_nav_or_flag_img(img, url):
                continue
            candidates.append(url)

    return _dedupe_images(candidates, max_images=max_images)


def scrape_product_page_from_response(
    resp: requests.Response,
    brand_name: str,
    *,
    skip_categories: bool = False,
) -> ScrapedProduct:
    """Parse one HTTP response into ScrapedProduct (single fetch per page)."""
    product = ScrapedProduct(
        product_url=str(resp.url).rstrip("/"), Brand_table=brand_name
    )
    if resp.status_code >= 400:
        product.scrape_ok = False
        product.scrape_error = f"HTTP {resp.status_code}"
        return product

    final_url = product.product_url
    soup = BeautifulSoup(resp.content, "lxml", from_encoding="utf-8")
    h1 = soup.find("h1")

    product.product_name = h1.get_text(strip=True) if h1 else _slug_to_title(final_url)
    product.product_description = clean_product_description(
        _main_paragraphs(soup, h1)
    )
    product.designer, product.designerDescription = _parse_designer(soup)
    product.price = _find_price(soup)
    if not skip_categories:
        product.product_category, product.sub_category = _parse_categories(
            soup, h1, final_url
        )
        capture_source_categories(product)
        normalize_product_categories(product)

    images = collect_product_images(soup, h1, final_url)
    product.product_images = images
    if images:
        product.hero_images = [images[0]]
        product.lifestyle_images = images[1:4] if len(images) > 1 else []
        product.detail_image = images[-1] if len(images) > 2 else ""

    return product


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

    return scrape_product_page_from_response(resp, brand_name)
