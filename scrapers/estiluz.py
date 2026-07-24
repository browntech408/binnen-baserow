"""
Estiluz (estiluz.com) — PLAYBRAND / Laravel product pages.

Discovery: /en-sitemap.xml + category listing pages derived from product URLs
(and /en/collections* when linked from the site). Prefer English (/en).

Categories: ONLY labels found on the product page (breadcrumb). No static maps,
no invented subcategories. Product ref in breadcrumb is not used as subcategory.

Designer: product CTA "Designed by" (name + image + link); bio from /en/designer/{slug}.
Images: gallery data-preview → product_images; white/no-bg → hero; rest → lifestyle.
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

EN_SITEMAP = "https://www.estiluz.com/en-sitemap.xml"
EN_HOME = "https://www.estiluz.com/en"
MAX_IMAGES = 0  # 0 = no limit

# Path prefixes that are never product pages (discovered dynamically elsewhere).
NON_PRODUCT_PREFIXES = (
    "/en/designer/",
    "/en/designers",
    "/en/collections",
    "/en/collection/",
    "/en/rooms/",
    "/en/room/",
    "/en/projects-estiluz",
    "/en/projects/",
    "/en/project/",
    "/en/blog/",
    "/en/blogs/",
    "/en/product-hub",
    "/en/about",
    "/en/contact",
    "/en/news",
    "/en/press",
    "/en/legal",
    "/en/privacy",
    "/en/search",
    "/en/where-to-buy",
    "/en/downloads",
    "/en/professionals",
    "/en/configurator",
)

ESTILUZ_LIFESTYLE_HINTS = (
    "lifestyle",
    "interior",
    "ambiance",
    "room",
    "hometour",
    "setting",
    "gallery",
    "ambiente",
    "installation",
)

_designer_cache: dict[str, tuple[str, str, str]] = {}


def _abs(base: str, href: str | None) -> str:
    if not href:
        return ""
    return urljoin(base, href.strip())


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _is_product_url(url: str) -> bool:
    """
    Product pages look like /en/{category-slug}/{product-slug}
    e.g. /en/suspension-lights/volta-3534
    """
    path = urlparse(url).path.lower().rstrip("/")
    if not path.startswith("/en/"):
        return False
    low = path + "/"
    for prefix in NON_PRODUCT_PREFIXES:
        p = prefix.rstrip("/")
        if path == p or low.startswith(p + "/"):
            return False
    parts = [p for p in path.split("/") if p]
    # en, category, product
    if len(parts) != 3 or parts[0] != "en":
        return False
    # Real lamp products include a model/ref code in the slug (digits).
    # Filters out editorial pages that share the same URL shape.
    product_slug = parts[2]
    if not re.search(r"\d", product_slug):
        return False
    return True


def _category_listing_url(product_url: str) -> str:
    parts = [p for p in urlparse(product_url).path.split("/") if p]
    if len(parts) >= 2:
        return f"https://www.estiluz.com/{parts[0]}/{parts[1]}"
    return ""


def _soup(content: bytes) -> BeautifulSoup:
    # Site nests <p> inside <p>; html.parser keeps text, lxml drops it.
    return BeautifulSoup(content, "html.parser", from_encoding="utf-8")


def _fetch(url: str, timeout: float) -> requests.Response:
    return requests.get(
        url,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        allow_redirects=True,
    )


def _parse_sitemap_locs(xml_text: str) -> list[str]:
    locs: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.findall(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", xml_text, re.I)

    # handle default / custom namespaces
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag == "loc" and el.text:
            locs.append(el.text.strip())
    return locs


def _listing_product_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("div.col-md-3.item a[href], a.item[href], div.item a[href]"):
        href = _abs(page_url, a.get("href"))
        if not href or not _is_product_url(href):
            continue
        key = _norm_url(href)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    # fallback: any product-shaped link on the page
    if not out:
        for a in soup.select("a[href]"):
            href = _abs(page_url, a.get("href"))
            if href and _is_product_url(href):
                key = _norm_url(href)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    return out


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    """Sitemap products + category/collection listing pages (no hardcoded category list)."""
    base = normalize_url(site_url) or EN_HOME
    seen: set[str] = set()
    products: list[str] = []
    listing_pages: set[str] = set()

    def add_product(url: str) -> None:
        key = _norm_url(url)
        if not key or key in seen or not _is_product_url(key):
            return
        seen.add(key)
        products.append(key)
        cat = _category_listing_url(key)
        if cat:
            listing_pages.add(cat)

    # 1) EN sitemap
    try:
        resp = _fetch(EN_SITEMAP, timeout)
        if resp.status_code < 400:
            for loc in _parse_sitemap_locs(resp.text):
                if "estiluz.com" in loc.lower():
                    add_product(loc)
    except requests.RequestException:
        pass

    # 2) Seed listings from /en home + /en/collections (follow only what site links)
    seed_pages = [EN_HOME, urljoin(base if "estiluz.com" in base else EN_HOME, "/en")]
    seed_pages.append("https://www.estiluz.com/en/collections")
    for seed in seed_pages:
        try:
            resp = _fetch(seed, timeout)
            if resp.status_code >= 400:
                continue
            soup = _soup(resp.content)
            for a in soup.select("a[href]"):
                href = _abs(str(resp.url), a.get("href"))
                path = urlparse(href).path.lower()
                if _is_product_url(href):
                    add_product(href)
                elif "/en/collections/" in path or re.match(
                    r"^/en/[a-z0-9-]+/?$", path
                ):
                    # category index or collection page — only same-host
                    if "estiluz.com" in urlparse(href).netloc.lower():
                        listing_pages.add(_norm_url(href))
        except requests.RequestException:
            continue

    # 3) Crawl discovered listing pages for more product cards
    for listing in sorted(listing_pages):
        try:
            resp = _fetch(listing, timeout)
            if resp.status_code >= 400:
                continue
            soup = _soup(resp.content)
            for href in _listing_product_links(soup, str(resp.url)):
                add_product(href)
            # collection pages may link to other collections
            for a in soup.select("a[href*='/en/collections/']"):
                href = _norm_url(_abs(str(resp.url), a.get("href")))
                if href and "estiluz.com" in href:
                    listing_pages.add(href)
        except requests.RequestException:
            continue

    return products


def _breadcrumb_labels(soup: BeautifulSoup) -> list[str]:
    labels: list[str] = []
    for li in soup.select("ol.breadcrumb li"):
        text = li.get_text(" ", strip=True)
        if text:
            labels.append(text)
    return labels


def _apply_page_categories(product: ScrapedProduct, soup: BeautifulSoup) -> None:
    """
    Store only what the page shows.
    Breadcrumb: Home → {Category} → {product ref}
    Category = category crumb. Product ref is NOT stored as subcategory.
    Extra middle crumbs (if ever present) become subcategory as shown — never invented.
    """
    crumbs = _breadcrumb_labels(soup)
    meaningful = [
        c
        for c in crumbs
        if c.strip().lower() not in {"home", "inicio", "inicio / home"}
    ]
    category = ""
    subcategory = ""
    if not meaningful:
        product.product_category = ""
        product.sub_category = ""
        capture_source_categories(product)
        normalize_product_categories(product)
        return

    last = meaningful[-1].strip()
    # Typical product crumb ends with numeric ref (3722) — drop it
    if last.isdigit() or re.fullmatch(r"[\d\-xX]+", last):
        mids = meaningful[:-1]
    else:
        mids = meaningful

    if mids:
        category = mids[0]
        if len(mids) > 1:
            subcategory = " / ".join(mids[1:])

    product.product_category = category
    product.sub_category = subcategory
    capture_source_categories(product)
    normalize_product_categories(product)


def _product_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1.h1") or soup.select_one("h1")
    if not h1:
        return ""
    return h1.get_text(" ", strip=True)


def _product_description(soup: BeautifulSoup) -> str:
    el = soup.select_one("p.product-description")
    if not el:
        return ""
    text = el.get_text("\n\n", strip=True)
    return clean_product_description(text)


def _gallery_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(url: str) -> None:
        u = _norm_url(url.split("?")[0])
        if not u or u in seen:
            return
        low = u.lower()
        if any(x in low for x in ("logo", "icon", "favicon", "sprite")):
            return
        if "designer" in low and "estiluz" not in low.split("/")[-1]:
            # keep product shots; designer portraits handled separately
            if "_designer" in low or "designer_" in low or "-designer" in low:
                return
        seen.add(u)
        out.append(u)

    for a in soup.select("div.slide_prod_sm a[data-preview]"):
        add(_abs(page_url, a.get("data-preview")))
    if not out:
        for img in soup.select("div.slide_prod_sm img, div.slide_prod img"):
            add(_abs(page_url, img.get("src") or img.get("data-src")))
    if not out:
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            add(_abs(page_url, og["content"]))

    if MAX_IMAGES > 0:
        return out[:MAX_IMAGES]
    return out


def _fetch_designer(
    designer_url: str, timeout: float
) -> tuple[str, str, str]:
    """Return (name, description, image_url) from designer page; cached."""
    key = _norm_url(designer_url)
    if not key:
        return "", "", ""
    if key in _designer_cache:
        return _designer_cache[key]

    name, desc, image = "", "", ""
    try:
        resp = _fetch(key, timeout)
        if resp.status_code < 400:
            soup = _soup(resp.content)
            title = soup.select_one("h1.page-title") or soup.select_one("h1")
            if title:
                name = title.get_text(" ", strip=True)
            bio = soup.select_one("p.page-information")
            if bio:
                desc = clean_product_description(bio.get_text("\n\n", strip=True))
            # prefer designer portrait (filename often contains designer)
            for img in soup.select("img[src*='/assets/img/cms/']"):
                src = _abs(key, img.get("src"))
                low = src.lower()
                if "designer" in low or "estudio" in low or "studio" in low:
                    image = src
                    break
            if not image:
                for img in soup.select("img[src*='/assets/img/cms/']"):
                    src = _abs(key, img.get("src"))
                    # skip obvious product family shots on designer page
                    if re.search(r"_\d{3,}|product_|family_", src, re.I):
                        continue
                    image = src
                    break
    except requests.RequestException:
        pass

    _designer_cache[key] = (name, desc, image)
    return name, desc, image


def _apply_designer(
    product: ScrapedProduct, soup: BeautifulSoup, page_url: str, timeout: float
) -> None:
    box = soup.select_one("div.cta-box-designerstudio") or soup.select_one(
        "div.row.cta-box-designerstudio"
    )
    scope = box or soup

    name_el = scope.select_one("p.design-name")
    name = name_el.get_text(" ", strip=True) if name_el else ""

    img_el = None
    if box:
        img_el = box.select_one("img[src]")
    image = _abs(page_url, img_el.get("src") if img_el else None)
    if image and "logo" in image.lower():
        image = ""

    link = None
    if box:
        link = box.select_one("a[track-gotodesigner]") or box.select_one(
            "a.btn[href*='/designer/']"
        )
    if not link:
        link = scope.select_one("a[track-gotodesigner]") or scope.select_one(
            "a.btn[href*='/designer/']"
        )
    href = _abs(page_url, link.get("href") if link else None)

    bio_name, bio_desc, bio_image = ("", "", "")
    if href:
        bio_name, bio_desc, bio_image = _fetch_designer(href, timeout)

    product.designer = name or bio_name
    product.designerDescription = bio_desc
    # Prefer CTA portrait; fall back to designer-page image
    product.designerImage = image or bio_image


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
        urls,
        timeout,
        lifestyle_hints=ESTILUZ_LIFESTYLE_HINTS,
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
    product.price = ""  # Estiluz product pages do not show prices

    _apply_page_categories(product, soup)
    _apply_designer(product, soup, final_url, timeout)

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
    urls = discover_product_urls(site_url, timeout)
    if max_products > 0:
        urls = urls[:max_products]
    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        products.append(scrape_product_page(url, brand_name, timeout))
    return urls, products
