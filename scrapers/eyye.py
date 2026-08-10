"""
EYYE (eyye.nl) — Craft CMS product pages.

Discovery: collection sitemap → /banken|fauteuils|accessoires/{slug}
  (skip PDFs and category index URLs).

Breadcrumb: Home → Banken|Fauteuils|Accessoires
  product_category = Banken | Fauteuils | Accessoires
  sub_category = empty (no deeper crumb on product pages)
Name: h2.large model name (e.g. Dura Lounge).
Designer: link to designer bio pages on the product page.
Price: not on pages → empty.
Images: /media/EYYE/Producten/… (keep Craft transforms; skip stof swatches).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

from brand_scraper import normalize_url
from product_schema import ScrapedProduct
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description

HOME = "https://www.eyye.nl"
SITEMAP_INDEX = "https://www.eyye.nl/sitemaps-6-sitemap.xml"
COLLECTION_SITEMAP = (
    "https://www.eyye.nl/sitemaps-6-section-collection_isMain-4-sitemap.xml"
)
MAX_IMAGES = 0

PRODUCT_ROOTS = frozenset({"banken", "fauteuils", "accessoires"})

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

SKIP_CRUMBS = frozenset({"home", "eyye"})

DESIGNER_HREF_HINTS = (
    "marike-andeweg",
    "studio-tom-dissel",
    "studio-foorumi",
    "designlab21",
    "ontwerpers",
)

EYYE_LIFESTYLE_HINTS = (
    "sfeer",
    "lifestyle",
    "interior",
    "ambiance",
    "setting",
    "room",
    "inspiratie",
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


def _is_product_url(url: str) -> bool:
    if ".pdf" in url.lower():
        return False
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        return False
    if parts[0] not in PRODUCT_ROOTS:
        return False
    if parts[1].startswith("prijslijst"):
        return False
    return True


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    urls_xml = ""
    try:
        resp = _fetch(COLLECTION_SITEMAP, timeout)
        if resp.status_code < 400:
            urls_xml = resp.text
        else:
            idx = _fetch(SITEMAP_INDEX, timeout)
            for loc in _parse_sitemap_locs(idx.text):
                if "collection_isMain" in loc:
                    resp = _fetch(loc, timeout)
                    if resp.status_code < 400:
                        urls_xml = resp.text
                    break
    except requests.RequestException:
        return []

    if not urls_xml:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for loc in _parse_sitemap_locs(urls_xml):
        if not _is_product_url(loc):
            continue
        key = _norm_url(loc).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(_norm_url(loc))
    return out


def _breadcrumb_labels(soup: BeautifulSoup) -> list[str]:
    root = soup.select_one("nav.breadcrumb") or soup.select_one(".breadcrumb")
    if not root:
        return []
    labels: list[str] = []
    for el in root.select('[property="name"], span[property="name"], a span'):
        text = _decode(el.get_text(" ", strip=True))
        if text and text not in labels:
            labels.append(text)
    if labels:
        return labels
    return [
        _decode(a.get_text(" ", strip=True))
        for a in root.select("a")
        if _decode(a.get_text(" ", strip=True))
    ]


def _apply_page_categories(product: ScrapedProduct, soup: BeautifulSoup) -> None:
    crumbs = [
        c
        for c in _breadcrumb_labels(soup)
        if c.lower() not in SKIP_CRUMBS
    ]
    # Drop product name if present as last crumb
    name = (product.product_name or "").strip().lower()
    if crumbs and name and crumbs[-1].strip().lower() == name:
        crumbs = crumbs[:-1]

    category = crumbs[0] if crumbs else ""
    subcategory = " / ".join(crumbs[1:]) if len(crumbs) > 1 else ""

    # URL fallback
    if not category:
        parts = [p for p in urlparse(product.product_url).path.split("/") if p]
        if parts and parts[0] in PRODUCT_ROOTS:
            category = parts[0].capitalize()

    product.product_category = category
    product.sub_category = subcategory
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    h2 = soup.select_one("h2.large") or soup.select_one("h2.large.no-line")
    if h2:
        text = _decode(h2.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = _decode(og["content"])
        title = re.split(r"\s*\|\s*", title)[0].strip()
        # "Modulaire loungebank Dura Lounge" → try last two words if long
        return title
    h1 = soup.select_one("h1")
    return _decode(h1.get_text(" ", strip=True)) if h1 else ""


def _product_description(soup: BeautifulSoup) -> str:
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        desc = clean_product_description(_decode(og["content"]))
        if desc:
            return desc
    article = soup.select_one("article")
    if article:
        text = _decode(article.get_text("\n", strip=True))
        # Keep first substantial block before configurator noise
        text = re.split(
            r"\n(?:Stel jouw|EYYE promise|Nieuwsbrief|Magazine)\b",
            text,
            maxsplit=1,
            flags=re.I,
        )[0]
        return clean_product_description(text)
    return ""


def _designer(soup: BeautifulSoup) -> str:
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").lower()
        if not any(h in href for h in DESIGNER_HREF_HINTS):
            continue
        if "ontwerpers" in href and href.rstrip("/").endswith("ontwerpers"):
            continue
        name = _decode(a.get_text(" ", strip=True))
        if name and len(name) < 80 and name.lower() not in {"ontwerpers", "designers"}:
            return name
    return ""


def _transform_size(url: str) -> int:
    m = re.search(r"/_(\d+|AUTO)x(\d+|AUTO)_", url)
    if not m:
        return 0
    a, b = m.group(1), m.group(2)
    wa = 0 if a == "AUTO" else int(a)
    ha = 0 if b == "AUTO" else int(b)
    return max(wa, ha)


def _image_basename(url: str) -> str:
    path = unquote(urlparse(url.split("?")[0]).path)
    return path.rsplit("/", 1)[-1].lower()


def _gallery_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    best: dict[str, tuple[int, str]] = {}

    def add(src: str | None) -> None:
        if not src:
            return
        full = _abs(page_url, src).split("#")[0]
        if not full:
            return
        low = full.lower()
        if "/media/eyye/producten/" not in low:
            return
        if any(x in low for x in ("logo", "favicon", "icon", "social-share")):
            return
        if not re.search(r"\.(jpe?g|png|webp|gif)(?:$|\?)", low):
            return
        key = _image_basename(full)
        score = _transform_size(full)
        prev = best.get(key)
        if prev is None or score >= prev[0]:
            best[key] = (score, full.split("?")[0])

    for img in soup.select("img"):
        add(img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
        srcset = img.get("srcset") or ""
        for part in srcset.split(","):
            add(part.strip().split(" ")[0])
    for source in soup.select("source[srcset]"):
        for part in (source.get("srcset") or "").split(","):
            add(part.strip().split(" ")[0])

    if not best:
        og = soup.find("meta", property="og:image")
        if og:
            add(og.get("content"))

    out = [u for _, u in best.values()]
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
        urls, timeout, lifestyle_hints=EYYE_LIFESTYLE_HINTS
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
    if not _is_product_url(final_url):
        product.scrape_ok = False
        product.scrape_error = "Not a product page"
        return product

    soup = _soup(resp.content)
    product.product_name = _product_name(soup)
    product.product_description = _product_description(soup)
    product.price = ""
    product.designer = _designer(soup)
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
