"""
Jori (jori.com) — Drupal 7 product pages.

Discovery: EN product-category listing pages (sitemap product URLs often redirect
to categories). Prefer NL product page when available, else EN.

Breadcrumb: Home → Collection/Collectie → {Armchairs|Sofas|…} → Product
  - product_category = Collection / Collectie
  - sub_category = Armchairs / Sofas / …
Designer: h4.designer link text (year stripped).
Price: not on pages → empty.
Images: product carousel + packshot in .product-info (not related-product grid).
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

SITEMAP = "https://www.jori.com/sitemap.xml"
HOME = "https://www.jori.com"
MAX_IMAGES = 0

# Drupal image style: /sites/default/files/styles/{style}/public/{path}
STYLE_RE = re.compile(
    r"/sites/default/files/styles/[^/]+/public/",
    re.I,
)
DESIGNER_YEAR_RE = re.compile(r",\s*\d{4}\s*$")

SKIP_CRUMBS = frozenset(
    {
        "home",
        "jori",
        "click here",
        "klik hier",
    }
)
CATEGORY_CRUMBS = frozenset({"collection", "collectie", "kollektion"})

JORI_LIFESTYLE_HINTS = (
    "carousel",
    "header",
    "lifestyle",
    "reference",
    "interior",
    "ambiance",
    "setting",
    "room",
    "showroom",
    "inspiration",
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


def _is_product_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    parts = [p for p in path.strip("/").split("/") if p]
    # /en/products/{slug} or /nl/products/{slug}
    return (
        len(parts) == 3
        and parts[0] in {"en", "nl", "de", "fr"}
        and parts[1] == "products"
        and bool(parts[2])
    )


def _category_urls_from_sitemap(timeout: float) -> list[str]:
    try:
        resp = _fetch(SITEMAP, timeout)
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []
    cats: list[str] = []
    seen: set[str] = set()
    for loc in _parse_sitemap_locs(resp.text):
        path = urlparse(loc).path.lower()
        if "/en/product-category/" not in path:
            continue
        key = _norm_url(loc)
        if key in seen:
            continue
        seen.add(key)
        cats.append(key)
    return cats


def _product_links_from_category(cat_url: str, timeout: float) -> list[str]:
    try:
        resp = _fetch(cat_url, timeout)
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []
    soup = _soup(resp.content)
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/products/']"):
        href = _norm_url(_abs(cat_url, a.get("href")))
        if not _is_product_path(href):
            continue
        # Prefer EN discovery URLs
        if "/en/products/" not in href.lower():
            # normalize nl/de/fr → en for dedupe key later
            parsed = urlparse(href)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 3:
                parts[0] = "en"
                href = _norm_url(
                    f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts)}"
                )
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def _looks_like_product_html(html: str, final_url: str) -> bool:
    if "product-category" in urlparse(final_url).path.lower():
        return False
    if not _is_product_path(final_url):
        return False
    return "node-type-product" in html


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    products: list[str] = []
    seen: set[str] = set()
    for cat in _category_urls_from_sitemap(timeout):
        for href in _product_links_from_category(cat, timeout):
            if href not in seen:
                seen.add(href)
                products.append(href)
    return products


def _prefer_nl_url(en_url: str) -> str:
    return re.sub(r"(https?://[^/]+)/en/", r"\1/nl/", en_url, count=1, flags=re.I)


def _breadcrumb_labels(soup: BeautifulSoup) -> list[str]:
    labels: list[str] = []
    root = soup.select_one(".breadcrumbs")
    if not root:
        return labels
    for el in root.select("a, span.active"):
        text = _decode(el.get_text(" ", strip=True))
        low = text.lower()
        if not text or low in SKIP_CRUMBS:
            continue
        if "configure" in low or "configur" in low:
            continue
        labels.append(text)
    return labels


def _apply_page_categories(product: ScrapedProduct, soup: BeautifulSoup) -> None:
    """
    Breadcrumb: Home → Collection → Armchairs → Product
      category = Collection/Collectie
      subcategory = Armchairs/Sofas/…
    """
    crumbs = _breadcrumb_labels(soup)
    name = (product.product_name or "").strip().lower()
    rest = list(crumbs)
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
    h1 = soup.select_one("h1.productname") or soup.select_one("h1")
    if h1:
        text = _decode(h1.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        # "Oasis | Armchairs | JORI"
        return _decode(og["content"].split("|")[0])
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    el = soup.select_one(".product-info .product-description") or soup.select_one(
        ".product-description"
    )
    if not el:
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            return clean_product_description(_decode(og["content"]))
        return ""
    return clean_product_description(el.get_text("\n\n", strip=True))


def _designer(soup: BeautifulSoup) -> str:
    el = soup.select_one("h4.designer a") or soup.select_one("h4.designer")
    if not el:
        return ""
    text = _decode(el.get_text(" ", strip=True))
    text = DESIGNER_YEAR_RE.sub("", text).strip(" ,")
    return text


def _fullsize_image(url: str) -> str:
    u = (url or "").split("?")[0].strip()
    if not u:
        return ""
    # strip Drupal image style wrapper
    u = STYLE_RE.sub("/sites/default/files/", u)
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
        if any(
            x in low
            for x in (
                "logo",
                "icon",
                "favicon",
                "sprite",
                "placeholder",
                "designedfordynamic",
            )
        ):
            return
        if not re.search(r"\.(jpe?g|png|webp|gif)$", low):
            return
        # skip related-product thumbs outside this product's blocks
        key = _image_key(full)
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    # Hero / lifestyle carousel for this product
    for img in soup.select(
        ".region-carousel img, .view-j2-product-carousel img, "
        "#block-views-j2-product-carousel-block img"
    ):
        add(img.get("src") or img.get("data-src"))
        for src in img.get("srcset", "").split(","):
            add(src.strip().split(" ")[0])
    for source in soup.select(
        ".region-carousel source, .view-j2-product-carousel source"
    ):
        add(source.get("srcset", "").split(",")[0].strip().split(" ")[0])

    # Packshot next to description
    for img in soup.select(".product-info .product-image img"):
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
        urls, timeout, lifestyle_hints=JORI_LIFESTYLE_HINTS
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

    urls_to_try = [_norm_url(product_url)]
    if "/en/products/" in product_url.lower():
        urls_to_try.insert(0, _prefer_nl_url(product_url))

    resp: requests.Response | None = None
    last_err = ""
    for url in urls_to_try:
        try:
            candidate = _fetch(url, timeout)
        except requests.RequestException as exc:
            last_err = str(exc)
            continue
        if candidate.status_code >= 400:
            last_err = f"HTTP {candidate.status_code}"
            continue
        final = _norm_url(str(candidate.url))
        if not _looks_like_product_html(candidate.text, final):
            last_err = "Not a product page"
            continue
        resp = candidate
        break

    if resp is None:
        product.scrape_ok = False
        product.scrape_error = last_err or "Fetch failed"
        return product

    final_url = _norm_url(str(resp.url))
    product.product_url = final_url
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
