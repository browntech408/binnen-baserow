"""
Tonone (tonone.com) — Shopware 6 store.

Product URLs: /{slug}/{numeric-id}  e.g. /atlas-d350/1401
Discovery: paginate /products/types/all/?p=N and other listing pages.

Images: all → product_images; white/no-bg → hero_images; room/bg → lifestyle_images.
Description: JSON-LD Product first, else Shopware description block (cleaned).
"""
from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from product_scraper import _find_price, _slug_to_title
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import capture_source_categories, normalize_product_categories
from scrapers.text_clean import clean_product_description, description_from_json_ld

PRODUCT_PATH_RE = re.compile(r"^/[a-z0-9][a-z0-9.-]*/\d+$", re.I)
LISTING_MARKERS = ("/products/types/", "/products/model/", "/products/room/")
DESCRIPTION_JUNK = (
    "Check the FAQ page for more information",
    "Read more",
    "Read less",
    "Reviews can only be submitted while being logged in",
)
MAX_IMAGES = 0  # 0 = no limit
TONONE_LIFESTYLE_HINTS = (
    "hometour",
    "wonderwood",
    "interior",
    "ambiance",
    "lifestyle",
    "room",
    "-hero.",
    "/hero.",
    "website.jpg",
    "gallery",
)


def _encode_media_url(url: str) -> str:
    parsed = urlparse(url)
    path = quote(parsed.path, safe="/%")
    return urlunparse(parsed._replace(path=path))


def _property_value(soup: BeautifulSoup, label: str) -> str:
    want = label.lower().strip(":").strip()
    for row in soup.select("tr.properties-row"):
        th = row.select_one("th.properties-label")
        td = row.select_one("td.properties-value")
        if not th or not td:
            continue
        if th.get_text(strip=True).lower().rstrip(":") == want:
            return td.get_text(" ", strip=True)
    return ""


def _tonone_description(soup: BeautifulSoup) -> str:
    el = soup.select_one(".product-detail-description-text")
    if el:
        container = el.select_one(".product-description-text-container") or el
        text = container.get_text("\n\n", strip=True)
        for junk in DESCRIPTION_JUNK:
            text = text.replace(junk, "").strip()
        if len(text) > 80:
            return clean_product_description(text[:3000])

    for meta in (
        soup.find("meta", property="og:description"),
        soup.find("meta", attrs={"name": "description"}),
    ):
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 40:
                return clean_product_description(content)
    return ""


def _tonone_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    gallery = soup.select_one(".gallery-slider-container")
    scope = gallery if gallery else soup
    imgs = scope.select("img.gallery-slider-image")
    if not imgs:
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            imgs = [og]

    seen: set[str] = set()
    out: list[str] = []
    for img in imgs:
        if isinstance(img, BeautifulSoup) or hasattr(img, "get"):
            raw = (img.get("data-full-image") or img.get("content") or img.get("src") or "").strip()
        else:
            raw = str(img).strip()
        if not raw or raw.startswith("data:"):
            continue
        url = _encode_media_url(urljoin(page_url, raw))
        low = url.lower()
        if ".svg" in low or "logo" in low or "/thumbnail/" in low:
            continue
        key = urlparse(url).path.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if MAX_IMAGES > 0 and len(out) >= MAX_IMAGES:
            break
    return out


def _tonone_price(soup: BeautifulSoup) -> str:
    buybox = soup.select_one(".product-detail-buy") or soup.select_one(
        ".cms-block-gallery-buybox"
    )
    scope = buybox or soup
    price_el = scope.select_one("p.product-price")
    if price_el:
        return price_el.get_text(strip=True)
    return _find_price(soup)


def _tonone_designer(soup: BeautifulSoup) -> tuple[str, str]:
    name = _property_value(soup, "designer")
    return name, ""


TONONE_SKIP_CRUMBS = frozenset({"model", "models", "types", "type"})
TONONE_TYPE_SUBS = frozenset(
    {
        "pendants",
        "floor lamps",
        "wall lamps",
        "table lamps",
        "ceiling lamps",
        "desk lamps",
        "spare parts",
        "suspension lamps",
    }
)
FETCH_RETRIES = 3
RETRY_STATUS = frozenset({429, 502, 503, 504})


def _slug_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 2 and parts[-1].isdigit():
        return parts[-2]
    return parts[0] if parts else ""


TONONE_COLLECTION_MAP = {
    "bolt10": "Bolt10",
    "bolt": "Bolt",
    "beads": "Beads",
    "ella": "Ella",
    "bridge": "Bridge",
    "atlas": "Atlas",
    "bella": "Bella",
    "lumi": "Lumi",
    "one": "ONE",
    "mr. tubes": "Mr. Tubes",
    "mr tubes": "Mr. Tubes",
}


def _tonone_collection(soup: BeautifulSoup) -> str:
    """Product line from technical spec collection field (BOLT10, ELLA, BEADS, …)."""
    raw = _property_value(soup, "collection").strip()
    if not raw:
        return ""
    return TONONE_COLLECTION_MAP.get(raw.lower(), raw.title())


def _tonone_model_line(product_url: str, product_name: str) -> str:
    """Product line from URL slug when breadcrumb is a type listing (pendants, floor lamps, …)."""
    slug = _slug_from_url(product_url).lower()
    name_low = (product_name or "").lower()

    if slug.startswith("mr") or "mr. tubes" in name_low or "mr tubes" in name_low:
        return "Mr. Tubes"
    if slug.startswith("bridge") or name_low.startswith("bridge"):
        return "Bridge"
    if slug.startswith("bolt10") or name_low.startswith("bolt10"):
        return "Bolt10"
    if slug.startswith("bolt") or name_low.startswith("bolt") or " bolt" in name_low:
        return "Bolt"
    if slug.startswith("beads") or name_low.startswith("beads"):
        return "Beads"
    if slug.startswith("atlas") or name_low.startswith("atlas"):
        return "Atlas"
    if slug.startswith("bella") or name_low.startswith("bella"):
        return "Bella"
    if slug.startswith("lumi") or name_low.startswith("lumi"):
        return "Lumi"
    if slug.startswith("ella") or name_low == "ella":
        return "Ella"
    if slug.startswith("wingnut") or name_low.startswith("wingnut"):
        return "Wingnut"
    if slug.startswith("one") or name_low.startswith("one"):
        return "ONE"
    if "wall-ring" in slug or "wall ring" in name_low:
        return "Bolt"

    token = slug.split("-")[0]
    if token == "one":
        return "ONE"
    return _slug_to_title(token)


def _tonone_categories(
    soup: BeautifulSoup, product_name: str, product_url: str
) -> tuple[str, str]:
    """
    Breadcrumb model line: Products > model > Bridge
    Type listing fallback: Products > types > pendants → infer Atlas/Bolt/… from URL.
    """
    crumbs: list[str] = []
    for item in soup.select(".breadcrumb-item .breadcrumb-title"):
        text = item.get_text(strip=True)
        if not text:
            continue
        low = text.lower()
        if low in TONONE_SKIP_CRUMBS:
            continue
        if low == product_name.lower():
            continue
        crumbs.append(text)

    category = "Products"
    sub = ""
    if crumbs:
        if crumbs[0].lower() == "products":
            category = "Products"
            sub = crumbs[-1] if len(crumbs) > 1 else ""
        else:
            category, sub = crumbs[0], crumbs[-1] if len(crumbs) > 1 else ""

    if not sub or sub.lower() in TONONE_TYPE_SUBS:
        sub = _tonone_collection(soup) or _tonone_model_line(product_url, product_name)

    return category, sub


def _fetch_product_page(product_url: str, timeout: float) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            resp = requests.get(
                product_url,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            if resp.status_code in RETRY_STATUS and attempt < FETCH_RETRIES - 1:
                time.sleep(2 ** attempt + 1)
                continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < FETCH_RETRIES - 1:
                time.sleep(2 ** attempt + 1)
    if last_exc:
        raise last_exc
    raise requests.RequestException(f"Failed to fetch {product_url}")


def _tonone_preview_url(url: str) -> str:
    """Small Tonone thumbnail for faster background checks."""
    parsed = urlparse(url)
    path = parsed.path
    if "/media/" not in path or "." not in path:
        return url
    stem, ext = path.rsplit(".", 1)
    preview_path = stem.replace("/media/", "/thumbnail/", 1) + f"_300x300.{ext}"
    return urlunparse(parsed._replace(path=quote(preview_path, safe="/%")))


def assign_tonone_images(
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
        preview_url=_tonone_preview_url,
        lifestyle_hints=TONONE_LIFESTYLE_HINTS,
    )
    product.product_images = list(urls)
    product.hero_images = hero_shots
    product.lifestyle_images = lifestyle_shots
    product.detail_image = hero_shots[-1] if len(hero_shots) > 2 else ""


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
    path = parsed.path.rstrip("/")
    if not path:
        return False
    if not PRODUCT_PATH_RE.match(path if path.startswith("/") else f"/{path}"):
        return False
    slug = path.strip("/").split("/")[0].lower()
    return slug not in {"products", "explore", "professionals", "account"}


def _is_listing_url(url: str, host_key: str) -> bool:
    parsed = urlparse(url)
    h = parsed.netloc.lower()
    if h.startswith("www."):
        h = h[4:]
    if h != host_key:
        return False
    path = parsed.path.lower()
    return any(m in path for m in LISTING_MARKERS)


def _listing_url(url: str) -> str:
    """Shopware listing pages need a trailing slash."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _extract_product_urls(html: str, page_url: str, host_key: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for a in soup.select(".product-box a[href], a.product-image-link[href]"):
        href = urljoin(page_url, a["href"]).split("#")[0].rstrip("/")
        if _is_product_url(href, host_key):
            found.add(href)
    return found


def _extract_links(html: str, page_url: str, host_key: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        if _is_listing_url(href, host_key) or _is_product_url(href, host_key):
            yield href


def _paginate_listing(listing_url: str, host_key: str, timeout: float) -> set[str]:
    listing_url = _listing_url(listing_url)
    found: set[str] = set()
    for page in range(1, 51):
        page_url = listing_url if page == 1 else f"{listing_url}?p={page}"
        try:
            resp = requests.get(
                page_url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        except requests.RequestException:
            break
        if resp.status_code >= 400:
            break
        batch = _extract_product_urls(resp.text, str(resp.url), host_key)
        if not batch:
            break
        new = batch - found
        found |= batch
        if not new:
            break
    return found


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    base = normalize_url(site_url)
    if not base:
        return []

    host_key = _host_key(base)
    found: set[str] = set()
    listings: set[str] = {_listing_url(urljoin(base + "/", "products/types/all"))}

    try:
        resp = requests.get(
            base + "/", headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
        )
        if resp.status_code < 400:
            for href in _extract_links(resp.text, str(resp.url), host_key):
                if _is_listing_url(href, host_key):
                    listings.add(_listing_url(href))
    except requests.RequestException:
        pass

    for listing in sorted(listings):
        found |= _paginate_listing(listing, host_key, timeout)
        time.sleep(0.2)

    return sorted(found)


def scrape_product_categories(
    product_url: str, brand_name: str, timeout: float
) -> ScrapedProduct:
    """Fetch page and extract only category / sub_category (no images or description)."""
    product = ScrapedProduct(product_url=product_url.rstrip("/"), Brand_table=brand_name)
    try:
        resp = _fetch_product_page(product_url, timeout)
    except requests.RequestException as exc:
        product.scrape_ok = False
        product.scrape_error = str(exc)
        return product

    if resp.status_code >= 400:
        product.scrape_ok = False
        product.scrape_error = f"HTTP {resp.status_code}"
        return product

    final_url = str(resp.url).rstrip("/")
    product.product_url = final_url
    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")
    product.product_name = h1.get_text(strip=True) if h1 else _slug_to_title(final_url)
    product.product_category, product.sub_category = _tonone_categories(
        soup, product.product_name, final_url
    )
    if not product.product_category:
        product.scrape_ok = False
        product.scrape_error = "No product category found"
        return product

    capture_source_categories(product)
    normalize_product_categories(product)
    return product


def scrape_product_page(product_url: str, brand_name: str, timeout: float) -> ScrapedProduct:
    product = ScrapedProduct(product_url=product_url.rstrip("/"), Brand_table=brand_name)
    try:
        resp = _fetch_product_page(product_url, timeout)
    except requests.RequestException as exc:
        product.scrape_ok = False
        product.scrape_error = str(exc)
        return product

    if resp.status_code >= 400:
        product.scrape_ok = False
        product.scrape_error = f"HTTP {resp.status_code}"
        return product

    final_url = str(resp.url).rstrip("/")
    product.product_url = final_url
    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")

    product.product_name = h1.get_text(strip=True) if h1 else _slug_to_title(final_url)

    json_desc = description_from_json_ld(soup)
    if json_desc:
        product.product_description = json_desc
    else:
        product.product_description = _tonone_description(soup)

    product.designer, product.designerDescription = _tonone_designer(soup)
    product.price = _tonone_price(soup)
    product.product_category, product.sub_category = _tonone_categories(
        soup, product.product_name, final_url
    )
    assign_tonone_images(
        product, _tonone_images(soup, final_url), timeout=timeout
    )

    if not product.product_images and not product.lifestyle_images:
        product.scrape_ok = False
        product.scrape_error = "No product images found"
        return product

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
