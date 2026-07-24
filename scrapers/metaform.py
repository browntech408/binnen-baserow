"""
Metaform (metaformmeubelen.nl) — WordPress pages (Create / SiteOrigin).

Discovery: "alle-*-2024" collection listing pages (nav categories).
Category from listing membership (site nav):
  Tafels → Bijzettafels/salontafels | Eetkamertafels | Sidetables
  Diversen / Outdoor → top-level category, empty sub
Designer: "Ontwerp: …" in entry-content.
Price: not on pages → empty.
Images: entry-content uploads; white/no-bg → hero, rest → lifestyle.
"""
from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description

HOME = "https://metaformmeubelen.nl"
MAX_IMAGES = 0
WP_SIZE_SUFFIX = re.compile(r"-(\d+)x(\d+)(?=\.(jpe?g|png|webp|gif)$)", re.I)
DESIGNER_RE = re.compile(
    r"Ontwerp\s*:\s*([^\n\r]+)",
    re.I,
)

# Listing URL → (product_category, sub_category) from site nav labels
LISTING_CATEGORIES: dict[str, tuple[str, str]] = {
    "alle-bijzettafels-salontafels-2024": ("Tafels", "Bijzettafels/salontafels"),
    "alle-eetkamertafels-2024": ("Tafels", "Eetkamertafels"),
    "alle-sidetables-2024": ("Tafels", "Sidetables"),
    "alle-diversen-2024": ("Diversen", ""),
    "alle-outdoor-2024": ("Outdoor", ""),
}

SKIP_SLUGS = frozenset(
    {
        "contact",
        "materialen",
        "materialen-oud",
        "onderhoud",
        "onderhoud-2",
        "verkooppunten",
        "europa",
        "outlet",
        "ral",
        "cache",
        "testpagina",
        "voorbeeld-pagina",
        "metaform-prijslijst-mei-2022",
        "outdoor_oud",
        "outdoor-collectie",
        "alle-sidetables",
        "bijzettafels",
        "eetkamertafels",
        "sidetables",
        "diversen",
        "outdoor",
        "author",
        "project",
        "skill",
        "home",
    }
)

METAFORM_LIFESTYLE_HINTS = (
    "sfeer",
    "lifestyle",
    "interior",
    "ambiance",
    "setting",
    "room",
    "showroom",
    "van-waay",
    "soetekouw",
    "hometour",
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


def _slug_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[-1].lower() if parts else ""


def _is_product_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().replace("www.", "")
    if host and host != "metaformmeubelen.nl":
        return False
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) != 1:
        return False
    slug = parts[0].lower()
    if slug in SKIP_SLUGS:
        return False
    if slug.startswith("alle-"):
        return False
    if slug.startswith("author"):
        return False
    return True


def _listing_product_links(listing_url: str, timeout: float) -> list[str]:
    try:
        resp = _fetch(listing_url, timeout)
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []
    soup = _soup(resp.content)
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = _norm_url(_abs(listing_url, a.get("href")))
        if not _is_product_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    # url → (cat, sub); first listing wins if product appears twice
    cats: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    for slug, cat_pair in LISTING_CATEGORIES.items():
        listing = f"{HOME}/{slug}/"
        for href in _listing_product_links(listing, timeout):
            if href not in cats:
                cats[href] = cat_pair
                order.append(href)
    # stash for scrape_product_page via module-level cache
    _CATEGORY_BY_URL.clear()
    _CATEGORY_BY_URL.update(cats)
    return order


_CATEGORY_BY_URL: dict[str, tuple[str, str]] = {}


def _apply_listing_categories(product: ScrapedProduct) -> None:
    key = _norm_url(product.product_url)
    # NL product URL may match discovery EN-less key
    pair = _CATEGORY_BY_URL.get(key)
    if not pair:
        # try without trailing differences / alternate path
        for u, p in _CATEGORY_BY_URL.items():
            if _slug_from_url(u) == _slug_from_url(key):
                pair = p
                break
    if pair:
        product.product_category = pair[0]
        product.sub_category = pair[1]
    else:
        product.product_category = ""
        product.sub_category = ""
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1:
        text = _decode(h1.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return _decode(og["content"].split("|")[0])
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if title:
        return _decode(title.split("|")[0])
    return ""


def _entry_content(soup: BeautifulSoup) -> BeautifulSoup | None:
    return soup.select_one("article .entry-content") or soup.select_one(
        ".entry-content"
    )


def _product_description(soup: BeautifulSoup) -> str:
    el = _entry_content(soup)
    if not el:
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            return clean_product_description(_decode(og["content"]))
        return ""
    # clone-ish: drop overview nav links noise at end
    text = el.get_text("\n", strip=True)
    text = re.split(r"\nOVERZICHT\b", text, maxsplit=1, flags=re.I)[0]
    return clean_product_description(text)


def _designer_from_text(text: str) -> str:
    m = DESIGNER_RE.search(text or "")
    if not m:
        return ""
    return _decode(m.group(1)).strip(" .;")


def _fullsize_image(url: str) -> str:
    u = (url or "").split("?")[0].strip()
    u = WP_SIZE_SUFFIX.sub("", u)
    return u


def _image_key(url: str) -> str:
    path = urlparse(_fullsize_image(url)).path.lower()
    return re.sub(r"-e\d+(?=\.(jpe?g|png|webp|gif)$)", "", path, flags=re.I)


def _gallery_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    best: dict[str, str] = {}
    el = _entry_content(soup)

    def add(src: str | None) -> None:
        if not src:
            return
        full = _fullsize_image(_abs(page_url, src))
        if not full:
            return
        low = full.lower()
        if "wp-content/uploads" not in low:
            return
        if any(x in low for x in ("logo", "icon", "favicon", "sprite", "placeholder")):
            return
        if not re.search(r"\.(jpe?g|png|webp|gif)$", low):
            return
        key = _image_key(full)
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    if el:
        for img in el.select("img"):
            add(img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
            srcset = img.get("srcset") or ""
            for part in srcset.split(","):
                add(part.strip().split(" ")[0])
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
        urls, timeout, lifestyle_hints=METAFORM_LIFESTYLE_HINTS
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

    # listing / home redirects
    if not _is_product_url(final_url):
        product.scrape_ok = False
        product.scrape_error = "Not a product page"
        return product

    product.product_name = _product_name(soup)
    product.product_description = _product_description(soup)
    product.price = ""
    product.designer = _designer_from_text(product.product_description)
    product.designerDescription = ""
    product.designerImage = ""

    _apply_listing_categories(product)
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
