"""
Create or update scraped products in Baserow productsDetails table (802).
"""
from __future__ import annotations

import re
from typing import Any

from baserow_client import BaserowClient
from baserow_images import BaserowImageUploader, build_image_fields
from brand_scraper import extract_brand_name, row_field
from config import Settings
from product_schema import ScrapedProduct

# Website breadcrumb labels → Baserow subcategory names (table 807)
SUB_CATEGORY_ALIASES: dict[str, str] = {
    "accessories": "Woonaccessoires",
    "accessoires": "Woonaccessoires",
    "accessory": "Woonaccessoires",
}

# Top-level breadcrumb segment — not a Baserow category row
IGNORE_CATEGORY_NAMES = frozenset({"collectie", "collection", "catalogus", "catalog"})


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


class CategoryLookup:
    """Maps scraped category names to link_row IDs (tables 806 / 807)."""

    def __init__(self, client: BaserowClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._categories: dict[str, int] = {}
        self._subcategories: dict[str, int] = {}
        self._sub_parents: dict[int, int] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        s = self._settings
        for row in self._client.list_table_rows(s.category_table_id):
            name = row.get(s.field_category_name)
            if isinstance(name, str) and name.strip():
                self._categories[_norm_name(name)] = row["id"]

        for row in self._client.list_table_rows(s.subcategory_table_id):
            name = row.get(s.field_subcategory_name)
            if not isinstance(name, str) or not name.strip():
                continue
            sub_id = row["id"]
            self._subcategories[_norm_name(name)] = sub_id
            parents = row.get(s.field_subcategory_parent) or []
            if parents and isinstance(parents, list):
                self._sub_parents[sub_id] = parents[0]["id"]

        self._loaded = True

    def resolve(self, category: str, sub_category: str) -> tuple[list[int], list[int]]:
        self._load()
        cat_ids: list[int] = []
        sub_ids: list[int] = []

        sub_key = SUB_CATEGORY_ALIASES.get(
            _norm_name(sub_category), _norm_name(sub_category)
        )
        if sub_key and sub_key in self._subcategories:
            sub_id = self._subcategories[sub_key]
            sub_ids = [sub_id]
            parent = self._sub_parents.get(sub_id)
            if parent:
                cat_ids = [parent]

        if not cat_ids:
            for candidate in (sub_category, category):
                key = _norm_name(candidate)
                if not key or key in IGNORE_CATEGORY_NAMES:
                    continue
                if key in self._categories:
                    cat_ids = [self._categories[key]]
                    break

        return cat_ids, sub_ids


def find_brand_row_id(
    client: BaserowClient, settings: Settings, brand_name: str
) -> int | None:
    target = _norm_name(brand_name)
    for row in client.list_table_rows(settings.brands_table_id):
        name = extract_brand_name(row, settings.field_brand_name)
        if _norm_name(name) == target:
            return row["id"]
    return None


def find_product_row_by_url(
    client: BaserowClient, settings: Settings, product_url: str
) -> dict[str, Any] | None:
    field = settings.field_product_url
    url = _norm_url(product_url)
    if not url:
        return None

    for variant in (url, url + "/"):
        rows = client.list_table_rows(
            settings.products_table_id,
            filters={f"filter__{field}__equal": variant},
            size=5,
        )
        for row in rows:
            stored = row.get(field)
            if _norm_url(str(stored or "")) == url:
                return row
    return None


def scraped_product_to_fields(
    product: ScrapedProduct,
    settings: Settings,
    *,
    brand_row_id: int | None,
    category_lookup: CategoryLookup,
    image_uploader: BaserowImageUploader | None = None,
) -> dict[str, Any]:
    s = settings
    fields: dict[str, Any] = {}

    def set_field(key: str, value: Any) -> None:
        attr = getattr(s, key, "")
        if attr and value is not None and value != "":
            fields[attr] = value

    set_field("field_product_name", product.product_name)
    set_field("field_product_description", product.product_description)
    set_field("field_product_url", _norm_url(product.product_url))
    set_field("field_product_status", product.Status)
    set_field("field_designer", product.designer)
    set_field("field_designer_description", product.designerDescription)
    set_field("field_source_category", product.source_product_category)
    set_field("field_source_subcategory", product.source_product_subcategory)
    set_field("field_price", product.price)

    if brand_row_id and s.field_brand_link:
        fields[s.field_brand_link] = [brand_row_id]

    cat_ids, sub_ids = category_lookup.resolve(
        product.product_category, product.sub_category
    )
    if cat_ids and s.field_product_category:
        fields[s.field_product_category] = cat_ids
    if sub_ids and s.field_sub_category:
        fields[s.field_sub_category] = sub_ids

    if image_uploader:
        fields.update(build_image_fields(product, settings, image_uploader))

    return fields


def link_product_to_brand(
    client: BaserowClient,
    settings: Settings,
    brand_row_id: int,
    product_row_id: int,
) -> None:
    field = settings.field_products
    if not field:
        return

    row = client.get_row(settings.brands_table_id, brand_row_id)
    current = row.get(field) or []
    existing_ids = []
    for item in current:
        if isinstance(item, dict) and "id" in item:
            existing_ids.append(item["id"])
        elif isinstance(item, int):
            existing_ids.append(item)

    if product_row_id not in existing_ids:
        existing_ids.append(product_row_id)
        client.update_row(
            settings.brands_table_id,
            brand_row_id,
            {field: existing_ids},
        )


def save_product(
    client: BaserowClient,
    settings: Settings,
    product: ScrapedProduct,
    *,
    brand_row_id: int | None,
    category_lookup: CategoryLookup,
    image_uploader: BaserowImageUploader | None = None,
) -> tuple[str, int, int]:
    """
    Create or update one product row. Returns ('created'|'updated', row_id).
    """
    if not product.scrape_ok:
        raise ValueError(product.scrape_error or "scrape failed")

    fields = scraped_product_to_fields(
        product,
        settings,
        brand_row_id=brand_row_id,
        category_lookup=category_lookup,
        image_uploader=image_uploader,
    )
    if not fields.get(settings.field_product_url):
        raise ValueError("product_url is required for save")

    existing = find_product_row_by_url(client, settings, product.product_url)
    table_id = settings.products_table_id

    if existing:
        row_id = existing["id"]
        client.update_row(table_id, row_id, fields)
        action = "updated"
    else:
        created = client.create_row(table_id, fields)
        row_id = created["id"]
        action = "created"

    if brand_row_id:
        link_product_to_brand(client, settings, brand_row_id, row_id)

    images_uploaded = len(fields.get(settings.field_product_images, []))
    return action, row_id, images_uploaded


def validate_products_table(client: BaserowClient, settings: Settings) -> None:
    """Ensure PRODUCTS_TABLE_ID is the productsDetails table (has product_name)."""
    fields = client.get_table_fields(settings.products_table_id)
    names = {f.get("name") for f in fields}
    if "product_name" not in names:
        raise ValueError(
            f"Table {settings.products_table_id} is not productsDetails "
            f"(columns: {sorted(names)}). Set PRODUCTS_TABLE_ID=802 in .env. "
            f"Table 806 is productCategory (Name/subCategory only)."
        )


def save_products(
    client: BaserowClient,
    settings: Settings,
    products: list[ScrapedProduct],
    brand_name: str,
) -> list[dict[str, Any]]:
    """Save all scraped products; returns per-product result dicts."""
    validate_products_table(client, settings)
    brand_row_id = find_brand_row_id(client, settings, brand_name)
    if brand_row_id is None:
        raise ValueError(f"Brand not found in Baserow: {brand_name!r}")

    lookup = CategoryLookup(client, settings)
    image_uploader = BaserowImageUploader(client, settings) if settings.upload_product_images else None
    results: list[dict[str, Any]] = []

    for product in products:
        name = product.product_name or product.product_url
        try:
            action, row_id, images_uploaded = save_product(
                client,
                settings,
                product,
                brand_row_id=brand_row_id,
                category_lookup=lookup,
                image_uploader=image_uploader,
            )
            results.append(
                {
                    "product_name": name,
                    "product_url": product.product_url,
                    "ok": True,
                    "action": action,
                    "row_id": row_id,
                    "images_uploaded": images_uploaded,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "product_name": name,
                    "product_url": product.product_url,
                    "ok": False,
                    "error": str(exc),
                }
            )

    return results
