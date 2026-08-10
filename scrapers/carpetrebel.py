"""
CarpetRebel / Janssens Oriënt (carpetrebel.com) — Magento 1 B2B catalog.

Product URLs: /{slug}  e.g. /renzo-blauw  (category is NOT in the URL)
Discovery: media/sitemap.xml (single-segment product slugs).

Categories from breadcrumbs on each product page:
  Katoen / Renzo / RENZO blauw  → category=Katoen, sub_category=Renzo

Images: all → product_images; white/no-bg → hero_images; room/bg → lifestyle_images.
Description: JSON-LD first, else specs block (Maakwijze, Materiaal, Omschrijving).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from product_scraper import _slug_to_title
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description, description_from_json_ld

SITEMAP_URL = "https://www.carpetrebel.com/media/sitemap.xml"
PRODUCT_VIEW_MARKERS = ("catalog-product-view", "product-view")
CACHE_PATH_RE = re.compile(
    r"/media/catalog/product/cache/[^/]+/[^/]+/[^/]+/[^/]+/", re.I
)

SKIP_CRUMBS = frozenset({"", "/", "home"})
STATIC_SLUGS = frozenset(
    {
        "tapijten",
        "schapenvachten",
        "koeienhuiden",
        "ondertapijt",
        "specials",
        "katoen",
        "polyester",
        "wol",
        "overig",
        "kussens",
        "contacts",
        "contact",
        "over-ons",
        "b2b",
        "customer",
        "account",
        "login",
        "algemene-voorwaarden",
        "disclaimer",
        "cookies",
        "maathulp",
        "veelgestelde-vragen",
        "verzending",
        "privacy",
        "onestepcheckout",
        "catalogsearch",
        "sendfriend",
        "home",
        "index",
    }
)

CARPETREBEL_LIFESTYLE_HINTS = (
    "interieur",
    "interior",
    "sfeer",
    "ambient",
    "lifestyle",
    "room",
    "kamer",
    "woonkamer",
    "living",
    "scene",
)


def _host_key(site_url: str) -> str:
    host = urlparse(normalize_url(site_url) or site_url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_carpetrebel_host(url: str, host_key: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host == host_key or host.endswith("carpetrebel.com")


def _is_product_html(html: str) -> bool:
    return any(marker in html for marker in PRODUCT_VIEW_MARKERS)


def _direct_image_url(url: str) -> str:
    clean = (url or "").split("?")[0]
    return CACHE_PATH_RE.sub("/media/catalog/product/", clean)


def _image_key(url: str) -> str:
    return urlparse(_direct_image_url(url)).path.lower()


def _urls_from_sitemap(host_key: str, timeout: float) -> list[str]:
    try:
        resp = requests.get(SITEMAP_URL, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    root = ET.fromstring(resp.content)
    singles: list[str] = []
    for loc in root.findall(".//{*}loc"):
        url = (loc.text or "").strip().rstrip("/")
        if not url or not _is_carpetrebel_host(url, host_key):
            continue
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        if len(parts) != 1:
            continue
        slug = parts[0].lower()
        if slug in STATIC_SLUGS or "ondertapijt" in slug:
            continue
        singles.append(url)

    return sorted(set(singles))


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    """Product URLs from sitemap (single-segment slugs, static pages excluded)."""
    host_key = _host_key(normalize_url(site_url) or site_url)
    return _urls_from_sitemap(host_key, timeout)


def _parse_breadcrumbs(soup: BeautifulSoup, product_name: str) -> tuple[str, str]:
    """
    Breadcrumb: Katoen / Renzo / RENZO blauw
    → category Katoen, sub_category Renzo (product name stripped).
    """
    bc = soup.select_one(".breadcrumbs")
    if not bc:
        return "", ""

    parts: list[str] = []
    for li in bc.find_all("li"):
        text = li.get_text(strip=True).strip("/").strip()
        if not text or text == "/":
            continue
        if text.lower() in SKIP_CRUMBS:
            continue
        parts.append(text)

    if parts and product_name and _norm(parts[-1]) == _norm(product_name):
        parts = parts[:-1]

    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


def _carpetrebel_description(soup: BeautifulSoup) -> str:
    """Build description from specs (Maakwijze / Materiaal / Omschrijving)."""
    lines: list[str] = []
    for li in soup.select(".product-shop .specs li"):
        label_el = li.find("p")
        value_el = li.find("span")
        if not value_el:
            continue
        value = value_el.get_text(" ", strip=True)
        if not value:
            continue
        label = label_el.get_text(strip=True).rstrip(":") if label_el else ""
        if label:
            lines.append(f"{label}: {value}")
        else:
            lines.append(value)
    if lines:
        return clean_product_description("\n".join(lines))

    json_desc = description_from_json_ld(soup)
    if json_desc:
        return json_desc

    for meta in (
        soup.find("meta", property="og:description"),
        soup.find("meta", attrs={"name": "description"}),
    ):
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 20:
                return clean_product_description(content)
    return ""


def _carpetrebel_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    raw_urls: list[str] = []
    selectors = (
        ".product-image-gallery img",
        ".product-img-box img",
        "img.gallery-image",
        "[data-zoom-image]",
    )
    for sel in selectors:
        for img in soup.select(sel):
            for attr in ("data-zoom-image", "data-src", "src"):
                val = (img.get(attr) or "").strip()
                if val and not val.startswith("data:"):
                    raw_urls.append(urljoin(page_url, val))

    for el in soup.select("[data-zoom-image]"):
        val = (el.get("data-zoom-image") or "").strip()
        if val:
            raw_urls.append(urljoin(page_url, val))

    best: dict[str, str] = {}
    for url in raw_urls:
        low = url.lower()
        if "cdn.carpetrebel.com/media/catalog/product" not in low:
            continue
        if any(x in low for x in ("logo", "carpetrebel.png", "/thumbnail/135x/")):
            continue
        if not low.endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        key = _image_key(url)
        direct = _direct_image_url(url)
        prev = best.get(key)
        if not prev or "1800x" in url or len(direct) >= len(prev):
            best[key] = direct

    return list(best.values())


def assign_carpetrebel_images(
    product: ScrapedProduct,
    urls: list[str],
    *,
    timeout: float,
) -> None:
    if not urls:
        product.product_images = []
        product.hero_images = []
        product.lifestyle_images = []
        product.detail_image = ""
        return

    hero_shots, lifestyle_shots = split_images_by_background(
        urls,
        timeout=timeout,
        lifestyle_hints=CARPETREBEL_LIFESTYLE_HINTS,
    )
    product.product_images = list(urls)
    product.hero_images = hero_shots
    product.lifestyle_images = lifestyle_shots
    product.detail_image = hero_shots[-1] if len(hero_shots) > 2 else ""


def scrape_product_categories(
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
    product.product_category, product.sub_category = _parse_breadcrumbs(
        soup, product.product_name
    )
    if not product.product_category:
        product.scrape_ok = False
        product.scrape_error = "No breadcrumb category found"
        return product

    capture_source_categories(product)
    normalize_product_categories(product)
    return product


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

    if not _is_product_html(resp.text):
        product.scrape_ok = False
        product.scrape_error = "Not a product page"
        return product

    final_url = str(resp.url).rstrip("/")
    product.product_url = final_url
    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")
    product.product_name = h1.get_text(strip=True) if h1 else _slug_to_title(final_url)

    product.product_description = _carpetrebel_description(soup)
    product.product_category, product.sub_category = _parse_breadcrumbs(
        soup, product.product_name
    )

    images = _carpetrebel_images(soup, final_url)
    assign_carpetrebel_images(product, images, timeout=timeout)

    if not product.product_images:
        product.scrape_ok = False
        product.scrape_error = "No product images found"
        return product
    if not product.product_category:
        product.scrape_ok = False
        product.scrape_error = "No breadcrumb category found"
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
