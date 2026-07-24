"""
Label (label.nl) — /collectie/{category}/{product} WordPress site.

Discovery: /collectie paths; deduped by canonical URL after redirects (typo slugs
like fautieuls/footstools map to the same product).
Images: all → product_images; white/no-bg → hero_images; room/bg → lifestyle_images.
Description: JSON-LD Product first, else cleaned page paragraphs.
"""
from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers import leolux
from scrapers.extract_common import scrape_product_page_from_response
from scrapers.image_bg import merge_scraped_image_urls, split_images_by_background
from scrapers.taxonomy import (
    capture_source_categories,
    label_category_from_segment,
    normalize_product_categories,
)
from scrapers.text_clean import clean_product_description, description_from_json_ld

WP_UPLOAD_RE = re.compile(
    r"https?://(?:www\.)?label\.nl/wp-content/uploads/[^\"'\s<>\\]+", re.I
)
IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)
WP_SIZE_SUFFIX = re.compile(r"-\d+x\d+(?=\.(jpe?g|png|webp|gif)$)", re.I)
MAX_IMAGES = 0  # 0 = no limit
LABEL_LIFESTYLE_HINTS = (
    "mg_",
    "-scaled",
    "scaled.",
    "interior",
    "sfeer",
    "ambient",
    "lifestyle",
    "showroom",
    "fabriek",
    "beeldbank",
)


def _label_base(site_url: str) -> str:
    url = normalize_url(site_url) or site_url
    if "label.nl" in urlparse(url).netloc.lower():
        return "https://label.nl"
    return url


def _upload_key(url: str) -> str:
    path = urlparse(unescape(url.split("?")[0])).path.lower()
    path = WP_SIZE_SUFFIX.sub("", path)
    path = re.sub(r"-scaled(?=\.(jpe?g|png|webp|gif)$)", "", path)
    return path


def _upload_rank(url: str) -> tuple[int, int]:
    """Prefer full-size uploads over WordPress thumbnails."""
    path = url.lower()
    match = re.search(r"-(\d+)x(\d+)\.", path)
    if match:
        width = int(match.group(1))
        if width <= 150:
            return (0, width)
        return (1, width)
    if "-scaled" in path:
        return (2, 10_000)
    return (3, 10_000)


def _label_images(html: str) -> list[str]:
    best: dict[str, tuple[tuple[int, int], str]] = {}
    for raw in WP_UPLOAD_RE.findall(html):
        url = unescape(raw.split("?")[0])
        low = url.lower()
        if not IMAGE_EXT.search(low):
            continue
        if any(x in low for x in ("logo", "icon", "favicon", "flag", "wpml")):
            continue
        key = _upload_key(url)
        rank = _upload_rank(url)
        prev = best.get(key)
        if not prev or rank > prev[0]:
            best[key] = (rank, url)

    out = [url for _, url in sorted(best.values(), key=lambda item: item[0], reverse=True)]
    return out[:MAX_IMAGES] if MAX_IMAGES > 0 else out


def assign_label_images(
    product: ScrapedProduct,
    urls: list[str],
    *,
    timeout: float,
) -> None:
    """All images in product_images; white/no-bg → hero; coloured bg → lifestyle."""
    if not urls:
        product.product_images = []
        product.hero_images = []
        product.lifestyle_images = []
        product.detail_image = ""
        return

    hero_shots, lifestyle_shots = split_images_by_background(
        urls,
        timeout,
        lifestyle_hints=LABEL_LIFESTYLE_HINTS,
    )
    product.product_images = list(urls)
    product.hero_images = hero_shots
    product.lifestyle_images = lifestyle_shots
    product.detail_image = hero_shots[-1] if len(hero_shots) > 2 else ""


def _norm_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    p = urlparse(u)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{p.scheme}://{host}{p.path.rstrip('/').lower()}"


def _resolve_canonical_url(url: str, timeout: float) -> str:
    """Follow redirects so fautieuls/… and fauteuils/… collapse to one product URL."""
    try:
        resp = requests.head(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            resp = requests.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
        if resp.status_code < 400:
            return _norm_url(str(resp.url))
    except requests.RequestException:
        pass
    return _norm_url(url)


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    raw = leolux.discover_product_urls(_label_base(site_url), timeout)
    seen: set[str] = set()
    out: list[str] = []
    for url in raw:
        canonical = _resolve_canonical_url(url, timeout)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    return sorted(out)


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    try:
        resp = requests.get(
            product_url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        product = ScrapedProduct(
            product_url=product_url.rstrip("/"), Brand_table=brand_name
        )
        product.scrape_ok = False
        product.scrape_error = str(exc)
        return product

    if resp.status_code >= 400:
        product = ScrapedProduct(
            product_url=product_url.rstrip("/"), Brand_table=brand_name
        )
        product.scrape_ok = False
        product.scrape_error = f"HTTP {resp.status_code}"
        return product

    final_url = _norm_url(str(resp.url))
    soup = BeautifulSoup(resp.content, "lxml", from_encoding="utf-8")
    product = scrape_product_page_from_response(
        resp, brand_name, skip_categories=True
    )
    if not product.scrape_ok:
        return product
    product.product_url = final_url

    json_desc = description_from_json_ld(soup)
    if json_desc:
        product.product_description = json_desc
    else:
        product.product_description = clean_product_description(
            product.product_description
        )

    category, _slug, title = leolux._path_meta(final_url)
    if category:
        product.source_product_category = category
        product.source_product_subcategory = ""
        product.product_category = label_category_from_segment(category)
        product.sub_category = ""
    if title and (
        not product.product_name
        or product.product_name.lower() == category.lower()
    ):
        product.product_name = title

    label_images = _label_images(resp.text)
    image_urls = label_images or merge_scraped_image_urls(product)
    assign_label_images(product, image_urls, timeout=timeout)

    if not product.product_images and not product.lifestyle_images:
        product.scrape_ok = False
        product.scrape_error = "No product images found"
        return product

    capture_source_categories(product)
    normalize_product_categories(product)
    return product


def scrape_product_categories(
    product_url: str, brand_name: str, timeout: float
) -> ScrapedProduct:
    """Refresh Label category from /collectie/{segment}/ URL only."""
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

    final_url = _norm_url(str(resp.url))
    product.product_url = final_url
    category, _slug, _title_slug = leolux._path_meta(final_url)
    if not category:
        product.scrape_ok = False
        product.scrape_error = "No category in URL"
        return product

    product.source_product_category = category
    product.source_product_subcategory = ""
    product.product_category = label_category_from_segment(category)
    product.sub_category = ""
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
