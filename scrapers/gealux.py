"""
Gealux (gealux.nl) — WordPress + Avada portfolio items.

Product URLs: /portfolio-item/{slug}
Discovery: avada_portfolio-sitemap.xml (NL only) + category listing pages.
Images: all → product_images; white/no-bg → hero_images; room/bg → lifestyle_images.
Description: JSON-LD Product first, else page paragraphs (cleaned).
Categories: breadcrumb portfolio_category slugs (e.g. revolution-serie / fauteuils).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers.extract_common import scrape_product_page_common
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description, description_from_json_ld

GEALUX_SKIP_CRUMBS = frozenset({"home", "ga naar inhoud"})
GEALUX_LIFESTYLE_HINTS = (
    "hometour",
    "wonderwood",
    "interior",
    "ambiance",
    "lifestyle",
    "room",
    "herz-waage",
    "topswing",
    "gallery",
)

SITEMAP_URL = "https://gealux.nl/avada_portfolio-sitemap.xml"
LISTING_PAGES = (
    "https://gealux.nl/relax-fauteuils/",
    "https://gealux.nl/bijzet-fauteuils/",
    "https://gealux.nl/eetkamerstoelen/",
    "https://gealux.nl/banken/",
    "https://gealux.nl/collectie/",
)

LISTING_CATEGORIES: dict[str, tuple[str, str]] = {
    "relax-fauteuils": ("Stoelen", "Fauteuils"),
    "bijzet-fauteuils": ("Stoelen", "Fauteuils"),
    "eetkamerstoelen": ("Stoelen", "Eetkamerstoelen"),
    "banken": ("Banken", ""),
}

_listing_categories: dict[str, tuple[str, str]] = {}


def _host_key(site_url: str) -> str:
    host = urlparse(normalize_url(site_url) or site_url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_product_url(url: str, host_key: str) -> bool:
    parsed = urlparse(url)
    h = parsed.netloc.lower()
    if h.startswith("www."):
        h = h[4:]
    if h != host_key:
        return False
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) != 2 or parts[0].lower() != "portfolio-item":
        return False
    slug = parts[1].lower()
    return bool(slug) and slug not in {"portfolio-item", "portfolio"}


def _listing_key(url: str) -> str:
    return (url or "").rstrip("/")


def _categories_from_listing(listing_url: str) -> tuple[str, str]:
    slug = urlparse(listing_url).path.strip("/").split("/")[-1].lower()
    return LISTING_CATEGORIES.get(slug, ("", ""))


def _urls_from_sitemap(host_key: str, timeout: float) -> set[str]:
    try:
        resp = requests.get(SITEMAP_URL, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return set()

    root = ET.fromstring(resp.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    found: set[str] = set()
    for loc in root.findall(".//sm:loc", ns) or root.findall(".//{*}loc"):
        url = (loc.text or "").strip().rstrip("/")
        if not url:
            continue
        if "/en/" in url or "/de/" in url:
            continue
        if _is_product_url(url, host_key):
            found.add(url)
    return found


def _extract_portfolio_links(html: str, page_url: str, host_key: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).split("#")[0].rstrip("/")
        if _is_product_url(href, host_key):
            found.add(href)
    return found


def _paginate_listing(listing_url: str, host_key: str, timeout: float) -> set[str]:
    found: set[str] = set()
    listing_url = listing_url if listing_url.endswith("/") else listing_url + "/"
    cat = _categories_from_listing(listing_url)

    for page in range(1, 21):
        page_url = listing_url if page == 1 else f"{listing_url}?paged={page}"
        try:
            resp = requests.get(
                page_url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        except requests.RequestException:
            break
        if resp.status_code >= 400:
            break
        batch = _extract_portfolio_links(resp.text, str(resp.url), host_key)
        if not batch:
            break
        new = batch - found
        for url in batch:
            if cat[0] and url not in _listing_categories:
                _listing_categories[url] = cat
        found |= batch
        if not new:
            break
    return found


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    global _listing_categories
    _listing_categories = {}

    base = normalize_url(site_url)
    if not base:
        return []

    host_key = _host_key(base)
    found = _urls_from_sitemap(host_key, timeout)

    for listing in LISTING_PAGES:
        found |= _paginate_listing(listing, host_key, timeout)
        time.sleep(0.2)

    return sorted(found)


PORTFOLIO_CATEGORY_RE = re.compile(r"/portfolio_category/([^/]+)/?", re.I)


def _portfolio_category_slug(href: str) -> str:
    """Slug from /portfolio_category/revolution-serie/ → revolution-serie."""
    path = urlparse(href or "").path
    match = PORTFOLIO_CATEGORY_RE.search(path)
    if not match:
        return ""
    return match.group(1).strip().lower()


def _parse_fusion_breadcrumbs(soup: BeautifulSoup, h1: BeautifulSoup | None) -> tuple[str, str]:
    """
    Home > [R]Evolution Serie > Fauteuils > Model Arc 2003
    Uses portfolio_category URL slugs: revolution-serie / fauteuils.
    """
    crumb = soup.select_one(".fusion-breadcrumbs")
    if not crumb:
        return "", ""

    cat_slugs: list[str] = []
    for a in crumb.find_all("a", href=True):
        text = a.get_text(strip=True)
        if text.lower() in GEALUX_SKIP_CRUMBS:
            continue
        slug = _portfolio_category_slug(a["href"])
        if slug:
            cat_slugs.append(slug)

    if len(cat_slugs) >= 2:
        return cat_slugs[-2], cat_slugs[-1]
    if len(cat_slugs) == 1:
        return cat_slugs[0], ""

    parts = [p.strip() for p in crumb.get_text("|", strip=True).split("|") if p.strip()]
    parts = [p for p in parts if p.lower() not in GEALUX_SKIP_CRUMBS]
    if h1:
        name = h1.get_text(strip=True)
        if parts and parts[-1].lower() == name.lower():
            parts = parts[:-1]

    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


def _gealux_description(soup: BeautifulSoup) -> str:
    for sel in (".post-content", ".project-content", ".fusion-text"):
        el = soup.select_one(sel)
        if not el:
            continue
        paras = [
            p.get_text(" ", strip=True)
            for p in el.find_all("p")
            if len(p.get_text(strip=True)) > 40
        ]
        if paras:
            return "\n\n".join(paras[:4])
        text = el.get_text(" ", strip=True)
        if len(text) > 60:
            return text[:2000]
    return ""


def _filter_images(images: list[str]) -> list[str]:
    out: list[str] = []
    for url in images:
        low = url.lower()
        if any(
            x in low
            for x in (
                "gealux-logo",
                "gealux80",
                "/flags/",
                "sitepress-multilingual",
                "wpml",
                "placeholder",
            )
        ):
            continue
        out.append(url)
    return out


def _wp_resized_url(url: str, size: str) -> str:
    base = (url or "").split("?")[0]
    match = re.search(r"\.(jpg|jpeg|png|webp)$", base, re.I)
    if not match:
        return url
    stem = base[: match.start()]
    if re.search(rf"-{re.escape(size)}$", stem, re.I):
        return base
    stem = re.sub(r"-\d+x\d+$", "", stem, flags=re.I)
    return f"{stem}-{size}{base[match.start() :]}"


def _url_is_image_ok(url: str, timeout: float) -> bool:
    try:
        resp = requests.head(
            url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
        )
        if resp.status_code in (403, 405):
            resp = requests.get(
                url, headers=DEFAULT_HEADERS, timeout=timeout, stream=True
            )
        if resp.status_code >= 400:
            return False
        ctype = (resp.headers.get("content-type") or "").lower()
        return "image" in ctype or url.lower().endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        )
    except requests.RequestException:
        return False


def _working_image_url(url: str, timeout: float) -> str | None:
    """Gealux often only serves WordPress -800x800 variants (full size 404)."""
    if not url:
        return None
    candidates: list[str] = []
    clean = url.split("?")[0]
    candidates.append(clean)
    if not re.search(r"-\d+x\d+\.(jpg|jpeg|png|webp)$", clean, re.I):
        candidates.append(_wp_resized_url(clean, "800x800"))
        candidates.append(_wp_resized_url(clean, "1024x1024"))
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        if _url_is_image_ok(cand, timeout):
            return cand
    return None


def _resolve_gealux_images(urls: list[str], timeout: float) -> list[str]:
    out: list[str] = []
    seen_bases: set[str] = set()
    for url in urls:
        working = _working_image_url(url, timeout)
        if not working:
            continue
        base = re.sub(
            r"-\d+x\d+(?=\.(jpg|jpeg|png|webp)$)",
            "",
            urlparse(working).path.lower(),
            flags=re.I,
        )
        if base in seen_bases:
            continue
        seen_bases.add(base)
        out.append(working)
    return out


def assign_gealux_images(
    product: ScrapedProduct,
    urls: list[str],
    *,
    timeout: float,
) -> None:
    """All images in product_images; white/no-bg → hero; coloured bg → lifestyle."""
    if not urls:
        product.product_images = []
        product.hero_images = []
        product.lifestyle_images = []
        product.detail_image = ""
        return

    hero_shots, lifestyle_shots = split_images_by_background(
        urls,
        timeout,
        lifestyle_hints=GEALUX_LIFESTYLE_HINTS,
    )
    product.product_images = list(urls)
    product.hero_images = hero_shots
    product.lifestyle_images = lifestyle_shots
    product.detail_image = hero_shots[-1] if len(hero_shots) > 2 else ""


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = scrape_product_page_common(product_url, brand_name, timeout)
    if not product.scrape_ok:
        return product

    key = _listing_key(product.product_url or product_url)
    listing = _listing_categories.get(key)

    try:
        resp = requests.get(
            key, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
        )
        if resp.status_code < 400:
            soup = BeautifulSoup(resp.text, "lxml")
            h1 = soup.find("h1")

            bcat, bsub = _parse_fusion_breadcrumbs(soup, h1)
            if bcat or bsub:
                product.source_product_category = bcat
                product.source_product_subcategory = bsub
                product.product_category = bcat
                product.sub_category = bsub
            elif listing and listing[0]:
                product.product_category, product.sub_category = listing
                product.source_product_category = listing[0]
                product.source_product_subcategory = listing[1]

            json_desc = description_from_json_ld(soup)
            if json_desc:
                product.product_description = json_desc
            else:
                desc = _gealux_description(soup)
                if desc:
                    product.product_description = clean_product_description(desc)
                elif product.product_description:
                    product.product_description = clean_product_description(
                        product.product_description
                    )

            images = _resolve_gealux_images(
                _filter_images(product.product_images), timeout
            )
            assign_gealux_images(product, images, timeout=timeout)

            if not product.product_images and not product.lifestyle_images:
                product.scrape_ok = False
                product.scrape_error = "No product images found"
                return product
    except requests.RequestException:
        pass

    capture_source_categories(product)
    normalize_product_categories(product)
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
