"""
Pode (pode.eu) — Storyblok / Next.js product pages.

Discovery: /collection/{category}/{product}
Categories: URL path /collection/{segment}/{product} (Storyblok/Apollo JSON has no category).
Images: all → product_images; white/no-bg → hero_images; room/bg → lifestyle_images.
"""
from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS
from product_schema import ScrapedProduct
from scrapers import collection_paths
from scrapers.extract_common import scrape_product_page_from_response
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import normalize_product_categories
from scrapers.text_clean import clean_product_description, description_from_json_ld

STORYBLOK_IMAGE_RE = re.compile(
    r"https://a\.storyblok\.com/f/95982/[^\"'\s<>\\]+", re.I
)
MAX_IMAGES = 0  # 0 = no limit
PODE_LIFESTYLE_HINTS = (
    "hometour",
    "wonderwood",
    "interior",
    "ambiance",
    "lifestyle",
    "room",
    "-hero.",
    "/hero.",
)


def _product_slug(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[-1].lower() if parts else ""


def _storyblok_key(url: str) -> str:
    return urlparse(unescape(url.split("?")[0])).path.lower()


def _apply_pode_categories(product: ScrapedProduct, final_url: str) -> None:
    """
    Category from /collection/{segment}/{product} URL path.
    Pode pages have no breadcrumb; Storyblok product JSON has no category field.
    """
    category_seg, _slug, title = collection_paths._path_meta(final_url)
    if not category_seg:
        return

    product.source_product_category = category_seg
    product.source_product_subcategory = ""
    product.product_category = category_seg
    product.sub_category = ""

    normalize_product_categories(product)

    if product.sub_category and not product.source_product_subcategory:
        product.source_product_subcategory = product.sub_category


def _pode_images(html: str, product_url: str) -> list[str]:
    slug = _product_slug(product_url)
    if not slug:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for raw in STORYBLOK_IMAGE_RE.findall(html):
        url = unescape(raw.split("?")[0])
        path = urlparse(url).path.lower()
        if slug not in path and slug.replace("-", "") not in path.replace("-", ""):
            continue
        if any(x in path for x in ("logo", "icon", "favicon")):
            continue
        key = _storyblok_key(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if MAX_IMAGES > 0 and len(out) >= MAX_IMAGES:
            break
    return out


def assign_pode_images(
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
        lifestyle_hints=PODE_LIFESTYLE_HINTS,
    )
    product.product_images = list(urls)
    product.hero_images = hero_shots
    product.lifestyle_images = lifestyle_shots
    product.detail_image = hero_shots[-1] if len(hero_shots) > 2 else ""


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

    final_url = str(resp.url).rstrip("/")
    html = resp.text
    soup = BeautifulSoup(resp.content, "lxml", from_encoding="utf-8")

    product = scrape_product_page_from_response(
        resp, brand_name, skip_categories=True
    )
    if not product.scrape_ok:
        return product

    json_desc = description_from_json_ld(soup)
    if json_desc:
        product.product_description = json_desc
    else:
        product.product_description = clean_product_description(
            product.product_description
        )

    _apply_pode_categories(product, final_url)

    _category_seg, _slug, title = collection_paths._path_meta(final_url)
    if title and (
        not product.product_name
        or product.product_name.lower() == _category_seg.lower()
    ):
        product.product_name = title

    pode_images = _pode_images(html, final_url)
    image_urls = pode_images or list(product.product_images or [])
    assign_pode_images(product, image_urls, timeout=timeout)

    if not product.product_images and not product.lifestyle_images:
        product.scrape_ok = False
        product.scrape_error = "No product images found"
        return product

    return product


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    return collection_paths.discover_product_urls(site_url, timeout)


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
