"""
Spectrum Design (spectrumdesign.nl) — dedicated scraper.

Implementation lives in product_scraper.py (frozen /collectie/ behaviour).
Edit product_scraper.py for Spectrum-specific HTML changes.
"""
from __future__ import annotations

# Delegate 100% to existing working implementation
from product_scraper import (
    discover_product_urls,
    scrape_brand_products,
    scrape_product_page,
)

__all__ = ["discover_product_urls", "scrape_product_page", "scrape_brand_products"]
