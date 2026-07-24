"""Shopify-only image classification: lifestyle vs white bg vs transparent (no bg).

Separate from scrapers/image_bg.py — does not treat transparent borders as white.
"""
from __future__ import annotations

import io
from typing import Literal

import requests
from PIL import Image

from brand_scraper import DEFAULT_HEADERS

ImageKind = Literal["lifestyle", "white", "no_bg", "unknown"]

WHITE_RGB_MIN = 230
LIGHT_LUM_MIN = 180
LIGHT_SAT_MAX = 0.15
BORDER_TRANSPARENT_RATIO = 0.65
BORDER_WHITE_RATIO = 0.72
STUDIO_LIGHT_NEUTRAL_RATIO = 0.70
STUDIO_MIN_WHITE_BORDER = 0.48
STUDIO_MAX_SATURATION = 0.22
FULL_TRANSPARENT_RATIO = 0.12
THUMB_SIZE = 220


def _transparent_ratio(img: Image.Image) -> float:
    pixels = img.getdata()
    total = img.size[0] * img.size[1]
    if total == 0:
        return 0.0
    transparent = sum(1 for px in pixels if _is_transparent(px))
    return transparent / total


def _border_samples(img: Image.Image) -> list[tuple[int, ...]]:
    w, h = img.size
    border = max(2, min(w, h) // 12)
    samples: list[tuple[int, ...]] = []
    for x in range(w):
        for y in range(border):
            samples.append(img.getpixel((x, y)))
            samples.append(img.getpixel((x, h - 1 - y)))
    for y in range(border, h - border):
        for x in range(border):
            samples.append(img.getpixel((x, y)))
            samples.append(img.getpixel((w - 1 - x, y)))
    return samples


def _is_transparent(pixel: tuple[int, ...]) -> bool:
    return len(pixel) == 4 and pixel[3] < 128


def _is_opaque_white(pixel: tuple[int, ...]) -> bool:
    if len(pixel) == 4:
        r, g, b, a = pixel
        if a < 128:
            return False
    else:
        r, g, b = pixel[:3]
    return r >= WHITE_RGB_MIN and g >= WHITE_RGB_MIN and b >= WHITE_RGB_MIN


def _pixel_luminance(pixel: tuple[int, ...]) -> float:
    r, g, b = pixel[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _pixel_saturation(pixel: tuple[int, ...]) -> float:
    r, g, b = (c / 255.0 for c in pixel[:3])
    maximum = max(r, g, b)
    minimum = min(r, g, b)
    if maximum == 0:
        return 0.0
    return (maximum - minimum) / maximum


def _is_light_neutral(pixel: tuple[int, ...]) -> bool:
    if len(pixel) == 4 and pixel[3] < 128:
        return False
    return (
        _pixel_luminance(pixel) >= LIGHT_LUM_MIN
        and _pixel_saturation(pixel) < LIGHT_SAT_MAX
    )


def classify_image_bytes(image_bytes: bytes) -> ImageKind:
    """lifestyle=room/scene, white=white packshot, no_bg=transparent cutout."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except (OSError, ValueError):
        return "unknown"

    img.thumbnail((THUMB_SIZE, THUMB_SIZE))
    if img.size[0] < 2 or img.size[1] < 2:
        return "unknown"

    # Cutouts often fill the frame (product touches border) but large transparent areas remain.
    if _transparent_ratio(img) >= FULL_TRANSPARENT_RATIO:
        return "no_bg"

    samples = _border_samples(img)
    if not samples:
        return "unknown"

    total = len(samples)
    transparent = sum(1 for px in samples if _is_transparent(px))
    white = sum(1 for px in samples if _is_opaque_white(px))
    light_neutral = sum(1 for px in samples if _is_light_neutral(px))

    if transparent / total >= BORDER_TRANSPARENT_RATIO:
        return "no_bg"

    ln_ratio = light_neutral / total
    wh_ratio = white / total
    avg_sat = sum(_pixel_saturation(px) for px in samples) / total

    # Uniform studio/off-white background (Coolmotion JPEG packshots).
    if ln_ratio >= STUDIO_LIGHT_NEUTRAL_RATIO:
        return "white"
    if (
        ln_ratio >= 0.55
        and wh_ratio >= STUDIO_MIN_WHITE_BORDER
        and avg_sat < STUDIO_MAX_SATURATION
    ):
        return "white"
    if wh_ratio >= BORDER_WHITE_RATIO:
        return "white"
    if wh_ratio >= 0.50 and avg_sat < STUDIO_MAX_SATURATION:
        return "white"

    # Light walls / room scenes: not enough uniform white on borders.
    return "lifestyle"


def classify_image_url(
    url: str,
    timeout: float,
    *,
    preview_url: str | None = None,
) -> ImageKind:
    fetch_url = preview_url or url
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
            return "unknown"
        return classify_image_bytes(resp.content)
    except (requests.RequestException, OSError, ValueError):
        return "unknown"


def product_category(
    *,
    lifestyle: int,
    white: int,
    no_bg: int,
    unknown: int,
    image_count: int,
) -> str:
    """lifestyle only when every image is lifestyle (no white/no_bg/unknown)."""
    if image_count == 0:
        return "no_images"

    if lifestyle > 0 and white == 0 and no_bg == 0 and unknown == 0:
        return "lifestyle"

    if white > no_bg:
        return "white"
    if no_bg > white:
        return "no_bg"
    if white > 0:
        return "white"
    if no_bg > 0:
        return "no_bg"
    if unknown > 0:
        return "unknown"
    return "unknown"
