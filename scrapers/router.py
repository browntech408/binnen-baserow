"""
Pick scraper by website domain. Same output: list[ScrapedProduct].
Each brand has its own module under scrapers/ — edit that file when site HTML changes.
"""
from __future__ import annotations

from urllib.parse import urlparse

from brand_scraper import normalize_url
from product_schema import ScrapedProduct

# domain (no www) → dedicated scraper module
DOMAIN_MODULES: dict[str, str] = {
    "spectrumdesign.nl": "spectrum",
    "designonstock.com": "design_on_stock",
    "sleepworldhelmond.nl": "sleepworld",
    "artifort.com": "artifort",
    "baenks.nl": "baenks",
    "beekcollection.nl": "beek",
    "bertplantagie.com": "bert_plantagie",
    "castelijn.nl": "castelijn",
    "pode.eu": "pode",
    "label.nl": "label",
    "tonone.com": "tonone",
    "gealux.nl": "gealux",
    "carpetrebel.com": "carpetrebel",
    "houseofdutchz.nl": "houseofdutchz",
    "estiluz.com": "estiluz",
    "harvink.nl": "harvink",
    "montis.nl": "montis",
    "pastoe.com": "pastoe",
    "breesnewworld.nl": "breesnewworld",
    "jori.com": "jori",
    "metaformmeubelen.nl": "metaform",
    "brinker.nl": "brinker",
    "brinkercarpets.nl": "brinker",
    "fontanaarte.com": "fontanaarte",
    "gazzda.com": "gazzda",
}

ROUTER_DEFAULT_URLS: dict[str, str] = {
    "label.nl": "https://label.nl",
    "pode.eu": "https://www.pode.eu",
    "tonone.com": "https://www.tonone.com",
    "gealux.nl": "https://www.gealux.nl",
    "carpetrebel.com": "https://www.carpetrebel.com",
    "spectrumdesign.nl": "https://www.spectrumdesign.nl",
    "designonstock.com": "https://www.designonstock.com",
    "sleepworldhelmond.nl": "https://www.sleepworldhelmond.nl",
    "artifort.com": "https://www.artifort.com",
    "baenks.nl": "https://www.baenks.nl",
    "beekcollection.nl": "https://www.beekcollection.nl",
    "bertplantagie.com": "https://www.bertplantagie.com",
    "castelijn.nl": "https://www.castelijn.nl",
    "houseofdutchz.nl": "https://www.houseofdutchz.nl",
    "estiluz.com": "https://www.estiluz.com",
    "harvink.nl": "https://www.harvink.nl",
    "montis.nl": "https://montis.nl",
    "pastoe.com": "https://www.pastoe.com",
    "breesnewworld.nl": "https://www.breesnewworld.nl",
    "jori.com": "https://www.jori.com",
    "metaformmeubelen.nl": "https://metaformmeubelen.nl",
    "brinker.nl": "https://www.brinker.nl",
    "brinkercarpets.nl": "https://www.brinker.nl",
    "fontanaarte.com": "https://www.fontanaarte.com",
    "gazzda.com": "https://www.gazzda.com",
}

ROUTER_BRAND_HINTS: dict[str, str] = {
    "label.nl": "label",
    "pode.eu": "pode",
    "tonone.com": "tonone",
    "gealux.nl": "gealux",
    "carpetrebel.com": "carpetrebel",
    "houseofdutchz.nl": "houseofdutchz",
    "estiluz.com": "estiluz",
    "harvink.nl": "harvink",
    "montis.nl": "montis",
    "pastoe.com": "pastoe",
    "breesnewworld.nl": "breesnewworld",
    "jori.com": "jori",
    "metaformmeubelen.nl": "metaform",
    "brinker.nl": "brinker",
    "brinkercarpets.nl": "brinker",
    "fontanaarte.com": "fontanaarte",
    "gazzda.com": "gazzda",
}


def domain_key(site_url: str) -> str:
    host = urlparse(normalize_url(site_url) or site_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_router_domain(site_url: str) -> bool:
    return domain_key(site_url) in DOMAIN_MODULES


def router_domain_order() -> list[str]:
    return list(DOMAIN_MODULES.keys())


def get_scraper_module(site_url: str):
    key = domain_key(site_url)
    name = DOMAIN_MODULES.get(key, "spectrum")

    if name == "spectrum":
        from scrapers import spectrum as mod
    elif name == "design_on_stock":
        from scrapers import design_on_stock as mod
    elif name == "sleepworld":
        from scrapers import sleepworld as mod
    elif name == "artifort":
        from scrapers import artifort as mod
    elif name == "baenks":
        from scrapers import baenks as mod
    elif name == "beek":
        from scrapers import beek as mod
    elif name == "bert_plantagie":
        from scrapers import bert_plantagie as mod
    elif name == "castelijn":
        from scrapers import castelijn as mod
    elif name == "leolux":
        from scrapers import leolux as mod
    elif name == "label":
        from scrapers import label as mod
    elif name == "tonone":
        from scrapers import tonone as mod
    elif name == "pode":
        from scrapers import pode as mod
    elif name == "gealux":
        from scrapers import gealux as mod
    elif name == "carpetrebel":
        from scrapers import carpetrebel as mod
    elif name == "houseofdutchz":
        from scrapers import houseofdutchz as mod
    elif name == "estiluz":
        from scrapers import estiluz as mod
    elif name == "harvink":
        from scrapers import harvink as mod
    elif name == "montis":
        from scrapers import montis as mod
    elif name == "pastoe":
        from scrapers import pastoe as mod
    elif name == "breesnewworld":
        from scrapers import breesnewworld as mod
    elif name == "jori":
        from scrapers import jori as mod
    elif name == "metaform":
        from scrapers import metaform as mod
    elif name == "brinker":
        from scrapers import brinker as mod
    elif name == "fontanaarte":
        from scrapers import fontanaarte as mod
    elif name == "gazzda":
        from scrapers import gazzda as mod
    else:
        from scrapers import spectrum as mod

    return mod, name


def scrape_brand_products(
    site_url: str,
    brand_name: str,
    *,
    timeout: float = 30,
    max_products: int = 5,
    delay_seconds: float = 1.0,
) -> tuple[list[str], list[ScrapedProduct]]:
    mod, scraper_name = get_scraper_module(site_url)
    print(f"Scraper: {scraper_name} ({domain_key(site_url)})")
    return mod.scrape_brand_products(
        site_url,
        brand_name,
        timeout=timeout,
        max_products=max_products,
        delay_seconds=delay_seconds,
    )
