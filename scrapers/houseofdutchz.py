"""
House of Dutchz (houseofdutchz.nl) — Magento catalog.

Discovery: sitemap.xml product URLs (`*-{sku}.html`), optionally enriched from
woonbloq.xml feed (categories, images, description).

Images: all → product_images; white/no-bg → hero_images; room/bg → lifestyle_images
(same split as Pode).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from brand_scraper import DEFAULT_HEADERS, normalize_url
from product_schema import ScrapedProduct
from scrapers.image_bg import split_images_by_background
from scrapers.taxonomy import normalize_product_categories
from scrapers.text_clean import clean_product_description, description_from_json_ld

FEED_URL = "https://www.houseofdutchz.nl/var/feeds/woonbloq.xml"
SITEMAP_URL = "https://www.houseofdutchz.nl/sitemap.xml"
PRODUCT_URL_RE = re.compile(r"-\d{5,}\.html$", re.I)
SKU_FROM_URL_RE = re.compile(r"-(\d{5,})\.html$", re.I)
CATALOG_IMG_RE = re.compile(
    r"https://www\.houseofdutchz\.nl/media/catalog/product/[^\s\"'<>]+",
    re.I,
)
MAX_IMAGES = 0  # 0 = no limit

DUTCHZ_LIFESTYLE_HINTS = (
    "hometour",
    "interior",
    "ambiance",
    "lifestyle",
    "room",
    "sfeer",
    "setting",
    "website.jpg",
    "gallery",
)

# Feed Turnover_Group → Baserow category / subcategory
TURNOVER_MAP: dict[str, tuple[str, str]] = {
    "bankstellen": ("Banken", ""),
    "eetkamerstoelen": ("Stoelen", "Eetkamerstoelen"),
    "karpetten": ("Overig", "Vloerkleden"),
    "verlichting": ("Overig", "Verlichting"),
    "relaxfauteuils": ("Stoelen", "Fauteuils"),
    "fauteuils": ("Stoelen", "Fauteuils"),
    "eetkamertafels": ("Tafels", "Eettafel"),
    "salon/hoektafels": ("Tafels", "Salontafel"),
    "salon hoektafels": ("Tafels", "Salontafel"),
}

UITVOERING_SUB: dict[str, str] = {
    "hoekbank": "Hoekbanken",
    "hoekbank element": "Hoekbanken",
    "3 zitsbank": "3-zitsbank",
    "3,5 zitsbank": "3-zitsbank",
    "loveseat": "2-zitsbank",
    "eetstoel": "Eetkamerstoelen",
    "armstoel": "Eetkamerstoelen",
    "armstoel met wiel": "Eetkamerstoelen",
    "eetbank": "Eetkamerbanken",
    "eettafel": "Eettafel",
    "salontafel": "Salontafel",
    "bijzettafel": "Bijzettafel",
    "karpet": "Vloerkleden",
    "verlichting": "Verlichting",
    "fauteuil": "Fauteuils",
    "fauteuil met relax": "Fauteuils",
    "draaifauteuil": "Fauteuils",
    "hocker": "Poefjes",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _sku_from_url(url: str) -> str:
    m = SKU_FROM_URL_RE.search(urlparse(url).path)
    return m.group(1) if m else ""


def _uncached_catalog_url(url: str) -> str:
    """Prefer original Magento path over /cache/... variants."""
    u = (url or "").split("?")[0].strip()
    m = re.search(
        r"(https://www\.houseofdutchz\.nl/media/catalog/product/)(?:cache/[^/]+/)?(.+)$",
        u,
        re.I,
    )
    if m:
        return m.group(1) + m.group(2)
    return u


def _cdata_text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _load_feed_items(timeout: float) -> dict[str, dict]:
    """SKU → feed fields. Tries live feed, then local upload copy."""
    candidates: list[str | Path] = [
        Path("output") / "dutchz_woonbloq.xml",
        Path(
            r"C:\Users\Admin\.cursor\projects\c-projects-binnen-baserow2"
            r"\uploads\woonbloq-1.xml"
        ),
        Path(
            r"C:\Users\Admin\.cursor\projects\c-projects-binnen-baserow2"
            r"\uploads\woonbloq-0.xml"
        ),
        FEED_URL,
    ]
    xml_text = ""
    for src in candidates:
        try:
            if isinstance(src, Path):
                if not src.is_file():
                    continue
                raw = src.read_text(encoding="utf-8", errors="replace")
            else:
                resp = requests.get(
                    src, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
                )
                if resp.status_code >= 400:
                    continue
                raw = resp.text
            idx = raw.find("<?xml")
            xml_text = raw[idx:] if idx >= 0 else raw
            if "<item>" in xml_text:
                break
        except Exception:
            continue

    if not xml_text or "<item>" not in xml_text:
        return {}

    # Recover truncated CDATA if needed
    if xml_text.count("<![CDATA[") > xml_text.count("]]>"):
        xml_text += "]]>" * (xml_text.count("<![CDATA[") - xml_text.count("]]>"))

    by_sku: dict[str, dict] = {}
    try:
        root = ET.fromstring(xml_text)
        items = root.findall("item")
    except ET.ParseError:
        # Regex fallback
        blocks = re.findall(r"<item>(.*?)</item>", xml_text, flags=re.S | re.I)
        for block in blocks:
            def g(tag: str) -> str:
                m = re.search(
                    rf"<{tag}><!\[CDATA\[(.*?)\]\]></{tag}>",
                    block,
                    flags=re.S | re.I,
                )
                return (m.group(1).strip() if m else "")

            sku = g("Artikelnummer")
            if not sku:
                continue
            imgs = re.findall(
                r"https://www\.houseofdutchz\.nl/media/catalog/product/[^\s\]<]+",
                block,
                flags=re.I,
            )
            by_sku[sku] = {
                "name": g("Name"),
                "description": g("Ct"),
                "turnover": g("Turnover_Group"),
                "uitvoering": g("Uitvoering") or g("Uitvoeringen"),
                "main_article": g("Main_article"),
                "price": g("Price"),
                "images": [_uncached_catalog_url(u) for u in imgs],
            }
        return by_sku

    for item in items:
        sku = _cdata_text(item.find("Artikelnummer"))
        if not sku:
            continue
        images_el = item.find("Images")
        imgs: list[str] = []
        if images_el is not None:
            for child in images_el:
                src = _uncached_catalog_url(_cdata_text(child))
                if src.startswith("http"):
                    imgs.append(src)
        by_sku[sku] = {
            "name": _cdata_text(item.find("Name")),
            "description": _cdata_text(item.find("Ct")),
            "turnover": _cdata_text(item.find("Turnover_Group")),
            "uitvoering": _cdata_text(item.find("Uitvoering"))
            or _cdata_text(item.find("Uitvoeringen")),
            "main_article": _cdata_text(item.find("Main_article")),
            "price": _cdata_text(item.find("Price")),
            "images": imgs,
        }
    return by_sku


def _apply_dutchz_categories(
    product: ScrapedProduct,
    *,
    turnover: str,
    uitvoering: str,
) -> None:
    product.source_product_category = turnover.strip()
    product.source_product_subcategory = uitvoering.strip()

    mapped = TURNOVER_MAP.get(_norm(turnover))
    if mapped:
        cat, sub = mapped
    else:
        cat, sub = turnover.strip(), ""

    uit_sub = UITVOERING_SUB.get(_norm(uitvoering))
    if uit_sub:
        # Prefer finer uitvoering as subcategory when top cat already set
        if cat in ("Banken", "Stoelen", "Tafels", "Overig") or not sub:
            sub = uit_sub
        elif not sub:
            sub = uit_sub

    product.product_category = cat
    product.sub_category = sub
    normalize_product_categories(product)


def _page_images(html: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in CATALOG_IMG_RE.finditer(html):
        url = _uncached_catalog_url(unescape(m.group(0)))
        if not re.search(r"\.(jpg|jpeg|png|webp)$", url, re.I):
            continue
        key = urlparse(url).path.lower()
        if key in seen:
            continue
        # skip tiny cache-only duplicates of same file already added
        seen.add(key)
        out.append(url)
        if MAX_IMAGES > 0 and len(out) >= MAX_IMAGES:
            break
    return out


def assign_dutchz_images(
    product: ScrapedProduct,
    urls: list[str],
    *,
    timeout: float,
) -> None:
    if not urls:
        product.product_images = []
        product.hero_images = []
        product.lifestyle_images = []
        product.detail_image = ""
        return

    # Dedupe by uncached path
    deduped: list[str] = []
    seen: set[str] = set()
    for u in urls:
        key = urlparse(_uncached_catalog_url(u)).path.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(_uncached_catalog_url(u))

    hero_shots, lifestyle_shots = split_images_by_background(
        deduped,
        timeout,
        lifestyle_hints=DUTCHZ_LIFESTYLE_HINTS,
    )
    product.product_images = list(deduped)
    product.hero_images = hero_shots
    product.lifestyle_images = lifestyle_shots
    product.detail_image = hero_shots[-1] if len(hero_shots) > 2 else ""


def discover_product_urls(site_url: str, timeout: float) -> list[str]:
    base = normalize_url(site_url) or "https://www.houseofdutchz.nl"
    host = urlparse(base).netloc
    urls: list[str] = []
    seen: set[str] = set()

    try:
        resp = requests.get(
            SITEMAP_URL, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
        )
        if resp.status_code < 400:
            for loc in re.findall(r"<loc>(.*?)</loc>", resp.text):
                u = loc.strip().rstrip("/")
                if urlparse(u).netloc != host:
                    continue
                if not PRODUCT_URL_RE.search(urlparse(u).path):
                    continue
                if u in seen:
                    continue
                seen.add(u)
                urls.append(u)
    except requests.RequestException:
        pass

    if urls:
        return urls

    # Fallback: build from feed SKUs (name slug unknown — use feed Link pattern via crawl)
    feed = _load_feed_items(timeout)
    for sku, row in feed.items():
        name = row.get("name") or f"product-{sku}"
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        u = f"https://www.houseofdutchz.nl/{slug}-{sku}.html"
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def scrape_product_page(
    product_url: str,
    brand_name: str,
    timeout: float,
    *,
    feed_by_sku: dict[str, dict] | None = None,
) -> ScrapedProduct:
    product_url = product_url.rstrip("/")
    sku = _sku_from_url(product_url)
    feed = (feed_by_sku or {}).get(sku) or {}

    try:
        resp = requests.get(
            product_url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        product = ScrapedProduct(product_url=product_url, Brand_table=brand_name)
        product.scrape_ok = False
        product.scrape_error = str(exc)
        return product

    if resp.status_code >= 400:
        product = ScrapedProduct(product_url=product_url, Brand_table=brand_name)
        product.scrape_ok = False
        product.scrape_error = f"HTTP {resp.status_code}"
        return product

    final_url = str(resp.url).rstrip("/")
    html = resp.text
    soup = BeautifulSoup(resp.content, "lxml", from_encoding="utf-8")

    product = ScrapedProduct(product_url=final_url, Brand_table=brand_name)

    # Name
    json_name = ""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json

            data = json.loads(script.string or "")
        except Exception:
            continue
        if isinstance(data, list):
            data = next((x for x in data if isinstance(x, dict)), {})
        if not isinstance(data, dict):
            continue
        if str(data.get("@type", "")).lower() == "product":
            json_name = str(data.get("name") or "").strip()
            break
    h1 = soup.select_one("h1.page-title span, h1.page-title, h1")
    product.product_name = (
        json_name
        or (h1.get_text(strip=True) if h1 else "")
        or str(feed.get("name") or "")
        or final_url.rsplit("/", 1)[-1].replace(".html", "")
    )

    # Description: JSON-LD / page, else feed Ct (always plain text)
    json_desc = description_from_json_ld(soup)
    if json_desc:
        product.product_description = clean_product_description(json_desc)
    else:
        desc_el = soup.select_one(
            "#description .product.attribute.overview, "
            ".product.attribute.description .value, "
            ".product.info.detailed .description"
        )
        if desc_el:
            product.product_description = clean_product_description(
                desc_el.get_text("\n\n", strip=True)
            )
        elif feed.get("description"):
            product.product_description = clean_product_description(
                BeautifulSoup(feed["description"], "lxml").get_text("\n\n", strip=True)
            )

    if not product.product_description and feed.get("description"):
        product.product_description = clean_product_description(
            BeautifulSoup(feed["description"], "lxml").get_text("\n\n", strip=True)
        )

    # If description still looks like HTML, strip tags
    if "<" in (product.product_description or "") and ">" in (
        product.product_description or ""
    ):
        product.product_description = clean_product_description(
            BeautifulSoup(product.product_description, "lxml").get_text(
                "\n\n", strip=True
            )
        )

    # Price (feed often 0 — still try page)
    from product_scraper import _find_price

    product.price = _find_price(soup) or ""
    feed_price = str(feed.get("price") or "").strip()
    if (not product.price or product.price in ("0", "0.00")) and feed_price not in (
        "",
        "0",
        "0.00",
    ):
        product.price = feed_price

    # Categories from feed (+ URL slug hints via taxonomy)
    _apply_dutchz_categories(
        product,
        turnover=str(feed.get("turnover") or ""),
        uitvoering=str(feed.get("uitvoering") or ""),
    )
    # If feed missing, infer from product name / URL slug
    if not product.product_category:
        product.source_product_category = ""
        product.source_product_subcategory = ""
        from scrapers.taxonomy import _infer_from_slug

        cat, sub = _infer_from_slug(final_url, product.product_name)
        product.product_category = cat
        product.sub_category = sub
        normalize_product_categories(product)

    # Images: prefer feed order, else page
    feed_imgs = list(feed.get("images") or [])
    page_imgs = _page_images(html)
    image_urls = feed_imgs or page_imgs
    if feed_imgs and page_imgs:
        # merge: feed first, then any extra page urls
        seen = {urlparse(_uncached_catalog_url(u)).path.lower() for u in feed_imgs}
        image_urls = list(feed_imgs)
        for u in page_imgs:
            key = urlparse(_uncached_catalog_url(u)).path.lower()
            if key not in seen:
                seen.add(key)
                image_urls.append(u)

    assign_dutchz_images(product, image_urls, timeout=timeout)

    if not product.product_images and not product.lifestyle_images:
        product.scrape_ok = False
        product.scrape_error = "No product images found"
        return product

    product.scrape_ok = True
    return product


def scrape_brand_products(
    site_url: str,
    brand_name: str,
    *,
    timeout: float = 30,
    max_products: int = 5,
    delay_seconds: float = 1.0,
) -> tuple[list[str], list[ScrapedProduct]]:
    print("Loading Dutchz woonbloq.xml feed (categories/images)...")
    feed_by_sku = _load_feed_items(timeout)
    print(f"  feed SKUs: {len(feed_by_sku)}")

    urls = discover_product_urls(site_url, timeout)
    print(f"  discovered product URLs: {len(urls)}")
    if max_products > 0:
        urls = urls[:max_products]

    products: list[ScrapedProduct] = []
    for i, url in enumerate(urls):
        if i > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        products.append(
            scrape_product_page(
                url, brand_name, timeout, feed_by_sku=feed_by_sku
            )
        )
    return urls, products
