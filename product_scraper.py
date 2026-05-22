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

    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        product.product_description = og_desc["content"].strip()
    else:
        product.product_description = _main_paragraphs(soup)

    product.designer, product.designerDescription = _parse_designer(soup)
    product.price = _find_price(soup)
    product.product_category, product.sub_category = _parse_categories(soup, final_url)
    product.source_product_category = product.product_category
    product.source_product_subcategory = product.sub_category

    images = _collect_images(resp.text, final_url)
    product.product_images = images
    if images:
        product.hero_images = [images[0]]
        product.lifestyle_images = images[1:4] if len(images) > 1 else []
        product.detail_image = images[-1] if len(images) > 2 else ""

    return product


def _slug_to_title(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").title()


def _main_paragraphs(soup: BeautifulSoup) -> str:
    main = soup.find("main") or soup.body
    if not main:
        return ""
    chunks = []
    for p in main.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) > 60:
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


def _parse_categories(soup: BeautifulSoup, url: str) -> tuple[str, str]:
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if "/categorie" in href or "/category" in href:
            if text:
                return text, text
    # fallback from URL segment
    slug = urlparse(url).path.split("/")[-1]
    return "Collectie", slug.replace("-", " ").title()


def _collect_images(html: str, page_url: str, *, max_images: int = 12) -> list[str]:
    slug = urlparse(page_url).path.rstrip("/").split("/")[-1].lower()
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []

    for match in re.findall(
        r"https?://[^\s\"')]+?/wp-content/uploads/[^\s\"')]+\.(?:jpg|jpeg|png|webp)",
        html,
        flags=re.I,
    ):
        url = match.split("?")[0]
        low = url.lower()
        if any(x in low for x in ("icon", "logo", "marker", "globe", "search", ".svg")):
            continue
        # Skip WordPress thumbnail variants (e.g. -249x300.jpg)
        if re.search(r"-\d+x\d+\.(jpg|jpeg|png|webp)$", low):
            continue
        if url in seen:
            continue
        seen.add(url)
        score = 2 if slug and slug in low else 1
        scored.append((score, url))

    scored.sort(key=lambda x: (-x[0], x[1]))
    out = [u for _, u in scored[:max_images]]

    og = BeautifulSoup(html, "lxml").find("meta", property="og:image")
    if og and og.get("content"):
        u = og["content"].split("?")[0]
        if u not in out:
            out.insert(0, u)
    return out[:max_images]


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
