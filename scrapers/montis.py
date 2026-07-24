"""
Montis (montis.nl) — WordPress + Elementor product pages.

Discovery: page-sitemap.xml, filter /{category}/{slug}/ NL product URLs (~53).
Category: Yoast JSON-LD BreadcrumbList (position 2) or URL path — page only.
Designer: product "Ontwerper" section; bio/image from /ontwerpers/{slug}/ when found.
Price: not on pages → empty.
Images: hero + Galerij + page images; white/no-bg → hero, rest → lifestyle.
"""
from __future__ import annotations

import json
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

PAGE_SITEMAP = "https://montis.nl/page-sitemap.xml"
HOME = "https://montis.nl"
MAX_IMAGES = 0
WP_SIZE_SUFFIX = re.compile(r"-(\d+)x(\d+)(?=\.(jpe?g|png|webp|gif)$)", re.I)

PRODUCT_CATEGORIES = frozenset(
    {"banken", "fauteuils", "stoelen", "tafels", "kussens", "hockers"}
)

MONTIS_LIFESTYLE_HINTS = (
    "lifestyle",
    "sfeer",
    "interior",
    "ambiance",
    "gallery",
    "galerij",
    "setting",
    "room",
    "magazine",
    "brochure",
)

_designer_cache: dict[str, tuple[str, str]] = {}  # slug -> (bio, image)


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


def _slugify_name(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def _is_product_url(url: str) -> bool:
    path = urlparse(url).path.strip("/").split("/")
    if len(path) != 2:
        return False
    if path[0] in {"en", "de", "nl"}:
        return False
    return path[0] in PRODUCT_CATEGORIES and bool(path[1])


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
        resp = _fetch(PAGE_SITEMAP, timeout)
        if resp.status_code < 400:
            for loc in _parse_sitemap_locs(resp.text):
                key = _norm_url(loc)
                if "montis.nl" not in key.lower():
                    continue
                if _is_product_url(key) and key not in seen:
                    seen.add(key)
                    products.append(key)
    except requests.RequestException:
        pass

    if products:
        return products

    # Fallback: crawl category listing pages from sitemap category roots
    for cat in sorted(PRODUCT_CATEGORIES):
        listing = f"{HOME}/{cat}/"
        try:
            resp = _fetch(listing, timeout)
        except requests.RequestException:
            continue
        if resp.status_code >= 400:
            continue
        soup = _soup(resp.content)
        for a in soup.select(".products-box-link a[href], a[href]"):
            href = _norm_url(_abs(str(resp.url), a.get("href")))
            if _is_product_url(href) and href not in seen:
                seen.add(href)
                products.append(href)
    return products


def _breadcrumb_category(soup: BeautifulSoup) -> str:
    script = soup.find("script", type="application/ld+json", class_="yoast-schema-graph")
    if script and script.string:
        try:
            graph = json.loads(script.string).get("@graph") or []
            for node in graph:
                if node.get("@type") != "BreadcrumbList":
                    continue
                items = node.get("itemListElement") or []
                if len(items) >= 2:
                    name = str(items[1].get("name") or "").strip()
                    if name and name.lower() != "home":
                        return name
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    link = soup.select_one(".elementor-icon-box-title a")
    if link:
        text = link.get_text(" ", strip=True)
        if text:
            return text

    return ""


def _product_name(soup: BeautifulSoup) -> str:
    h4 = soup.select_one(".page-content h4.elementor-heading-title")
    if h4:
        text = h4.get_text(" ", strip=True)
        if text:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
        if " - Montis" in title:
            title = title.split(" - Montis", 1)[0].strip()
        if title:
            return title
    if soup.title:
        title = soup.title.get_text(" ", strip=True)
        if " - Montis" in title:
            title = title.split(" - Montis", 1)[0].strip()
        return title
    return ""


def _product_description(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    for p in soup.select(".elementor-widget-text-editor .elementor-widget-container p"):
        text = p.get_text(" ", strip=True)
        if not text or len(text) < 25:
            continue
        parts.append(text)
    if not parts:
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            parts.append(og["content"].strip())
    return clean_product_description("\n\n".join(parts))


def _designer_from_page(soup: BeautifulSoup) -> tuple[str, str]:
    """Return (designer_name, designer_image_on_product_page)."""
    h2s = soup.select("h2.elementor-heading-title")
    for i, h in enumerate(h2s):
        if h.get_text(strip=True) != "Ontwerper":
            continue
        section = h.find_parent("div", class_="e-con") or h.find_parent("section")
        name = ""
        image = ""
        if i + 1 < len(h2s):
            name = h2s[i + 1].get_text(" ", strip=True)
        if section:
            for img in section.select("img[src]"):
                src = img.get("src") or ""
                if "wp-content/uploads" in src:
                    image = _abs(str(soup.base_url or HOME), src)
                    break
        return name, image
    return "", ""


def _fetch_designer_page(
    designer_name: str, timeout: float
) -> tuple[str, str]:
    slug = _slugify_name(designer_name)
    if not slug:
        return "", ""
    if slug in _designer_cache:
        return _designer_cache[slug]

    bio, image = "", ""
    url = f"{HOME}/ontwerpers/{slug}/"
    try:
        resp = _fetch(url, timeout)
        if resp.status_code < 400:
            soup = _soup(resp.content)
            ps = soup.select(".elementor-widget-text-editor p")
            bio = clean_product_description(
                "\n\n".join(p.get_text(" ", strip=True) for p in ps if p.get_text(strip=True))
            )
            for img in soup.select("img[src*='wp-content/uploads']"):
                src = _abs(url, img.get("src"))
                low = src.lower()
                if slug.replace("-", "") in low.replace("-", "") or "ontwerper" in low:
                    image = src
                    break
            if not image:
                for img in soup.select("img[src*='wp-content/uploads']"):
                    src = _abs(url, img.get("src"))
                    if re.search(r"\.(jpe?g|png|webp)$", src, re.I):
                        image = src
                        break
    except requests.RequestException:
        pass

    _designer_cache[slug] = (bio, image)
    return bio, image


def _fullsize_image(url: str) -> str:
    u = (url or "").split("?")[0].strip()
    return WP_SIZE_SUFFIX.sub("", u)


def _image_key(url: str) -> str:
    path = urlparse(_fullsize_image(url)).path.lower()
    path = re.sub(r"-scaled(?=\.(jpe?g|png|webp|gif)$)", "", path)
    return path


SKIP_SECTION_HEADINGS = frozenset(
    {
        "materialen",
        "ontwerper",
        "op de plank",
        "magazines en brochures",
        "kleuren en materialen",
    }
)

SKIP_IMAGE_NAME_PARTS = (
    "logo",
    "icon",
    "favicon",
    "sprite",
    "placeholder",
    "brochure",
    "magazine",
    "menu.svg",
)


def _section_heading_text(section) -> str:
    h = section.select_one("h2.elementor-heading-title")
    return (h.get_text(" ", strip=True) if h else "").strip().lower()


def _is_skipped_section(heading: str) -> bool:
    if not heading:
        return False
    if heading in SKIP_SECTION_HEADINGS:
        return True
    return heading.startswith("materialen")


def _is_product_photo_url(url: str, *, designer_slug: str = "") -> bool:
    low = url.lower()
    if "wp-content/uploads" not in low:
        return False
    if any(part in low for part in SKIP_IMAGE_NAME_PARTS):
        return False
    if not re.search(r"\.(jpe?g|png|webp)$", low):
        return False
    if designer_slug and designer_slug.replace("-", "") in low.replace("-", ""):
        return False
    # numeric material swatches like 0182-150x150.jpg / 0182.jpg
    filename = low.rsplit("/", 1)[-1]
    if re.match(r"^\d{3,4}(?:-\d+x\d+)?\.(jpe?g|png|webp)$", filename):
        return False
    return True


def _gallery_images(
    soup: BeautifulSoup, page_url: str, *, designer_name: str
) -> list[str]:
    """
    Only real product photos:
    - hero / Galerij attachment-full images
    - never Materialen swatches, designer portraits, magazines/brochures
    """
    best: dict[str, str] = {}
    designer_slug = _slugify_name(designer_name)

    def add(src: str | None) -> None:
        if not src:
            return
        full = _fullsize_image(_abs(page_url, src))
        if not _is_product_photo_url(full, designer_slug=designer_slug):
            return
        key = _image_key(full)
        prev = best.get(key)
        if prev is None or len(full) >= len(prev):
            best[key] = full

    scope = soup.select_one(".page-content") or soup

    # 1) Top hero image(s) before Materialen / Ontwerper sections
    for img in scope.select("img.attachment-full"):
        # skip if inside a skipped section
        skipped = False
        parent = img.parent
        for _ in range(12):
            if parent is None:
                break
            if getattr(parent, "name", None) == "div" and "e-con" in (
                parent.get("class") or []
            ):
                if _is_skipped_section(_section_heading_text(parent)):
                    skipped = True
                    break
            parent = getattr(parent, "parent", None)
        if skipped:
            continue
        classes = " ".join(img.get("class") or []).lower()
        if "ue-simple-popup" in classes:
            continue
        add(img.get("src") or img.get("data-src"))

    # 2) Explicit Galerij section images
    for section in scope.select("div.e-con"):
        heading = _section_heading_text(section)
        if heading != "galerij":
            continue
        for img in section.select("img"):
            classes = " ".join(img.get("class") or []).lower()
            if "ue-simple-popup" in classes:
                continue
            add(img.get("src") or img.get("data-src"))

    if not best:
        og = soup.find("meta", property="og:image")
        if og:
            add(og.get("content"))

    out = list(best.values())
    if MAX_IMAGES > 0:
        return out[:MAX_IMAGES]
    return out


def _apply_categories(product: ScrapedProduct, soup: BeautifulSoup, page_url: str) -> None:
    cat = _breadcrumb_category(soup)
    if not cat:
        parts = urlparse(page_url).path.strip("/").split("/")
        if len(parts) >= 1 and parts[0] in PRODUCT_CATEGORIES:
            cat = parts[0].replace("-", " ").title()
    product.product_category = cat
    product.sub_category = ""
    capture_source_categories(product)
    normalize_product_categories(product)


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
        urls, timeout, lifestyle_hints=MONTIS_LIFESTYLE_HINTS
    )
    product.product_images = list(urls)
    product.hero_images = hero
    product.lifestyle_images = lifestyle
    product.detail_image = hero[-1] if len(hero) > 2 else ""


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = ScrapedProduct(product_url=_norm_url(product_url), Brand_table=brand_name)
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

    designer_name, designer_img = _designer_from_page(soup)
    bio, page_img = _fetch_designer_page(designer_name, timeout) if designer_name else ("", "")
    product.designer = designer_name
    product.designerDescription = bio
    product.designerImage = designer_img or page_img

    _apply_categories(product, soup, final_url)
    images = _gallery_images(soup, final_url, designer_name=designer_name)
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
    urls = discover_product_urls(site_url or HOME, timeout)
    if max_products > 0:
        urls = urls[:max_products]
    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        products.append(scrape_product_page(url, brand_name, timeout))
    return urls, products
