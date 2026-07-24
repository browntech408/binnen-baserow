"""
Map scraped category labels to Baserow taxonomy (806 / 807).
Used before save and in scrape post-processing.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from product_schema import ScrapedProduct

IGNORE_TOP = frozenset({"collectie", "collection", "catalogus", "catalog", "home"})

# Footer / nav crumbs — never use as taxonomy
IGNORE_NAV_SUB = frozenset(
    {"verkooppunten", "contact", "dealers", "over ons", "privacy", "cookie"}
)

# Subcategories we may auto-create in Baserow (807) when missing
ALLOW_AUTO_CREATE_SUB = frozenset(
    {"bedtextiel", "boxsprings", "slaapkamers", "aanbiedingen", "bartafels"}
)

# When breadcrumb sub is a top-level category name → default sub link
TOP_CATEGORY_DEFAULT_SUB: dict[str, str] = {
    "stoelen": "Stoelen",
    "banken": "",
    "tafels": "",
    "kasten": "",
}

# Scraped top-level label → Baserow productCategory name
CATEGORY_ALIASES: dict[str, str] = {
    "armchairs": "Stoelen",
    "chairs": "Stoelen",
    "sofas": "Banken",
    "sofa": "Banken",
    "tables": "Tafels",
    "table": "Tafels",
    "cabinets": "Kasten",
    "storage": "Kasten",
    "accessories": "Overig",
    "accessoires": "Overig",
    "kleinmeubelen": "Overig",
    "lighting": "Overig",
    "lamps": "Overig",
    "banken": "Banken",
    "stoelen": "Stoelen",
    "tafels": "Tafels",
    "kasten": "Kasten",
    "bankstellen": "Banken",
    "karpetten": "Overig",
    "verlichting": "Overig",
    "relaxfauteuils": "Stoelen",
    "fauteuils": "Stoelen",
    "eetkamertafels": "Tafels",
    "salon/hoektafels": "Tafels",
    "eetkamerstoelen": "Stoelen",
}

# Scraped sub label → Baserow subCategory name
SUB_ALIASES: dict[str, str] = {
    "accessories": "Woonaccessoires",
    "accessoires": "Woonaccessoires",
    "armchairs": "Fauteuils",
    "fauteuils": "Fauteuils",
    "hoekbanken": "Hoekbanken",
    "hoekbank": "Hoekbanken",
    "eetkamerstoelen": "Eetkamerstoelen",
    "eetkamerbanken": "Eetkamerbanken",
    "barkrukken": "Barkrukken",
    "salontafels": "Salontafel",
    "salontafel": "Salontafel",
    "eettafels": "Eettafel",
    "eettafel": "Eettafel",
    "bijzettafels": "Bijzettafel",
    "bijzettafel": "Bijzettafel",
    "wandkasten": "Opbergkasten",
    "opbergkasten": "Opbergkasten",
    "poefjes": "Poefjes",
    "poefs": "Poefjes",
    "hockers": "Poefjes",
    "hocker": "Poefjes",
    "krukken": "Krukken",
    "bedtextiel": "Bedtextiel",
    "slaapkamers": "Slaapkamers",
    "boxsprings": "Boxsprings",
    "aanbiedingen": "Aanbiedingen",
    "bartafels": "Bartafels",
    "lamps": "Woonaccessoires",
    "coffee tables": "Salontafel",
    "table lamps": "Woonaccessoires",
    "floor lamps": "Woonaccessoires",
    "suspension lamps": "Woonaccessoires",
    "banken 2": "",
    "stoelen 2": "",
    "tafels 2": "",
    "3-zitsbank": "3-zitsbank",
    "2-zitsbank": "2-zitsbank",
    "4-zitsbank": "4-zitsbank",
    "karpetten": "Vloerkleden",
    "karpet": "Vloerkleden",
    "verlichting": "Verlichting",
    "vloerkleden": "Vloerkleden",
    "relaxfauteuils": "Fauteuils",
}

# /collection/{segment}/product — Artifort, Pode, similar sites
COLLECTION_SEGMENT: dict[str, tuple[str, str]] = {
    "armchairs": ("Stoelen", "Fauteuils"),
    "easy chairs": ("Stoelen", "Fauteuils"),
    "sofas": ("Banken", ""),
    "sofa": ("Banken", ""),
    "corner sofas": ("Banken", "Hoekbanken"),
    "modular sofas": ("Banken", ""),
    "chairs": ("Stoelen", "Stoelen"),
    "tables": ("Tafels", ""),
    "coffee tables": ("Tafels", "Salontafel"),
    "cabinets": ("Kasten", "Opbergkasten"),
    "ottomans": ("Banken", "Poefjes"),
    "poufs": ("Banken", "Poefjes"),
    "lighting": ("Overig", "Verlichting"),
    "accessories": ("Overig", "Woonaccessoires"),
}


def _map_collection_segment(category: str) -> tuple[str, str] | None:
    seg = _norm(category.replace("-", " "))
    if seg in COLLECTION_SEGMENT:
        return COLLECTION_SEGMENT[seg]
    return None


# /collectie/{segment}/product — Label, Leolux, Dutch sites
COLLECTIE_SEGMENT: dict[str, tuple[str, str]] = {
    "banken": ("Banken", ""),
    "barstoelen": ("Stoelen", "Barkrukken"),
    "bijzettafel": ("Tafels", "Bijzettafel"),
    "bijzettafels": ("Tafels", "Bijzettafel"),
    "eetkamerbanken": ("Stoelen", "Eetkamerbanken"),
    "eetkamerstoelen": ("Stoelen", "Eetkamerstoelen"),
    "eetkamertafel": ("Tafels", "Eettafel"),
    "eetkamertafels": ("Tafels", "Eettafel"),
    "fauteuils": ("Stoelen", "Fauteuils"),
    "fautieuls": ("Stoelen", "Fauteuils"),
    "gautieuls": ("Stoelen", "Fauteuils"),
    "footstools": ("Stoelen", "Poefjes"),
    "voetenbank": ("Stoelen", "Poefjes"),
    "pouffes": ("Stoelen", "Poefjes"),
    "outdoor": ("Overig", ""),
    "salontafel": ("Tafels", "Salontafel"),
    "kussens": ("Overig", "Woonaccessoires"),
    "tassen": ("Overig", "Woonaccessoires"),
    "vloerkleden": ("Overig", "Vloerkleden"),
    "dressoir": ("Kasten", "Opbergkasten"),
    "bureau secretair": ("Kasten", "Opbergkasten"),
    "uncategorized": ("Overig", ""),
}


def _map_collectie_segment(category: str) -> tuple[str, str] | None:
    seg = _norm(category.replace("-", " "))
    if seg in COLLECTIE_SEGMENT:
        return COLLECTIE_SEGMENT[seg]
    return None


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _title(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    return n[0].upper() + n[1:]


def label_category_from_segment(segment: str) -> str:
    """Label.nl /collectie/{segment}/product — one category only, no sub."""
    raw = re.sub(r"\s+", " ", (segment or "").replace("-", " ").strip())
    if not raw:
        return ""
    key = _norm(raw)
    display = {
        "barstoelen": "Barstoelen",
        "bijzettafel": "Bijzettafels",
        "bijzettafels": "Bijzettafels",
        "eetkamerbanken": "Eetkamerbanken",
        "eetkamerstoelen": "Eetkamerstoelen",
        "eetkamertafel": "Eetkamertafels",
        "eetkamertafels": "Eetkamertafels",
        "fauteuils": "Fauteuils",
        "fautieuls": "Fauteuils",
        "gautieuls": "Fauteuils",
        "voetenbank": "Voetenbank",
        "footstools": "Voetenbank",
        "pouffes": "Pouffes",
        "salontafel": "Salontafels",
        "tassen": "Tassen",
        "kussens": "Kussens",
        "vloerkleden": "Vloerkleden",
        "dressoir": "Dressoir",
        "outdoor": "Outdoor",
        "bureau secretair": "Bureau & Secretair",
    }
    return display.get(key, _title(raw))


def _slug_blob(url: str, product_name: str) -> str:
    path = urlparse(url).path.lower()
    return f"{path} {product_name.lower()}"


def _infer_from_slug(url: str, product_name: str) -> tuple[str, str]:
    blob = _slug_blob(url, product_name)
    rules: list[tuple[tuple[str, ...], tuple[str, str]]] = [
        (("fauteuil", "armchair", "easy-chair"), ("Stoelen", "Fauteuils")),
        (("bijzet",), ("Tafels", "Bijzettafel")),
        (("salon", "salontafel"), ("Tafels", "Salontafel")),
        (("eettafel", "eet-tafel", "lenny-eettafel"), ("Tafels", "Eettafel")),
        (("hoekbank",), ("Banken", "Hoekbanken")),
        (("2-zits", "2.5-zits", "2-5-zits"), ("Banken", "2-zitsbank")),
        (("3-zits", "3.5-zits"), ("Banken", "3-zitsbank")),
        (("4-zits",), ("Banken", "4-zitsbank")),
        (("bank", "sofa", "zit"), ("Banken", "")),
        (("stoel", "chair"), ("Stoelen", "Eetkamerstoelen")),
        (("barstool", "barkruk"), ("Stoelen", "Barkrukken")),
        (("kruk", "hocker"), ("Stoelen", "Krukken")),
        (("poef", "pouf", "ottoman"), ("Stoelen", "Poefjes")),
        (("kast", "apparatuur", "plank", "kantoor", "lvkantoor", "rpkantoor"), ("Kasten", "Opbergkasten")),
        (
            (
                "tafel",
                "table",
                "disk",
                "foliant",
                "lucia",
                "polaris",
                "groove",
                "line",
                "rptafel",
                "circlips",
                "effect",
                "reflexion",
                "rhombic",
                "rondo",
                "solo",
                "sticks",
                "tapa",
            ),
            ("Tafels", ""),
        ),
        (("kussen", "accessoire", "spiegel", "kapstok"), ("Overig", "Woonaccessoires")),
    ]
    for keys, pair in rules:
        if any(k in blob for k in keys):
            return pair
    return "Overig", ""


def _bert_from_listing_path(path: str) -> tuple[str, str]:
    parts = [p for p in urlparse(path).path.split("/") if p]
    if "collecties" not in parts:
        return "", ""
    idx = parts.index("collecties")
    rest = parts[idx + 1 :]
    if len(rest) >= 2:
        sub = _title(rest[1].replace("-", " "))
        if _norm(sub).endswith(" 2") and _norm(rest[0]) in sub.lower():
            sub = ""
        return _title(rest[0]), sub
    if len(rest) == 1:
        return _title(rest[0]), ""
    return "", ""


def _apply_aliases(category: str, sub_category: str) -> tuple[str, str]:
    cat = category.strip()
    sub = sub_category.strip()

    if _norm(cat) in CATEGORY_ALIASES:
        mapped = CATEGORY_ALIASES[_norm(cat)]
        if mapped:
            cat = mapped

    if _norm(sub) in SUB_ALIASES:
        sub = SUB_ALIASES[_norm(sub)]

    if _norm(cat) in IGNORE_TOP:
        cat = ""

    if cat and sub and _norm(cat) == _norm(sub):
        sub = ""

    # Breadcrumb sub matches a top-level category (e.g. Collectie » Banken)
    if sub and not cat and _norm(sub) in CATEGORY_ALIASES:
        cat = CATEGORY_ALIASES[_norm(sub)]
        sub = ""

    # Top-level names should not be subcategories — except rows that exist in 807
    ALSO_VALID_SUB = frozenset({"stoelen", "banken", "tafels", "kasten"})
    if (
        sub
        and _norm(sub) in CATEGORY_ALIASES
        and _norm(sub) != "overig"
        and _norm(sub) not in ALSO_VALID_SUB
    ):
        cat = CATEGORY_ALIASES[_norm(sub)]
        sub = ""

    return cat, sub


SLEEP_SHOP_CATEGORIES = frozenset(
    {
        "bedtextiel",
        "slaapkamers",
        "boxsprings",
        "aanbiedingen",
        "matrassen",
        "bedden",
        "slapen",
    }
)


def capture_source_categories(product: ScrapedProduct) -> None:
    """Store raw website labels before normalization (for source_* fields)."""
    if not product.source_product_category:
        product.source_product_category = product.product_category
    if not product.source_product_subcategory:
        product.source_product_subcategory = product.sub_category


# Breadcrumb segment that is a Baserow top-level category (806), not a sub (807)
TOP_LEVEL_LABELS = frozenset({"banken", "stoelen", "tafels", "kasten", "overig"})


def _from_collectie_breadcrumb(src_cat: str, src_sub: str) -> tuple[str, str] | None:
    """
    Collectie » Stoelen » Product  →  Stoelen / (empty sub)
    Collectie » Accessories » …   →  Overig / Woonaccessoires
    """
    if _norm(src_cat) not in IGNORE_TOP or not src_sub:
        return None
    sub_key = _norm(src_sub)
    if sub_key in TOP_LEVEL_LABELS:
        return CATEGORY_ALIASES.get(sub_key, _title(src_sub)), ""
    mapped_sub = SUB_ALIASES.get(sub_key, "")
    mapped_cat = CATEGORY_ALIASES.get(sub_key, "")
    if mapped_sub:
        return mapped_cat or "Overig", mapped_sub
    if mapped_cat:
        return mapped_cat, ""
    return None


def normalize_product_categories(product: ScrapedProduct, site_url: str = "") -> None:
    """Rewrite product_category / sub_category only (not source_*)."""
    url = product.product_url or site_url
    host = urlparse(url).netloc.lower().replace("www.", "")
    cat = product.product_category
    sub = product.sub_category

    if host == "tonone.com":
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("gealux.nl"):
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("carpetrebel.com"):
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("houseofdutchz.nl"):
        # Feed already mapped Turnover_Group / Uitvoering → Baserow cat/sub
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("estiluz.com"):
        # Keep page breadcrumb labels only — no static alias / invent mapping.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("harvink.nl"):
        # Category comes from collection listing membership only.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("montis.nl"):
        # Breadcrumb / URL category only — no static alias mapping.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("pastoe.com"):
        # Category from JSON full_label only — no static alias mapping.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("breesnewworld.nl"):
        # Breadcrumb category only — no static invent mapping.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("jori.com"):
        # Breadcrumb Collection → cat, product type → sub — no static invent.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("metaformmeubelen.nl"):
        # Listing-nav category only — no static invent mapping.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("brinker.nl") or host.endswith("brinkercarpets.nl"):
        # Collection listing category only — no static invent mapping.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("fontanaarte.com"):
        # Page title_label only — no static invent mapping.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("gazzda.com"):
        # Nested /products/{cat}/{sub} listing only — no static invent.
        product.product_category = (cat or "").strip()
        product.sub_category = (sub or "").strip()
        return

    if host.endswith("label.nl"):
        seg = (product.source_product_category or cat or "").strip()
        product.product_category = label_category_from_segment(seg) if seg else (cat or "").strip()
        product.sub_category = ""
        return

    breadcrumb = _from_collectie_breadcrumb(
        product.source_product_category or cat,
        product.source_product_subcategory or sub,
    )
    if breadcrumb and host.endswith("spectrumdesign.nl"):
        cat, sub = breadcrumb

    if sub and _norm(sub) in IGNORE_NAV_SUB:
        sub = ""
    if sub and _norm(sub) == _norm(product.product_name):
        sub = ""
    if cat and _norm(cat) == _norm(product.product_name):
        cat = ""

    # Map from raw URL/listing segment (source_*) first — avoids re-mapping
    # normalized Baserow names like "Banken" via COLLECTIE_SEGMENT (which clears sub).
    raw_seg = (product.source_product_category or "").strip()
    mapped = _map_collection_segment(raw_seg) if raw_seg else None
    if not mapped and raw_seg and _norm(raw_seg) not in TOP_LEVEL_LABELS:
        mapped = _map_collectie_segment(raw_seg)
    if not mapped and cat and _norm(cat) not in TOP_LEVEL_LABELS:
        mapped = _map_collection_segment(cat) or _map_collectie_segment(cat)
    if mapped:
        cat, sub = mapped

    if host == "designonstock.com" and _norm(cat) == "accessoires":
        cat, sub = "Overig", "Woonaccessoires"

    if host.endswith("sleepworldhelmond.nl"):
        if _norm(cat) == "home" and sub:
            cat = sub
            sub = ""
        if _norm(cat) in SLEEP_SHOP_CATEGORIES:
            sub = _title(cat)
            cat = "Overig"

    if host == "bertplantagie.com":
        listing_cat, listing_sub = cat, sub
        slug_cat, slug_sub = _infer_from_slug(url, product.product_name)
        if listing_cat and _norm(listing_cat) not in IGNORE_TOP and _norm(
            listing_cat
        ) != _norm(product.product_name):
            cat, sub = listing_cat, listing_sub
        elif slug_cat:
            cat, sub = slug_cat, slug_sub

    if host == "baenks.nl" and (not cat or _norm(cat) == "banken"):
        inf_cat, inf_sub = _infer_from_slug(url, product.product_name)
        if inf_cat:
            cat, sub = inf_cat, inf_sub or sub

    if _norm(cat) in IGNORE_TOP or (not cat and sub):
        inferred_cat, inferred_sub = _infer_from_slug(url, product.product_name)
        if inferred_cat:
            cat = inferred_cat
        if inferred_sub and not sub:
            sub = inferred_sub
        elif _norm(cat) in IGNORE_TOP and sub and _norm(sub) == _norm(product.product_name):
            cat, inferred_sub = _infer_from_slug(url, product.product_name)
            sub = inferred_sub or ""

    cat, sub = _apply_aliases(cat, sub)

    # URL-based sub only when website breadcrumb has no finer sub (e.g. 3-zitsbank from slug)
    if cat and not sub and host == "baenks.nl":
        _c, inf_sub = _infer_from_slug(url, product.product_name)
        if inf_sub:
            sub = inf_sub

    cat, sub = _apply_aliases(cat, sub)
    product.product_category = cat
    product.sub_category = sub


def categories_from_listing_url(listing_url: str) -> tuple[str, str]:
    """Derive category pair from a WooCommerce / collection listing page URL."""
    path = urlparse(listing_url).path
    if "/collections/collecties/" in path:
        return _bert_from_listing_path(listing_url)

    low = path.lower()
    if "/product-categorie/" in low or "/product-category/" in low:
        parts = [p for p in path.split("/") if p]
        markers = ("product-categorie", "product-category")
        for marker in markers:
            if marker in parts:
                idx = parts.index(marker)
                rest = parts[idx + 1 :]
                if len(rest) >= 2:
                    return _title(rest[0]), _title(rest[1].replace("-", " "))
                if len(rest) == 1:
                    return _title(rest[0]), ""
    return "", ""
