"""Send a single Baserow product row to Shopify (Woonbloq).

Supports both CREATE (new product) and UPDATE (existing product).

Usage:
  python send_to_shopify.py --table 742 --row-id 2 --dry-run
  python send_to_shopify.py --table 742 --row-id 2 --apply
  python send_to_shopify.py --table 802 --row-id 8009 --apply
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COMPRESS_THRESHOLD = 900_000
SHOPIFY_IMAGE_MAX = 20 * 1024 * 1024
MAX_SINGLE_DOWNLOAD = 2 * 1024 * 1024
MAX_IMAGES = 8
PRODUCT_BUDGET = 8 * 1024 * 1024
THUMB_EST = 200 * 1024


# ---------------------------------------------------------------------------
# Field mapping per table
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TableFieldMap:
    """Maps generic field names to the table-specific field_XXXX keys."""
    table_id: int
    product_name: str
    product_description: str
    ai_description_nl: str
    accordion_product_description: str
    product_images: str
    bg_removed_hero: str
    hero_images: str
    lifestyle_images: str
    detail_image: str
    designer: str
    designer_image: str
    product_category: str       # link_row
    sub_category: str           # link_row
    brand_table: str            # link_row
    price: str
    score: str
    stores: str                 # link_row
    woonbloq_product_id: str
    woonbloq_status: str
    shopify_stores: str         # link_row (table 742 only, field_8509)


TABLE_742 = TableFieldMap(
    table_id=742,
    product_name="field_7347",
    product_description="field_7348",
    ai_description_nl="field_7362",
    accordion_product_description="field_7410",
    product_images="field_7349",
    bg_removed_hero="field_7400",
    hero_images="field_7358",
    lifestyle_images="field_7359",
    detail_image="field_7360",
    designer="field_7356",
    designer_image="field_7355",
    product_category="field_7363",
    sub_category="field_7364",
    brand_table="field_7376",
    price="field_7371",
    score="field_7394",
    stores="field_7375",
    woonbloq_product_id="field_7425",
    woonbloq_status="field_7427",
    shopify_stores="field_8509",
)

TABLE_802 = TableFieldMap(
    table_id=802,
    product_name="field_8224",
    product_description="field_8225",
    ai_description_nl="field_8239",
    accordion_product_description="field_8287",
    product_images="field_8226",
    bg_removed_hero="field_8277",
    hero_images="field_8235",
    lifestyle_images="field_8236",
    detail_image="field_8237",
    designer="field_8233",
    designer_image="field_8232",
    product_category="field_8240",
    sub_category="field_8241",
    brand_table="field_8253",
    price="field_8248",
    score="field_8271",
    stores="field_8252",
    woonbloq_product_id="field_8302",
    woonbloq_status="field_8304",
    shopify_stores="",          # table 802 doesn't have this extra field
)

TABLE_MAP: dict[int, TableFieldMap] = {
    742: TABLE_742,
    802: TABLE_802,
}

METAFIELD_NS = "custom"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _link_value(row: dict, field_key: str) -> str:
    """Extract first link_row value text."""
    links = row.get(field_key) or []
    if isinstance(links, list) and links:
        return str(links[0].get("value") or "").strip()
    return ""


def _unique_file_images(images: Any) -> list[dict]:
    if not isinstance(images, list):
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for img in images:
        if not isinstance(img, dict):
            continue
        url = str(img.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(img)
    return out


def _thumb_url(img: dict) -> str:
    thumbs = img.get("thumbnails") or {}
    for key in ("card_cover", "small"):
        url = str((thumbs.get(key) or {}).get("url") or "").strip()
        if url:
            return url
    return ""


def _pick_download_url(img: dict) -> str | None:
    full_url = str(img.get("url") or "").strip()
    size = int(img.get("size") or 0)
    thumb = _thumb_url(img)
    if size > MAX_SINGLE_DOWNLOAD and thumb:
        return thumb
    if full_url:
        return full_url
    return thumb or None


def _ext_for_image(img: dict) -> str:
    mime = str(img.get("mime_type") or "image/jpeg").lower()
    if "png" in mime:
        return "png"
    if "webp" in mime:
        return "webp"
    return "jpg"


def _compress_jpeg(data: bytes, *, quality: int, max_dim: int) -> bytes:
    im = Image.open(io.BytesIO(data))
    if im.mode in ("P", "RGBA", "LA"):
        im = im.convert("RGBA")
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _prepare_image_bytes(data: bytes) -> bytes:
    if len(data) <= COMPRESS_THRESHOLD:
        return data
    for quality, max_dim in ((80, 1600), (70, 1400), (60, 1200), (50, 1000)):
        data = _compress_jpeg(data, quality=quality, max_dim=max_dim)
        if len(data) <= SHOPIFY_IMAGE_MAX:
            return data
    return data


def _download_image(
    session: requests.Session, url: str, *, timeout: float
) -> bytes:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return _prepare_image_bytes(resp.content)


def _image_file_urls(images: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for img in _unique_file_images(images):
        url = _pick_download_url(img)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# Extract row data using field map
# ---------------------------------------------------------------------------
def _pick_description(row: dict, fm: TableFieldMap) -> str:
    """AI description first, then accordion, then raw description, then empty."""
    for key in (fm.ai_description_nl, fm.accordion_product_description, fm.product_description):
        text = str(row.get(key) or "").strip()
        if text:
            return text
    return ""


def _extract_product_data(row: dict, fm: TableFieldMap) -> dict[str, Any]:
    """Extract all relevant product fields from a Baserow row."""
    title = str(row.get(fm.product_name) or "").strip()
    description = _pick_description(row, fm)
    brand = _link_value(row, fm.brand_table)
    category = _link_value(row, fm.product_category)
    subcategory = _link_value(row, fm.sub_category)
    designer = str(row.get(fm.designer) or "").strip()
    price_raw = str(row.get(fm.price) or "0")
    # Handle range prices like "€2.968 - €5.000": take the first number
    price_match = re.search(r"[\d]+(?:[.,]\d+)*", price_raw.replace(".", "").replace(",", "."))
    price = price_match.group(0) if price_match else "0.00"
    score = row.get(fm.score)
    woonbloq_pid = str(row.get(fm.woonbloq_product_id) or "").strip()

    # Product type
    if category and subcategory:
        product_type = f"{category} / {subcategory}"
    else:
        product_type = category or subcategory or "Overig"

    # Image sources (priority: product_images -> bg_removed_hero -> hero_images)
    product_images = _unique_file_images(row.get(fm.product_images))
    bg_removed = _unique_file_images(row.get(fm.bg_removed_hero))
    hero_images = _unique_file_images(row.get(fm.hero_images))
    image_source = product_images or bg_removed or hero_images
    image_source_name = (
        "product_images" if product_images
        else "bg_removed_hero" if bg_removed
        else "hero_images" if hero_images
        else "none"
    )

    # Lifestyle images
    lifestyle_urls = _image_file_urls(row.get(fm.lifestyle_images))

    # Designer image URL
    designer_imgs = _unique_file_images(row.get(fm.designer_image))
    designer_image_url = ""
    if designer_imgs:
        designer_image_url = str(designer_imgs[0].get("url") or "").strip()

    # Completeness check
    has_image = len(image_source) > 0
    missing = [
        name
        for name, ok in (
            ("title", bool(title)),
            ("description", bool(description)),
            ("category", bool(category)),
            ("subcategory", bool(subcategory)),
            ("brand", bool(brand)),
            ("product_image", has_image),
        )
        if not ok
    ]
    shopify_status = "draft" if missing else "active"

    return {
        "title": title or "Untitled Product",
        "description": description,
        "brand": brand or "Unknown",
        "category": category,
        "subcategory": subcategory,
        "product_type": product_type,
        "price": price,
        "designer": designer,
        "designer_image_url": designer_image_url,
        "score": score,
        "shopify_status": shopify_status,
        "missing": missing,
        "image_source": image_source,
        "image_source_name": image_source_name,
        "lifestyle_urls": lifestyle_urls,
        "woonbloq_product_id": woonbloq_pid,
    }


# ---------------------------------------------------------------------------
# Build Shopify images (CREATE only)
# ---------------------------------------------------------------------------
def _build_shopify_images(
    images: list[dict],
    session: requests.Session,
    *,
    timeout: float,
) -> tuple[list[dict], list[dict]]:
    """Download and encode images for Shopify product create."""
    shopify_images: list[dict] = []
    skipped: list[dict] = []
    budget_left = PRODUCT_BUDGET

    for i, img in enumerate(images, 1):
        if len(shopify_images) >= MAX_IMAGES:
            skipped.append({"index": i, "reason": "max_8_images", "src": img.get("url")})
            continue

        download_url = _pick_download_url(img)
        if not download_url:
            skipped.append({"index": i, "reason": "no_url", "src": img.get("url")})
            continue

        try:
            data = _download_image(session, download_url, timeout=timeout)
        except Exception as exc:
            skipped.append({"index": i, "reason": f"download_failed: {exc}", "src": download_url})
            continue

        if len(data) > budget_left:
            skipped.append({"index": i, "reason": "product_budget_8mb", "src": download_url})
            continue

        ext = _ext_for_image(img)
        if ext != "jpg" and len(data) > COMPRESS_THRESHOLD:
            ext = "jpg"
        shopify_images.append(
            {
                "attachment": base64.b64encode(data).decode("ascii"),
                "filename": f"image-{len(shopify_images) + 1}.{ext}",
                "position": len(shopify_images) + 1,
            }
        )
        budget_left -= len(data)

    return shopify_images, skipped


# ---------------------------------------------------------------------------
# Build metafields list
# ---------------------------------------------------------------------------
def _build_metafields(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build metafield list for GraphQL upsert.

    Note: designer_image is NOT included here because it is stored as
    list.file_reference on Shopify (not url type). It is handled separately
    by the lifestyle/file-upload flow in the existing sync logic.
    """
    mfs: list[dict[str, Any]] = []

    # Score
    score = data.get("score")
    if score is not None and score != "":
        try:
            score_int = int(float(score))
            mfs.append({
                "namespace": METAFIELD_NS,
                "key": "score",
                "value": str(score_int),
                "type": "number_integer",
            })
        except (ValueError, TypeError):
            pass

    # Category
    if data.get("category"):
        mfs.append({
            "namespace": METAFIELD_NS,
            "key": "product_category",
            "value": data["category"],
            "type": "single_line_text_field",
        })

    # Subcategory
    if data.get("subcategory"):
        mfs.append({
            "namespace": METAFIELD_NS,
            "key": "sub_category",
            "value": data["subcategory"],
            "type": "single_line_text_field",
        })

    # Designer
    if data.get("designer"):
        mfs.append({
            "namespace": METAFIELD_NS,
            "key": "designer",
            "value": data["designer"],
            "type": "single_line_text_field",
        })

    return mfs


# ---------------------------------------------------------------------------
# CREATE flow
# ---------------------------------------------------------------------------
def _create_product(
    row: dict,
    fm: TableFieldMap,
    data: dict[str, Any],
    *,
    baserow: BaserowClient,
    shopify: ShopifyClient,
    session: requests.Session,
    settings: Any,
    timeout: float,
    dry_run: bool,
) -> None:
    print(f"\n{'='*60}")
    print(f"CREATE: {data['title']}")
    print(f"  Brand: {data['brand']}")
    print(f"  Category: {data['category']} / {data['subcategory']}")
    print(f"  Price: {data['price']}")
    print(f"  Score: {data['score']}")
    print(f"  Description: {data['description'][:80]}..." if data["description"] else "  Description: (empty)")
    print(f"  Status: {data['shopify_status']}")
    print(f"  Missing: {', '.join(data['missing']) if data['missing'] else 'none'}")
    print(f"  Image source: {data['image_source_name']} ({len(data['image_source'])} images)")
    print(f"  Lifestyle images: {len(data['lifestyle_urls'])}")

    if dry_run:
        print("\n  [DRY RUN] Would create product on Shopify.")
        return

    # Download and encode images
    print("  Downloading images...")
    images, skipped = _build_shopify_images(
        data["image_source"], session, timeout=timeout
    )
    if skipped:
        print(f"  Images skipped: {len(skipped)}")
        for s in skipped:
            print(f"    #{s['index']}: {s['reason']}")

    # Build product payload
    payload: dict[str, Any] = {
        "title": data["title"],
        "body_html": data["description"],
        "vendor": data["brand"],
        "product_type": data["product_type"],
        "tags": "" if not data["missing"] else ", ".join(data["missing"]),
        "status": data["shopify_status"],
        "variants": [{"price": data["price"]}],
    }
    if images:
        payload["images"] = images

    # Create on Shopify
    print("  Creating product on Shopify...")
    created = shopify.create_product(payload)
    pid = int(created["id"])
    gid = f"gid://shopify/Product/{pid}"
    print(f"  → Shopify product ID: {pid} ({data['shopify_status']})")

    # Set metafields via GraphQL
    metafields = _build_metafields(data)
    if metafields:
        mf_ok, mf_fail, mf_errors = shopify.set_metafields_graphql(gid, metafields)
        print(f"  → Metafields: {mf_ok} saved" + (f", {mf_fail} failed" if mf_fail else ""))
        for err in mf_errors:
            print(f"     ERROR: {err}")

    # Lifestyle images as file_reference metafield
    if data["lifestyle_urls"]:
        try:
            file_gids = shopify.create_image_files_from_urls(data["lifestyle_urls"])
            if file_gids:
                shopify.set_product_list_file_reference_metafield(
                    pid,
                    METAFIELD_NS,
                    "lifestyle_images",
                    file_gids,
                )
                print(f"  → Lifestyle images: {len(file_gids)} files uploaded")
            else:
                print("  → Lifestyle images: no files created")
        except RuntimeError as exc:
            print(f"  → Lifestyle images ERROR: {exc}")

    # Update Baserow with Shopify ID + status
    baserow.update_row(
        fm.table_id,
        int(row["id"]),
        {
            fm.woonbloq_product_id: gid,
            fm.woonbloq_status: "Added" if data["shopify_status"] == "active" else "Draft",
        },
    )
    print(f"  → Baserow updated: {fm.woonbloq_product_id} = {gid}")
    print(f"  ✓ Product created successfully!")


# ---------------------------------------------------------------------------
# UPDATE flow
# ---------------------------------------------------------------------------
def _extract_shopify_numeric_id(gid: str) -> int:
    """Extract numeric ID from gid://shopify/Product/12345."""
    match = re.search(r"(\d+)$", gid)
    if not match:
        raise ValueError(f"Cannot extract Shopify product ID from: {gid}")
    return int(match.group(1))


def _update_product(
    row: dict,
    fm: TableFieldMap,
    data: dict[str, Any],
    *,
    shopify: ShopifyClient,
    dry_run: bool,
) -> None:
    gid = data["woonbloq_product_id"]
    pid = _extract_shopify_numeric_id(gid)

    print(f"\n{'='*60}")
    print(f"UPDATE: {data['title']} (Shopify #{pid})")
    print(f"  Brand: {data['brand']}")
    print(f"  Category: {data['category']} / {data['subcategory']}")
    print(f"  Price: {data['price']}")
    print(f"  Score: {data['score']}")
    print(f"  Description: {data['description'][:80]}..." if data["description"] else "  Description: (empty)")
    print(f"  Images: NOT UPDATING (keeping existing)")

    if dry_run:
        print("\n  [DRY RUN] Would update product on Shopify.")
        metafields = _build_metafields(data)
        print(f"  Metafields to upsert: {len(metafields)}")
        for mf in metafields:
            print(f"    {mf['key']}: {str(mf['value'])[:60]}")
        return

    # Update product fields (no images)
    update_fields: dict[str, Any] = {
        "title": data["title"],
        "body_html": data["description"],
        "vendor": data["brand"],
        "product_type": data["product_type"],
    }
    # Only set price on variant if we have one
    price = data["price"]
    if price and price != "0.00":
        update_fields["variants"] = [{"price": price}]

    print("  Updating product on Shopify...")
    try:
        updated = shopify.update_product(pid, update_fields)
        print(f"  → Product updated: {updated.get('title', '?')}")
    except RuntimeError as exc:
        print(f"  → Product update FAILED: {exc}")
        return

    # Update metafields via GraphQL
    metafields = _build_metafields(data)
    if metafields:
        mf_ok, mf_fail, mf_errors = shopify.set_metafields_graphql(gid, metafields)
        print(f"  → Metafields: {mf_ok} saved" + (f", {mf_fail} failed" if mf_fail else ""))
        for err in mf_errors:
            print(f"     ERROR: {err}")
    else:
        print("  → No metafields to update")

    print(f"  ✓ Product updated successfully!")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a Baserow product row to Shopify (Woonbloq). "
                    "Creates new or updates existing products."
    )
    parser.add_argument(
        "--table", type=int, required=True, choices=[742, 802],
        help="Baserow table ID (742 or 802).",
    )
    parser.add_argument(
        "--row-id", type=int, required=True,
        help="Baserow row ID to sync.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually create/update on Shopify.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default).")
    parser.add_argument(
        "--timeout", type=float, default=120.0,
        help="HTTP timeout per image download (seconds, default 120).",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    fm = TABLE_MAP[args.table]
    settings = load_settings()
    baserow = BaserowClient(settings)
    shopify = ShopifyClient(load_shopify_config())
    session = requests.Session()
    session.headers.update({"Authorization": f"Token {settings.baserow_token}"})

    print(f"Baserow: {settings.baserow_url} table {fm.table_id}")
    print(f"Shopify: {shopify.config.shop_host}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Row ID: {args.row_id}")

    # Fetch the row
    print(f"\nFetching row {args.row_id} from table {fm.table_id}...")
    try:
        row = baserow.get_row(fm.table_id, args.row_id)
    except Exception as exc:
        print(f"ERROR: Could not fetch row: {exc}")
        return 1

    # Extract data
    data = _extract_product_data(row, fm)

    # Decide: CREATE or UPDATE
    if data["woonbloq_product_id"]:
        _update_product(row, fm, data, shopify=shopify, dry_run=dry_run)
    else:
        _create_product(
            row, fm, data,
            baserow=baserow,
            shopify=shopify,
            session=session,
            settings=settings,
            timeout=args.timeout,
            dry_run=dry_run,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
