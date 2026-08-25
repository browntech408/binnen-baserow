"""
generate_detail_images_pipeline.py
===================================
AI-generated detail image pipeline for Binnen / Woonbloq.

Targets products in Baserow Table 742 created after a configurable date
(default: 2026-08-17).  For each qualifying product:

  1. Classifies existing images via GPT-4o Vision (hero / lifestyle / detail).
  2. Processes each image with process_master() and stores results in the
     correct Baserow fields (hero_images / lifestyle_images / detail_image).
  3. If the product has < 2 real detail images, generates 1-3 TRUE detail close-ups
     (material / joinery / base / edge / hardware — NOT full packshots) via fal.ai
     FLUX.2 Pro and stores them in field_7401.
  4. Never auto-publishes to Shopify -- draft/QA gate is fully preserved.

Usage:
  python generate_detail_images_pipeline.py --dry-run --limit 5
  python generate_detail_images_pipeline.py --limit 20 --since 2026-08-17
  python generate_detail_images_pipeline.py --furniture sofa,chair,bed --limit 3
  python generate_detail_images_pipeline.py --row-id 3182 --num-images 2
  python generate_detail_images_pipeline.py --mode detail --engine flux2-pro --limit 3
  python generate_detail_images_pipeline.py --mode packshot --limit 3
  python generate_detail_images_pipeline.py --dry-run --row-id 5630

By default only sofa / chair / bed products are selected (carpets like
Carpetrebel / Janssens Orient / CS Rugs / Brinker are excluded).
Already-generated AI images and products with enough real details are skipped.
Default generation mode is DETAIL close-ups (not full product shots).

Audit log is written to: output/detail_gen_audit.jsonl
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
from fal_image_processor import (
    generate_detail_images,
    process_master,
)

# ----------------------------------------------------------------
# Baserow field IDs -- Table 742
# ----------------------------------------------------------------
TABLE_ID = 742

FIELD_PRODUCT_NAME = "field_7347"
FIELD_PRODUCT_DESC = "field_7348"
FIELD_PRODUCT_IMAGES = "field_7349"
FIELD_HERO_IMAGES = "field_7358"
FIELD_LIFESTYLE_IMAGES = "field_7359"
FIELD_DETAIL_IMAGE = "field_7360"
FIELD_CREATED_ON = "field_7365"
FIELD_IMAGE_CLASSIFICATION = "field_7377"
FIELD_DETAILED_IMAGE_GEN = "field_7401"   # AI-generated detail output
FIELD_PRODUCT_CATEGORY = "field_7363"
FIELD_SUB_CATEGORY = "field_7364"
FIELD_SOURCE_CATEGORY = "field_7368"
FIELD_SOURCE_SUBCATEGORY = "field_7369"
FIELD_BRAND_LINK = "field_7376"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
AUDIT_LOG_PATH = OUTPUT_DIR / "detail_gen_audit.jsonl"

MIN_REAL_DETAIL_IMAGES = 2   # Skip AI gen if product already has this many real details

# Client demo focus: sofa / chair / bed furniture (not carpets, lighting, accessories).
DEFAULT_FURNITURE_TYPES = ("sofa", "chair", "bed")

# Brands that dominate random samples with carpets/rugs — exclude by default.
DEFAULT_EXCLUDE_BRANDS = (
    "carpetrebel",
    "cs rugs",
    "brinker",
    "janssens",
    "janssens orient",
    "janssens oriënt",
)

# Keyword matchers (category + source + name + description). Dutch + EN.
_FURNITURE_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sofa": (
        "bank", "banken", "bankstel", "sofa", "sofas", "couch", "canape", "canapé",
        "hoekbank", "zitbank", "eetkamerbank", "2-zits", "2.5-zits", "3-zits", "3.5-zits",
        "4-zits", "modulaire bank", "lounge sofa",
    ),
    "chair": (
        "stoel", "stoelen", "chair", "chairs", "fauteuil", "fauteuils", "armchair",
        "eetkamerstoel", "barkruk", "lounge chair", "dining chair", "office chair",
        "relaxfauteuil", "draaifauteuil", "poef", "kruk",
    ),
    "bed": (
        "bedden", "boxspring", "boxsprings", "matras", "matrassen",
        "twijfelaar", "tweepersoonsbed", "eenpersoonsbed", "slaapbank",
        "bedframe", "beddenkast",
        # bare "bed" matched carefully via word-ish patterns in matcher
        " bed ", "bed ", " bed",
    ),
}

_CARPET_EXCLUDE_KEYWORDS = (
    "vloerkleed", "vloerkleden", "carpet", "carpets", "karpet", "karpetten",
    "rug", "rugs", "tapijt", "area rug", "eco color", "didim",
)

_NON_FURNITURE_EXCLUDE_KEYWORDS = (
    "verlichting", "lamp", "lamps", "pendant", "sconce", "chandelier",
    "woonaccessoires", "accessories", "accessoires",
)


# ----------------------------------------------------------------
# Image classification (GPT-4o Vision via OpenRouter)
# ----------------------------------------------------------------

def classify_images_batch(image_urls: list, api_key: str) -> list:
    """Classifies a batch of images for the same product at once."""
    if not image_urls:
        return []

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }

    prompt = (
        "You are an expert product image classifier for a high-end furniture store.\n"
        "You are given a set of images for ONE single product.\n"
        "Classify EACH image into EXACTLY ONE of these three categories:\n"
        "1. 'hero': The MAIN, fully visible product shot on a plain, white, studio, or transparent background. "
        "CRITICAL RULE: The image MUST clearly show the full shape of the furniture. "
        "If the image is just a flat wood texture, fabric swatch, close-up material, diagram, or line-drawing, "
        "it is NOT a hero image. There must be ABSOLUTELY NO tables, vases, lamps, or other furniture.\n"
        "2. 'detail': A zoomed-in shot of fabric, wood grain, stitching, legs, OR a schematic diagram/drawing. "
        "CRITICAL RULE: All flat textures, close-ups of wood/fabric, and drawings MUST be 'detail'.\n"
        "3. 'lifestyle': The full product placed in a real-world setting. "
        "CRITICAL RULE: If the product is on a plain background but HAS PROPS it MUST be 'lifestyle'.\n"
        "Respond ONLY with a valid JSON array of strings in the exact same order as the images provided. "
        'For example: ["hero", "detail", "lifestyle"]'
    )

    content = [{"type": "text", "text": prompt}]
    for img_url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": img_url}})

    content[0]["text"] += "\nReturn ONLY a JSON object with a single key 'classifications' containing the array."

    payload = {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        print("  [!] Classification API error: " + resp.text)
        return ["hero"] * len(image_urls)

    try:
        data = resp.json()
        result_text = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(result_text)
        classes = parsed.get("classifications", [])

        final_classes = []
        for i in range(len(image_urls)):
            if i < len(classes):
                c = classes[i].lower()
                if "hero" in c:
                    final_classes.append("hero")
                elif "detail" in c:
                    final_classes.append("detail")
                elif "lifestyle" in c:
                    final_classes.append("lifestyle")
                else:
                    final_classes.append("hero")
            else:
                final_classes.append("hero")
        return final_classes
    except Exception as exc:
        print("  [!] Error parsing classification response: " + str(exc))
        return ["hero"] * len(image_urls)


# ----------------------------------------------------------------
# Baserow file upload helpers
# ----------------------------------------------------------------

def upload_pil_to_baserow(pil_img, filename, settings):
    """Upload a PIL image as a file to Baserow and return the API response dict."""
    buf = io.BytesIO()
    ext = filename.rsplit(".", 1)[-1].upper()
    fmt = "JPEG" if ext == "JPG" else ext
    if fmt == "JPEG" and pil_img.mode in ("RGBA", "P"):
        pil_img = pil_img.convert("RGB")
    pil_img.save(buf, format=fmt, quality=90)
    buf.seek(0)

    url = settings.api_base + "/user-files/upload-file/"
    headers = {"Authorization": "Token " + settings.baserow_token}
    files = {"file": (filename, buf, "image/" + fmt.lower())}

    resp = requests.post(url, headers=headers, files=files, timeout=120)
    resp.raise_for_status()
    return resp.json()


# ----------------------------------------------------------------
# Copy Brand & Product Cloning Helpers
# ----------------------------------------------------------------

def get_or_create_copy_brand(baserow, brand_table_id: int = 745) -> int:
    """Ensure 'Copy' brand exists in the Brands table (Table 745) and return its ID."""
    print("  Checking for 'Copy' brand in Brands table (Table %d)..." % brand_table_id)
    try:
        for row in baserow.list_table_rows(brand_table_id):
            if str(row.get("field_7446") or "").strip().lower() == "copy":
                brand_id = row["id"]
                print("  Found existing 'Copy' brand (ID=%d)" % brand_id)
                return brand_id
    except Exception as exc:
        print("  [!] Error listing brands: %s" % exc)

    new_brand = baserow.create_row(brand_table_id, {"field_7446": "Copy", "field_7447": "Copy"})
    brand_id = new_brand["id"]
    print("  Created new 'Copy' brand (ID=%d)" % brand_id)
    return brand_id


def get_table_read_only_fields(baserow, table_id: int) -> set:
    """Fetch read-only fields for a table so we do not attempt to write to them."""
    try:
        resp = baserow.session.get(baserow._url("/database/fields/table/%d/" % table_id))
        if resp.ok:
            return set("field_%s" % f["id"] for f in resp.json() if f.get("read_only", False))
    except Exception:
        pass
    return {"field_created_on", "field_updated_on", "id", "order"}


def clone_product_row(row: dict, copy_brand_id: int, baserow, read_only_fields: set) -> dict:
    """
    Clone a product row in Table 742, rename to '... - COPY',
    link to the 'Copy' brand (field_7376), and clear generated/classified fields.
    """
    orig_name = str(row.get(FIELD_PRODUCT_NAME) or ("Product %s" % row["id"]))
    new_name = "%s - COPY" % orig_name

    new_row_data = {}
    for k, v in row.items():
        if k.startswith("field_") and k not in read_only_fields:
            if isinstance(v, list):
                cleaned = []
                for item in v:
                    if isinstance(item, dict) and "id" in item:
                        cleaned.append(item["id"])
                    else:
                        cleaned.append(item)
                new_row_data[k] = cleaned
            else:
                new_row_data[k] = v

    # Overrides
    new_row_data[FIELD_PRODUCT_NAME] = new_name
    new_row_data["field_7376"] = [copy_brand_id]  # Link to Copy brand
    new_row_data[FIELD_HERO_IMAGES] = []
    new_row_data[FIELD_LIFESTYLE_IMAGES] = []
    new_row_data[FIELD_DETAIL_IMAGE] = []
    new_row_data[FIELD_DETAILED_IMAGE_GEN] = []

    print("  Cloning product: '%s' -> '%s' (Brand ID: %d)..." % (orig_name, new_name, copy_brand_id))
    new_row = baserow.create_row(TABLE_ID, new_row_data)
    print("  Created copy row with ID: %d" % new_row["id"])
    return new_row


# ----------------------------------------------------------------
# Date filtering
# ----------------------------------------------------------------

def _parse_created_on(value):
    """Parse the Baserow created_on timestamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_created_after(row, cutoff):
    raw = row.get(FIELD_CREATED_ON) or ""
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        return False


def _linked_values(field_val) -> list[str]:
    """Extract display strings from Baserow link_row / select values."""
    if not field_val:
        return []
    if isinstance(field_val, str):
        return [field_val] if field_val.strip() else []
    if isinstance(field_val, dict):
        v = str(field_val.get("value") or field_val.get("name") or "").strip()
        return [v] if v else []
    out: list[str] = []
    if isinstance(field_val, list):
        for item in field_val:
            if isinstance(item, dict):
                v = str(item.get("value") or item.get("name") or "").strip()
                if v:
                    out.append(v)
            elif item:
                out.append(str(item).strip())
    return out


def _row_text_blob(row: dict) -> str:
    """Combined lowercase text used for furniture / carpet matching."""
    parts = [
        str(row.get(FIELD_PRODUCT_NAME) or ""),
        str(row.get(FIELD_PRODUCT_DESC) or ""),
        str(row.get(FIELD_SOURCE_CATEGORY) or ""),
        str(row.get(FIELD_SOURCE_SUBCATEGORY) or ""),
        " ".join(_linked_values(row.get(FIELD_PRODUCT_CATEGORY))),
        " ".join(_linked_values(row.get(FIELD_SUB_CATEGORY))),
        " ".join(_linked_values(row.get(FIELD_BRAND_LINK))),
    ]
    return " ".join(parts).lower()


def _row_brand_names(row: dict) -> list[str]:
    return [b.lower() for b in _linked_values(row.get(FIELD_BRAND_LINK))]


def _is_excluded_brand(row: dict, exclude_brands: list[str]) -> bool:
    if not exclude_brands:
        return False
    brands = _row_brand_names(row)
    for brand in brands:
        for ex in exclude_brands:
            ex_l = ex.lower().strip()
            if not ex_l:
                continue
            if ex_l in brand or brand in ex_l:
                return True
    return False


def _matches_furniture_types(row: dict, furniture_types: list[str]) -> bool:
    """True if product looks like sofa / chair / bed (Baserow cats often empty — use source+name too)."""
    if not furniture_types:
        return True
    blob = " " + _row_text_blob(row) + " "
    # Avoid matching bedtextiel / bedding accessories as beds
    if "bedtextiel" in blob or "bed linen" in blob:
        blob_for_bed = blob.replace("bedtextiel", " ").replace("bed linen", " ")
    else:
        blob_for_bed = blob

    for ftype in furniture_types:
        key = ftype.lower().strip()
        aliases = {
            "sofa": "sofa",
            "bank": "sofa",
            "banken": "sofa",
            "chair": "chair",
            "stoel": "chair",
            "stoelen": "chair",
            "bed": "bed",
            "bedden": "bed",
        }
        canon = aliases.get(key, key)
        keywords = _FURNITURE_TYPE_KEYWORDS.get(canon)
        if not keywords:
            if key in blob:
                return True
            continue
        search_blob = blob_for_bed if canon == "bed" else blob
        for kw in keywords:
            if kw in search_blob:
                return True
        # Exact category hits
        if canon == "sofa" and any(c in search_blob for c in (" banken ", " bank ")):
            return True
        if canon == "chair" and any(c in search_blob for c in (" stoelen ", " stoel ")):
            return True
        if canon == "bed" and " bedden " in search_blob:
            return True
    return False


def _is_carpet_or_non_furniture(row: dict) -> bool:
    blob = _row_text_blob(row)
    if any(kw in blob for kw in _CARPET_EXCLUDE_KEYWORDS):
        return True
    if any(kw in blob for kw in _NON_FURNITURE_EXCLUDE_KEYWORDS) and not _matches_furniture_types(
        row, list(DEFAULT_FURNITURE_TYPES)
    ):
        return True
    # Strong carpet signal: rug brands with empty furniture cats
    src = str(row.get(FIELD_SOURCE_CATEGORY) or "").lower()
    if any(m in src for m in ("wol", "polyester", "viscose", "sisal")) and not _matches_furniture_types(
        row, list(DEFAULT_FURNITURE_TYPES)
    ):
        brand = " ".join(_row_brand_names(row))
        if any(b in brand for b in ("janssens", "carpet", "rug", "brinker")):
            return True
    return False


def _already_has_ai_detail_gen(row: dict) -> bool:
    return bool(row.get(FIELD_DETAILED_IMAGE_GEN))


def _real_detail_count(row: dict) -> int:
    return len(row.get(FIELD_DETAIL_IMAGE) or [])


def _row_is_pipeline_candidate(
    row: dict,
    *,
    furniture_types: list[str],
    exclude_brands: list[str],
    require_furniture: bool,
    skip_existing_gen: bool,
    skip_enough_details: bool,
) -> tuple[bool, str]:
    """
    Decide if a source row should enter the AI packshot pipeline.
    Returns (ok, skip_reason).
    """
    name = str(row.get(FIELD_PRODUCT_NAME) or "")
    if "- COPY" in name:
        return False, "copy_row"
    if not (row.get(FIELD_PRODUCT_IMAGES) or []):
        return False, "no_images"
    if skip_existing_gen and _already_has_ai_detail_gen(row):
        return False, "already_has_ai_gen"
    if skip_enough_details and _real_detail_count(row) >= MIN_REAL_DETAIL_IMAGES:
        return False, "already_has_real_details"
    if _is_excluded_brand(row, exclude_brands):
        return False, "excluded_brand"
    if _is_carpet_or_non_furniture(row):
        return False, "carpet_or_non_furniture"
    if require_furniture and not _matches_furniture_types(row, furniture_types):
        return False, "not_sofa_chair_bed"
    return True, ""


def select_candidate_rows(
    rows: list[dict],
    *,
    furniture_types: list[str],
    exclude_brands: list[str],
    require_furniture: bool,
    skip_existing_gen: bool,
    skip_enough_details: bool,
) -> tuple[list[dict], dict[str, int]]:
    """Filter Baserow rows for client-demo furniture packshots."""
    selected: list[dict] = []
    reasons: dict[str, int] = {}
    for row in rows:
        ok, reason = _row_is_pipeline_candidate(
            row,
            furniture_types=furniture_types,
            exclude_brands=exclude_brands,
            require_furniture=require_furniture,
            skip_existing_gen=skip_existing_gen,
            skip_enough_details=skip_enough_details,
        )
        if ok:
            selected.append(row)
        else:
            reasons[reason] = reasons.get(reason, 0) + 1
    return selected, reasons


# ----------------------------------------------------------------
# Image classification (GPT-4o Vision via OpenRouter)
# ----------------------------------------------------------------
    """Return True if the row was created on or after the cutoff (UTC)."""
    raw = str(row.get(FIELD_CREATED_ON) or "").strip()
    dt = _parse_created_on(raw)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return dt >= cutoff


# ----------------------------------------------------------------
# Audit log
# ----------------------------------------------------------------

def _write_audit_entry(entry):
    """Append one JSONL line to the audit log."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(str(AUDIT_LOG_PATH), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _build_audit_entry(
    product_id, product_name, reference_url, prompt,
    fal_request_ids, generated_filenames, baserow_names,
    skipped_reason, dry_run, classification_summary, real_detail_count
):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "product_id": product_id,
        "product_name": product_name,
        "reference_image_url": reference_url,
        "prompt": prompt,
        "fal_request_ids": fal_request_ids,
        "generated_filenames": generated_filenames,
        "baserow_names": baserow_names,
        "real_detail_count_before_gen": real_detail_count,
        "skipped_reason": skipped_reason,
        "dry_run": dry_run,
        "classification_summary": classification_summary,
    }

def process_product(row, settings, baserow, openrouter_key, num_images, strength, dry_run, engine="flux2-pro", mode="detail"):
    """
    Full pipeline for one product row.

    1. Classify all product_images via GPT-4o Vision.
    2. process_master() each image and upload to correct Baserow field.
    3. If real detail count < MIN_REAL_DETAIL_IMAGES, generate AI images via fal.ai
       (default: TRUE detail close-ups of 2-3 features — not full packshots).
    4. Upload AI images to field_7401 (Detailed_image_gen).

    Returns the audit entry dict.
    """
    row_id = row["id"]
    product_name = str(row.get(FIELD_PRODUCT_NAME) or "Product " + str(row_id))
    product_desc = str(row.get(FIELD_PRODUCT_DESC) or "")
    product_images = row.get(FIELD_PRODUCT_IMAGES) or []

    hero_images_existing = row.get(FIELD_HERO_IMAGES) or []
    lifestyle_images_existing = row.get(FIELD_LIFESTYLE_IMAGES) or []
    detail_images_existing = row.get(FIELD_DETAIL_IMAGE) or []

    print("\n" + "="*60)
    print("[Row %s] %s" % (row_id, product_name))
    print("  Raw images: %d | Hero: %d | Lifestyle: %d | Detail: %d" % (
        len(product_images), len(hero_images_existing),
        len(lifestyle_images_existing), len(detail_images_existing)
    ))

    # Step 1: Classify raw product images
    hero_files = []
    lifestyle_files = []
    detail_files = []
    classification_summary = {"hero": 0, "lifestyle": 0, "detail": 0}

    if product_images:
        image_urls = [img["url"] for img in product_images if img.get("url")]
        print("  Classifying %d images via GPT-4o Vision..." % len(image_urls))

        if dry_run:
            classifications = ["hero"] * len(image_urls)
            print("  [dry-run] Skipping actual classification call.")
        else:
            classifications = classify_images_batch(image_urls, openrouter_key)

        for img_idx, img in enumerate(product_images):
            img_url = img.get("url", "")
            if not img_url:
                continue
            img_class = classifications[img_idx] if img_idx < len(classifications) else "hero"
            classification_summary[img_class] = classification_summary.get(img_class, 0) + 1
            print("    [%d/%d] %s -> %s..." % (
                img_idx + 1, len(product_images), img_class.upper(), img_url[:70]
            ))

            if dry_run:
                continue

            try:
                processed, ext = process_master(img_url, img_class)
                filename = "%s_%s_%d.%s" % (img_class, row_id, img_idx, ext)
                uploaded = upload_pil_to_baserow(processed, filename, settings)
                entry = {"name": uploaded["name"]}
                if img_class == "hero":
                    hero_files.append(entry)
                elif img_class == "lifestyle":
                    lifestyle_files.append(entry)
                elif img_class == "detail":
                    detail_files.append(entry)
            except Exception as exc:
                print("    [!] Error processing image %d: %s" % (img_idx + 1, exc))

    # Step 2: Update Baserow with classified/processed images
    real_detail_count = len(detail_files) + len(detail_images_existing)
    class_text = json.dumps(classification_summary)

    if not dry_run:
        update_payload = {}
        if hero_files:
            update_payload[FIELD_HERO_IMAGES] = list(hero_images_existing) + hero_files
        if lifestyle_files:
            update_payload[FIELD_LIFESTYLE_IMAGES] = list(lifestyle_images_existing) + lifestyle_files
        if detail_files:
            update_payload[FIELD_DETAIL_IMAGE] = list(detail_images_existing) + detail_files
        if classification_summary:
            update_payload[FIELD_IMAGE_CLASSIFICATION] = class_text

        if update_payload:
            print("  Updating row %s with classified images..." % row_id)
            baserow.update_row(TABLE_ID, row_id, update_payload)

    # Step 3: Check if AI detail generation is needed
    if real_detail_count >= MIN_REAL_DETAIL_IMAGES:
        print("  SKIP: already has %d real detail images (threshold=%d)." % (
            real_detail_count, MIN_REAL_DETAIL_IMAGES
        ))
        audit = _build_audit_entry(
            row_id, product_name, "", "", [], [], [],
            "already_has_%d_detail_images" % real_detail_count,
            dry_run, classification_summary, real_detail_count
        )
        _write_audit_entry(audit)
        return audit

    # Step 4: Pick reference image for AI generation
    # Packshot: prefer hero / raw product only (avoid lifestyle — causes bad framing).
    # Macro: hero > lifestyle > raw is fine.
    ref_img = ""
    ref_field = ""

    all_hero = list(hero_files) + [{"url": i.get("url", "")} for i in hero_images_existing]
    all_lifestyle = list(lifestyle_files) + [{"url": i.get("url", "")} for i in lifestyle_images_existing]

    mode_l = (mode or "detail").lower()
    if mode_l == "macro":
        mode_l = "detail"
    if mode_l == "packshot":
        ref_pools = [(all_hero, "hero"), (product_images, "raw")]
    else:
        # Detail close-ups: hero first, then raw, then lifestyle (may show texture/edges)
        ref_pools = [(all_hero, "hero"), (product_images, "raw"), (all_lifestyle, "lifestyle")]

    for pool, label in ref_pools:
        for item in pool:
            url_candidate = item.get("url", "")
            if url_candidate:
                ref_img = url_candidate
                ref_field = label
                break
        if ref_img:
            break

    if not ref_img:
        print("  SKIP: no reference image available for generation.")
        audit = _build_audit_entry(
            row_id, product_name, "", "", [], [], [],
            "no_reference_image", dry_run, classification_summary, real_detail_count
        )
        _write_audit_entry(audit)
        return audit

    reference_url = ref_img
    print("  Reference image (%s): %s..." % (ref_field, reference_url[:80]))
    print("  Generating %d AI image(s) (mode=%s, engine=%s, strength=%.2f, dry_run=%s)..." % (
        num_images, mode_l, engine, strength, dry_run
    ))

    # Prefer hero URLs for multi-ref packshot; include product images as extras.
    prefer_image_urls = []
    for item in all_hero:
        u = item.get("url", "")
        if u and u not in prefer_image_urls:
            prefer_image_urls.append(u)
    for img in product_images:
        u = img.get("url", "")
        if u and u not in prefer_image_urls:
            prefer_image_urls.append(u)

    all_image_urls = list(prefer_image_urls)
    for pool in [lifestyle_images_existing, detail_images_existing]:
        for img_obj in pool:
            u = img_obj.get("url", "")
            if u and u not in all_image_urls:
                all_image_urls.append(u)

    # Category hint for Vision feature selection (sofa/chair/table/rug/lighting logic)
    cat_hint_parts = [
        str(row.get(FIELD_SOURCE_CATEGORY) or ""),
        str(row.get(FIELD_SOURCE_SUBCATEGORY) or ""),
        " ".join(_linked_values(row.get(FIELD_PRODUCT_CATEGORY))),
        " ".join(_linked_values(row.get(FIELD_SUB_CATEGORY))),
    ]
    product_category_hint = " / ".join(p for p in cat_hint_parts if p.strip())

    # Step 5: Generate AI detail close-ups (default) or packshots
    generated_filenames = []
    baserow_names = []
    fal_request_ids = []
    prompt_used = ""
    detail_gen_files = []

    if dry_run:
        print("  [dry-run] Skipping fal.ai generation call.")
    else:
        try:
            results = generate_detail_images(
                reference_url=reference_url,
                product_name=product_name,
                product_description=product_desc,
                openrouter_key=openrouter_key,
                num_images=num_images,
                strength=strength,
                all_image_urls=all_image_urls,
                prefer_image_urls=prefer_image_urls,
                engine=engine,
                mode=mode_l,
                product_category_hint=product_category_hint,
            )

            for var_idx, result_tuple in enumerate(results):
                pil_img, req_id, gen_prompt = result_tuple
                prompt_used = gen_prompt
                filename = "detail_ai_%s_%d.jpg" % (row_id, var_idx)
                try:
                    uploaded = upload_pil_to_baserow(pil_img, filename, settings)
                    generated_filenames.append(filename)
                    baserow_names.append(uploaded["name"])
                    fal_request_ids.append(req_id)
                    detail_gen_files.append({"name": uploaded["name"]})
                    print("    Uploaded %s -> %s" % (filename, uploaded["name"]))
                except Exception as exc:
                    print("    [!] Upload failed for %s: %s" % (filename, exc))

        except Exception as exc:
            print("  [!] Generation failed: %s" % exc)

    # Step 6: Store AI detail images in field_7401 (Detailed_image_gen)
    if detail_gen_files and not dry_run:
        existing_gen = row.get(FIELD_DETAILED_IMAGE_GEN) or []
        new_gen_list = list(existing_gen) + detail_gen_files
        print("  Saving %d AI detail image(s) to Detailed_image_gen (field_7401)..." % len(detail_gen_files))
        baserow.update_row(TABLE_ID, row_id, {FIELD_DETAILED_IMAGE_GEN: new_gen_list})
        print("  Done. Row %s updated." % row_id)

    audit = _build_audit_entry(
        row_id, product_name, reference_url, prompt_used,
        fal_request_ids, generated_filenames, baserow_names,
        None, dry_run, classification_summary, real_detail_count
    )
    _write_audit_entry(audit)
    return audit


# ----------------------------------------------------------------
# CLI
# ----------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "AI detail close-up pipeline for Binnen/Woonbloq. "
            "Generates TRUE detail images (material/joinery/base/edge/hardware close-ups — "
            "not full packshots) for sofa/chair/bed products after --since in Table 742."
        )
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and log without writing to Baserow or calling fal.ai.",
    )
    p.add_argument(
        "--copy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clone each product to the 'Copy' brand (Table 745) before processing (default: True). Use --no-copy to process originals.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max products to process (0 = all qualifying).",
    )
    p.add_argument(
        "--since",
        default="2026-08-17",
        help="Only process products created on or after this date (YYYY-MM-DD). Default: 2026-08-17",
    )
    p.add_argument(
        "--row-id",
        type=int,
        default=None,
        help="Process a single specific Baserow row ID (overrides --since / --limit).",
    )
    p.add_argument(
        "--num-images",
        type=int,
        default=3,
        help="Number of AI image variations to generate per product (default: 3).",
    )
    p.add_argument(
        "--strength",
        type=float,
        default=0.30,
        help="FLUX img2img denoising strength for legacy img2img engine only (0.0-1.0). Default: 0.30",
    )
    p.add_argument(
        "--random",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Randomly sample products from the qualifying date range (default: True). Use --no-random for sequential.",
    )
    p.add_argument(
        "--mode",
        choices=["detail", "macro", "packshot"],
        default="detail",
        help=(
            "Generation mode: 'detail' (default — TRUE feature close-ups: material/joinery/base/edge/hardware) "
            "or 'packshot' (full product). 'macro' is an alias for detail."
        ),
    )
    p.add_argument(
        "--engine",
        choices=["flux2-pro", "flux2-max", "kontext", "img2img", "redux"],
        default="flux2-pro",
        help=(
            "fal.ai engine. Default: flux2-pro. "
            "Packshot: flux2-pro | flux2-max | kontext. "
            "Macro: img2img | redux."
        ),
    )
    p.add_argument(
        "--furniture",
        default="sofa,chair,bed",
        help=(
            "Comma-separated furniture types for client demos (default: sofa,chair,bed). "
            "Matches Baserow category + source category + product name. "
            "Use empty string with --no-require-furniture to disable."
        ),
    )
    p.add_argument(
        "--require-furniture",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Only process sofa/chair/bed-like products (default: True). Use --no-require-furniture for all.",
    )
    p.add_argument(
        "--exclude-brands",
        default=",".join(DEFAULT_EXCLUDE_BRANDS),
        help=(
            "Comma-separated brand name substrings to skip "
            "(default: carpetrebel,cs rugs,brinker,janssens,...)."
        ),
    )
    p.add_argument(
        "--skip-existing-gen",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip products that already have Detailed_image_gen (field_7401). Default: True.",
    )
    p.add_argument(
        "--skip-enough-details",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip products that already have >=2 real detail images (before clone). Default: True.",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between products (rate limiting). Default: 1.0",
    )
    return p.parse_args()


def main():
    import random
    args = parse_args()

    settings = load_settings()
    baserow = BaserowClient(settings)

    openrouter_key = settings.openrouter_api_key
    fal_key = os.getenv("FAL_KEY", "").strip()

    if not openrouter_key:
        print("ERROR: OPENROUTER_API_KEY not set in environment.")
        return 1
    if not fal_key and not args.dry_run:
        print("ERROR: FAL_KEY not set in environment (required unless --dry-run).")
        return 1

    furniture_types = [t.strip().lower() for t in (args.furniture or "").split(",") if t.strip()]
    exclude_brands = [b.strip().lower() for b in (args.exclude_brands or "").split(",") if b.strip()]

    # Print run header
    print("=" * 60)
    print("AI Detail Image Generation Pipeline")
    print("=" * 60)
    print("  dry-run    : %s" % args.dry_run)
    print("  clone/copy : %s (link to 'Copy' brand in Table %d)" % (args.copy, settings.brands_table_id))
    print("  mode       : %s" % args.mode)
    print("  engine     : %s" % args.engine)
    print("  furniture  : %s (require=%s)" % (",".join(furniture_types) or "(any)", args.require_furniture))
    print("  exclude-brands : %s" % (", ".join(exclude_brands) or "(none)"))
    print("  skip-existing-gen : %s" % args.skip_existing_gen)
    print("  skip-enough-details : %s" % args.skip_enough_details)
    print("  random     : %s" % args.random)
    print("  limit      : %s" % (args.limit or "all"))
    print("  since      : %s" % args.since)
    print("  row-id     : %s" % (args.row_id or "batch mode"))
    print("  num-images : %d" % args.num_images)
    print("  strength   : %.2f (img2img only)" % args.strength)
    print("  audit log  : %s" % AUDIT_LOG_PATH)
    print()

    # Fetch Copy Brand and Read-Only fields if cloning
    copy_brand_id = None
    read_only_fields = set()
    if args.copy and not args.dry_run:
        copy_brand_id = get_or_create_copy_brand(baserow, settings.brands_table_id)
        read_only_fields = get_table_read_only_fields(baserow, TABLE_ID)
        print("  Read-only fields identified: %d" % len(read_only_fields))
        print()

    # Fetch qualifying candidate rows
    target_rows = []
    if args.row_id:
        print("Fetching single row %d..." % args.row_id)
        try:
            target_rows = [baserow.get_row(TABLE_ID, args.row_id)]
        except Exception as exc:
            print("ERROR: Could not fetch row %d: %s" % (args.row_id, exc))
            return 1
    else:
        cutoff = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        print("Scanning Table %d for products created on/after %s (UTC)..." % (TABLE_ID, args.since))
        dated = []
        for row in baserow.list_table_rows(TABLE_ID):
            if _row_created_after(row, cutoff):
                dated.append(row)

        print("Date-window products: %d" % len(dated))
        qualifying, skip_reasons = select_candidate_rows(
            dated,
            furniture_types=furniture_types,
            exclude_brands=exclude_brands,
            require_furniture=args.require_furniture,
            skip_existing_gen=args.skip_existing_gen,
            skip_enough_details=args.skip_enough_details,
        )
        print("After furniture/carpet filters: %d candidates" % len(qualifying))
        if skip_reasons:
            print("  Filter skips:")
            for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
                print("    %-28s %d" % (reason, count))

        if not qualifying:
            print("No qualifying sofa/chair/bed products found. Exiting.")
            return 0

        # Sample randomly or take sequentially
        if args.limit and args.limit > 0 and len(qualifying) > args.limit:
            if args.random:
                target_rows = random.sample(qualifying, args.limit)
                print("Randomly sampled %d furniture products from %d candidates:" % (
                    args.limit, len(qualifying)
                ))
            else:
                target_rows = qualifying[: args.limit]
                print("Sequentially selected first %d furniture products:" % args.limit)
        else:
            target_rows = qualifying

        for i, r in enumerate(target_rows):
            brands = ", ".join(_linked_values(r.get(FIELD_BRAND_LINK))) or "?"
            src = str(r.get(FIELD_SOURCE_CATEGORY) or "") or ", ".join(
                _linked_values(r.get(FIELD_PRODUCT_CATEGORY))
            ) or "?"
            print("  [%d] Row ID %s: '%s' | brand=%s | cat=%s" % (
                i + 1, r["id"], r.get(FIELD_PRODUCT_NAME, "Untitled"), brands, src
            ))
        print()

    # Process products
    t0 = time.perf_counter()
    processed = 0
    skipped = 0
    generated = 0
    errors = 0

    for idx, source_row in enumerate(target_rows):
        orig_name = str(source_row.get(FIELD_PRODUCT_NAME) or ("Product %s" % source_row["id"]))
        print("\n" + "=" * 60)
        print("[%d/%d] Processing product: '%s' (Row ID: %s)" % (
            idx + 1, len(target_rows), orig_name, source_row["id"]
        ))

        # Clone product if --copy is enabled
        if args.copy:
            if args.dry_run:
                print("  [dry-run] Would clone '%s' -> '%s - COPY' and link to Brand ID: %s" % (
                    orig_name, orig_name, copy_brand_id or "(Copy Brand)"
                ))
                row_to_process = source_row
            else:
                try:
                    row_to_process = clone_product_row(
                        source_row, copy_brand_id, baserow, read_only_fields
                    )
                except Exception as exc:
                    print("  [!] Failed to clone product row %s: %s" % (source_row["id"], exc))
                    errors += 1
                    continue
        else:
            row_to_process = source_row

        try:
            audit = process_product(
                row=row_to_process,
                settings=settings,
                baserow=baserow,
                openrouter_key=openrouter_key,
                num_images=args.num_images,
                strength=args.strength,
                dry_run=args.dry_run,
                engine=args.engine,
                mode=args.mode,
            )
            processed += 1
            if audit.get("skipped_reason"):
                skipped += 1
            else:
                generated += len(audit.get("generated_filenames") or [])
        except Exception as exc:
            print("  [!] Unhandled error for row %s: %s" % (row_to_process.get("id", "?"), exc))
            errors += 1

        if args.delay > 0:
            time.sleep(args.delay)

    # Summary
    elapsed = round(time.perf_counter() - t0, 1)
    print()
    print("=" * 60)
    print("Pipeline Complete")
    print("=" * 60)
    print("  Products processed               : %d" % processed)
    print("  Skipped (enough detail images)   : %d" % skipped)
    print("  AI detail images generated       : %d" % generated)
    print("  Errors                           : %d" % errors)
    print("  Elapsed                          : %.1fs" % elapsed)
    print("  Audit log                        : %s" % AUDIT_LOG_PATH)
    print()

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
