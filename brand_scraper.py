from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DataScraper/1.0; +https://example.com/bot)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en,nl;q=0.9",
}


@dataclass
class ScrapeResult:
    ok: bool
    url: str
    page_title: str | None = None
    meta_description: str | None = None
    error: str | None = None
    status_code: int | None = None
    final_url: str | None = None
    html_size_bytes: int | None = None
    h1_headings: list[str] | None = None
    sample_links: list[str] | None = None
    og_image: str | None = None


def normalize_url(domain_or_url: str) -> str | None:
    raw = (domain_or_url or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return None
    return raw.rstrip("/")


def scrape_brand_homepage(url: str, timeout: float) -> ScrapeResult:
    try:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        status = response.status_code
        if status >= 400:
            return ScrapeResult(
                ok=False,
                url=url,
                error=f"HTTP {status}",
                status_code=status,
            )

        soup = BeautifulSoup(response.text, "lxml")
        title_tag = soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else None

        meta_desc = None
        desc_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
        if desc_tag and desc_tag.get("content"):
            meta_desc = desc_tag["content"].strip()
        if not meta_desc:
            og = soup.find("meta", property="og:description")
            if og and og.get("content"):
                meta_desc = og["content"].strip()

        og_image = None
        og_img_tag = soup.find("meta", property="og:image")
        if og_img_tag and og_img_tag.get("content"):
            og_image = og_img_tag["content"].strip()

        h1_headings = [
            tag.get_text(strip=True)
            for tag in soup.find_all("h1")
            if tag.get_text(strip=True)
        ][:8]

        base_host = urlparse(str(response.url)).netloc
        sample_links: list[str] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            if href.startswith("/"):
                href = f"{response.url.rstrip('/')}{href}"
            elif not href.startswith("http"):
                continue
            if urlparse(href).netloc != base_host:
                continue
            if href in seen:
                continue
            seen.add(href)
            sample_links.append(href)
            if len(sample_links) >= 15:
                break

        return ScrapeResult(
            ok=True,
            url=url,
            final_url=str(response.url),
            page_title=page_title,
            meta_description=meta_desc,
            status_code=status,
            html_size_bytes=len(response.content),
            h1_headings=h1_headings,
            sample_links=sample_links,
            og_image=og_image,
        )
    except requests.RequestException as exc:
        return ScrapeResult(ok=False, url=url, error=str(exc))


def row_field(row: dict[str, Any], field_name: str) -> Any:
    """Read a cell by API field name (e.g. field_123 or human name)."""
    if not field_name:
        return None
    if field_name in row:
        return row[field_name]
    # Baserow sometimes exposes human-readable keys on row dict
    for key, value in row.items():
        if key == field_name or key.endswith(field_name):
            return value
    return None


def extract_domain(row: dict[str, Any], settings: Any) -> str | None:
    if settings.field_website_url:
        value = row_field(row, settings.field_website_url)
        if value:
            return str(value).strip()
    value = row_field(row, settings.field_domain)
    if value:
        return str(value).strip()
    return None


def extract_brand_name(row: dict[str, Any], field_name: str) -> str:
    value = row_field(row, field_name)
    if value is None or value == "":
        return f"row_{row.get('id', '?')}"
    return str(value).strip()


def _has_linked_products(value: Any) -> bool:
    if not value:
        return False
    if isinstance(value, list):
        return len(value) > 0
    return True


def should_scrape_row(row: dict[str, Any], settings: Any) -> tuple[bool, str]:
    """Return (scrape?, skip_reason)."""
    status_field = settings.field_scrape_status
    if status_field:
        status = row_field(row, status_field)
        if status is None or status == "":
            if not (
                "pending" in settings.scrape_only_statuses
                or "" in settings.scrape_only_statuses
            ):
                return False, "status filter (empty)"
        else:
            normalized = str(status).strip().lower()
            if normalized not in settings.scrape_only_statuses:
                return False, f"status filter ({normalized})"

    if settings.skip_if_brand_quote and settings.field_brand_quote:
        quote = row_field(row, settings.field_brand_quote)
        if quote and str(quote).strip():
            return False, "Brand quote already set"

    if settings.scrape_only_bg_remove and settings.field_bg_remove:
        if not row_field(row, settings.field_bg_remove):
            return False, "bg_remove is False"

    if settings.skip_if_has_products and settings.field_products:
        if _has_linked_products(row_field(row, settings.field_products)):
            return False, "productsDetails already linked"

    return True, ""
