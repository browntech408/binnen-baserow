"""
Spectrum Design — frozen path: uses product_scraper.py unchanged.
Do not modify product_scraper.py behaviour for this domain.
"""
from __future__ import annotations

# Delegate 100% to existing working implementation
from product_scraper import (
    discover_product_urls,
    scrape_brand_products,
    scrape_product_page,
)

__all__ = ["discover_product_urls", "scrape_product_page", "scrape_brand_products"]
