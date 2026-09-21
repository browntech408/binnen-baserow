"""
Generate AI detail close-ups for Image Categorization demo products.

Rules (based on real detail_image count in field_7360):
  0 details -> generate 3
  1 detail  -> generate 2
  2 details -> generate 1
  >= 3      -> skip

Generated images are appended to Detailed_image_gen (field_7401).

Usage:
  python demo_generate_detail_for_categorization.py --dry-run
  python demo_generate_detail_for_categorization.py
  python demo_generate_detail_for_categorization.py --brand-id 40 --limit 10
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from fal_image_processor import generate_detail_images

TABLE_ID = 742
DEFAULT_BRAND_ID = 40
BRAND_NAME = "Image Categorization"

FIELD_PRODUCT_NAME = "field_7347"
FIELD_PRODUCT_DESC = "field_7348"
FIELD_PRODUCT_IMAGES = "field_7349"
FIELD_HERO_IMAGES = "field_7358"
FIELD_LIFESTYLE_IMAGES = "field_7359"
FIELD_DETAIL_IMAGE = "field_7360"
FIELD_DETAILED_IMAGE_GEN = "field_7401"
FIELD_BRAND_LINK = "field_7376"
FIELD_PRODUCT_CATEGORY = "field_7363"
FIELD_SUB_CATEGORY = "field_7364"
FIELD_SOURCE_CATEGORY = "field_7368"
FIELD_SOURCE_SUBCATEGORY = "field_7369"
FIELD_BRAND_NAME = "field_7446"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
REPORT_PATH = OUTPUT_DIR / "image_categorization_detail_gen_report.json"


def _linked_ids(field_val) -> list[int]:
    if not field_val:
        return []
    ids = []
    for item in field_val if isinstance(field_val, list) else []:
        if isinstance(item, dict) and "id" in item:
            ids.append(int(item["id"]))
        elif isinstance(item, int):
            ids.append(item)
    return ids


def _linked_values(field_val) -> list[str]:
    if not field_val:
        return []
    if isinstance(field_val, str):
        return [field_val] if field_val.strip() else []
    out = []
    for item in field_val if isinstance(field_val, list) else []:
        if isinstance(item, dict):
            val = item.get("value") or item.get("name") or ""
            if val:
                out.append(str(val))
        elif item:
            out.append(str(item))
    return out


def _file_urls(field_val) -> list[str]:
    urls = []
    for item in field_val if isinstance(field_val, list) else []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(item["url"])
    return urls


def _file_name_refs(field_val) -> list[dict]:
    refs = []
    for item in field_val if isinstance(field_val, list) else []:
        if isinstance(item, dict) and item.get("name"):
            refs.append({"name": item["name"]})
    return refs


def num_to_generate(detail_count: int) -> int:
    """Fill up to 3 detail images; skip when already >= 3."""
    if detail_count <= 0:
        return 3
    if detail_count == 1:
        return 2
    if detail_count == 2:
        return 1
    return 0  # 3 or more -> skip


def resolve_brand_id(baserow: BaserowClient, brand_table_id: int, brand_id: int | None) -> int:
    if brand_id:
        return brand_id
    for row in baserow.list_table_rows(brand_table_id):
        if str(row.get(FIELD_BRAND_NAME) or "").strip().lower() == BRAND_NAME.lower():
            return int(row["id"])
    raise RuntimeError("Brand '%s' not found" % BRAND_NAME)


def upload_pil_to_baserow(pil_img: Image.Image, filename: str, settings) -> dict:
    buf = io.BytesIO()
    if pil_img.mode in ("RGBA", "P"):
        pil_img = pil_img.convert("RGB")
    pil_img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    url = settings.api_base + "/user-files/upload-file/"
    headers = {"Authorization": "Token " + settings.baserow_token}
    files = {"file": (filename, buf, "image/jpeg")}
    resp = requests.post(url, headers=headers, files=files, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _row_ids_from_categorization_report() -> list[int]:
    """Prefer the demo categorization report (fast) over full-table scan."""
    cat_report = OUTPUT_DIR / "image_categorization_demo_report.json"
    if not cat_report.exists():
        return []
    try:
        data = json.loads(cat_report.read_text(encoding="utf-8"))
    except Exception:
        return []
    ids = []
    for p in data.get("products") or []:
        rid = p.get("row_id")
        if rid:
            ids.append(int(rid))
    return ids


def collect_demo_products(baserow: BaserowClient, brand_id: int) -> list[dict]:
    report_ids = _row_ids_from_categorization_report()
    if report_ids:
        print(
            "Loading %d demo products from categorization report (brand id=%d)..."
            % (len(report_ids), brand_id)
        )
        rows = []
        for i, rid in enumerate(report_ids, 1):
            try:
                row = baserow.get_row(TABLE_ID, rid)
                if brand_id in _linked_ids(row.get(FIELD_BRAND_LINK)):
                    rows.append(row)
                else:
                    print("  WARN row %d not linked to brand %d — skipped" % (rid, brand_id))
            except Exception as exc:
                print("  WARN could not load row %d: %s" % (rid, exc))
            if i % 10 == 0:
                print("  ...loaded %d/%d" % (i, len(report_ids)))
        print("Loaded %d products." % len(rows))
        return rows

    rows = []
    print("No categorization report — scanning table for brand id=%d ..." % brand_id)
    scanned = 0
    for row in baserow.list_table_rows(TABLE_ID):
        scanned += 1
        if scanned % 500 == 0:
            print("  ...scanned %d, found %d" % (scanned, len(rows)))
        if brand_id in _linked_ids(row.get(FIELD_BRAND_LINK)):
            rows.append(row)
    print("Found %d products on brand (scanned %d)." % (len(rows), scanned))
    return rows


def pick_reference_urls(row: dict) -> tuple[str, list[str], list[str]]:
    """Prefer hero, then raw product images, then lifestyle/detail."""
    heroes = _file_urls(row.get(FIELD_HERO_IMAGES))
    raw = _file_urls(row.get(FIELD_PRODUCT_IMAGES))
    lifestyle = _file_urls(row.get(FIELD_LIFESTYLE_IMAGES))
    details = _file_urls(row.get(FIELD_DETAIL_IMAGE))

    prefer: list[str] = []
    for pool in (heroes, raw):
        for u in pool:
            if u not in prefer:
                prefer.append(u)

    all_urls = list(prefer)
    for pool in (lifestyle, details):
        for u in pool:
            if u not in all_urls:
                all_urls.append(u)

    reference = prefer[0] if prefer else (all_urls[0] if all_urls else "")
    return reference, prefer, all_urls


def category_hint(row: dict) -> str:
    parts = [
        str(row.get(FIELD_SOURCE_CATEGORY) or ""),
        str(row.get(FIELD_SOURCE_SUBCATEGORY) or ""),
        " ".join(_linked_values(row.get(FIELD_PRODUCT_CATEGORY))),
        " ".join(_linked_values(row.get(FIELD_SUB_CATEGORY))),
    ]
    return " / ".join(p for p in parts if p.strip())


def process_row(
    *,
    row: dict,
    baserow: BaserowClient,
    settings,
    openrouter_key: str,
    engine: str,
    dry_run: bool,
) -> dict[str, Any]:
    row_id = row["id"]
    name = str(row.get(FIELD_PRODUCT_NAME) or ("Product %s" % row_id))
    desc = str(row.get(FIELD_PRODUCT_DESC) or "")
    detail_count = len(row.get(FIELD_DETAIL_IMAGE) or [])
    existing_gen = _file_name_refs(row.get(FIELD_DETAILED_IMAGE_GEN))
    quota = num_to_generate(detail_count)
    # Resume-safe: only generate what is still missing toward the quota
    need = max(0, quota - len(existing_gen))

    print("\n" + "=" * 64)
    print("[Row %s] %s" % (row_id, name[:80]))
    print(
        "  real_details=%d | already_gen=%d | quota=%d | will_generate=%d"
        % (detail_count, len(existing_gen), quota, need)
    )

    if quota <= 0:
        print("  SKIP: already has %d detail images (>=3)." % detail_count)
        return {
            "row_id": row_id,
            "name": name,
            "status": "skipped_enough_details",
            "detail_count": detail_count,
            "generated": 0,
            "need": 0,
            "quota": 0,
        }

    if need <= 0:
        print(
            "  SKIP: Detailed_image_gen already has %d (quota=%d)."
            % (len(existing_gen), quota)
        )
        return {
            "row_id": row_id,
            "name": name,
            "status": "skipped_already_generated",
            "detail_count": detail_count,
            "generated": 0,
            "need": 0,
            "quota": quota,
            "existing_gen": len(existing_gen),
        }

    reference, prefer, all_urls = pick_reference_urls(row)
    if not reference:
        print("  SKIP: no reference image.")
        return {
            "row_id": row_id,
            "name": name,
            "status": "skipped_no_reference",
            "detail_count": detail_count,
            "generated": 0,
            "need": need,
        }

    print("  Reference: %s..." % reference[:90])
    print("  Generating %d detail close-up(s) (engine=%s)..." % (need, engine))

    if dry_run:
        print("  [dry-run] No fal.ai / Baserow writes.")
        return {
            "row_id": row_id,
            "name": name,
            "status": "dry_run",
            "detail_count": detail_count,
            "generated": 0,
            "need": need,
            "reference": reference,
        }

    try:
        results = generate_detail_images(
            reference_url=reference,
            product_name=name,
            product_description=desc,
            openrouter_key=openrouter_key,
            num_images=need,
            strength=0.30,
            all_image_urls=all_urls,
            prefer_image_urls=prefer,
            engine=engine,
            mode="detail",
            product_category_hint=category_hint(row),
        )
    except Exception as exc:
        print("  [!] Generation failed: %s" % exc)
        return {
            "row_id": row_id,
            "name": name,
            "status": "generation_failed",
            "error": str(exc),
            "detail_count": detail_count,
            "generated": 0,
            "need": need,
        }

    uploaded = []
    for i, result_tuple in enumerate(results):
        pil_img, req_id, _prompt = result_tuple
        filename = "detail_ai_demo_%s_%d.jpg" % (row_id, i)
        try:
            up = upload_pil_to_baserow(pil_img, filename, settings)
            uploaded.append({"name": up["name"]})
            print("    uploaded %s -> %s (fal=%s)" % (filename, up["name"], req_id))
        except Exception as exc:
            print("    [!] upload failed %s: %s" % (filename, exc))

    if uploaded:
        new_list = existing_gen + uploaded
        baserow.update_row(TABLE_ID, row_id, {FIELD_DETAILED_IMAGE_GEN: new_list})
        print(
            "  Saved %d image(s) to Detailed_image_gen (total now %d)."
            % (len(uploaded), len(new_list))
        )

    return {
        "row_id": row_id,
        "name": name,
        "status": "ok" if uploaded else "upload_failed",
        "detail_count": detail_count,
        "need": need,
        "generated": len(uploaded),
        "detailed_image_gen_total": len(existing_gen) + len(uploaded),
    }


def parse_args():
    p = argparse.ArgumentParser(
        description="Generate detail images for Image Categorization demo brand products"
    )
    p.add_argument("--brand-id", type=int, default=DEFAULT_BRAND_ID)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0, help="Max products to process (0=all needing gen)")
    p.add_argument("--row-id", type=int, default=0, help="Only one product row id")
    p.add_argument("--engine", default="flux2-pro")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    if not args.dry_run:
        if not settings.openrouter_api_key:
            print("ERROR: OPENROUTER_API_KEY missing")
            return 1
        if not os.getenv("FAL_KEY"):
            print("ERROR: FAL_KEY missing")
            return 1

    baserow = BaserowClient(settings)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    brand_id = resolve_brand_id(baserow, settings.brands_table_id, args.brand_id)

    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "brand_id": brand_id,
        "brand_name": BRAND_NAME,
        "engine": args.engine,
        "dry_run": args.dry_run,
        "rules": {"0": 3, "1": 2, "2": 1, ">=3": "skip"},
        "products": [],
        "errors": [],
    }

    if args.row_id:
        rows = [baserow.get_row(TABLE_ID, args.row_id)]
    else:
        rows = collect_demo_products(baserow, brand_id)

    # Plan summary (resume-aware)
    plan = {"skip": [], "gen": []}
    for row in rows:
        n = len(row.get(FIELD_DETAIL_IMAGE) or [])
        already = len(row.get(FIELD_DETAILED_IMAGE_GEN) or [])
        quota = num_to_generate(n)
        need = max(0, quota - already)
        item = {
            "row_id": row["id"],
            "name": str(row.get(FIELD_PRODUCT_NAME) or "")[:60],
            "detail_count": n,
            "existing_gen": already,
            "quota": quota,
            "need": need,
        }
        if quota <= 0:
            plan["skip"].append({**item, "reason": "enough_real_details"})
        elif need <= 0:
            plan["skip"].append({**item, "reason": "already_generated"})
        else:
            plan["gen"].append(item)

    report["plan"] = {
        "skip_count": len(plan["skip"]),
        "process_count": len(plan["gen"]),
        "images_to_generate": sum(i["need"] for i in plan["gen"]),
        "skip": plan["skip"],
        "process": plan["gen"],
    }

    print("\n=== PLAN ===")
    print("Skip (>=3 details): %d" % len(plan["skip"]))
    print("Process: %d products, %d images to generate" % (
        len(plan["gen"]), sum(i["need"] for i in plan["gen"])
    ))
    for i, item in enumerate(plan["gen"], 1):
        print(
            "  [%d] id=%d details=%d -> gen %d | %s"
            % (i, item["row_id"], item["detail_count"], item["need"], item["name"])
        )

    to_run = plan["gen"]
    if args.limit and args.limit > 0:
        to_run = to_run[: args.limit]
        print("Limited to first %d products." % len(to_run))

    if args.dry_run:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("\nDry-run only. Report: %s" % REPORT_PATH)
        return 0

    # Map row_id -> full row
    by_id = {r["id"]: r for r in rows}
    for i, item in enumerate(to_run, 1):
        print("\n>>> %d/%d" % (i, len(to_run)))
        row = by_id.get(item["row_id"]) or baserow.get_row(TABLE_ID, item["row_id"])
        # Fresh fetch for file URLs
        row = baserow.get_row(TABLE_ID, row["id"])
        try:
            result = process_row(
                row=row,
                baserow=baserow,
                settings=settings,
                openrouter_key=settings.openrouter_api_key,
                engine=args.engine,
                dry_run=False,
            )
            report["products"].append(result)
            if result.get("status") not in ("ok", "skipped_enough_details", "skipped_no_reference"):
                report["errors"].append(result)
        except Exception as exc:
            print("  FATAL: %s" % exc)
            err = {"row_id": item["row_id"], "status": "fatal", "error": str(exc)}
            report["products"].append(err)
            report["errors"].append(err)
        time.sleep(0.3)

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["totals"] = {
        "processed": len(report["products"]),
        "ok": sum(1 for p in report["products"] if p.get("status") == "ok"),
        "images_generated": sum(int(p.get("generated") or 0) for p in report["products"]),
        "errors": len(report["errors"]),
        "skipped_planned": len(plan["skip"]),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n=== DONE ===")
    print("OK: %d | images generated: %d | errors: %d | skipped: %d" % (
        report["totals"]["ok"],
        report["totals"]["images_generated"],
        report["totals"]["errors"],
        report["totals"]["skipped_planned"],
    ))
    print("Report: %s" % REPORT_PATH)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
