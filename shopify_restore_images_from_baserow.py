"""Restore Woonbloq Shopify product images from Baserow table 742 (product_images).

Only Baserow rows linked to Woonbloq via shopify_stores (not brand stores) are used.

Match order per zero-image Woonbloq Shopify product:
  1. product_name title match (Baserow table 742)
  2. must be linked to Woonbloq in shopify_stores -> then upload product_images
  3. if 2+ title matches with Woonbloq link -> description vs Shopify body_html (last)
  4. if no title match -> fallback WoonbloqProductID (same store check)

Examples:
  python shopify_restore_images_from_baserow.py --dry-run
  python shopify_restore_images_from_baserow.py --dry-run --limit 10
  python shopify_restore_images_from_baserow.py --apply --limit 5
  python shopify_restore_images_from_baserow.py --apply
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config

COMPRESS_THRESHOLD = 900_000
SHOPIFY_IMAGE_MAX = 20 * 1024 * 1024

# Baserow database 178 — productsDetails table 742 (user URL)
TABLE_742 = 742
F_NAME = "field_7347"
F_DESC = "field_7348"
F_PRODUCT_IMAGES = "field_7349"
F_STORES = "field_7375"
F_SHOPIFY_STORES = "field_8509"
F_WOONBLOQ_PRODUCT_ID = "field_7425"
WOONBLOQ_STORE_ROW_ID = 1

DEFAULT_ZERO_REPORT = Path("output") / "shopify_zero_image_products.json"
DEFAULT_CLASSIFY_REPORT = Path(r"c:\Users\Admin\Desktop\shopify_before_june10_report.json")


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


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


def _compress_jpeg(data: bytes, *, quality: int, max_dim: int) -> bytes:
    im = Image.open(io.BytesIO(data))
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


def _build_shopify_images(
    images: list[dict],
    session: requests.Session,
    *,
    timeout: float,
) -> tuple[list[dict], list[dict]]:
    shopify_images: list[dict] = []
    skipped: list[dict] = []
    for i, img in enumerate(images, 1):
        download_url = _pick_download_url(img)
        if not download_url:
            skipped.append({"index": i, "reason": "no_url"})
            continue
        try:
            resp = session.get(download_url, timeout=timeout)
            resp.raise_for_status()
            data = _prepare_image_bytes(resp.content)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"index": i, "reason": str(exc), "src": download_url})
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


def _parse_shopify_id(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{10,})", text)
    return int(m.group(1)) if m else None


def _normalize_description(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _descriptions_match(a: str, b: str) -> bool:
    na, nb = _normalize_description(a), _normalize_description(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if shorter in longer:
        return True
    if len(shorter) >= 80 and longer.startswith(shorter[: min(300, len(shorter))]):
        return True
    return False


def _row_title(row: dict) -> str:
    return str(row.get(F_NAME) or "").strip()


def _row_desc(row: dict) -> str:
    return str(row.get(F_DESC) or "").strip()


def _row_images(row: dict) -> list[dict]:
    return _unique_file_images(row.get(F_PRODUCT_IMAGES))


def _row_has_woonbloq_shopify_store(row: dict, store_row_id: int) -> bool:
    shopify_stores = row.get(F_SHOPIFY_STORES) or []
    if not isinstance(shopify_stores, list):
        return False
    return any(int(link.get("id") or 0) == store_row_id for link in shopify_stores if link)


def _woonbloq_store_filter(store_row_id: int) -> dict[str, Any]:
    return {"filter__shopify_stores__link_row_has": store_row_id}


def _filter_woonbloq_store(rows: list[dict], store_row_id: int) -> list[dict]:
    return [r for r in rows if _row_has_woonbloq_shopify_store(r, store_row_id)]


@dataclass
class RestoreJob:
    shopify_id: int
    shopify_title: str
    shopify_status: str
    match_method: str = ""
    baserow_row_id: int | None = None
    baserow_title: str = ""
    image_count: int = 0
    lifestyle_only_in_report: bool = False
    action: str = "pending"  # upload | skip_no_match | skip_ambiguous | skip_no_images | uploaded | error
    skip_reason: str = ""
    error: str = ""


def _load_lifestyle_ids(report_path: Path) -> set[int]:
    if not report_path.exists():
        return set()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    ids: set[int] = set()
    for row in data.get("products") or []:
        if str(row.get("category") or "").strip() == "lifestyle":
            pid = int(row.get("id") or 0)
            if pid:
                ids.add(pid)
    return ids


def _search_by_shopify_id(
    client: BaserowClient,
    shopify_id: int,
    *,
    store_row_id: int,
) -> list[dict]:
    rows: list[dict] = []
    needle = str(shopify_id)
    filters = {
        f"filter__{F_WOONBLOQ_PRODUCT_ID}__contains": needle,
        **_woonbloq_store_filter(store_row_id),
    }
    for row in client.list_table_rows(TABLE_742, filters=filters):
        if _parse_shopify_id(str(row.get(F_WOONBLOQ_PRODUCT_ID) or "")) != shopify_id:
            continue
        if not _row_has_woonbloq_shopify_store(row, store_row_id):
            continue
        rows.append(row)
    return rows


def _search_by_title_all(client: BaserowClient, title: str) -> list[dict]:
    title = title.strip()
    if not title:
        return []
    return list(
        client.list_table_rows(
            TABLE_742,
            filters={f"filter__{F_NAME}__equal": title},
        )
    )


def _search_by_title_woonbloq(
    client: BaserowClient,
    title: str,
    *,
    store_row_id: int,
) -> tuple[list[dict], list[dict]]:
    """Return (woonbloq-linked rows, all title matches)."""
    all_rows = _search_by_title_all(client, title)
    return _filter_woonbloq_store(all_rows, store_row_id), all_rows


def _pick_baserow_row(
    candidates: list[dict],
    *,
    shopify_id: int,
    shopify_body_html: str,
) -> tuple[dict | None, str]:
    if not candidates:
        return None, "no_baserow_match"
    if len(candidates) == 1:
        return candidates[0], "single_match"

    by_pid = [
        r
        for r in candidates
        if _parse_shopify_id(str(r.get(F_WOONBLOQ_PRODUCT_ID) or "")) == shopify_id
    ]
    if len(by_pid) == 1:
        return by_pid[0], "woonbloq_product_id"

    # Description disambiguation — last step only when multiple Woonbloq-linked rows.
    for row in candidates:
        if _descriptions_match(_row_desc(row), shopify_body_html):
            return row, "description_match"
    return None, "ambiguous_description"


def _match_baserow_row(
    client: BaserowClient,
    *,
    shopify_id: int,
    title: str,
    shopify_body_html: str,
    store_row_id: int,
) -> tuple[dict | None, str]:
    # 1) Title first
    woonbloq_rows, all_title_rows = _search_by_title_woonbloq(
        client, title, store_row_id=store_row_id
    )
    if woonbloq_rows:
        if len(woonbloq_rows) == 1:
            return woonbloq_rows[0], "title+woonbloq_store"
        row, reason = _pick_baserow_row(
            woonbloq_rows, shopify_id=shopify_id, shopify_body_html=shopify_body_html
        )
        if row:
            return row, f"title+woonbloq_store+{reason}"
        return None, "ambiguous_description"

    if all_title_rows:
        return None, "title_match_not_woonbloq"

    # 2) Fallback: WoonbloqProductID (still requires Woonbloq shopify_stores link)
    by_id = _search_by_shopify_id(client, shopify_id, store_row_id=store_row_id)
    if len(by_id) == 1:
        return by_id[0], "woonbloq_product_id_fallback"
    if len(by_id) > 1:
        row, reason = _pick_baserow_row(
            by_id, shopify_id=shopify_id, shopify_body_html=shopify_body_html
        )
        if row:
            return row, f"woonbloq_product_id+{reason}"
        return None, "ambiguous_woonbloq_product_id"

    return None, "no_baserow_match"


def _build_jobs(
    *,
    zero_products: list[dict],
    lifestyle_ids: set[int],
    baserow: BaserowClient,
    shopify: ShopifyClient,
    store_row_id: int,
    limit: int | None,
) -> list[RestoreJob]:
    jobs: list[RestoreJob] = []
    items = zero_products[:limit] if limit else zero_products

    for i, prod in enumerate(items, 1):
        sid = int(prod["id"])
        title = str(prod.get("title") or "").strip()
        job = RestoreJob(
            shopify_id=sid,
            shopify_title=title,
            shopify_status=str(prod.get("status") or ""),
            lifestyle_only_in_report=sid in lifestyle_ids,
        )

        shopify_body = ""
        try:
            live = shopify.get_product(sid)
            shopify_body = str(live.get("body_html") or "")
            if live.get("images"):
                job.action = "skip_has_images"
                job.skip_reason = f"shopify already has {len(live['images'])} images"
                jobs.append(job)
                continue
        except Exception as exc:  # noqa: BLE001
            job.action = "error"
            job.error = f"shopify_get_failed: {exc}"
            jobs.append(job)
            continue

        row, method = _match_baserow_row(
            baserow,
            shopify_id=sid,
            title=title,
            shopify_body_html=shopify_body,
            store_row_id=store_row_id,
        )
        job.match_method = method

        if not row:
            if method == "title_match_not_woonbloq":
                job.action = "skip_wrong_store"
            elif method in ("ambiguous_description", "ambiguous_woonbloq_product_id"):
                job.action = "skip_ambiguous"
            else:
                job.action = "skip_no_match"
            job.skip_reason = method
            jobs.append(job)
            continue

        if not _row_has_woonbloq_shopify_store(row, store_row_id):
            job.action = "skip_wrong_store"
            job.skip_reason = f"not linked to Woonbloq shopify_stores row {store_row_id}"
            jobs.append(job)
            continue

        images = _row_images(row)
        job.baserow_row_id = int(row["id"])
        job.baserow_title = _row_title(row)
        job.image_count = len(images)

        if not images:
            job.action = "skip_no_images"
            job.skip_reason = "baserow product_images empty"
            jobs.append(job)
            continue

        job.action = "upload"
        jobs.append(job)

        if i % 10 == 0:
            print(f"  matched {i}/{len(items)}...")

    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore Woonbloq Shopify images from Baserow table 742 product_images."
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only (default).")
    parser.add_argument("--apply", action="store_true", help="Upload images to Shopify.")
    parser.add_argument(
        "--store-id",
        type=int,
        default=WOONBLOQ_STORE_ROW_ID,
        help=f"Baserow shopify_stores link row id for Woonbloq (default: {WOONBLOQ_STORE_ROW_ID}).",
    )
    parser.add_argument(
        "--zero-report",
        type=Path,
        default=DEFAULT_ZERO_REPORT,
        help="JSON from shopify_find_zero_images.py",
    )
    parser.add_argument(
        "--classify-report",
        type=Path,
        default=DEFAULT_CLASSIFY_REPORT,
        help="Classify report for lifestyle cross-check.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Process first N products only.")
    parser.add_argument("--output", type=Path, default=Path("output") / "shopify_restore_images_report.json")
    args = parser.parse_args()

    if args.apply:
        mode = "apply"
    else:
        mode = "dry-run"

    if not args.zero_report.exists():
        print(f"ERROR: zero-image report not found: {args.zero_report}")
        print("Run: python shopify_find_zero_images.py")
        return 1

    zero_data = json.loads(args.zero_report.read_text(encoding="utf-8"))
    zero_products = list(zero_data.get("products") or [])
    lifestyle_ids = _load_lifestyle_ids(args.classify_report)
    limit = args.limit if args.limit > 0 else None

    settings = load_settings()
    baserow = BaserowClient(settings)
    shopify = ShopifyClient(load_shopify_config())
    session = requests.Session()
    timeout = settings.http_timeout

    overlap = sum(1 for p in zero_products if int(p["id"]) in lifestyle_ids)
    print(f"Shop: {shopify.config.shop_host} (Woonbloq)")
    print(f"Mode: {mode}")
    print(f"Woonbloq shopify_stores row id (Baserow): {args.store_id}")
    print(f"Zero-image products: {len(zero_products)}")
    print(f"Lifestyle-only in classify report: {len(lifestyle_ids)}")
    print(f"Overlap (zero images AND lifestyle report): {overlap}")
    print(f"Baserow table: {TABLE_742}")
    print()

    t0 = time.perf_counter()
    jobs = _build_jobs(
        zero_products=zero_products,
        lifestyle_ids=lifestyle_ids,
        baserow=baserow,
        shopify=shopify,
        store_row_id=args.store_id,
        limit=limit,
    )

    upload_jobs = [j for j in jobs if j.action == "upload"]
    print(f"\nMatch results ({len(jobs)} checked):")
    by_action: dict[str, int] = {}
    for j in jobs:
        by_action[j.action] = by_action.get(j.action, 0) + 1
    for action, count in sorted(by_action.items()):
        print(f"  {action}: {count}")

    if mode == "apply" and upload_jobs:
        print(f"\nUploading images for {len(upload_jobs)} products...")
        for job in upload_jobs:
            try:
                row = baserow.get_row(TABLE_742, int(job.baserow_row_id))
                raw_images = _row_images(row)
                shopify_images, skipped = _build_shopify_images(
                    raw_images, session, timeout=timeout
                )
                if not shopify_images:
                    job.action = "skip_no_images"
                    job.skip_reason = f"all downloads failed ({len(skipped)} skipped)"
                    continue
                shopify.add_product_images(job.shopify_id, shopify_images)
                job.action = "uploaded"
                job.image_count = len(shopify_images)
                print(
                    f"  OK {job.shopify_title!r} "
                    f"(shopify={job.shopify_id}, baserow={job.baserow_row_id}, "
                    f"images={len(shopify_images)})"
                )
            except Exception as exc:  # noqa: BLE001
                job.action = "error"
                job.error = str(exc)
                print(f"  FAIL {job.shopify_title!r}: {exc}")

    print("\n--- Sample upload candidates ---")
    for j in upload_jobs[:15]:
        flag = " [was lifestyle-only]" if j.lifestyle_only_in_report else ""
        print(
            f"  {j.shopify_title!r} | match={j.match_method} | "
            f"baserow={j.baserow_row_id} | images={j.image_count}{flag}"
        )
    if len(upload_jobs) > 15:
        print(f"  ... +{len(upload_jobs) - 15} more")

    skipped = [j for j in jobs if j.action.startswith("skip")]
    if skipped:
        print("\n--- Skipped (first 10) ---")
        for j in skipped[:10]:
            print(f"  {j.shopify_title!r}: {j.skip_reason or j.action}")

    by_action = {}
    for j in jobs:
        by_action[j.action] = by_action.get(j.action, 0) + 1

    payload = {
        "mode": mode,
        "shop": shopify.config.shop_host,
        "baserow_table_id": TABLE_742,
        "woonbloq_store_row_id": args.store_id,
        "zero_report": str(args.zero_report),
        "classify_report": str(args.classify_report),
        "lifestyle_overlap": overlap,
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "summary": by_action,
        "jobs": [
            {
                "shopify_id": j.shopify_id,
                "shopify_title": j.shopify_title,
                "shopify_status": j.shopify_status,
                "match_method": j.match_method,
                "baserow_row_id": j.baserow_row_id,
                "baserow_title": j.baserow_title,
                "image_count": j.image_count,
                "lifestyle_only_in_report": j.lifestyle_only_in_report,
                "action": j.action,
                "skip_reason": j.skip_reason,
                "error": j.error,
            }
            for j in jobs
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {args.output}")

    if mode == "dry-run":
        print("\nTip: use Baserow product_images for Woonbloq (shopify_stores=WoonBloq).")
        print("Do not restore deleted lifestyle_urls from Shopify CDN.")
        print("Run with --apply when ready.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
