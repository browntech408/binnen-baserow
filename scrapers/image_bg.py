"""Classify packshots (white background) vs lifestyle photos for Baserow image fields."""
from __future__ import annotations

import io
from collections.abc import Callable
from html import unescape

import requests
from PIL import Image

from brand_scraper import DEFAULT_HEADERS
from product_schema import ScrapedProduct

WHITE_RGB_MIN = 230
WHITE_SAMPLE_RATIO = 0.72
SOLID_BG_RATIO = 0.72
SOLID_COLOR_TOLERANCE = 28
SOLID_CHANNEL_SPREAD_MAX = 55

DEFAULT_LIFESTYLE_HINTS = (
    "hometour",
    "website.jpg",
    "gallery",
    "wonderwood",
    "interior",
    "ambiance",
    "lifestyle",
    "room-scene",
)


def _pixel_is_white(pixel: tuple[int, ...]) -> bool:
    if len(pixel) == 4:
        r, g, b, a = pixel
        if a < 128:
            return True
    else:
        r, g, b = pixel[:3]
    return r >= WHITE_RGB_MIN and g >= WHITE_RGB_MIN and b >= WHITE_RGB_MIN


def has_white_background(image_bytes: bytes) -> bool:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    img.thumbnail((220, 220))
    w, h = img.size
    if w < 2 or h < 2:
        return False

    samples: list[tuple[int, ...]] = []
    border = max(2, min(w, h) // 12)
    for x in range(w):
        for y in range(border):
            samples.append(img.getpixel((x, y)))
            samples.append(img.getpixel((x, h - 1 - y)))
    for y in range(border, h - border):
        for x in range(border):
            samples.append(img.getpixel((x, y)))
            samples.append(img.getpixel((w - 1 - x, y)))

    if not samples:
        return False
    white = sum(1 for px in samples if _pixel_is_white(px))
    return (white / len(samples)) >= WHITE_SAMPLE_RATIO


def _border_samples(img: Image.Image) -> list[tuple[int, int, int]]:
    w, h = img.size
    if w < 2 or h < 2:
        return []
    border = max(2, min(w, h) // 12)
    samples: list[tuple[int, int, int]] = []
    for x in range(w):
        for y in range(border):
            samples.append(img.getpixel((x, y))[:3])
            samples.append(img.getpixel((x, h - 1 - y))[:3])
    for y in range(border, h - border):
        for x in range(border):
            samples.append(img.getpixel((x, y))[:3])
            samples.append(img.getpixel((w - 1 - x, y))[:3])
    return samples


def _pixel_near_color(
    pixel: tuple[int, int, int], color: tuple[int, int, int], tolerance: int
) -> bool:
    return (
        abs(pixel[0] - color[0]) <= tolerance
        and abs(pixel[1] - color[1]) <= tolerance
        and abs(pixel[2] - color[2]) <= tolerance
    )


def has_solid_background(image_bytes: bytes) -> bool:
    """
    True when the image border is mostly one flat color
    (white, grey, beige, black, or any solid studio backdrop).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    img.thumbnail((220, 220))
    samples = _border_samples(img)
    if len(samples) < 20:
        return False

    # Quantize to reduce noise, then take the dominant border color.
    buckets: dict[tuple[int, int, int], int] = {}
    for r, g, b in samples:
        key = (r // 8 * 8, g // 8 * 8, b // 8 * 8)
        buckets[key] = buckets.get(key, 0) + 1
    dominant = max(buckets.items(), key=lambda kv: kv[1])[0]

    # Reject busy multi-color borders (lifestyle / room scenes).
    channel_spread = max(
        max(px[i] for px in samples) - min(px[i] for px in samples) for i in range(3)
    )
    near = sum(
        1 for px in samples if _pixel_near_color(px, dominant, SOLID_COLOR_TOLERANCE)
    )
    ratio = near / len(samples)
    if ratio < SOLID_BG_RATIO:
        return False
    # Allow solid even if spread is high when almost all pixels match dominant
    # (e.g. white with tiny noise). Otherwise require low channel spread.
    if ratio >= 0.90:
        return True
    return channel_spread <= SOLID_CHANNEL_SPREAD_MAX


def classify_solid_background_url(
    url: str,
    timeout: float,
    *,
    preview_url: Callable[[str], str] | None = None,
) -> bool | None:
    """Return True=solid studio bg, False=not solid, None=unknown/fetch fail."""
    fetch_url = preview_url(url) if preview_url else url
    try:
        resp = requests.get(
            fetch_url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code >= 400 and fetch_url != url:
            resp = requests.get(
                url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        if resp.status_code >= 400 or not resp.content:
            return None
        return has_solid_background(resp.content)
    except (requests.RequestException, OSError, ValueError):
        return None


def classify_image_url(
    url: str,
    timeout: float,
    *,
    preview_url: Callable[[str], str] | None = None,
) -> bool | None:
    """Return True=white background, False=lifestyle, None=unknown."""
    fetch_url = preview_url(url) if preview_url else url
    try:
        resp = requests.get(
            fetch_url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code >= 400 and fetch_url != url:
            resp = requests.get(
                url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
            )
        if resp.status_code >= 400 or not resp.content:
            return None
        return has_white_background(resp.content)
    except (requests.RequestException, OSError, ValueError):
        return None


def split_images_by_background(
    urls: list[str],
    timeout: float,
    *,
    preview_url: Callable[[str], str] | None = None,
    lifestyle_hints: tuple[str, ...] = DEFAULT_LIFESTYLE_HINTS,
) -> tuple[list[str], list[str]]:
    product: list[str] = []
    lifestyle: list[str] = []
    for url in urls:
        is_white = classify_image_url(url, timeout, preview_url=preview_url)
        if is_white is True:
            product.append(url)
        elif is_white is False:
            lifestyle.append(url)
        else:
            low = unescape(url).lower()
            if any(hint in low for hint in lifestyle_hints):
                lifestyle.append(url)
            else:
                product.append(url)
    return product, lifestyle


def merge_scraped_image_urls(product: ScrapedProduct) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in (
        *product.product_images,
        *product.hero_images,
        *product.lifestyle_images,
    ):
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    if product.detail_image and product.detail_image not in seen:
        out.append(product.detail_image)
    return out


def assign_images_by_background(
    product: ScrapedProduct,
    urls: list[str],
    *,
    timeout: float,
    preview_url: Callable[[str], str] | None = None,
    lifestyle_hints: tuple[str, ...] = DEFAULT_LIFESTYLE_HINTS,
) -> None:
    product_shots, lifestyle_shots = split_images_by_background(
        urls,
        timeout,
        preview_url=preview_url,
        lifestyle_hints=lifestyle_hints,
    )
    product.product_images = product_shots
    product.lifestyle_images = lifestyle_shots
    product.hero_images = (
        [product_shots[0]]
        if product_shots
        else ([lifestyle_shots[0]] if lifestyle_shots else [])
    )
    product.detail_image = product_shots[-1] if len(product_shots) > 2 else ""
