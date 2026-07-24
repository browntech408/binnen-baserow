"""
Bree's New World (breesnewworld.nl) — WooCommerce product pages.

Discovery: product-sitemap.xml, NL /collectie/{category}/{product}/ only.
Breadcrumb: Brand → Collectie → {Subcategory} → {Product name}
  - product_category = Collectie
  - sub_category = e.g. Eetkamerstoelen
Designer: optional "Ontwerper: …" in description tab.
Price: always 0 on site (dealer-only) → empty.
Images: .product-single-slide gallery; white/no-bg → hero, rest → lifestyle.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description

PRODUCT_SITEMAP = "https://www.breesnewworld.nl/product-sitemap.xml"
HOME = "https://www.breesnewworld.nl"
MAX_IMAGES = 0
WP_SIZE_SUFFIX = re.compile(r"-(\d+)x(\d+)(?=\.(jpe?g|png|webp|gif)$)", re.I)
DESIGNER_RE = re.compile(r"Ontwerper:\s*(.+?)(?:\.|$)", re.I)

SKIP_CRUMBS = frozenset(
    {
        "bree's new world",
        "brees new world",
        "home",
        "winkel",
        "shop",
    }
)

# Collectie / Collection is the top category crumb — keep it.
CATEGORY_CRUMBS = frozenset({"collectie", "collection"})


BREES_LIFESTYLE_HINTS = (
    "lifestyle",
    "sfeer",
    "interior",
    "ambiance",
    "setting",
    "room",
    "showroom",
    "hometour",
    "interieur",
)


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _abs(base: str, href: str | None) -> str:
    if not href:
        return ""
    return urljoin(base, href.strip())


def _soup(content: bytes | str) -> BeautifulSoup:
    return BeautifulSoup(content, "html.parser")


def _fetch(url: str, timeout: float) -> requests.Response:
    return requests.get(
        url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
    )


def _decode(text: str) -> str:
    return unescape(text or "").replace("\xa0", " ").strip()


def _is_product_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if not path.startswith("/collectie/"):
        return False
    # skip language mirrors
    if any(f"/{lang}/" in f"/{path}" for lang in ("en", "de", "da")):
        return False
    parts = [p for p in path.strip("/").split("/") if p]
    # collectie / category / product
    return len(parts) == 3 and parts[0] == "collectie" and bool(parts[2])


def _parse_sitemap_locs(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.findall(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", xml_text, re.I)
    locs: list[str] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag == "loc" and el.text:
            locs.append(el.text.strip())
    return locs


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    seen: set[str] = set()
    products: list[str] = []
    try:
        resp = _fetch(PRODUCT_SITEMAP, timeout)
        if resp.status_code < 400:
            for loc in _parse_sitemap_locs(resp.text):
                key = _norm_url(loc)
                if "breesnewworld.nl" not in key.lower():
                    continue
                if _is_product_url(key) and key not in seen:
                    seen.add(key)
                    products.append(key)
    except requests.RequestException:
        pass
    return products


def _breadcrumb_labels(soup: BeautifulSoup) -> list[str]:
    labels: list[str] = []
    for div in soup.select(".component-breadcrumbs .breadcrumbs-in > div, .breadcrumbs-in > div"):
        a = div.select_one("a")
        text = _decode(a.get_text(" ", strip=True) if a else div.get_text(" ", strip=True))
        if text:
            labels.append(text)
    return labels


def _apply_page_categories(product: ScrapedProduct, soup: BeautifulSoup) -> None:
    """
    Breadcrumb (as on site):
      1 Brand (Bree's New World)  → skip
      2 Collectie / Collection    → product_category
      3 Eetkamerstoelen / …       → sub_category
      4 Product name              → skip
    Store only crumbs from the page — never invent.
    """
    crumbs = _breadcrumb_labels(soup)
    # Drop brand / home only
    rest: list[str] = []
    for c in crumbs:
        low = _decode(c).lower().replace("’", "'").replace("'", "'")
        if low in SKIP_CRUMBS or "bree" in low:
            continue
        rest.append(_decode(c))

    # Drop last crumb when it is the product name
    name = (product.product_name or "").strip().lower()
    if rest and name:
        last = rest[-1].strip().lower()
        if last == name or last in name or name in last:
            rest = rest[:-1]

    category = ""
    subcategory = ""
    if rest:
        first_low = rest[0].strip().lower().replace("’", "'")
        if first_low in CATEGORY_CRUMBS or first_low == "collectie":
            category = rest[0]
            subcategory = " / ".join(rest[1:]) if len(rest) > 1 else ""
        else:
            # unexpected trail — keep as-is without inventing
            category = rest[0]
            subcategory = " / ".join(rest[1:]) if len(rest) > 1 else ""

    # URL path fallback only if breadcrumb missing
    if not category:
        parts = [p for p in urlparse(product.product_url).path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "collectie":
            category = "Collectie"
            subcategory = parts[1].replace("-", " ").title()

    product.product_category = category
    product.sub_category = subcategory
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1.title") or soup.select_one("h1")
    if h1:
        text = _decode(h1.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return _decode(og["content"])
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    el = soup.select_one("#tab-description") or soup.select_one(
        ".woocommerce-Tabs-panel--description"
    )
    if not el:
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            return clean_product_description(_decode(og["content"]))
        return ""
    text = el.get_text("\n\n", strip=True)
    text = re.sub(r"^Productinformatie\s*", "", text, flags=re.I).strip()
    return clean_product_description(text)


def _designer_from_description(text: str) -> str:
    m = DESIGNER_RE.search(text or "")
    return _decode(m.group(1)) if m else ""


def _fullsize_image(url: str) -> str:
    u = (url or "").split("?")[0].strip()
    return WP_SIZE_SUFFIX.sub("", u)


def _image_key(url: str) -> str:
    path = urlparse(_fullsize_image(url)).path.lower()
    path = re.sub(r"-scaled(?=\.(jpe?g|png|webp|gif)$)", "", path)
    return path


def _gallery_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    best: dict[str, str] = {}

    def add(src: str | None) -> None:
        if not src:
            return
        full = _fullsize_image(_abs(page_url, src))
        if not full:
            return
        low = full.lower()
        if any(x in low for x in ("logo", "icon", "favicon", "sprite", "placeholder")):
            return
        if not re.search(r"\.(jpe?g|png|webp|gif)$", low):
            return
        key = _image_key(full)
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    for img in soup.select(".product-single-slide img, .product-single-slider img"):
        add(img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
    if not best:
        og = soup.find("meta", property="og:image")
        if og:
            add(og.get("content"))

    out = list(best.values())
    if MAX_IMAGES > 0:
        return out[:MAX_IMAGES]
    return out


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
        urls, timeout, lifestyle_hints=BREES_LIFESTYLE_HINTS
    )
    product.product_images = list(urls)
    product.hero_images = hero
    product.lifestyle_images = lifestyle
    product.detail_image = hero[-1] if len(hero) > 2 else ""


def scrape_product_page(
    product_url: str, brand_name: str, timeout: float
) -> ScrapedProduct:
    product = ScrapedProduct(
        product_url=_norm_url(product_url), Brand_table=brand_name
    )
    try:
        resp = _fetch(product_url, timeout)
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
    soup = _soup(resp.content)

    product.product_name = _product_name(soup)
    product.product_description = _product_description(soup)
    product.price = ""  # dealer-only / 0 on site
    product.designer = _designer_from_description(product.product_description)
    product.designerDescription = ""
    product.designerImage = ""

    _apply_page_categories(product, soup)
    images = _gallery_images(soup, final_url)
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
    # robots.txt Crawl-delay: 10 — keep polite default if caller passes 1.0
    delay = max(delay_seconds, 1.0)
    urls = discover_product_urls(normalize_url(site_url) or HOME, timeout)
    if max_products > 0:
        urls = urls[:max_products]
    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay > 0:
            time.sleep(delay)
        products.append(scrape_product_page(url, brand_name, timeout))
    return urls, products
