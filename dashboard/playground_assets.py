"""Collect Baserow + Shopify image assets for the fal.ai evaluation playground."""
from __future__ import annotations

from typing import Any

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient


def _row_get(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if not key:
            continue
        val = row.get(key)
        if val not in (None, "", [], {}):
            return val
    return None


def _resolve_image_url(img: dict[str, Any]) -> str:
    """Extract best usable URL from a Baserow file field object."""
    url = str(img.get("url") or "").strip()
    if url:
        return url
    thumbs = img.get("thumbnails") or {}
    for key in ("card_cover", "small", "tiny", "thumbnail", "file_preview"):
        thumb = thumbs.get(key) or {}
        turl = str(thumb.get("url") or "").strip()
        if turl:
            return turl
    return ""


def _file_images(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        url = _resolve_image_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({**item, "url": url})
    return out


def _single_image(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return _file_images(value)
    if isinstance(value, dict):
        url = _resolve_image_url(value)
        return [{**value, "url": url}] if url else []
    if isinstance(value, str) and value.strip().startswith("http"):
        return [{"url": value.strip(), "name": "detail"}]
    return []


def _image_field_map() -> list[tuple[str, str, str]]:
    """Return (asset_type, label_prefix, *row_keys) tuples from settings + aliases."""
    s = load_settings()
    return [
        ("hero", "Hero", s.field_hero_images, "hero_images", "field_7358"),
        ("product", "Product", s.field_product_images, "product_images", "field_7349"),
        ("hero", "BG Removed", "bg_removed_hero", "field_7400"),
        ("lifestyle", "Lifestyle", s.field_lifestyle_images, "lifestyle_images", "field_7359"),
        ("detail", "Detail", s.field_detail_image, "detail_image", "field_7360"),
    ]


def _brand_name(row: dict[str, Any]) -> str:
    links = _row_get(row, "Brand_table", "brands", "field_7376") or []
    if links and isinstance(links, list) and isinstance(links[0], dict):
        return str(links[0].get("value") or links[0].get("name") or "—").strip()
    return "—"


def _shopify_id(row: dict[str, Any]) -> str:
    raw = _row_get(row, "WoonbloqProductID", "field_7425", "woonbloq_product_id") or ""
    return str(raw).strip()


def _product_title(row: dict[str, Any]) -> str:
    return str(
        _row_get(row, "product_name", "Name", "field_7347") or f"Product #{row.get('id', '?')}"
    ).strip()


def collect_baserow_assets(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize all Baserow media fields into playground asset records."""
    row_id = row.get("id")
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    asset_idx = 0

    for asset_type, label_prefix, *field_keys in _image_field_map():
        value = _row_get(row, *field_keys)
        if asset_type == "detail":
            images = _single_image(value)
        else:
            images = _file_images(value)

        for img in images:
            url = str(img.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            name = str(img.get("name") or f"{label_prefix} {asset_idx + 1}").strip()
            assets.append(
                {
                    "id": f"baserow-{asset_type}-{asset_idx}",
                    "url": url,
                    "source": "baserow",
                    "type": asset_type,
                    "label": name,
                    "row_id": row_id,
                }
            )
            asset_idx += 1

    return assets


def collect_shopify_assets(shopify_id: str, shopify: ShopifyClient | None = None) -> list[dict[str, Any]]:
    if not shopify_id or not str(shopify_id).isdigit():
        return []
    client = shopify or ShopifyClient()
    try:
        product = client.get_product(int(shopify_id))
    except Exception:
        return []

    assets: list[dict[str, Any]] = []
    for img in product.get("images") or []:
        url = str(img.get("src") or "").strip()
        if not url:
            continue
        assets.append(
            {
                "id": f"shopify-{img.get('id', len(assets))}",
                "url": url,
                "source": "shopify",
                "type": "shopify",
                "label": str(img.get("alt") or f"Shopify #{img.get('position', len(assets) + 1)}"),
                "shopify_image_id": img.get("id"),
                "position": img.get("position"),
            }
        )
    return assets


def merge_product_assets(row: dict[str, Any], include_shopify: bool = True) -> dict[str, Any]:
    """Build full asset payload for one catalog row."""
    shopify_id = _shopify_id(row)
    baserow_assets = collect_baserow_assets(row)
    shopify_assets: list[dict[str, Any]] = []
    if include_shopify and shopify_id:
        shopify_assets = collect_shopify_assets(shopify_id)

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset in baserow_assets + shopify_assets:
        url = asset["url"]
        if url in seen:
            continue
        seen.add(url)
        merged.append(asset)

    thumb = merged[0]["url"] if merged else ""
    shopify_admin_url = None
    if shopify_id:
        try:
            cfg = load_shopify_config()
            shopify_admin_url = f"https://admin.shopify.com/store/{cfg.store_slug}/products/{shopify_id}"
        except Exception:
            shopify_admin_url = None

    return {
        "row_id": row.get("id"),
        "title": _product_title(row),
        "brand": _brand_name(row),
        "shopify_id": shopify_id or None,
        "shopify_admin_url": shopify_admin_url,
        "thumb_url": thumb,
        "image_count": len(merged),
        "assets": merged,
        "prompt_detail": _detail_prompt_hint(row),
    }


def _detail_prompt_hint(row: dict[str, Any]) -> str:
    title = _product_title(row)
    brand = _brand_name(row)
    designer = str(_row_get(row, "Designer", "designer", "field_7356") or "").strip()
    bits = [f"Extreme macro close-up of {title}"]
    if brand and brand != "—":
        bits.append(f"by {brand}")
    if designer:
        bits.append(f"design by {designer}")
    bits.append("material texture and craftsmanship detail, studio lighting, 8k")
    return ", ".join(bits)


def _fetch_row(client: BaserowClient, settings: Any, row_id: int) -> dict[str, Any]:
    """Fetch a product row with human-readable field names."""
    resp = client.session.get(
        f"{settings.api_base}/database/rows/table/{settings.products_table_id}/{row_id}/",
        params={"user_field_names": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def search_products_for_playground(
    search: str = "",
    filter_type: str = "all",
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    settings = load_settings()
    client = BaserowClient(settings)
    w_field = "field_7425"

    params: dict[str, Any] = {
        "page": max(1, page),
        "size": min(max(1, size), 50),
        "user_field_names": "true",
    }
    if search:
        params["search"] = search
    if filter_type == "linked":
        params[f"filter__{w_field}__not_empty"] = ""

    resp = client.session.get(
        f"{settings.api_base}/database/rows/table/{settings.products_table_id}/",
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []

    products: list[dict[str, Any]] = []
    for row in results:
        # Include Shopify images when counting — many linked products only have images in Shopify
        include_shopify = bool(_shopify_id(row))
        summary = merge_product_assets(row, include_shopify=include_shopify)

        if filter_type == "has_images" and summary["image_count"] == 0:
            continue
        if summary["image_count"] == 0 and filter_type == "linked":
            continue
        products.append(
            {
                "row_id": summary["row_id"],
                "title": summary["title"],
                "brand": summary["brand"],
                "shopify_id": summary["shopify_id"],
                "thumb_url": summary["thumb_url"],
                "image_count": summary["image_count"],
            }
        )

    return {
        "ok": True,
        "count": len(products),
        "page": page,
        "page_size": size,
        "results": products,
    }


def get_playground_product_assets(row_id: int, include_shopify: bool = True) -> dict[str, Any]:
    settings = load_settings()
    client = BaserowClient(settings)
    row = _fetch_row(client, settings, row_id)
    payload = merge_product_assets(row, include_shopify=include_shopify)
    payload["ok"] = True
    return payload
