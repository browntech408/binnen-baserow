"""
Client demo: Image Categorization brand + 50 random product copies.

Creates (or reuses) a Baserow brand named "Image Categorization", picks N random
products that have raw product_images, clones them onto that brand, then runs
high-accuracy AI vision categorization (hero / lifestyle / detail) and writes
processed images into the typed Baserow fields.

Accuracy strategy (same taxonomy as production pipelines):
  1. Per-image GPT-4o Vision with strict rules + confidence score
  2. Auto second pass when confidence < threshold
  3. Adjudication third pass when pass-1 and pass-2 labels disagree
  4. Batch cross-check: if batch label differs, one final adjudication call
  5. process_master() then uploads into hero / lifestyle / detail fields

Usage:
  python demo_image_categorization_brand.py --dry-run
  python demo_image_categorization_brand.py --limit 50
  python demo_image_categorization_brand.py --limit 50 --seed 42
  python demo_image_categorization_brand.py --limit 5 --skip-process   # classify only
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from fal_image_processor import process_master

# ----------------------------------------------------------------
# Constants
# ----------------------------------------------------------------
TABLE_ID = 742
BRAND_NAME = "Image Categorization"
NAME_SUFFIX = " - Image Cat Demo"

FIELD_PRODUCT_NAME = "field_7347"
FIELD_PRODUCT_IMAGES = "field_7349"
FIELD_HERO_IMAGES = "field_7358"
FIELD_LIFESTYLE_IMAGES = "field_7359"
FIELD_DETAIL_IMAGE = "field_7360"
FIELD_IMAGE_CLASSIFICATION = "field_7377"
FIELD_DETAILED_IMAGE_GEN = "field_7401"
FIELD_BRAND_LINK = "field_7376"
FIELD_BRAND_NAME = "field_7446"
FIELD_BRAND_DOMAIN = "field_7447"

LABELS = ("hero", "lifestyle", "detail")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o"
CONFIDENCE_THRESHOLD = 0.80  # stricter than Shopify pipeline (0.72) for client demo

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
REPORT_PATH = OUTPUT_DIR / "image_categorization_demo_report.json"

STRICT_SYSTEM_PROMPT = """\
You are an EXPERT ecommerce product image classifier for a high-end furniture
and interior design store. Classify the image into EXACTLY ONE category.

CATEGORIES:
  hero      - Clean packshot. The product is FULLY VISIBLE on a plain, white,
              studio, off-white, or transparent/removed background. No room
              context whatsoever. Props like vases or lamps that are clearly
              in front of the product count as lifestyle, not hero.

  lifestyle - Product shown in a real room scene OR styled/ambient interior
              setting. ANY visible floor, wall, ceiling, rug, curtain, or
              decorative object (vase, lamp, artwork, plant) means lifestyle.
              If background is plain but there are props AT THE SAME LEVEL as
              the product (side table, plant, lamp next to the sofa) -> lifestyle.

  detail    - Close-up or cropped shot focusing on material / fabric texture /
              stitching / wood grain / hardware / feet / legs / edge profile /
              mechanism. Also includes technical diagrams or dimension drawings.
              The full product silhouette is NOT visible in a detail shot.

STRICT RULES (apply in order):
  1. If any room, interior scene, or decorative prop is visible -> lifestyle.
  2. If the crop is tight and does NOT show the full product silhouette -> detail.
  3. Otherwise -> hero.
  4. NEVER classify a flat texture swatch or fabric close-up as hero.
  5. NEVER classify a full-product studio shot with a plain background as lifestyle.

CONFIDENCE: Be honest. Assign 0.95+ only when you are absolutely certain.

RESPOND with ONLY valid JSON (no markdown fences, no explanation):
{"label":"hero"|"lifestyle"|"detail","confidence":0.0-1.0,"reason":"one sentence"}
"""

BATCH_PROMPT = """\
You are an expert product image classifier for a high-end furniture store.
You are given a set of images for ONE single product.
Classify EACH image into EXACTLY ONE of: hero, lifestyle, detail.

Rules:
- hero: full product, plain/studio/transparent background, no props/room.
- lifestyle: room scene OR props next to the product.
- detail: close-up of material/joinery/legs/hardware OR diagram; not full silhouette.

Return ONLY JSON: {"classifications":["hero"|"lifestyle"|"detail", ...]}
in the exact same order as the images.
"""


# ----------------------------------------------------------------
# Baserow helpers
# ----------------------------------------------------------------

def get_or_create_demo_brand(baserow: BaserowClient, brand_table_id: int) -> int:
    """Ensure brand 'Image Categorization' exists; return its row id."""
    print("Checking Brands table %d for '%s'..." % (brand_table_id, BRAND_NAME))
    for row in baserow.list_table_rows(brand_table_id):
        name = str(row.get(FIELD_BRAND_NAME) or "").strip()
        if name.lower() == BRAND_NAME.lower():
            print("  Found existing brand id=%d" % row["id"])
            return int(row["id"])

    created = baserow.create_row(
        brand_table_id,
        {
            FIELD_BRAND_NAME: BRAND_NAME,
            FIELD_BRAND_DOMAIN: "image-categorization-demo",
        },
    )
    print("  Created brand '%s' id=%d" % (BRAND_NAME, created["id"]))
    return int(created["id"])


def get_table_read_only_fields(baserow: BaserowClient, table_id: int) -> set[str]:
    try:
        resp = baserow.session.get(
            baserow._url("/database/fields/table/%d/" % table_id), timeout=60
        )
        if resp.ok:
            return {
                "field_%s" % f["id"]
                for f in resp.json()
                if f.get("read_only", False)
            }
    except Exception as exc:
        print("WARN: could not load read-only fields: %s" % exc)
    return {"field_created_on", "field_updated_on", "id", "order"}


def _clean_field_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "name" in value and "url" in value:
            return {"name": value["name"]}
        if "id" in value:
            return value["id"]
        return value
    if not isinstance(value, list):
        return value
    cleaned = []
    for item in value:
        if isinstance(item, dict):
            if "name" in item and ("url" in item or "size" in item or "mime_type" in item):
                cleaned.append({"name": item["name"]})
            elif "id" in item:
                cleaned.append(item["id"])
            else:
                cleaned.append(item)
        else:
            cleaned.append(item)
    return cleaned


def clone_product_row(
    row: dict,
    brand_id: int,
    baserow: BaserowClient,
    read_only_fields: set[str],
) -> dict:
    """Clone product, clear typed image fields, link to demo brand."""
    orig_name = str(row.get(FIELD_PRODUCT_NAME) or ("Product %s" % row["id"]))
    if orig_name.endswith(NAME_SUFFIX):
        new_name = orig_name
    else:
        new_name = "%s%s" % (orig_name, NAME_SUFFIX)

    new_row_data: dict[str, Any] = {}
    for k, v in row.items():
        if k.startswith("field_") and k not in read_only_fields:
            new_row_data[k] = _clean_field_value(v)

    new_row_data[FIELD_PRODUCT_NAME] = new_name
    new_row_data[FIELD_BRAND_LINK] = [brand_id]
    # Fresh categorization targets — do not copy prior AI results
    new_row_data[FIELD_HERO_IMAGES] = []
    new_row_data[FIELD_LIFESTYLE_IMAGES] = []
    new_row_data[FIELD_DETAIL_IMAGE] = []
    new_row_data[FIELD_DETAILED_IMAGE_GEN] = []
    new_row_data[FIELD_IMAGE_CLASSIFICATION] = ""

    print("  Cloning '%s' -> brand %d ..." % (orig_name[:70], brand_id))
    new_row = baserow.create_row(TABLE_ID, new_row_data)
    print("  Created copy row id=%d" % new_row["id"])
    return new_row


def upload_pil_to_baserow(pil_img: Image.Image, filename: str, settings) -> dict:
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
# Candidate selection
# ----------------------------------------------------------------

def _is_demo_or_copy_name(name: str) -> bool:
    upper = name.upper()
    return (
        NAME_SUFFIX.upper() in upper
        or " - COPY" in upper
        or upper.endswith("- COPY")
        or "IMAGE CAT DEMO" in upper
    )


def _linked_ids(field_val) -> list[int]:
    if not field_val:
        return []
    ids = []
    if isinstance(field_val, list):
        for item in field_val:
            if isinstance(item, dict) and "id" in item:
                ids.append(int(item["id"]))
            elif isinstance(item, int):
                ids.append(item)
    return ids


def collect_candidates(
    baserow: BaserowClient,
    *,
    demo_brand_id: int | None,
    min_images: int,
) -> list[dict]:
    """All products with enough raw images, excluding prior demos/copies."""
    candidates: list[dict] = []
    scanned = 0
    skipped_no_images = 0
    skipped_copy = 0
    skipped_demo_brand = 0

    print("Scanning products table %d ..." % TABLE_ID)
    for row in baserow.list_table_rows(TABLE_ID):
        scanned += 1
        if scanned % 500 == 0:
            print(
                "  ...scanned %d | candidates %d | skip images=%d copy=%d brand=%d"
                % (scanned, len(candidates), skipped_no_images, skipped_copy, skipped_demo_brand)
            )

        name = str(row.get(FIELD_PRODUCT_NAME) or "")
        if _is_demo_or_copy_name(name):
            skipped_copy += 1
            continue

        if demo_brand_id and demo_brand_id in _linked_ids(row.get(FIELD_BRAND_LINK)):
            skipped_demo_brand += 1
            continue

        images = row.get(FIELD_PRODUCT_IMAGES) or []
        if not isinstance(images, list) or len(images) < min_images:
            skipped_no_images += 1
            continue
        urls = [img.get("url") for img in images if isinstance(img, dict) and img.get("url")]
        if len(urls) < min_images:
            skipped_no_images += 1
            continue

        candidates.append(row)

    print(
        "Scan done: %d rows | %d eligible | skipped no_images=%d copy=%d demo_brand=%d"
        % (scanned, len(candidates), skipped_no_images, skipped_copy, skipped_demo_brand)
    )
    return candidates


def pick_random(candidates: list[dict], limit: int, seed: int | None) -> list[dict]:
    if seed is not None:
        random.seed(seed)
    if len(candidates) <= limit:
        picked = list(candidates)
        random.shuffle(picked)
        return picked
    return random.sample(candidates, limit)


# ----------------------------------------------------------------
# High-accuracy AI classification
# ----------------------------------------------------------------

def _parse_ai_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    label = str(data.get("label", "")).strip().lower()
    if label not in LABELS:
        raise ValueError("Invalid label: %r" % label)
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "label": label,
        "confidence": conf,
        "reason": str(data.get("reason", "")).strip(),
    }


def _image_url_to_data_url(image_url: str, timeout: float = 60.0) -> str:
    """Download image and encode as data URL so vision models can read blocked CDN URLs."""
    import base64

    resp = requests.get(image_url, timeout=timeout)
    resp.raise_for_status()
    content_type = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        content_type = "image/jpeg"
    b64 = base64.b64encode(resp.content).decode("ascii")
    return "data:%s;base64,%s" % (content_type, b64)


def _call_openrouter_once(
    *,
    image_ref: str,
    product_title: str,
    api_key: str,
    model: str,
    timeout: float = 90.0,
    extra_user_text: str = "",
) -> dict[str, Any]:
    user_text = "Product: %s\nClassify this image strictly per the rules." % product_title
    if extra_user_text:
        user_text += "\n" + extra_user_text

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://binnen.local",
            "X-Title": "Binnen image categorization demo",
        },
        json={
            "model": model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": STRICT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": image_ref}},
                    ],
                },
            ],
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError("OpenRouter HTTP %s: %s" % (resp.status_code, resp.text[:400]))
    payload = resp.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("No choices in response: %s" % payload)
    content = choices[0].get("message", {}).get("content", "")
    if not (content or "").strip():
        raise ValueError("Empty model content")
    return _parse_ai_json(content)


def _call_openrouter_single(
    *,
    image_url: str,
    product_title: str,
    api_key: str,
    model: str,
    timeout: float = 90.0,
    extra_user_text: str = "",
) -> dict[str, Any]:
    """
    Robust single-image call:
      1) URL + primary model (retry)
      2) base64 data URL + primary model
      3) alternate vision models
    """
    last_err: Exception | None = None
    attempts: list[tuple[str, str]] = [
        (image_url, model),
        (image_url, model),
    ]
    try:
        data_url = _image_url_to_data_url(image_url)
        attempts.append((data_url, model))
        for alt in ("openai/gpt-4o", "google/gemini-2.5-flash", "openai/gpt-4o-mini"):
            if alt != model:
                attempts.append((data_url, alt))
    except Exception as exc:
        last_err = exc

    for idx, (ref, mdl) in enumerate(attempts, 1):
        try:
            if idx > 1:
                time.sleep(0.6)
            return _call_openrouter_once(
                image_ref=ref,
                product_title=product_title,
                api_key=api_key,
                model=mdl,
                timeout=timeout,
                extra_user_text=extra_user_text,
            )
        except Exception as exc:
            last_err = exc
            continue

    raise RuntimeError("All classification attempts failed: %s" % last_err)


def classify_image_accurate(
    *,
    image_url: str,
    product_title: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    """
    Two-pass + disagreement adjudication.
    Prefer higher confidence; never invent labels outside LABELS.
    """
    first = _call_openrouter_single(
        image_url=image_url,
        product_title=product_title,
        api_key=api_key,
        model=model,
    )
    passes = [first]
    if first["confidence"] >= CONFIDENCE_THRESHOLD:
        first["passes"] = 1
        first["method"] = "single_high_confidence"
        return first

    try:
        second = _call_openrouter_single(
            image_url=image_url,
            product_title=product_title,
            api_key=api_key,
            model=model,
        )
        passes.append(second)
    except Exception as exc:
        first["passes"] = 1
        first["method"] = "single_retry_failed"
        first["retry_error"] = str(exc)
        return first

    if first["label"] == second["label"]:
        winner = second if second["confidence"] > first["confidence"] else first
        winner["passes"] = 2
        winner["method"] = "two_pass_agree"
        return winner

    # Label disagreement — adjudication with both candidates named
    try:
        third = _call_openrouter_single(
            image_url=image_url,
            product_title=product_title,
            api_key=api_key,
            model=model,
            extra_user_text=(
                "Previous passes disagreed: %s (%.2f) vs %s (%.2f). "
                "Pick the single correct label carefully."
                % (first["label"], first["confidence"], second["label"], second["confidence"])
            ),
        )
        passes.append(third)
        third["passes"] = 3
        third["method"] = "adjudication"
        third["prior"] = [
            {"label": p["label"], "confidence": p["confidence"]} for p in passes[:2]
        ]
        return third
    except Exception:
        winner = second if second["confidence"] > first["confidence"] else first
        winner["passes"] = 2
        winner["method"] = "two_pass_disagree_no_adjudication"
        return winner


def classify_batch(
    image_urls: list[str],
    api_key: str,
    model: str,
) -> list[str]:
    if not image_urls:
        return []
    content: list[dict] = [{"type": "text", "text": BATCH_PROMPT}]
    for img_url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": img_url}})

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://binnen.local",
            "X-Title": "Binnen image categorization demo",
        },
        json={
            "model": model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}],
        },
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError("Batch classify HTTP %s: %s" % (resp.status_code, resp.text[:300]))

    data = resp.json()
    result_text = data["choices"][0]["message"]["content"].strip()
    parsed = json.loads(result_text)
    classes = parsed.get("classifications", [])
    out: list[str] = []
    for i in range(len(image_urls)):
        if i < len(classes):
            c = str(classes[i]).lower()
            if "hero" in c:
                out.append("hero")
            elif "detail" in c:
                out.append("detail")
            elif "lifestyle" in c:
                out.append("lifestyle")
            else:
                out.append("")
        else:
            out.append("")
    return out


def classify_product_images(
    *,
    image_urls: list[str],
    product_title: str,
    api_key: str,
    model: str,
) -> list[dict[str, Any]]:
    """
    Per-image strict classify + batch cross-check.
    On mismatch, run one final adjudication; result is always one of LABELS.
    """
    print("  Per-image accurate classify (%d images, model=%s)..." % (len(image_urls), model))
    per_image: list[dict[str, Any]] = []
    for idx, url in enumerate(image_urls):
        try:
            result = classify_image_accurate(
                image_url=url,
                product_title=product_title,
                api_key=api_key,
                model=model,
            )
        except Exception as exc:
            print("    [%d] ERROR classify: %s — defaulting to lifestyle (safe)" % (idx + 1, exc))
            # Safer default than hero: avoids false packshots for client demo
            result = {
                "label": "lifestyle",
                "confidence": 0.0,
                "reason": "classification_error: %s" % exc,
                "method": "error_fallback",
                "passes": 0,
            }
        per_image.append(result)
        print(
            "    [%d/%d] %s conf=%.2f (%s) — %s"
            % (
                idx + 1,
                len(image_urls),
                result["label"].upper(),
                result["confidence"],
                result.get("method", "?"),
                (result.get("reason") or "")[:80],
            )
        )
        time.sleep(0.15)

    # Batch cross-check
    try:
        batch_labels = classify_batch(image_urls, api_key, model)
        print("  Batch cross-check: %s" % batch_labels)
    except Exception as exc:
        print("  [!] Batch cross-check failed (keeping per-image): %s" % exc)
        batch_labels = [""] * len(image_urls)

    final: list[dict[str, Any]] = []
    for idx, (url, per) in enumerate(zip(image_urls, per_image)):
        batch = batch_labels[idx] if idx < len(batch_labels) else ""
        entry = dict(per)
        entry["url"] = url
        entry["batch_label"] = batch or None
        entry["index"] = idx

        if batch and batch != per["label"]:
            print(
                "    [%d] MISMATCH per=%s vs batch=%s — adjudicating..."
                % (idx + 1, per["label"], batch)
            )
            try:
                adj = _call_openrouter_single(
                    image_url=url,
                    product_title=product_title,
                    api_key=api_key,
                    model=model,
                    extra_user_text=(
                        "Two classifiers disagreed: per-image=%s (conf %.2f), "
                        "batch=%s. Choose the single correct label."
                        % (per["label"], per["confidence"], batch)
                    ),
                )
                entry["label"] = adj["label"]
                entry["confidence"] = adj["confidence"]
                entry["reason"] = adj.get("reason") or entry.get("reason")
                entry["method"] = "batch_mismatch_adjudication"
                entry["per_image_label"] = per["label"]
                print(
                    "    [%d] adjudicated -> %s conf=%.2f"
                    % (idx + 1, adj["label"].upper(), adj["confidence"])
                )
            except Exception as exc:
                print("    [%d] adjudication failed (%s) — keeping per-image" % (idx + 1, exc))
                entry["method"] = "batch_mismatch_kept_per_image"

        if entry["label"] not in LABELS:
            entry["label"] = "lifestyle"
        final.append(entry)

    return final


# ----------------------------------------------------------------
# Process + write Baserow
# ----------------------------------------------------------------

def categorize_copy_row(
    *,
    row: dict,
    baserow: BaserowClient,
    settings,
    openrouter_key: str,
    model: str,
    dry_run: bool,
    skip_process: bool,
) -> dict[str, Any]:
    row_id = row["id"]
    product_name = str(row.get(FIELD_PRODUCT_NAME) or ("Product %s" % row_id))
    product_images = row.get(FIELD_PRODUCT_IMAGES) or []
    image_urls = [
        img["url"]
        for img in product_images
        if isinstance(img, dict) and img.get("url")
    ]

    print("\n" + "=" * 64)
    print("[Row %s] %s" % (row_id, product_name[:80]))
    print("  Raw images: %d" % len(image_urls))

    if not image_urls:
        return {
            "row_id": row_id,
            "name": product_name,
            "status": "skipped_no_images",
            "classifications": [],
            "summary": {"hero": 0, "lifestyle": 0, "detail": 0},
        }

    if dry_run:
        print("  [dry-run] Would classify %d images (no AI / no writes)." % len(image_urls))
        return {
            "row_id": row_id,
            "name": product_name,
            "status": "dry_run",
            "image_count": len(image_urls),
            "classifications": [],
            "summary": {"hero": 0, "lifestyle": 0, "detail": 0},
        }

    classifications = classify_product_images(
        image_urls=image_urls,
        product_title=product_name,
        api_key=openrouter_key,
        model=model,
    )

    summary = {"hero": 0, "lifestyle": 0, "detail": 0}
    for c in classifications:
        summary[c["label"]] = summary.get(c["label"], 0) + 1

    hero_files: list[dict] = []
    lifestyle_files: list[dict] = []
    detail_files: list[dict] = []
    process_errors: list[dict] = []

    if skip_process:
        print("  --skip-process: writing classification summary only (no image processing).")
    else:
        print("  Processing + uploading classified images...")
        for c in classifications:
            img_class = c["label"]
            img_url = c["url"]
            idx = c["index"]
            try:
                processed, ext = process_master(img_url, img_class)
                filename = "%s_%s_%d.%s" % (img_class, row_id, idx, ext)
                uploaded = upload_pil_to_baserow(processed, filename, settings)
                entry = {"name": uploaded["name"]}
                if img_class == "hero":
                    hero_files.append(entry)
                elif img_class == "lifestyle":
                    lifestyle_files.append(entry)
                else:
                    detail_files.append(entry)
                c["uploaded_name"] = uploaded["name"]
                print("    uploaded %s -> %s" % (filename, uploaded["name"]))
            except Exception as exc:
                print("    [!] process failed img %d (%s): %s" % (idx + 1, img_class, exc))
                process_errors.append({"index": idx, "label": img_class, "error": str(exc)})

    class_payload = {
        "hero": summary["hero"],
        "lifestyle": summary["lifestyle"],
        "detail": summary["detail"],
        "images": [
            {
                "index": c["index"],
                "label": c["label"],
                "confidence": round(float(c.get("confidence") or 0), 3),
                "method": c.get("method"),
                "reason": c.get("reason"),
                "batch_label": c.get("batch_label"),
            }
            for c in classifications
        ],
    }

    update_payload = {
        FIELD_IMAGE_CLASSIFICATION: json.dumps(class_payload, ensure_ascii=False),
        FIELD_HERO_IMAGES: hero_files,
        FIELD_LIFESTYLE_IMAGES: lifestyle_files,
        FIELD_DETAIL_IMAGE: detail_files,
    }
    print(
        "  Writing Baserow: hero=%d lifestyle=%d detail=%d"
        % (len(hero_files), len(lifestyle_files), len(detail_files))
    )
    baserow.update_row(TABLE_ID, row_id, update_payload)

    return {
        "row_id": row_id,
        "name": product_name,
        "status": "ok" if not process_errors else "ok_with_process_errors",
        "summary": summary,
        "classifications": class_payload["images"],
        "process_errors": process_errors,
        "uploaded": {
            "hero": len(hero_files),
            "lifestyle": len(lifestyle_files),
            "detail": len(detail_files),
        },
    }


# ----------------------------------------------------------------
# CLI
# ----------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Create 'Image Categorization' brand, clone N random Baserow products, "
            "run high-accuracy AI image categorization for client demo."
        )
    )
    p.add_argument("--limit", type=int, default=50, help="How many products to clone (default 50)")
    p.add_argument("--min-images", type=int, default=2, help="Min raw product_images required")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducible sample")
    p.add_argument("--dry-run", action="store_true", help="Select sample only; no brand/clone/AI writes")
    p.add_argument(
        "--classify-only-existing",
        action="store_true",
        help="Skip clone; categorize existing rows already linked to the demo brand",
    )
    p.add_argument(
        "--skip-process",
        action="store_true",
        help="Classify + write summary only; skip fal resize/BG removal/upload",
    )
    p.add_argument(
        "--model",
        default=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        help="OpenRouter vision model (default openai/gpt-4o)",
    )
    p.add_argument(
        "--brand-name",
        default=BRAND_NAME,
        help="Brand display name (default: Image Categorization)",
    )
    return p.parse_args()


def main() -> int:
    global BRAND_NAME
    args = parse_args()
    BRAND_NAME = args.brand_name.strip() or BRAND_NAME

    settings = load_settings()
    if not settings.openrouter_api_key and not args.dry_run:
        print("ERROR: OPENROUTER_API_KEY missing.")
        return 1
    if not args.dry_run and not args.skip_process and not os.getenv("FAL_KEY"):
        print("ERROR: FAL_KEY missing (needed for hero BG removal). Use --skip-process to classify only.")
        return 1

    baserow = BaserowClient(settings)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "brand_name": BRAND_NAME,
        "limit": args.limit,
        "seed": args.seed,
        "model": args.model,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "dry_run": args.dry_run,
        "skip_process": args.skip_process,
        "products": [],
        "errors": [],
    }

    # --- Brand ---
    brand_id = None
    if args.dry_run and not args.classify_only_existing:
        print("[dry-run] Would create/reuse brand '%s'" % BRAND_NAME)
    else:
        brand_id = get_or_create_demo_brand(baserow, settings.brands_table_id)
        report["brand_id"] = brand_id

    # --- Select / clone ---
    if args.classify_only_existing:
        if brand_id is None:
            brand_id = get_or_create_demo_brand(baserow, settings.brands_table_id)
            report["brand_id"] = brand_id
        print("Loading existing products linked to brand id=%d ..." % brand_id)
        rows_to_process = []
        for row in baserow.list_table_rows(TABLE_ID):
            if brand_id in _linked_ids(row.get(FIELD_BRAND_LINK)):
                rows_to_process.append(row)
                if len(rows_to_process) >= args.limit:
                    break
        print("Found %d products on demo brand (processing up to %d)." % (
            len(rows_to_process), args.limit
        ))
        source_map = {r["id"]: None for r in rows_to_process}
    else:
        # Peek existing brand id for exclusion (without creating in dry-run)
        existing_brand_id = None
        for brow in baserow.list_table_rows(settings.brands_table_id):
            if str(brow.get(FIELD_BRAND_NAME) or "").strip().lower() == BRAND_NAME.lower():
                existing_brand_id = int(brow["id"])
                break

        candidates = collect_candidates(
            baserow,
            demo_brand_id=existing_brand_id,
            min_images=args.min_images,
        )
        if not candidates:
            print("No eligible products found.")
            return 1

        picked = pick_random(candidates, args.limit, args.seed)
        print("\nSelected %d random products:" % len(picked))
        for i, row in enumerate(picked, 1):
            n_img = len(row.get(FIELD_PRODUCT_IMAGES) or [])
            print("  [%d] id=%d | %d imgs | %s" % (
                i, row["id"], n_img, str(row.get(FIELD_PRODUCT_NAME) or "")[:70]
            ))

        report["selected_source_ids"] = [r["id"] for r in picked]

        if args.dry_run:
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("\nDry-run complete. Report: %s" % REPORT_PATH)
            print("Re-run without --dry-run to create brand, clones, and categorize.")
            return 0

        read_only = get_table_read_only_fields(baserow, TABLE_ID)
        rows_to_process = []
        source_map = {}
        print("\nCloning %d products onto brand '%s' (id=%d)..." % (
            len(picked), BRAND_NAME, brand_id
        ))
        for i, src in enumerate(picked, 1):
            print("\n[%d/%d] source id=%d" % (i, len(picked), src["id"]))
            try:
                # Re-fetch fresh row to avoid stale file refs
                fresh = baserow.get_row(TABLE_ID, src["id"])
                copy_row = clone_product_row(fresh, brand_id, baserow, read_only)
                rows_to_process.append(copy_row)
                source_map[copy_row["id"]] = src["id"]
            except Exception as exc:
                print("  ERROR cloning %d: %s" % (src["id"], exc))
                report["errors"].append({"source_id": src["id"], "stage": "clone", "error": str(exc)})

    # --- Categorize ---
    print("\n" + "#" * 64)
    print("AI IMAGE CATEGORIZATION on %d copy products" % len(rows_to_process))
    print("#" * 64)

    for i, row in enumerate(rows_to_process, 1):
        print("\n>>> Product %d/%d" % (i, len(rows_to_process)))
        try:
            # Re-fetch after clone so file URLs are present
            fresh = baserow.get_row(TABLE_ID, row["id"])
            result = categorize_copy_row(
                row=fresh,
                baserow=baserow,
                settings=settings,
                openrouter_key=settings.openrouter_api_key,
                model=args.model,
                dry_run=False,
                skip_process=args.skip_process,
            )
            result["source_id"] = source_map.get(row["id"])
            report["products"].append(result)
        except Exception as exc:
            print("  FATAL on row %s: %s" % (row.get("id"), exc))
            report["errors"].append({
                "copy_id": row.get("id"),
                "source_id": source_map.get(row["id"]),
                "stage": "categorize",
                "error": str(exc),
            })

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["totals"] = {
        "cloned_or_loaded": len(rows_to_process),
        "categorized_ok": sum(1 for p in report["products"] if str(p.get("status", "")).startswith("ok")),
        "errors": len(report["errors"]),
        "label_counts": {"hero": 0, "lifestyle": 0, "detail": 0},
    }
    for p in report["products"]:
        for k, v in (p.get("summary") or {}).items():
            if k in report["totals"]["label_counts"]:
                report["totals"]["label_counts"][k] += int(v or 0)

    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n=== DONE ===")
    print("Brand: %s (id=%s)" % (BRAND_NAME, report.get("brand_id")))
    print("Products processed: %d" % report["totals"]["cloned_or_loaded"])
    print("Categorized OK: %d" % report["totals"]["categorized_ok"])
    print("Label totals: %s" % report["totals"]["label_counts"])
    if report["errors"]:
        print("Errors: %d (see report)" % len(report["errors"]))
    print("Report: %s" % REPORT_PATH)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
