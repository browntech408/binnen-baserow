"""
Bert Plantagie (bertplantagie.com) — WooCommerce shop.

Product URLs: /product/{slug}
Description: intro in .contact_info + specs in .product-specifications (not after h1).
"""
from __future__ import annotations

import time

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS
from product_schema import ScrapedProduct
from product_scraper import _find_price, _parse_categories, _parse_designer, _slug_to_title
from scrapers.extract_common import collect_product_images
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description, is_junk_paragraph
from scrapers.woocommerce import _listing_categories, _parse_woocommerce_breadcrumb, discover_product_urls

MAX_DESC_PARAS = 6


def _bert_plantagie_description(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
    for sel in (".contact_info p", ".product-specifications p", ".product-materials p"):
        for p in soup.select(sel):
            text = p.get_text(" ", strip=True)
            if len(text) < 40 or is_junk_paragraph(text):
                continue
            if text not in chunks:
                chunks.append(text)
            if len(chunks) >= MAX_DESC_PARAS:
                break
        if len(chunks) >= MAX_DESC_PARAS:
            break
    return clean_product_description("\n\n".join(chunks))


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
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
    soup = BeautifulSoup(resp.content, "lxml", from_encoding="utf-8")
    h1 = soup.find("h1")

    product.product_name = h1.get_text(strip=True) if h1 else _slug_to_title(final_url)
    product.product_description = _bert_plantagie_description(soup)
    product.designer, product.designerDescription = _parse_designer(soup)
    product.price = _find_price(soup)
    product.product_category, product.sub_category = _parse_categories(soup, h1, final_url)

    images = collect_product_images(soup, h1, final_url)
    product.product_images = images
    if images:
        product.hero_images = [images[0]]
        product.lifestyle_images = images[1:4] if len(images) > 1 else []
        product.detail_image = images[-1] if len(images) > 2 else ""

    key = final_url
    listing = _listing_categories.get(key)
    if listing and listing[0]:
        product.product_category, product.sub_category = listing
        product.source_product_category = listing[0]
        product.source_product_subcategory = listing[1]

    bcat, bsub = _parse_woocommerce_breadcrumb(soup)
    if bcat or bsub:
        crumb = soup.select_one(".woocommerce-breadcrumb")
        crumbs: list[str] = []
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

    if not product.product_description:
        product.scrape_ok = False
        product.scrape_error = "No product description found"
        return product

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
