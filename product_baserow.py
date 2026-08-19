"""
Create or update scraped products in Baserow productsDetails table (742).
"""
from __future__ import annotations

import re
from typing import Any

from urllib.parse import urlparse

from baserow_client import BaserowClient
from baserow_images import BaserowImageUploader, build_image_fields
from brand_scraper import extract_brand_name, row_field
from config import Settings
from description_ai import apply_ai_descriptions_if_enabled
from product_schema import ScrapedProduct
from scrapers.taxonomy import (
    ALLOW_AUTO_CREATE_SUB,
    CATEGORY_ALIASES,
    IGNORE_TOP,
    SUB_ALIASES,
    capture_source_categories,
    normalize_product_categories,
)


def _norm_url(url: str) -> str:
    """Canonical product URL for dedup (no query/fragment, lowercase path)."""
    u = (url or "").strip()
    if not u:
        return ""
    p = urlparse(u)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/").lower()
    if not host:
        return path
    return f"{p.scheme}://{host}{path}"


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _auto_create_subcategories(product: ScrapedProduct) -> bool:
    """Brand breadcrumb taxonomy — create missing rows in 807 when saving."""
    host = urlparse(product.product_url or "").netloc.lower().replace("www.", "")
    return host in {
        "tonone.com",
        "gealux.nl",
        "label.nl",
        "carpetrebel.com",
        "csrugs.com",
    }


def _map_category_name(name: str) -> str:
    key = _norm_name(name)
    if not key or key in IGNORE_TOP:
        return ""
    return CATEGORY_ALIASES.get(key, (name or "").strip())


def _map_sub_name(name: str) -> str:
    key = _norm_name(name)
    if not key:
        return ""
    return SUB_ALIASES.get(key, (name or "").strip())


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

    def ensure_category(self, name: str, *, preserve_label: bool = False) -> int:
        self._load()
        if preserve_label:
            display = (name or "").strip() or "Overig"
        else:
            display = _map_category_name(name) or (name or "").strip() or "Overig"
        key = _norm_name(display)
        if key in self._categories:
            cat_id = self._categories[key]
            if preserve_label and display:
                self._client.update_row(
                    self._settings.category_table_id,
                    cat_id,
                    {self._settings.field_category_name: display},
                )
            return cat_id

        row = self._client.create_row(
            self._settings.category_table_id,
            {self._settings.field_category_name: display},
        )
        cat_id = row["id"]
        self._categories[key] = cat_id
        return cat_id

    def ensure_subcategory(
        self,
        name: str,
        parent_cat_id: int | None,
        *,
        allow_create: bool = False,
        preserve_label: bool = False,
    ) -> int | None:
        self._load()
        if preserve_label:
            display = (name or "").strip()
        else:
            display = _map_sub_name(name) or (name or "").strip()
        if not display:
            return None
        key = _norm_name(display)
        if key in self._subcategories:
            return self._subcategories[key]
        if not allow_create and key not in ALLOW_AUTO_CREATE_SUB:
            return None

        fields: dict[str, Any] = {self._settings.field_subcategory_name: display}
        if parent_cat_id:
            fields[self._settings.field_subcategory_parent] = [parent_cat_id]

        row = self._client.create_row(self._settings.subcategory_table_id, fields)
        sub_id = row["id"]
        self._subcategories[key] = sub_id
        if parent_cat_id:
            self._sub_parents[sub_id] = parent_cat_id
        return sub_id

    def resolve(
        self,
        category: str,
        sub_category: str,
        *,
        create: bool = True,
        auto_create_sub: bool = False,
    ) -> tuple[list[int], list[int]]:
        """Resolve names to row IDs; optionally create missing rows in 806/807."""
        self._load()
        preserve = auto_create_sub
        if preserve:
            cat_name = (category or "").strip()
            sub_name = (sub_category or "").strip()
        else:
            cat_name = _map_category_name(category)
            sub_name = _map_sub_name(sub_category)

        cat_ids: list[int] = []
        sub_ids: list[int] = []

        if sub_name:
            sub_key = _norm_name(sub_name)
            if sub_key in self._subcategories:
                sub_ids = [self._subcategories[sub_key]]
                parent = self._sub_parents.get(sub_ids[0])
                if parent:
                    cat_ids = [parent]
            elif create:
                parent_id = self.ensure_category(
                    cat_name or "Products",
                    preserve_label=preserve,
                )
                sub_id = self.ensure_subcategory(
                    sub_name,
                    parent_id,
                    allow_create=auto_create_sub,
                    preserve_label=preserve,
                )
                if sub_id:
                    sub_ids = [sub_id]
                    cat_ids = [parent_id]
                elif cat_name:
                    cat_ids = [self.ensure_category(cat_name)]

        if not cat_ids and cat_name:
            if create:
                cat_ids = [
                    self.ensure_category(cat_name, preserve_label=preserve)
                ]
            else:
                cat_key = _norm_name(cat_name)
                if cat_key in self._categories:
                    cat_ids = [self._categories[cat_key]]

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
    set_field("field_ai_description_nl", product.ai_description_translated_NL)
    set_field("field_product_url", _norm_url(product.product_url))
    set_field("field_product_status", product.Status)
    set_field("field_designer", product.designer)
    set_field("field_designer_description", product.designerDescription)
    if image_uploader and s.field_designer_image:
        designer_local = getattr(product, "local_designer_image_file", "") or ""
        designer_url = getattr(product, "designerImage", "") or ""
        uploaded_designer: list[dict[str, str]] = []
        if designer_local:
            name = image_uploader.upload_local(designer_local)
            if name:
                uploaded_designer = [{"name": name}]
        elif designer_url:
            uploaded_designer = image_uploader.upload_many([designer_url])
        if uploaded_designer:
            fields[s.field_designer_image] = uploaded_designer
    if s.field_source_category:
        fields[s.field_source_category] = product.source_product_category or ""
    if s.field_source_subcategory:
        fields[s.field_source_subcategory] = product.source_product_subcategory or ""
    set_field("field_price", product.price)

    if brand_row_id and s.field_brand_link:
        fields[s.field_brand_link] = [brand_row_id]

    cat_ids, sub_ids = category_lookup.resolve(
        product.product_category,
        product.sub_category,
        auto_create_sub=_auto_create_subcategories(product),
    )
    if s.field_product_category:
        fields[s.field_product_category] = cat_ids if cat_ids else []
    if s.field_sub_category:
        fields[s.field_sub_category] = sub_ids if sub_ids else []

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

    capture_source_categories(product)
    normalize_product_categories(product)
    apply_ai_descriptions_if_enabled(product, settings)

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
            f"(columns: {sorted(names)}). Set PRODUCTS_TABLE_ID=742 in .env. "
            f"Table 746 is productCategory (Name/subCategory only)."
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
            cat_ids, sub_ids = lookup.resolve(
                product.product_category,
                product.sub_category,
                auto_create_sub=_auto_create_subcategories(product),
            )
            results.append(
                {
                    "product_name": name,
                    "product_url": product.product_url,
                    "ok": True,
                    "action": action,
                    "row_id": row_id,
                    "images_uploaded": images_uploaded,
                    "category_ids": cat_ids,
                    "subcategory_ids": sub_ids,
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


def update_product_categories(
    client: BaserowClient,
    settings: Settings,
    products: list[ScrapedProduct],
    brand_name: str,
) -> list[dict[str, Any]]:
    """Update only category link fields (+ source_*) for existing product rows."""
    validate_products_table(client, settings)
    brand_row_id = find_brand_row_id(client, settings, brand_name)
    if brand_row_id is None:
        raise ValueError(f"Brand not found in Baserow: {brand_name!r}")

    lookup = CategoryLookup(client, settings)
    results: list[dict[str, Any]] = []

    for product in products:
        name = product.product_name or product.product_url
        try:
            if not product.scrape_ok:
                raise ValueError(product.scrape_error or "category fetch failed")

            capture_source_categories(product)
            normalize_product_categories(product)

            existing = find_product_row_by_url(client, settings, product.product_url)
            if not existing:
                raise ValueError("product row not found in Baserow (scrape product first)")

            cat_ids, sub_ids = lookup.resolve(
                product.product_category,
                product.sub_category,
                auto_create_sub=_auto_create_subcategories(product),
            )
            fields: dict[str, Any] = {}
            if settings.field_product_category:
                fields[settings.field_product_category] = cat_ids if cat_ids else []
            if settings.field_sub_category:
                fields[settings.field_sub_category] = sub_ids if sub_ids else []
            if settings.field_source_category:
                fields[settings.field_source_category] = (
                    product.source_product_category or ""
                )
            if settings.field_source_subcategory:
                fields[settings.field_source_subcategory] = (
                    product.source_product_subcategory or ""
                )

            row_id = existing["id"]
            client.update_row(settings.products_table_id, row_id, fields)
            results.append(
                {
                    "product_name": name,
                    "product_url": product.product_url,
                    "ok": True,
                    "action": "updated",
                    "row_id": row_id,
                    "product_category": product.product_category,
                    "sub_category": product.sub_category,
                    "category_ids": cat_ids,
                    "subcategory_ids": sub_ids,
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
