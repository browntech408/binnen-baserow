"""
CS Rugs (csrugs.com) — WordPress collection catalog.

Discovery: collection-sitemap.xml, EN /collection/{slug}/ only
  (NL/DE mirrors skipped). WooCommerce /product/ designer pages skipped
  (bios / gallery, not the main rug catalog).

Breadcrumb: Collection → Custom made rugs|Wall objects → Product
  product_category = Collection
  sub_category = Custom made rugs | Wall objects
Designer: from og:description / intro ("Design by …").
Price: custom-made → empty.
Images: main content uploads (skip logo / magazine / swatches).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import normalize_url
from product_schema import ScrapedProduct
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description

HOME = "https://www.csrugs.com"
COLLECTION_SITEMAP = "https://www.csrugs.com/collection-sitemap.xml"
MAX_IMAGES = 0
WP_SIZE_SUFFIX = re.compile(r"-(\d+)x(\d+)(?=\.(jpe?g|png|webp|gif)$)", re.I)
SCALED_SUFFIX = re.compile(r"-scaled(?=\.(jpe?g|png|webp|gif)$)", re.I)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SKIP_CRUMBS = frozenset({"home", "cs rugs", "csrugs"})
CATEGORY_CRUMBS = frozenset({"collection", "collectie", "kollektion"})

DESIGNER_PATTERNS = (
    re.compile(r"Design by\s+([A-Z][\w.''\-]+(?:\s+[A-Z][\w.''\-]+){0,4})", re.I),
    re.compile(
        r"design of (?:German |Dutch |French )?top designer\s+([A-Z][A-Z\s.''\-]+)",
        re.I,
    ),
    re.compile(r"designer\s+([A-Z][\w.''\-]+(?:\s+[A-Z][\w.''\-]+){0,4})", re.I),
)

CSRUGS_LIFESTYLE_HINTS = (
    "room",
    "ambient",
    "interior",
    "lifestyle",
    "setting",
    "dining",
    "coffee-table",
    "horizontal",
    "vertical",
    "bird-view",
    "shooting",
    "whs-",
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
        url, headers=BROWSER_HEADERS, timeout=timeout, allow_redirects=True
    )


def _decode(text: str) -> str:
    return unescape(text or "").replace("\xa0", " ").strip()


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


def _is_en_collection_url(url: str) -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    parts = [p for p in path.split("/") if p]
    # /collection/{slug}
    if len(parts) != 2 or parts[0] != "collection":
        return False
    if parts[1] in {"collection"}:
        return False
    # skip language mirrors (/nl/collection/…)
    if parts[0] in {"nl", "de", "en"}:
        return False
    return True


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    try:
        resp = _fetch(COLLECTION_SITEMAP, timeout)
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for loc in _parse_sitemap_locs(resp.text):
        if "/nl/" in loc.lower() or "/de/" in loc.lower():
            continue
        if not _is_en_collection_url(loc):
            continue
        key = _norm_url(loc).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(_norm_url(loc))
    return out


def _breadcrumb_labels(soup: BeautifulSoup) -> list[str]:
    root = soup.select_one(".breadcrumbs")
    if not root:
        return []
    labels: list[str] = []
    # Prefer linked crumbs + current
    for el in root.select("a, span.breadcrumb_last, span[aria-current='page']"):
        text = _decode(el.get_text(" ", strip=True))
        if text:
            labels.append(text)
    if labels:
        return labels
    # fallback: split plain text
    raw = _decode(root.get_text(">", strip=True))
    return [p.strip() for p in raw.split(">") if p.strip()]


def _apply_page_categories(product: ScrapedProduct, soup: BeautifulSoup) -> None:
    crumbs = _breadcrumb_labels(soup)
    rest: list[str] = []
    for c in crumbs:
        low = c.lower()
        if low in SKIP_CRUMBS:
            continue
        rest.append(c)

    name = (product.product_name or "").strip().lower()
    if rest and name:
        last = rest[-1].strip().lower()
        if last == name or last in name or name in last:
            rest = rest[:-1]

    category = ""
    subcategory = ""
    if rest:
        first_low = rest[0].strip().lower()
        if first_low in CATEGORY_CRUMBS:
            category = rest[0]
            subcategory = " / ".join(rest[1:]) if len(rest) > 1 else ""
        else:
            category = rest[0]
            subcategory = " / ".join(rest[1:]) if len(rest) > 1 else ""

    product.product_category = category
    product.sub_category = subcategory
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1:
        text = _decode(h1.get_text(" ", strip=True))
        if text and text.lower() not in {"page not found", "collection"}:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = _decode(og["content"])
        title = re.split(r"\s+-\s+CS\s*rugs", title, maxsplit=1, flags=re.I)[0].strip()
        return title
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    # Intro column next to featured image
    intro = soup.select_one(".intro") or soup.select_one("main .col-lg-5")
    if intro:
        text = _decode(intro.get_text("\n", strip=True))
        # Drop breadcrumb/title repeats at top
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        name = _product_name(soup).lower()
        cleaned: list[str] = []
        for ln in lines:
            low = ln.lower()
            if low in {"collection", "custom made rugs", "wall objects", "designer rugs"}:
                continue
            if name and low == name:
                continue
            cleaned.append(ln)
        text = "\n".join(cleaned).strip()
        if len(text) > 40:
            return clean_product_description(text)
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return clean_product_description(_decode(og["content"]))
    return ""


def _designer(soup: BeautifulSoup, description: str) -> str:
    og = soup.find("meta", property="og:description")
    candidates = [description or ""]
    if og and og.get("content"):
        candidates.insert(0, _decode(og["content"]))
    for text in candidates:
        for pat in DESIGNER_PATTERNS:
            m = pat.search(text or "")
            if m:
                name = _decode(m.group(1))
                name = re.sub(r"\s+", " ", name).strip(" .,\"'")
                # Title-case if all caps
                if name.isupper() and len(name) > 3:
                    name = name.title()
                if name and name.lower() not in {"rugs", "designer", "design"}:
                    return name
    return ""


def _fullsize_image(url: str) -> str:
    u = (url or "").split("?")[0].strip()
    u = WP_SIZE_SUFFIX.sub("", u)
    u = SCALED_SUFFIX.sub("", u)
    return u


def _image_key(url: str) -> str:
    return urlparse(_fullsize_image(url)).path.lower()


def _gallery_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    best: dict[str, str] = {}

    def add(src: str | None) -> None:
        if not src:
            return
        full = _fullsize_image(_abs(page_url, src))
        if not full:
            return
        low = full.lower()
        if "wp-content/uploads" not in low:
            return
        if any(
            x in low
            for x in (
                "logo",
                "favicon",
                "icon",
                "sprite",
                "magazine",
                "prijslijst",
                "tekening",
                "newsletter",
                "placeholder",
            )
        ):
            return
        # colour/code swatches often look like Name-231010
        if re.search(r"/\w+-\d{5,}--?(?:scaled)?\.(jpe?g|png|webp)$", low):
            return
        if not re.search(r"\.(jpe?g|png|webp|gif)$", low):
            return
        key = _image_key(full)
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    root = soup.select_one("main") or soup
    # Featured first
    for img in root.select("img.featured-image, .featured-image img, img.threads-img"):
        add(img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
    for img in root.select("img"):
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
        urls, timeout, lifestyle_hints=CSRUGS_LIFESTYLE_HINTS
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
    if not _is_en_collection_url(final_url):
        product.scrape_ok = False
        product.scrape_error = "Not a collection product page"
        return product

    soup = _soup(resp.content)
    product.product_name = _product_name(soup)
    product.product_description = _product_description(soup)
    product.price = ""
    product.designer = _designer(soup, product.product_description)
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
