"""
Harvink (harvink.nl) — WordPress custom product CPT.

Discovery: product-sitemap.xml (~160+ /product/{slug}/ URLs).
Category: product pages have no furniture category; map from collection
listing pages discovered on the homepage nav (Banken, Fauteuils, …).
No static category invention — unlisted products keep empty category.

Designer / price: not on product pages → left empty.
Images: hero slider + #fotos gallery; white/no-bg → hero, rest → lifestyle.
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
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description

PRODUCT_SITEMAP = "https://www.harvink.nl/product-sitemap.xml"
HOME = "https://www.harvink.nl"
MAX_IMAGES = 0
WP_SIZE_SUFFIX = re.compile(r"-(\d+)x(\d+)(?=\.(jpe?g|png|webp|gif)$)", re.I)

# Skip these when collecting description paragraphs
DESC_SKIP_LABELS = frozenset(
    {
        "maatvoeringen",
        "kleurencombinaties",
        "download prijslijst",
        "prijslijst",
    }
)

HARVINK_LIFESTYLE_HINTS = (
    "lifestyle",
    "sfeer",
    "interior",
    "ambiance",
    "showroom",
    "hometour",
    "setting",
    "room",
    "iimg_",
    "_iimg",
    "beeldbank",
)

# Homepage nav labels that point at collection archives (used only to find links).
COLLECTION_NAV_LABELS = frozenset(
    {
        "banken",
        "fauteuils",
        "stoelen",
        "eetbanken",
        "tafels",
        "erbij",
        "actie",
        "acties",
    }
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


def _is_product_url(url: str) -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    if not path.startswith("/product/"):
        return False
    # archive page
    if path == "/product":
        return False
    parts = [p for p in path.split("/") if p]
    return len(parts) == 2 and parts[0] == "product" and bool(parts[1])


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


def _fullsize_image(url: str) -> str:
    """Prefer original WP upload over -300x200 thumbnails."""
    u = (url or "").split("?")[0].strip()
    return WP_SIZE_SUFFIX.sub("", u)


def _image_key(url: str) -> str:
    path = urlparse(_fullsize_image(url)).path.lower()
    path = re.sub(r"-scaled(?=\.(jpe?g|png|webp|gif)$)", "", path)
    return path


def discover_collection_pages(timeout: float) -> list[tuple[str, str]]:
    """
    Return [(category_label, listing_url), ...] from homepage navigation.
    Label comes from the link text on the site — not invented.
    """
    try:
        resp = _fetch(HOME, timeout)
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []

    soup = _soup(resp.content)
    found: dict[str, str] = {}  # listing_url → label
    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True)
        href = _norm_url(_abs(str(resp.url), a.get("href")))
        if not href or "harvink.nl" not in urlparse(href).netloc.lower():
            continue
        if _is_product_url(href):
            continue
        path = urlparse(href).path.lower().rstrip("/")
        label_key = text.strip().lower()
        # Prefer known collectie nav labels; also accept single-segment archive paths
        # that match those labels (e.g. /banken/).
        path_slug = path.strip("/").split("/")[-1] if path else ""
        if label_key not in COLLECTION_NAV_LABELS and path_slug not in COLLECTION_NAV_LABELS:
            continue
        if path.count("/") > 1:
            # skip deep pages like /harvink/verhaal
            continue
        label = text.strip() or path_slug.replace("-", " ").title()
        # Normalize Actie → use page title from link
        if href not in found:
            found[href] = label

    return [(label, url) for url, label in found.items()]


def _listing_product_urls(listing_url: str, timeout: float) -> list[str]:
    """Paginate Search & Filter listing until a page adds no new products."""
    out: list[str] = []
    seen: set[str] = set()
    for page in range(1, 30):
        page_url = listing_url if page == 1 else f"{listing_url}?sf_paged={page}"
        try:
            resp = _fetch(page_url, timeout)
        except requests.RequestException:
            break
        if resp.status_code >= 400:
            break
        soup = _soup(resp.content)
        added = 0
        for a in soup.select('a[href*="/product/"]'):
            href = _norm_url(_abs(str(resp.url), a.get("href")))
            if not _is_product_url(href) or href in seen:
                continue
            seen.add(href)
            out.append(href)
            added += 1
        if added == 0:
            break
    return out


def build_category_map(timeout: float) -> dict[str, str]:
    """
    product_url → category label from collection listings.
    If a product appears in multiple collections, keep the first discovered.
    """
    mapping: dict[str, str] = {}
    for label, listing_url in discover_collection_pages(timeout):
        for product_url in _listing_product_urls(listing_url, timeout):
            if product_url not in mapping:
                mapping[product_url] = label
    return mapping


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url  # fixed host
    seen: set[str] = set()
    products: list[str] = []

    try:
        resp = _fetch(PRODUCT_SITEMAP, timeout)
        if resp.status_code < 400:
            for loc in _parse_sitemap_locs(resp.text):
                if "harvink.nl" not in loc.lower():
                    continue
                key = _norm_url(loc)
                if _is_product_url(key) and key not in seen:
                    seen.add(key)
                    products.append(key)
    except requests.RequestException:
        pass

    # Fallback / supplement from collection listings
    if not products:
        for _label, listing_url in discover_collection_pages(timeout):
            for href in _listing_product_urls(listing_url, timeout):
                if href not in seen:
                    seen.add(href)
                    products.append(href)

    return products


def _product_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("#overzicht h1")
    if h1:
        text = h1.get_text(" ", strip=True)
        if text:
            return text
    h2 = soup.select_one("#overzicht h2")
    if h2:
        return h2.get_text(" ", strip=True)
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title:
        return soup.title.get_text(" ", strip=True)
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    ov = soup.select_one("#overzicht")
    if ov:
        for p in ov.select("p"):
            classes = " ".join(p.get("class") or []).lower()
            text = p.get_text(" ", strip=True)
            if not text or len(text) < 20:
                continue
            if "font-bold" in classes:
                continue
            if text.strip().lower() in DESC_SKIP_LABELS:
                continue
            parts.append(text)
    if not parts:
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            parts.append(og["content"].strip())
    return clean_product_description("\n\n".join(parts))


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
        # Prefer longer / non-thumbnail URL
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    for img in soup.select("section#hero .slider img, section#hero img"):
        add(img.get("src") or img.get("data-src"))
    for img in soup.select("#fotos img"):
        add(img.get("src") or img.get("data-src"))
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
        urls, timeout, lifestyle_hints=HARVINK_LIFESTYLE_HINTS
    )
    product.product_images = list(urls)
    product.hero_images = hero
    product.lifestyle_images = lifestyle
    product.detail_image = hero[-1] if len(hero) > 2 else ""


def _apply_category(
    product: ScrapedProduct, category_map: dict[str, str]
) -> None:
    cat = category_map.get(_norm_url(product.product_url), "")
    product.product_category = cat
    product.sub_category = ""  # listings don't expose a finer sub on product pages
    capture_source_categories(product)
    normalize_product_categories(product)


def scrape_product_page(
    product_url: str,
    brand_name: str,
    timeout: float,
    *,
    category_map: dict[str, str] | None = None,
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
    product.price = ""
    product.designer = ""
    product.designerDescription = ""
    product.designerImage = ""

    _apply_category(product, category_map or {})
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
    print("Harvink: building category map from collection listings…")
    category_map = build_category_map(timeout)
    print(f"Harvink: category map covers {len(category_map)} listing products")

    urls = discover_product_urls(site_url or HOME, timeout)
    if max_products > 0:
        urls = urls[:max_products]

    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        products.append(
            scrape_product_page(
                url, brand_name, timeout, category_map=category_map
            )
        )
    return urls, products
