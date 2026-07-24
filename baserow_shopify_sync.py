"""Sync Baserow products to Shopify (Woonbloq / Binnen).

Baserow → Shopify mapping:
  product_name                    -> title
  ai_description_translated_NL    -> body_html (fallback: accordion, then product_description)
  Brand_table                     -> vendor
  product_category                -> metafield product_category (+ product_type prefix)
  sub_category                    -> metafield sub_category (+ product_type suffix)
  price                           -> variants[0].price
  hero_images                     -> images (product gallery)
  lifestyle_images                -> metafield lifestyle_images (Shopify file references)
  designer                        -> metafield designer (if set)
  designerImage                   -> metafield designer_image URL (if set)

After create:
  WoonbloqProductID + WoonbloqStatus (default target)
  or BinnenProductID + BinnenStatus (--target binnen)

Draft when missing: title, description, category, subcategory, brand, or hero_images.
Shopify images: hero_images; if no hero, lifestyle_images go to product images (still draft).

With --pixelbin-bg (and --apply): after each product is created on Shopify, all its
images are processed via Pixelbin erase.bg and replaced in place (same image ids).

Examples:
  python baserow_shopify_sync.py --dry-run --brand-id 16
  python baserow_shopify_sync.py --apply --brand-id 16 --limit 3
  python baserow_shopify_sync.py --apply --brand-id 16
  python baserow_shopify_sync.py --apply --brand-id 16 --pixelbin-bg --limit 1
  python baserow_shopify_sync.py --apply --brand-ids 11,14,15,19,37
  python baserow_shopify_sync.py --apply --store-id 1
  python baserow_shopify_sync.py --apply --target binnen --store-id 3
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
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config
from shopify_pixelbin_remove_bg import pixelbin_replace_all_product_images

COMPRESS_THRESHOLD = 900_000
SHOPIFY_IMAGE_MAX = 20 * 1024 * 1024
MAX_SINGLE_DOWNLOAD = 2 * 1024 * 1024
FIELD_STORES = "field_8252"


@dataclass(frozen=True)
class StoreTarget:
    name: str
    product_id_field: str
    status_field: str
    default_store_id: int


STORE_TARGETS: dict[str, StoreTarget] = {
    "woonbloq": StoreTarget(
        name="WoonBloq",
        product_id_field="field_8302",
        status_field="field_8304",
        default_store_id=1,
    ),
    "binnen": StoreTarget(
        name="Binnen Design",
        product_id_field="field_8284",
        status_field="field_8283",
        default_store_id=3,
    ),
}


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _link_value(row: dict, field_key: str) -> str:
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
    if full_url:
        return full_url
    return _thumb_url(img) or None


def _ext_for_image(img: dict) -> str:
    mime = str(img.get("mime_type") or "image/jpeg").lower()
    if "png" in mime:
        return "png"
    if "webp" in mime:
        return "webp"
    return "jpg"


def _pick_shopify_upload_url(img: dict) -> str | None:
    """Prefer Baserow thumbnail when full file is large (faster upload)."""
    full_url = str(img.get("url") or "").strip()
    size = int(img.get("size") or 0)
    thumb = _thumb_url(img)
    if size > MAX_SINGLE_DOWNLOAD and thumb:
        return thumb
    if full_url:
        return full_url
    return thumb or None


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
    session: requests.Session,
    url: str,
    *,
    timeout: float,
) -> bytes:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return _prepare_image_bytes(resp.content)


def _pick_description(row: dict, settings) -> str:
    """AI description first, same priority as n8n Woonbloq workflow."""
    for key in (
        settings.field_ai_description_nl,
        settings.field_accordion_product_description,
        settings.field_product_description,
    ):
        text = str(row.get(key) or "").strip()
        if text:
            return text
    return ""


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


def _designer_image_url(row: dict, settings) -> str:
    imgs = _unique_file_images(row.get(settings.field_designer_image))
    if not imgs:
        return ""
    return _pick_download_url(imgs[0]) or ""


def _build_shopify_metafields(
    row: dict,
    settings,
    *,
    lifestyle_urls: list[str],
    designer_image_url: str,
) -> list[dict[str, Any]]:
    ns = settings.shopify_metafield_namespace or "custom"
    category = _link_value(row, settings.field_product_category)
    subcategory = _link_value(row, settings.field_sub_category)
    designer = str(row.get(settings.field_designer) or "").strip()
    out: list[dict[str, Any]] = []

    def add(key: str, value: str, mtype: str) -> None:
        if key and value:
            out.append(
                {"namespace": ns, "key": key, "value": value, "type": mtype}
            )

    add(settings.shopify_metafield_category, category, "single_line_text_field")
    add(settings.shopify_metafield_sub_category, subcategory, "single_line_text_field")
    add(settings.shopify_metafield_designer, designer, "single_line_text_field")
    add(settings.shopify_metafield_designer_image, designer_image_url, "url")
    return out


def _build_shopify_images(
    hero_images: list[dict],
    session: requests.Session,
    *,
    timeout: float,
) -> tuple[list[dict], list[dict]]:
    shopify_images: list[dict] = []
    skipped: list[dict] = []

    for i, img in enumerate(hero_images, 1):
        download_url = _pick_shopify_upload_url(img)
        if not download_url:
            skipped.append({"index": i, "reason": "no_url", "src": img.get("url")})
            continue
        try:
            data = _download_image(session, download_url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"index": i, "reason": f"download_failed: {exc}", "src": download_url})
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

    return shopify_images, skipped


@dataclass
class SyncJob:
    baserow_row_id: int
    title: str
    shopify_status: str
    missing_fields: list[str]
    product_payload: dict[str, Any]
    metafields: list[dict[str, Any]] = field(default_factory=list)
    lifestyle_urls: list[str] = field(default_factory=list)
    hero_image_count: int = 0
    lifestyle_image_count: int = 0
    images_skipped: list[dict] = field(default_factory=list)
    shopify_product_id: int | None = None
    metafields_ok: int = 0
    metafields_failed: int = 0
    pixelbin_images_ok: int = 0
    pixelbin_images_failed: int = 0
    error: str = ""


def _product_fields(
    row: dict,
    settings,
    *,
    hero_image_count: int,
    lifestyle_urls: list[str],
    designer_image_url: str,
) -> dict[str, Any]:
    title = str(row.get(settings.field_product_name) or "").strip()
    description = _pick_description(row, settings)
    brand = _link_value(row, settings.field_brand_link)
    category = _link_value(row, settings.field_product_category)
    subcategory = _link_value(row, settings.field_sub_category)

    has_hero = hero_image_count > 0
    missing = [
        name
        for name, ok in (
            ("title", bool(title)),
            ("description", bool(description)),
            ("category", bool(category)),
            ("subcategory", bool(subcategory)),
            ("brand", bool(brand)),
            ("product_image", has_hero),
        )
        if not ok
    ]
    shopify_status = "draft" if missing else "active"
    price = re.sub(r"[^0-9.]", "", str(row.get(settings.field_price) or "0")) or "0.00"
    if category and subcategory:
        product_type = f"{category} / {subcategory}"
    else:
        product_type = category or subcategory or "Overig"

    payload: dict[str, Any] = {
        "title": title or "Untitled Product",
        "body_html": description,
        "vendor": brand or "Unknown",
        "product_type": product_type,
        "tags": "" if not missing else ", ".join(missing),
        "status": shopify_status,
        "variants": [{"price": price}],
    }
    metafields = _build_shopify_metafields(
        row,
        settings,
        lifestyle_urls=lifestyle_urls,
        designer_image_url=designer_image_url,
    )
    return {
        "title": title,
        "shopify_status": shopify_status,
        "missing": missing,
        "payload": payload,
        "metafields": metafields,
        "hero_image_count": hero_image_count,
        "lifestyle_image_count": len(lifestyle_urls),
    }


def _preview_job(row: dict, settings) -> SyncJob:
    hero_raw = _unique_file_images(row.get(settings.field_hero_images))
    lifestyle_raw = _unique_file_images(row.get(settings.field_lifestyle_images))
    lifestyle_urls = _image_file_urls(row.get(settings.field_lifestyle_images))
    designer_image_url = _designer_image_url(row, settings)
    meta = _product_fields(
        row,
        settings,
        hero_image_count=len(hero_raw),
        lifestyle_urls=lifestyle_urls,
        designer_image_url=designer_image_url,
    )
    return SyncJob(
        baserow_row_id=int(row["id"]),
        title=meta["title"],
        shopify_status=meta["shopify_status"],
        missing_fields=meta["missing"],
        product_payload=meta["payload"],
        metafields=meta["metafields"],
        lifestyle_urls=lifestyle_urls,
        hero_image_count=len(hero_raw),
        lifestyle_image_count=len(lifestyle_raw),
    )


def _sync_lifestyle_metafield(
    shopify: ShopifyClient,
    settings,
    *,
    product_id: int,
    lifestyle_urls: list[str],
) -> tuple[int, int, list[str]]:
    key = settings.shopify_metafield_lifestyle_images
    if not lifestyle_urls or not key:
        return 0, 0, []
    try:
        file_gids = shopify.create_image_files_from_urls(lifestyle_urls)
        if not file_gids:
            return 0, 1, ["lifestyle_images: no files created"]
        shopify.set_product_list_file_reference_metafield(
            product_id,
            settings.shopify_metafield_namespace,
            key,
            file_gids,
        )
        return 1, 0, []
    except RuntimeError as exc:
        return 0, 1, [f"lifestyle_images: {exc}"]


def _build_job(row: dict, settings, session: requests.Session, *, timeout: float) -> SyncJob:
    hero_raw = _unique_file_images(row.get(settings.field_hero_images))
    lifestyle_raw = _unique_file_images(row.get(settings.field_lifestyle_images))
    lifestyle_urls = _image_file_urls(row.get(settings.field_lifestyle_images))
    designer_image_url = _designer_image_url(row, settings)
    image_source = hero_raw if hero_raw else lifestyle_raw
    images, skipped = _build_shopify_images(image_source, session, timeout=timeout)
    meta = _product_fields(
        row,
        settings,
        hero_image_count=len(hero_raw),
        lifestyle_urls=lifestyle_urls,
        designer_image_url=designer_image_url,
    )
    payload = meta["payload"]
    if images:
        payload["images"] = images

    return SyncJob(
        baserow_row_id=int(row["id"]),
        title=meta["title"],
        shopify_status=meta["shopify_status"],
        missing_fields=meta["missing"],
        product_payload=payload,
        metafields=meta["metafields"],
        lifestyle_urls=lifestyle_urls,
        hero_image_count=len(hero_raw),
        lifestyle_image_count=len(lifestyle_raw),
        images_skipped=skipped,
    )


def _iter_candidate_rows(
    client: BaserowClient,
    settings,
    target: StoreTarget,
    *,
    brand_id: int | None = None,
    brand_ids: set[int] | None = None,
    store_id: int | None,
    skip_synced: bool,
) -> list[dict]:
    allowed: set[int] | None = None
    if brand_ids:
        allowed = set(brand_ids)
    elif brand_id is not None:
        allowed = {brand_id}

    rows: list[dict] = []
    for row in client.list_table_rows(settings.products_table_id):
        if skip_synced and str(row.get(target.product_id_field) or "").strip():
            continue
        if allowed is not None:
            links = row.get(settings.field_brand_link) or []
            row_brand_ids = {int(x.get("id") or 0) for x in links if isinstance(x, dict)}
            if not (row_brand_ids & allowed):
                continue
        if store_id is not None:
            stores = row.get(FIELD_STORES) or []
            store_ids = {int(x.get("id") or 0) for x in stores if isinstance(x, dict)}
            if store_id not in store_ids:
                continue
        rows.append(row)
    return rows


def _parse_brand_ids(brand_id: int | None, brand_ids_csv: str | None) -> set[int] | None:
    ids: set[int] = set()
    if brand_id is not None:
        ids.add(brand_id)
    if brand_ids_csv:
        for part in brand_ids_csv.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
    return ids or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Baserow -> Shopify product sync")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--brand-id", type=int, help="Only products linked to this brand row.")
    parser.add_argument(
        "--brand-ids",
        type=str,
        help="Comma-separated brand row ids (e.g. 11,14,15,19,37).",
    )
    parser.add_argument(
        "--target",
        choices=tuple(STORE_TARGETS),
        default="woonbloq",
        help="Which store columns to update in Baserow (default: woonbloq).",
    )
    parser.add_argument(
        "--store-id",
        type=int,
        help="Only products linked to this Baserow store row (WoonBloq=1, Binnen=3).",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout per image download (seconds, default 120).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "baserow_shopify_sync_report.json",
    )
    parser.add_argument(
        "--pixelbin-bg",
        action="store_true",
        help="After create: Pixelbin erase.bg on all Shopify images (needs --apply).",
    )
    parser.add_argument(
        "--pixelbin-output-dir",
        type=Path,
        default=Path("output") / "pixelbin",
        help="Local backup folder for PNG cutouts when using --pixelbin-bg.",
    )
    args = parser.parse_args()
    dry_run = not args.apply
    if args.pixelbin_bg and dry_run:
        print("Note: --pixelbin-bg runs only with --apply (after product create).")
    target = STORE_TARGETS[args.target]
    brand_ids = _parse_brand_ids(args.brand_id, args.brand_ids)

    settings = load_settings()
    baserow = BaserowClient(settings)
    shopify = ShopifyClient(load_shopify_config())
    session = requests.Session()
    session.headers.update({"Authorization": f"Token {settings.baserow_token}"})

    print(f"Baserow: {settings.baserow_url} table {settings.products_table_id}")
    print(f"Shopify: {shopify.config.shop_host}")
    print(f"Target: {target.name} ({args.target})")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    if args.pixelbin_bg:
        print("Pixelbin: erase.bg on all images after each product create")
    if brand_ids:
        print(f"Brand filter: {', '.join(str(i) for i in sorted(brand_ids))}")
    if args.store_id:
        print(f"Store filter: {args.store_id}")
    print("Mapping: AI description; hero_images->images; lifestyle->metafield; category/sub/designer->metafields")
    print("Draft if missing: title, description, category, subcategory, brand, or hero_images")
    print()

    existing_titles: set[str] = set()
    try:
        existing_titles = {
            _normalize_title(p.get("title"))
            for p in shopify.iter_products(status="", fields="id,title")
            if _normalize_title(p.get("title"))
        }
        print(f"Shopify titles loaded: {len(existing_titles)}")
    except requests.RequestException as exc:
        print(f"WARN: Shopify title check failed ({exc}); duplicate titles not filtered")
    print()

    candidates = _iter_candidate_rows(
        baserow,
        settings,
        target,
        brand_ids=brand_ids,
        store_id=args.store_id,
        skip_synced=True,
    )
    print(f"Baserow candidates (not yet {target.name} synced): {len(candidates)}")

    jobs: list[SyncJob] = []
    rows_to_sync: list[dict] = []
    queued_titles: set[str] = set()
    skip_shopify_dup = 0
    skip_batch_dup = 0
    skip_no_title = 0

    for row in candidates:
        title_norm = _normalize_title(row.get(settings.field_product_name))
        if not title_norm:
            skip_no_title += 1
            continue
        if title_norm in existing_titles:
            skip_shopify_dup += 1
            continue
        if title_norm in queued_titles:
            skip_batch_dup += 1
            continue
        job = _preview_job(row, settings)
        queued_titles.add(title_norm)
        jobs.append(job)
        if not dry_run:
            rows_to_sync.append(row)
        if args.limit > 0 and len(jobs) >= args.limit:
            break

    active_n = sum(1 for j in jobs if j.shopify_status == "active")
    draft_n = len(jobs) - active_n
    print(f"Skip duplicate on Shopify: {skip_shopify_dup}")
    print(f"Skip duplicate name in Baserow batch: {skip_batch_dup}")
    print(f"Skip empty title: {skip_no_title}")
    print(f"Will push to Shopify: {len(jobs)} (active: {active_n}, draft: {draft_n})")
    print()
    for job in jobs[:15]:
        gallery = job.hero_image_count or job.lifestyle_image_count
        src = "hero" if job.hero_image_count else ("lifestyle" if gallery else "none")
        lifestyle_mf = 1 if job.lifestyle_urls and settings.shopify_metafield_lifestyle_images else 0
        print(
            f"  [{job.shopify_status}] {job.title} "
            f"(baserow={job.baserow_row_id}, hero={job.hero_image_count}, "
            f"lifestyle={job.lifestyle_image_count}, gallery={gallery} from {src}, "
            f"metafields={len(job.metafields) + lifestyle_mf})"
            + (
                " -> pixelbin all images after create"
                if args.pixelbin_bg and not dry_run
                else ""
            )
        )
    if len(jobs) > 15:
        print(f"  ... and {len(jobs) - 15} more")

    if dry_run:
        print()
        print("Dry run. Add --apply to create on Shopify and update Baserow.")
        return 0

    if not jobs:
        print("Nothing to sync.")
        return 0

    if args.pixelbin_bg:
        from pixelbin_bg import load_pixelbin_settings

        load_pixelbin_settings()

    t0 = time.perf_counter()
    ok = 0
    fail = 0
    applied_jobs: list[SyncJob] = []

    print()
    for i, row in enumerate(rows_to_sync, 1):
        preview = jobs[i - 1]
        print(
            f"[{i}/{len(rows_to_sync)}] {preview.title} — downloading hero images...",
            flush=True,
        )
        try:
            job = _build_job(row, settings, session, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            preview.error = str(exc)
            applied_jobs.append(preview)
            fail += 1
            print(f"  ERROR building job: {exc}")
            continue

        img_n = len(job.product_payload.get("images") or [])
        if job.images_skipped:
            print(f"  images: {img_n} uploaded, {len(job.images_skipped)} skipped")
        try:
            created = shopify.create_product(job.product_payload)
            pid = int(created["id"])
            job.shopify_product_id = pid
            baserow.update_row(
                settings.products_table_id,
                job.baserow_row_id,
                {
                    target.product_id_field: f"gid://shopify/Product/{pid}",
                    target.status_field: "Added" if job.shopify_status == "active" else "Draft",
                },
            )
            ok += 1
            print(f"  -> Shopify id {pid} ({job.shopify_status})")

            mf_errors: list[str] = []
            if job.metafields:
                mf_ok, mf_fail, mf_errors = shopify.create_product_metafields(
                    pid, job.metafields
                )
                job.metafields_ok += mf_ok
                job.metafields_failed += mf_fail
            ls_ok, ls_fail, ls_errors = _sync_lifestyle_metafield(
                shopify,
                settings,
                product_id=pid,
                lifestyle_urls=job.lifestyle_urls,
            )
            job.metafields_ok += ls_ok
            job.metafields_failed += ls_fail
            mf_errors = mf_errors + ls_errors
            if job.metafields_ok or job.metafields_failed:
                print(
                    f"  -> Metafields: {job.metafields_ok} saved"
                    + (f", {job.metafields_failed} failed" if job.metafields_failed else "")
                )
                for err in mf_errors:
                    print(f"     {err}")

            if args.pixelbin_bg:
                product_for_px = created
                if not product_for_px.get("images"):
                    product_for_px = shopify.get_product(pid)
                px_ok, px_fail, _ = pixelbin_replace_all_product_images(
                    shopify,
                    product_for_px,
                    output_dir=args.pixelbin_output_dir,
                )
                job.pixelbin_images_ok = px_ok
                job.pixelbin_images_failed = px_fail
                print(
                    f"  -> Pixelbin: {px_ok} images replaced"
                    + (f", {px_fail} failed" if px_fail else "")
                )
            applied_jobs.append(job)
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
            applied_jobs.append(job)
            fail += 1
            print(f"  ERROR: {exc}")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "target": args.target,
                "shop": shopify.config.shop_host,
                "synced": ok,
                "failed": fail,
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
                "jobs": [
                    {
                        "baserow_row_id": j.baserow_row_id,
                        "title": j.title,
                        "shopify_status": j.shopify_status,
                        "shopify_product_id": j.shopify_product_id,
                        "missing_fields": j.missing_fields,
                        "images_uploaded": len(j.product_payload.get("images") or []),
                        "lifestyle_images": j.lifestyle_image_count,
                        "metafields_planned": len(j.metafields),
                        "metafields_ok": j.metafields_ok,
                        "metafields_failed": j.metafields_failed,
                        "images_skipped": j.images_skipped,
                        "pixelbin_images_ok": j.pixelbin_images_ok,
                        "pixelbin_images_failed": j.pixelbin_images_failed,
                        "error": j.error,
                    }
                    for j in applied_jobs
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print()
    print(f"Done: {ok} created, {fail} failed")
    print(f"Report: {args.report}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
