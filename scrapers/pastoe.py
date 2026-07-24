"""
Pastoe (pastoe.com) — Next.js / Moooi headless storefront.

Discovery: /en/collection → __NEXT_DATA__ buildId →
  /_next/data/{buildId}/en/collection.json (~36 products).
Product data: /_next/data/{buildId}/en/product/{slug}.json

Category: longest categories[].full_label from JSON (page only).
Designer + gallery + price from product JSON.
Images: galleryImages; white/no-bg → hero, rest → lifestyle.
"""
from __future__ import annotations

import json
import re
import time
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description

BASE = "https://www.pastoe.com"
COLLECTION_EN = f"{BASE}/en/collection"
MAX_IMAGES = 0

PASTOE_LIFESTYLE_HINTS = (
    "lifestyle",
    "setting",
    "interior",
    "ambiance",
    "room",
    "applied",
    "scene",
    "gallery",
    "render set",
    "/applied/",
    "_setting_",
)


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _fetch(url: str, timeout: float) -> requests.Response:
    return requests.get(
        url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
    )


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(unescape(html), "html.parser").get_text("\n\n", strip=True)


def _get_build_id(timeout: float) -> str:
    resp = _fetch(COLLECTION_EN, timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to load Pastoe collection page: HTTP {resp.status_code}")
    m = re.search(r'"buildId":"([^"]+)"', resp.text)
    if not m:
        # fallback: __NEXT_DATA__ script
        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if script and script.string:
            data = json.loads(script.string)
            build_id = str(data.get("buildId") or "").strip()
            if build_id:
                return build_id
        raise RuntimeError("Pastoe buildId not found")
    return m.group(1)


def _collection_json(build_id: str, timeout: float) -> dict:
    url = f"{BASE}/_next/data/{build_id}/en/collection.json"
    resp = _fetch(url, timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"Pastoe collection JSON failed: HTTP {resp.status_code}")
    return resp.json()


def _product_json(build_id: str, slug: str, timeout: float) -> dict:
    url = f"{BASE}/_next/data/{build_id}/en/product/{slug}.json"
    resp = _fetch(url, timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"Pastoe product JSON failed for {slug}: HTTP {resp.status_code}")
    return resp.json()


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    build_id = _get_build_id(timeout)
    data = _collection_json(build_id, timeout)
    cards = data.get("pageProps", {}).get("firstPageOfProductCards") or {}
    items = cards.get("items") or []
    urls: list[str] = []
    seen: set[str] = set()
    for item in items:
        href = str(item.get("href") or "").strip()
        if not href.startswith("/product/"):
            continue
        slug = href.split("/product/", 1)[1].strip("/")
        if not slug:
            continue
        url = _norm_url(f"{BASE}/en/product/{slug}")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _slug_from_url(product_url: str) -> str:
    return product_url.rstrip("/").split("/product/")[-1]


def _categories_from_product(product: dict) -> tuple[str, str]:
    cats = product.get("categories") or []
    best: list[str] = []
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        labels = cat.get("full_label") or []
        if isinstance(labels, list) and len(labels) > len(best):
            best = [str(x).strip() for x in labels if str(x).strip()]
    if not best:
        return "", ""
    category = best[0]
    subcategory = " / ".join(best[1:]) if len(best) > 1 else ""
    return category, subcategory


def _gallery_urls(product: dict) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for img in product.get("galleryImages") or []:
        if not isinstance(img, dict):
            continue
        src = str(img.get("src") or img.get("url") or "").strip()
        if not src or src in seen:
            continue
        seen.add(src)
        out.append(src)
        if MAX_IMAGES > 0 and len(out) >= MAX_IMAGES:
            break
    return out


def _clean_price(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return text
    if "," in cleaned and "." in cleaned:
        # 1.995,00 (EU) vs 1,995.00 (US)
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # 825,00 or 825,0 → decimal comma
        if re.search(r",\d{1,2}$", cleaned):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    return cleaned or text


def assign_images(
    product: ScrapedProduct, urls: list[str], *, timeout: float
) -> None:
    if not urls:
        product.product_images = []
        product.hero_images = []
        product.lifestyle_images = []
        product.detail_image = ""
        return
    hero, lifestyle = split_images_by_background(
        urls, timeout, lifestyle_hints=PASTOE_LIFESTYLE_HINTS
    )
    product.product_images = list(urls)
    product.hero_images = hero
    product.lifestyle_images = lifestyle
    product.detail_image = hero[-1] if len(hero) > 2 else ""


def scrape_product_page(
    product_url: str,
    brand_name: str,
    timeout: float,
    *,
    build_id: str | None = None,
) -> ScrapedProduct:
    product = ScrapedProduct(product_url=_norm_url(product_url), Brand_table=brand_name)
    slug = _slug_from_url(product_url)
    if not slug:
        product.scrape_ok = False
        product.scrape_error = "Invalid product URL"
        return product

    try:
        bid = build_id or _get_build_id(timeout)
        payload = _product_json(bid, slug, timeout)
    except (requests.RequestException, RuntimeError, json.JSONDecodeError) as exc:
        product.scrape_ok = False
        product.scrape_error = str(exc)
        return product

    page_props = payload.get("pageProps") or {}
    meta = page_props.get("meta") or {}
    pdata = page_props.get("product") or {}

    product.product_url = _norm_url(f"{BASE}/en/product/{slug}")
    product.product_name = str(meta.get("title") or pdata.get("heading") or "").strip()

    desc_html = str(pdata.get("description") or meta.get("description") or "")
    product.product_description = clean_product_description(_html_to_text(desc_html))

    product.price = _clean_price(str(pdata.get("price") or ""))

    cat, sub = _categories_from_product(pdata)
    product.product_category = cat
    product.sub_category = sub
    capture_source_categories(product)
    normalize_product_categories(product)

    designer = pdata.get("designer") or {}
    if isinstance(designer, dict):
        product.designer = str(designer.get("name") or "").strip()
        product.designerDescription = clean_product_description(
            _html_to_text(str(designer.get("description") or ""))
        )
        image = designer.get("image") or {}
        if isinstance(image, dict):
            product.designerImage = str(image.get("src") or "").strip()

    images = _gallery_urls(pdata)
    assign_images(product, images, timeout=timeout)

    if not product.product_name:
        product.scrape_ok = False
        product.scrape_error = "No product name found"
        return product
    if not product.product_images and not product.hero_images:
        product.scrape_ok = False
        product.scrape_error = "No product images found"
        return product
    return product


def scrape_brand_products(
    site_url: str,
    brand_name: str,
    *,
    timeout: float = 30,
    max_products: int = 5,
    delay_seconds: float = 1.0,
) -> tuple[list[str], list[ScrapedProduct]]:
    _ = normalize_url(site_url)
    build_id = _get_build_id(timeout)
    urls = discover_product_urls(site_url or BASE, timeout)
    if max_products > 0:
        urls = urls[:max_products]

    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        products.append(
            scrape_product_page(url, brand_name, timeout, build_id=build_id)
        )
    return urls, products
