"""Upload scraped image URLs into Baserow file fields."""
from __future__ import annotations

from urllib.parse import urlparse

from baserow_client import BaserowClient
from config import Settings


def _image_dedupe_key(url: str) -> str:
    """Collapse srcset / resize variants to one key per image."""
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u)
    path = parsed.path
    if "storyblok.com" in parsed.netloc and "/m/" in path:
        path = path.split("/m/")[0]
    return f"{parsed.netloc}{path}".lower()


def dedupe_image_urls(urls: list[str], *, max_count: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = _image_dedupe_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(url.strip())
        if len(out) >= max_count:
            break
    return out


class BaserowImageUploader:
    """Upload remote URLs to Baserow and return file field payloads."""

    def __init__(self, client: BaserowClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._cache: dict[str, str] = {}

    def upload_many(self, urls: list[str]) -> list[dict[str, str]]:
        names: list[dict[str, str]] = []
        for url in urls:
            name = self.upload_one(url)
            if name:
                names.append({"name": name})
        return names

    def upload_one(self, url: str) -> str | None:
        url = (url or "").strip()
        if not url:
            return None
        key = _image_dedupe_key(url)
        if key in self._cache:
            return self._cache[key]
        try:
            name = self._client.upload_file_via_url(
                url, timeout=self._settings.http_timeout * 4
            )
        except Exception:
            return None
        self._cache[key] = name
        return name


def build_image_fields(
    product,
    settings: Settings,
    uploader: BaserowImageUploader,
) -> dict[str, list[dict[str, str]]]:
    """Map ScrapedProduct image URLs to Baserow file field values."""
    if not settings.upload_product_images:
        return {}

    s = settings
    fields: dict[str, list[dict[str, str]]] = {}

    product_urls = dedupe_image_urls(
        product.product_images, max_count=s.max_product_images_upload
    )
    hero_urls = dedupe_image_urls(product.hero_images, max_count=1)
    lifestyle_urls = dedupe_image_urls(
        product.lifestyle_images, max_count=s.max_lifestyle_images_upload
    )
    detail_urls = dedupe_image_urls(
        [product.detail_image] if product.detail_image else [],
        max_count=1,
    )

    if s.field_product_images and product_urls:
        uploaded = uploader.upload_many(product_urls)
        if uploaded:
            fields[s.field_product_images] = uploaded

    if s.field_hero_images and hero_urls:
        uploaded = uploader.upload_many(hero_urls)
        if uploaded:
            fields[s.field_hero_images] = uploaded

    if s.field_lifestyle_images and lifestyle_urls:
        uploaded = uploader.upload_many(lifestyle_urls)
        if uploaded:
            fields[s.field_lifestyle_images] = uploaded

    if s.field_detail_image and detail_urls:
        uploaded = uploader.upload_many(detail_urls)
        if uploaded:
            fields[s.field_detail_image] = uploaded

    return fields
