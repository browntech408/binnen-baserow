"""
Discover and scrape product pages from a brand website (Spectrum-style /collectie/ URLs).
"""
from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct

COLLECTIE_PATH = "/collectie/"
UPLOADS_PATH = "/wp-content/uploads/"
MAX_PRODUCTS_DEFAULT = 5  # demo limit; increase when ready


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    """Find product detail URLs under /collectie/{slug}."""
    base = normalize_url(site_url)
    if not base:
        return []

    candidates = [
        urljoin(base + "/", "collectie/"),
        urljoin(base + "/", "catalogus/"),
        base + "/",
    ]
    found: set[str] = set()
    host = urlparse(base).netloc

    for start in candidates:
        try:
            resp = requests.get(
                start, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        except requests.RequestException:
            continue
        if resp.status_code >= 400:
            continue

        for href in _extract_links(resp.text, str(resp.url), host):
            if _is_product_url(href):
                found.add(href.rstrip("/"))

        if found:
            break

    return sorted(found)


def _extract_links(html: str, page_url: str, host: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        if urlparse(href).netloc == host:
            yield href


def _is_product_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    if COLLECTIE_PATH not in path:
        return False
    # /collectie/slug — at least one segment after collectie
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return False
    if parts[-1] in ("collectie", "catalogus"):
        return False
    return True


def scrape_product_page(
    product_url: str, brand_name: str, timeout: float
) -> ScrapedProduct:
    product = ScrapedProduct(product_url=product_url.rstrip("/"), Brand_table=brand_name)
    try:
        resp = requests.get(
            product_url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
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

    product.product_description = _main_paragraphs(soup, h1)

    product.designer, product.designerDescription = _parse_designer(soup)
    product.price = _find_price(soup)
    product.product_category, product.sub_category = _parse_categories(soup, h1, final_url)
    product.source_product_category = product.product_category
    product.source_product_subcategory = product.sub_category

    images = _collect_product_images(soup, h1, final_url)
    product.product_images = images
    if images:
        product.hero_images = [images[0]]
        product.lifestyle_images = images[1:4] if len(images) > 1 else []
        product.detail_image = images[-1] if len(images) > 2 else ""

    return product


def _slug_to_title(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").title()


def _main_paragraphs(soup: BeautifulSoup, h1: BeautifulSoup | None) -> str:
    """Full product copy from the page body (not truncated og:description)."""
    if not h1:
        return _fallback_description(soup)

    chunks: list[str] = []
    stop_titles = {
        "product specificaties",
        "dit product bekijken",
        "downloads",
        "meer van deze ontwerper",
        "meer van deze productgroep",
    }

    for el in h1.find_all_next(["p", "h2"]):
        if el.find_previous("h1") is not h1:
            break
        prev_h2 = el.find_previous("h2")
        if prev_h2 and prev_h2.get_text(strip=True).lower() in stop_titles:
            break
        if el.name == "h2":
            if el.get_text(strip=True).lower() in stop_titles:
                break
            continue
        text = el.get_text(" ", strip=True)
        if len(text) < 60:
            continue
        if "»" in text and "collectie" in text.lower():
            continue
        if text not in chunks:
            chunks.append(text)

    if chunks:
        return "\n\n".join(chunks)

    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        return og_desc["content"].strip()
    return _fallback_description(soup)


def _fallback_description(soup: BeautifulSoup) -> str:
    main = soup.find("main") or soup.body
    if not main:
        return ""
    chunks = []
    for p in main.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) > 60 and "»" not in text:
            chunks.append(text)
    return "\n\n".join(chunks[:6])


def _parse_designer(soup: BeautifulSoup) -> tuple[str, str]:
    for h2 in soup.find_all("h2"):
        text = h2.get_text(" ", strip=True)
        if "|" in text and re.search(r"\d{4}", text):
            parts = [p.strip() for p in text.split("|")]
            name = parts[0].strip()
            year = parts[1].strip() if len(parts) > 1 else ""
            return name, year
    return "", ""


def _find_price(soup: BeautifulSoup) -> str:
    for node in soup.find_all(string=re.compile(r"€")):
        s = str(node).strip()
        if "€" in s and 3 < len(s) < 80:
            return s
    return ""


def _title_case_label(text: str) -> str:
    """Normalize breadcrumb labels (e.g. COLLECTIE → Collectie)."""
    t = text.strip()
    if not t:
        return ""
    if t.isupper() and len(t) > 3:
        return t.title()
    return t[0].upper() + t[1:] if len(t) > 1 else t.upper()


def _parse_breadcrumbs(soup: BeautifulSoup, h1: BeautifulSoup | None) -> list[str]:
    """e.g. ['Collectie', 'Accessories', 'Benno spiegel'] from Spectrum product pages."""
    product_name = h1.get_text(strip=True) if h1 else ""

    # Spectrum / Yoast: <p id="breadcrumbs"> with links + .breadcrumb_last
    crumb_el = soup.find(id="breadcrumbs") or soup.find(class_=re.compile(r"breadcrumb", re.I))
    if crumb_el:
        parts: list[str] = []
        for a in crumb_el.find_all("a"):
            t = a.get_text(strip=True)
            if t:
                parts.append(_title_case_label(t))
        last = crumb_el.find(class_=re.compile(r"breadcrumb_last", re.I))
        if last:
            parts.append(last.get_text(strip=True))
        elif product_name and (not parts or parts[-1].lower() != product_name.lower()):
            parts.append(product_name)
        if len(parts) >= 2:
            return parts

    if not h1:
        return []

    for el in h1.find_all_previous(["p", "nav"], limit=15):
        text = el.get_text(" ", strip=True)
        if "»" not in text and "›" not in text:
            continue
        parts = [_title_case_label(p) for p in re.split(r"\s*[»›]\s*", text) if p.strip()]
        if len(parts) >= 2:
            return parts

    p = h1.find_previous("p")
    if p:
        parts = []
        for a in p.find_all("a"):
            t = a.get_text(strip=True)
            if t:
                parts.append(_title_case_label(t))
        last = p.find("span", class_=re.compile(r"breadcrumb_last", re.I))
        if last:
            parts.append(last.get_text(strip=True))
        if len(parts) >= 2:
            return parts

    return [product_name] if product_name else []


def _parse_categories(
    soup: BeautifulSoup, h1: BeautifulSoup | None, url: str
) -> tuple[str, str]:
    """
    Map breadcrumb trail to product_category + sub_category.

    Spectrum layout: Collectie » Accessories » Benno spiegel
      → product_category=Collectie, sub_category=Accessories
    """
    crumbs = _parse_breadcrumbs(soup, h1)
    product_name = (h1.get_text(strip=True) if h1 else "").lower()

    if len(crumbs) >= 3 and crumbs[-1].lower() == product_name:
        # Top level + sub level; last crumb is the product title
        return crumbs[0], crumbs[1]

    if len(crumbs) >= 4 and crumbs[-1].lower() == product_name:
        return crumbs[-3], crumbs[-2]

    if len(crumbs) == 2:
        return crumbs[0], ""

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if "/categorie" in href or "/category" in href or "/collection-category" in href:
            if text:
                label = _title_case_label(text)
                return label, ""

    slug = urlparse(url).path.split("/")[-1]
    return "Collectie", slug.replace("-", " ").title()


def _normalize_image_url(url: str) -> str:
    url = url.split("?")[0]
    return re.sub(
        r"-\d+x\d+(?=\.(jpg|jpeg|png|webp)$)", "", url, flags=re.I
    )


def _is_product_image_url(url: str) -> bool:
    low = url.lower()
    if UPLOADS_PATH not in low:
        return False
    if any(x in low for x in ("icon", "logo", "marker", "globe", "search", ".svg")):
        return False
    return True


def _collect_product_images(
    soup: BeautifulSoup, h1: BeautifulSoup | None, page_url: str, *, max_images: int = 12
) -> list[str]:
    """Images from the product section only (excludes related products / footer)."""
    seen: set[str] = set()
    ordered: list[str] = []

    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        u = _normalize_image_url(og["content"])
        if _is_product_image_url(u):
            seen.add(u)
            ordered.append(u)

    if h1:
        for img in h1.find_all_next("img"):
            if img.find_previous("h1") is not h1:
                continue
            prev_h2 = img.find_previous("h2")
            if prev_h2 and "meer van" in prev_h2.get_text(strip=True).lower():
                break

            for attr in ("src", "data-src", "data-lazy-src"):
                raw = img.get(attr)
                if not raw:
                    continue
                url = _normalize_image_url(urljoin(page_url, raw))
                if not _is_product_image_url(url) or url in seen:
                    continue
                seen.add(url)
                ordered.append(url)

            srcset = img.get("srcset", "")
            for part in srcset.split(","):
                piece = part.strip().split()[0] if part.strip() else ""
                if not piece:
                    continue
                url = _normalize_image_url(urljoin(page_url, piece))
                if _is_product_image_url(url) and url not in seen:
                    seen.add(url)
                    ordered.append(url)

    if not ordered:
        slug = urlparse(page_url).path.rstrip("/").split("/")[-1].lower()
        for match in re.findall(
            r"https?://[^\s\"')]+?/wp-content/uploads/[^\s\"')]+\.(?:jpg|jpeg|png|webp)",
            str(soup),
            flags=re.I,
        ):
            url = _normalize_image_url(match)
            if not _is_product_image_url(url) or url in seen:
                continue
            if re.search(r"-\d+x\d+\.(jpg|jpeg|png|webp)$", url.lower()):
                continue
            if slug and slug.replace("-", "") not in url.lower().replace("-", ""):
                score = 0
            else:
                score = 1
            if score or len(ordered) < max_images:
                seen.add(url)
                ordered.append(url)

    return ordered[:max_images]


def scrape_brand_products(
    site_url: str,
    brand_name: str,
    *,
    timeout: float = 30,
    max_products: int = MAX_PRODUCTS_DEFAULT,
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
