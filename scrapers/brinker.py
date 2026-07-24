"""
Brinker Carpets (brinker.nl) — Odoo website product pages.

Discovery: collection listing pages (Ensuite / Feelgood / Pallio / Custom Sizes)
plus sitemap single-slug URLs validated as product pages.
Shop (/shop) is login-walled — public product CMS pages only.

Category from collection membership:
  product_category = Collectie
  sub_category = Ensuite | Feelgood | Feelgood Custom Sizes | Pallio
Price / designer: not on public pages → empty.
Images: /web/image/* product photos (skip logo / shapes / thumbs prefer full).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description

HOME = "https://www.brinker.nl"
SITEMAP = "https://www.brinker.nl/sitemap.xml"
MAX_IMAGES = 0

# Collection page → subcategory label (nav / site wording)
COLLECTION_PAGES: dict[str, str] = {
    "https://www.brinker.nl/collectie-ensuite": "Ensuite",
    "https://www.brinker.nl/feelgood-by-brinker": "Feelgood",
    "https://www.brinker.nl/feelgood-custom-sizes": "Feelgood Custom Sizes",
    "https://www.brinker.nl/pallio-by-brinker": "Pallio",
}

SKIP_SLUGS = frozenset(
    {
        "collectie",
        "catalogus",
        "dealers",
        "blog",
        "blogs",
        "faq",
        "contact",
        "contactus",
        "en",
        "nl",
        "shop",
        "home",
        "registreren",
        "privacybeleid",
        "servicevoorwaarden",
        "vacatures",
        "algemene-voorwaarden",
        "showroom",
        "web",
    }
)

SKIP_PREFIXES = (
    "collectie-",
    "feelgood-",
    "pallio-",
    "kleuren-",
    "het-",
    "tips-",
    "onderhoud",
    "surface",
    "anti-slip",
    "vlekken",
    "stofzuigen",
    "blog",
    "nieuws",
    "welkom-",
    "algemene-",
    "service",
    "privacy",
)

BRINKER_LIFESTYLE_HINTS = (
    "bedroom",
    "living",
    "interior",
    "ambiance",
    "lifestyle",
    "sfeer",
    "room",
    "cam",
    "setting",
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


def _slug(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[-1].lower() if parts else ""


def _is_product_candidate_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().replace("www.", "")
    if host and "brinker.nl" not in host:
        return False
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) != 1:
        return False
    slug = parts[0].lower()
    if slug in SKIP_SLUGS:
        return False
    if any(slug.startswith(p) for p in SKIP_PREFIXES):
        return False
    return True


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


def _links_from_collection(coll_url: str, timeout: float) -> list[str]:
    try:
        resp = _fetch(coll_url, timeout)
    except requests.RequestException:
        return []
    if resp.status_code >= 400:
        return []
    soup = _soup(resp.content)
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = _norm_url(_abs(coll_url, a.get("href")))
        if not _is_product_candidate_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


_CATEGORY_BY_URL: dict[str, str] = {}  # url → subcategory


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    del site_url
    _CATEGORY_BY_URL.clear()
    order: list[str] = []

    for coll_url, sub in COLLECTION_PAGES.items():
        for href in _links_from_collection(coll_url, timeout):
            if href not in _CATEGORY_BY_URL:
                _CATEGORY_BY_URL[href] = sub
                order.append(href)

    # Sitemap extras (not linked from collection pages)
    try:
        resp = _fetch(SITEMAP, timeout)
        if resp.status_code < 400:
            for loc in _parse_sitemap_locs(resp.text):
                href = _norm_url(loc)
                if not _is_product_candidate_url(href):
                    continue
                if href not in _CATEGORY_BY_URL:
                    _CATEGORY_BY_URL[href] = ""
                    order.append(href)
    except requests.RequestException:
        pass

    return order


def _apply_categories(product: ScrapedProduct) -> None:
    key = _norm_url(product.product_url)
    sub = _CATEGORY_BY_URL.get(key, "")
    if not sub:
        for u, s in _CATEGORY_BY_URL.items():
            if _slug(u) == _slug(key):
                sub = s
                break
    # Fallback: infer from image filenames later is optional; keep empty sub if unknown
    product.product_category = "Collectie" if sub or key else ""
    if sub:
        product.product_category = "Collectie"
        product.sub_category = sub
    else:
        # still mark as Collectie when it is a validated product page
        product.product_category = "Collectie"
        product.sub_category = ""
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    h2 = soup.select_one("main h2") or soup.select_one("#wrap h2") or soup.select_one("h2")
    if h2:
        text = _decode(h2.get_text(" ", strip=True))
        if text and text.lower() not in {"contact", "collecties"}:
            return text
    h1 = soup.select_one("h1")
    if h1:
        text = _decode(h1.get_text(" ", strip=True))
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        # "Ajour vloerkleed | Brinker"
        title = _decode(og["content"].split("|")[0])
        title = re.sub(r"\s+vloerkleed\s*$", "", title, flags=re.I).strip()
        return title
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    main = soup.select_one("main") or soup.select_one("#wrap")
    if main:
        text = main.get_text("\n", strip=True)
        # Drop leading product name line
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if lines and lines[0].lower() == (_product_name(soup) or "").lower():
            lines = lines[1:]
        # Stop at colour/shape swatch sections
        cut: list[str] = []
        for ln in lines:
            low = ln.lower()
            if low in {"kleuren", "vormen", "shapes", "colours", "colors"}:
                break
            if low.startswith("klik hier"):
                break
            cut.append(ln)
        text = "\n".join(cut).strip()
        if text:
            return clean_product_description(text)
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return clean_product_description(_decode(og["content"]))
    return ""


def _looks_like_product(soup: BeautifulSoup, final_url: str) -> bool:
    if not _is_product_candidate_url(final_url):
        return False
    text = soup.get_text(" ", strip=True).lower()
    if "samenstelling" in text or "vloerkleed" in text:
        return True
    og = soup.find("meta", property="og:title")
    if og and "vloerkleed" in (og.get("content") or "").lower():
        return True
    return bool(_product_name(soup))


def _prefer_full_image(url: str) -> str:
    u = (url or "").split("?")[0].strip()
    # Prefer non-thumb filename variants
    u = re.sub(r"_thumb(?:_\d+)?(?=\.(?:jpe?g|png|webp|gif)$)", "", u, flags=re.I)
    return u


def _image_key(url: str) -> str:
    path = unquote(urlparse(_prefer_full_image(url)).path).lower()
    # Odoo: /web/image/{id}-{hash}/filename → key by id
    m = re.search(r"/web/image/(\d+)", path)
    if m:
        return m.group(1)
    return path


def _gallery_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    best: dict[str, str] = {}

    def add(src: str | None) -> None:
        if not src:
            return
        full = _prefer_full_image(_abs(page_url, src))
        if not full:
            return
        low = unquote(full).lower()
        if "/web/image/" not in low and "odoo.com/web/image/" not in low:
            return
        if any(
            x in low
            for x in (
                "logo",
                "favicon",
                "icon",
                "vormen_png",
                "vormen_",
                "button",
                "rechthoek",
                "rond_",
                "ovaal",
                "vierkant",
                ".svg",
            )
        ):
            return
        # skip tiny UI
        if re.search(r"/web/image/website/", low):
            return
        key = _image_key(full)
        prev = best.get(key)
        # Prefer larger / non-thumb / jpg over webp thumb when same id
        score = len(full) + (50 if "_thumb" not in low else 0)
        prev_score = len(prev) + (50 if prev and "_thumb" not in prev.lower() else 0) if prev else -1
        if score >= prev_score:
            best[key] = full

    root = soup.select_one("main") or soup.select_one("#wrap") or soup
    for img in root.select("img"):
        add(img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
        srcset = img.get("srcset") or ""
        for part in srcset.split(","):
            add(part.strip().split(" ")[0])
    # og image
    if not best:
        og = soup.find("meta", property="og:image")
        if og:
            add(og.get("content"))

    # normalize host to www.brinker.nl when odoo CDN host
    out: list[str] = []
    for u in best.values():
        u2 = re.sub(
            r"https?://[^/]*odoo\.com",
            "https://www.brinker.nl",
            u,
            flags=re.I,
        )
        out.append(u2)
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
        urls, timeout, lifestyle_hints=BRINKER_LIFESTYLE_HINTS
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

    if not _looks_like_product(soup, final_url):
        product.scrape_ok = False
        product.scrape_error = "Not a product page"
        return product

    product.product_name = _product_name(soup)
    product.product_description = _product_description(soup)
    product.price = ""
    product.designer = ""
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
