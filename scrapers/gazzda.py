"""
Gazzda (gazzda.com) — Squarespace Commerce.

Discovery: sitemap.xml → /products/p/{slug} (~115).
Category from nested collection pages under /products/{cat}/[{sub}/]:
  product_category = Seating | Tables Desks | Storage Units | …
  sub_category = Lounge Chairs | Dining Tables | … (empty for top-only cats)
Designer: "Designer" label block on page.
Price: public EUR amount when present.
Images: .product-gallery only (white/no-bg → hero).
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

HOME = "https://www.gazzda.com"
SITEMAP = "https://www.gazzda.com/sitemap.xml"
MAX_IMAGES = 0

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DESIGNER_RE = re.compile(
    r"Designer\s*\n+\s*([^\n]+)",
    re.I,
)

GAZZDA_LIFESTYLE_HINTS = (
    "lifestyle",
    "interior",
    "ambiance",
    "setting",
    "room",
    "inspiration",
    "project",
    "showroom",
    "flagship",
)

_CATEGORY_BY_URL: dict[str, tuple[str, str]] = {}


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


def _slug_label(slug: str) -> str:
    return " ".join(p.capitalize() for p in (slug or "").split("-") if p)


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
    path = urlparse(url).path.lower().rstrip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) == 3 and parts[0] == "products" and parts[1] == "p" and bool(parts[2])


def _is_category_url(url: str) -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] != "products":
        return False
    if len(parts) == 1:
        return False
    if parts[1] == "p":
        return False
    return len(parts) in (2, 3)


def _category_pair_from_url(cat_url: str) -> tuple[str, str]:
    parts = [p for p in urlparse(cat_url).path.split("/") if p]
    # products / {cat} [/ {sub}]
    if len(parts) >= 3:
        return _slug_label(parts[1]), _slug_label(parts[2])
    if len(parts) == 2:
        return _slug_label(parts[1]), ""
    return "", ""


def _product_links_on_page(page_url: str, html: str) -> list[str]:
    soup = _soup(html)
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/products/p/']"):
        href = _norm_url(_abs(page_url, a.get("href")))
        if not _is_product_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    _CATEGORY_BY_URL.clear()
    try:
        resp = _fetch(SITEMAP, timeout)
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []

    locs = _parse_sitemap_locs(resp.text)
    products = sorted({_norm_url(u) for u in locs if _is_product_url(u)})
    categories = sorted(
        {_norm_url(u) for u in locs if _is_category_url(u)},
        key=lambda u: (-len(urlparse(u).path.strip("/").split("/")), u),
    )
    # Deepest category pages first so subcategory wins over parent
    for cat_url in categories:
        try:
            rr = _fetch(cat_url, timeout)
        except requests.RequestException:
            continue
        if rr.status_code >= 400:
            continue
        pair = _category_pair_from_url(cat_url)
        for href in _product_links_on_page(cat_url, rr.text):
            if href not in _CATEGORY_BY_URL:
                _CATEGORY_BY_URL[href] = pair

    # Ensure all sitemap products are listed even if missing from category pages
    order: list[str] = []
    seen: set[str] = set()
    for href in products:
        if href not in seen:
            seen.add(href)
            order.append(href)
            _CATEGORY_BY_URL.setdefault(href, ("", ""))
    return order


def _apply_categories(product: ScrapedProduct) -> None:
    key = _norm_url(product.product_url)
    pair = _CATEGORY_BY_URL.get(key)
    if not pair:
        for u, p in _CATEGORY_BY_URL.items():
            if urlparse(u).path.rstrip("/") == urlparse(key).path.rstrip("/"):
                pair = p
                break
    cat, sub = pair if pair else ("", "")
    product.product_category = cat
    product.sub_category = sub
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    el = soup.select_one(".product-title") or soup.select_one("h1")
    if el:
        text = _decode(el.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = _decode(og["content"])
        title = re.split(r"\s+[—–|-]\s+Gazzda\s*$", title, flags=re.I)[0].strip()
        return title
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    # Prefer first substantial product HTML block (not newsletter)
    for el in soup.select(".ProductItem-details .sqs-html-content, .product-description, .sqs-html-content"):
        text = _decode(el.get_text("\n", strip=True))
        if len(text) < 60:
            continue
        if "stay tuned" in text.lower() or "email address" in text.lower():
            continue
        # Drop trailing "Designer …" / "All items in this range" noise if present
        text = re.split(r"\n(?:Designer|All items in this range)\b", text, maxsplit=1)[0]
        return clean_product_description(text)
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return clean_product_description(_decode(og["content"]))
    return ""


def _designer(soup: BeautifulSoup) -> str:
    text = soup.get_text("\n", strip=True)
    m = DESIGNER_RE.search(text)
    if m:
        name = _decode(m.group(1))
        if name and name.lower() not in {"designer", "designers"}:
            return name
    # fallback: designer profile links near product
    for a in soup.select("a[href*='teskeredzic'], a[href*='designer'], a[href^='/']"):
        href = (a.get("href") or "").lower()
        if any(
            x in href
            for x in (
                "salih",
                "mustafa",
                "berin",
                "elvira",
                "teskeredzic",
                "cohadzic",
            )
        ):
            name = _decode(a.get_text(" ", strip=True))
            if name and len(name) < 60:
                return name
    return ""


def _price(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", property="product:price:amount")
    if meta and meta.get("content"):
        amount = _decode(meta["content"])
        currency_el = soup.find("meta", property="product:price:currency")
        currency = (
            _decode(currency_el["content"])
            if currency_el and currency_el.get("content")
            else "EUR"
        )
        if amount:
            return f"{amount} {currency}".strip()
    el = soup.select_one(".product-price")
    if el:
        text = _decode(el.get_text(" ", strip=True))
        text = re.sub(r"^from\s+", "", text, flags=re.I).strip()
        return text
    return ""


def _fullsize_image(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    u = u.split("?")[0].strip()
    return u


def _image_key(url: str) -> str:
    return unquote(urlparse(_fullsize_image(url)).path).lower()


def _gallery_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    best: dict[str, str] = {}

    def add(src: str | None) -> None:
        if not src:
            return
        full = _fullsize_image(_abs(page_url, src))
        if not full:
            return
        low = unquote(full).lower()
        if "squarespace" not in low and "static1.squarespace" not in low:
            return
        if any(
            x in low
            for x in (
                "logo",
                "logotip",
                "favicon",
                "icon",
                "red_dot",
                "award",
                "sprite",
                "placeholder",
            )
        ):
            return
        # skip tiny placeholders
        if not re.search(r"\.(jpe?g|png|webp|gif)$", low) and "/content/v1/" not in low:
            return
        key = _image_key(full)
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    for img in soup.select(".product-gallery img, [data-product-gallery] img"):
        add(
            img.get("data-src")
            or img.get("data-image")
            or img.get("src")
            or img.get("data-srcset", "").split(",")[0].strip().split(" ")[0]
        )
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
        urls, timeout, lifestyle_hints=GAZZDA_LIFESTYLE_HINTS
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
    product.price = _price(soup)
    product.designer = _designer(soup)
    product.designerDescription = ""
    product.designerImage = ""

    _apply_categories(product)
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
