"""
Fontana Arte (fontanaarte.com) — WooCommerce product pages behind Sucuri WAF.

Discovery: product-sitemap.xml (/prodotto/{slug}/), scrape EN twin /en/product/{slug}/.
WAF: one Playwright visit to set sucuri cookies, then requests.Session for pages.

Category from page `.title_label` e.g. "LIGHTING / TABLE LAMPS":
  product_category = Lighting
  sub_category = Table lamps
Designer: `.designer-name`
Price: not shown publicly → empty.
Images: hero + fancybox gallery (skip tech drawings / related cards).
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

HOME = "https://www.fontanaarte.com"
PRODUCT_SITEMAP = "https://www.fontanaarte.com/product-sitemap.xml"
MAX_IMAGES = 0
WP_SIZE_SUFFIX = re.compile(r"-(\d+)x(\d+)(?=\.(jpe?g|png|webp|gif)$)", re.I)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

FONTANA_LIFESTYLE_HINTS = (
    "lifestyle",
    "interior",
    "ambiance",
    "setting",
    "room",
    "gallery",
    "ambiente",
    "context",
    "installation",
)

_session: requests.Session | None = None


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _abs(base: str, href: str | None) -> str:
    if not href:
        return ""
    return urljoin(base, href.strip())


def _soup(content: bytes | str) -> BeautifulSoup:
    return BeautifulSoup(content, "html.parser")


def _decode(text: str) -> str:
    return unescape(text or "").replace("\xa0", " ").strip()


def _title_case_label(text: str) -> str:
    raw = _decode(text)
    if not raw:
        return ""
    # Keep short all-caps tokens readable: LIGHTING → Lighting
    return " ".join(w.capitalize() if w.isupper() else w for w in raw.split())


def _bootstrap_session() -> requests.Session:
    """Pass Sucuri once via Playwright; reuse cookies with requests."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=BROWSER_HEADERS["User-Agent"],
            locale="en-US",
        )
        page = context.new_page()
        page.goto(f"{HOME}/en/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)
        cookies = context.cookies()
        browser.close()

    sess = requests.Session()
    sess.headers.update(BROWSER_HEADERS)
    for c in cookies:
        sess.cookies.set(
            c["name"],
            c["value"],
            domain=c.get("domain") or ".fontanaarte.com",
            path=c.get("path") or "/",
        )
    return sess


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _bootstrap_session()
    return _session


def _fetch(url: str, timeout: float) -> requests.Response:
    sess = _get_session()
    resp = sess.get(url, timeout=timeout, allow_redirects=True)
    # Rare: cookie expired → re-bootstrap once
    if resp.status_code == 307 or (
        "being redirected" in resp.text[:800].lower()
        and "sucuri" in resp.text[:2000].lower()
    ):
        global _session
        _session = _bootstrap_session()
        resp = _session.get(url, timeout=timeout, allow_redirects=True)
    return resp


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


def _to_en_product_url(url: str) -> str:
    """Map IT /prodotto/slug/ → EN /en/product/slug/."""
    u = _norm_url(url)
    m = re.search(r"/prodotto/([^/]+)/?$", u, re.I)
    if m:
        return f"{HOME}/en/product/{m.group(1)}"
    if "/en/product/" in u.lower():
        return u
    return u


def _is_product_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(
        re.search(r"/prodotto/[^/]+/?$", path)
        or re.search(r"/en/product/[^/]+/?$", path)
    )


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    # Sitemap is usually reachable without Sucuri cookies
    try:
        resp = requests.get(
            PRODUCT_SITEMAP, headers=BROWSER_HEADERS, timeout=timeout
        )
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for loc in _parse_sitemap_locs(resp.text):
        if not _is_product_url(loc):
            continue
        en = _to_en_product_url(loc)
        key = _norm_url(en).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(_norm_url(en))
    return out


def _apply_page_categories(product: ScrapedProduct, soup: BeautifulSoup) -> None:
    label = soup.select_one(".title_label")
    text = _decode(label.get_text(" ", strip=True)) if label else ""
    category = ""
    subcategory = ""
    if text:
        parts = [p.strip() for p in text.split("/") if p.strip()]
        if parts:
            category = _title_case_label(parts[0])
        if len(parts) > 1:
            subcategory = _title_case_label(" / ".join(parts[1:]))
    product.product_category = category
    product.sub_category = subcategory
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one(".scheda-prodotto h1") or soup.select_one("h1")
    if h1:
        text = _decode(h1.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return _decode(og["content"].split("-")[0].split("|")[0])
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    block = soup.select_one(".scheda-prodotto")
    if block:
        # First substantial paragraph in product card
        for p in block.select("p"):
            if p.select_one(".single_product_label"):
                continue
            text = _decode(p.get_text(" ", strip=True))
            if len(text) > 40:
                return clean_product_description(text)
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return clean_product_description(_decode(og["content"]))
    return ""


def _designer(soup: BeautifulSoup) -> str:
    el = soup.select_one("a.designer-name") or soup.select_one(
        ".scheda-prodotto-designer-under-title a"
    )
    if el:
        return _decode(el.get_text(" ", strip=True))
    return ""


def _fullsize_image(url: str) -> str:
    u = (url or "").split("?")[0].strip()
    return WP_SIZE_SUFFIX.sub("", u)


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
                "icon",
                "favicon",
                "sprite",
                "placeholder",
                "technical-drawing",
                "lampade-da-",
                "/card-",
                "-card.",
                "g_odo-",
                "designer",
                "tavola-disegno",
            )
        ):
            return
        if not re.search(r"\.(jpe?g|png|webp|gif)$", low):
            return
        key = _image_key(full)
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    # Hero
    for img in soup.select(".product-visore .background-image > img"):
        add(img.get("src") or img.get("data-src"))
    # Fancybox gallery
    for a in soup.select("a.button-media-gallery[href], a[data-fancybox='product-gallery']"):
        add(a.get("href"))

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
        urls, timeout, lifestyle_hints=FONTANA_LIFESTYLE_HINTS
    )
    product.product_images = list(urls)
    product.hero_images = hero
    product.lifestyle_images = lifestyle
    product.detail_image = hero[-1] if len(hero) > 2 else ""


def scrape_product_page(
    product_url: str, brand_name: str, timeout: float
) -> ScrapedProduct:
    url = _to_en_product_url(product_url)
    product = ScrapedProduct(product_url=_norm_url(url), Brand_table=brand_name)
    try:
        resp = _fetch(url, timeout)
    except requests.RequestException as exc:
        product.scrape_ok = False
        product.scrape_error = str(exc)
        return product

    if resp.status_code >= 400:
        # Fallback to Italian page
        it = re.sub(
            r"/en/product/([^/]+)/?$",
            r"/prodotto/\1/",
            url,
            flags=re.I,
        )
        if it != url:
            try:
                resp = _fetch(it, timeout)
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

    if "single-product" not in " ".join(soup.body.get("class") or []) if soup.body else "":
        # soft check via scheda
        if not soup.select_one(".scheda-prodotto h1"):
            product.scrape_ok = False
            product.scrape_error = "Not a product page"
            return product

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
    # Warm Sucuri cookies before product loop
    _get_session()
    urls = discover_product_urls(normalize_url(site_url) or HOME, timeout)
    if max_products > 0:
        urls = urls[:max_products]
    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay > 0:
            time.sleep(delay)
        products.append(scrape_product_page(url, brand_name, timeout))
    return urls, products
