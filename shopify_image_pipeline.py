"""
Shopify Product Image Categorization & Processing Pipeline
==========================================================

PIPELINE STEPS
  Step 1  Fetch products + images from Shopify (all products or specific ID/IDs).
  Step 2  AI categorization via OpenRouter vision (hero / lifestyle / detail).
          Strict two-pass classification with confidence threshold + fallback.
  Step 3  Resize each image per its AI-assigned category:
            hero:      1000 x 880  px   PNG  transparent canvas  (1.14:1)
            lifestyle: 1760 x 1100 px   JPEG keep background     (16:10)
            detail:    1760 x 1100 px   JPEG keep background     (16:10)
  Step 4  Background removal verified / preserved (fal.ai rembg).
          Hero images must be transparent -- BG removed if not already.
          Lifestyle/detail images keep their background EXACTLY as-is.
  Step 5  Products with NO hero image (only lifestyle, detail, or mix)
          have their Shopify status changed directly (e.g. to "unlisted")
          without tags or metafields.

USAGE
  # Dry-run (default) -- classify locally, nothing written to Shopify:
  python shopify_image_pipeline.py --dry-run

  # Full run for all products:
  python shopify_image_pipeline.py --apply

  # Single product by ID:
  python shopify_image_pipeline.py --apply --product-id 8234567890123

  # Multiple products by comma/space-separated IDs:
  python shopify_image_pipeline.py --apply --product-ids 8234567890123,8234567890124,8234567890125

  # Active products only:
  python shopify_image_pipeline.py --apply --status active

  # Test with a small batch:
  python shopify_image_pipeline.py --apply --limit 5

  # Specify status to set for products without hero image (default: unlisted):
  python shopify_image_pipeline.py --apply --no-hero-status unlisted

  # Use a different AI model:
  python shopify_image_pipeline.py --apply --model google/gemini-2.5-flash

  # Classify + status change only (skip image resize/upload):
  python shopify_image_pipeline.py --apply --skip-resize

  # Resume from a prior report (skip fetch + classify):
  python shopify_image_pipeline.py --apply --from-report output/shopify_image_pipeline_report.json

REQUIRED ENV VARS
  SHOPIFY_SHOP, SHOPIFY_ACCESS_TOKEN (or CLIENT_ID + CLIENT_SECRET)
  OPENROUTER_API_KEY
  FAL_KEY              (hero BG removal via fal.ai; optional -- skipped if missing)

OPTIONAL ENV VARS
  PIPELINE_WORKERS=8               parallel image download/resize threads
  PIPELINE_AI_WORKERS=3            parallel AI calls (keep low vs rate limits)
  HTTP_TIMEOUT=30
  OPENROUTER_MODEL=google/gemini-2.5-flash
  NO_HERO_STATUS=unlisted          status for products missing a hero image
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash").strip()

LABELS = ("hero", "lifestyle", "detail")

# If AI confidence is below this threshold a second pass is made.
# The result with higher confidence wins.
CONFIDENCE_THRESHOLD = 0.72

# Target pixel dimensions per category (width, height)
CATEGORY_DIMS: dict[str, tuple[int, int]] = {
    "hero":      (1000, 880),    # 1.14:1
    "lifestyle": (1760, 1100),   # 16:10
    "detail":    (1760, 1100),   # 16:10
}

# Output file format per category
CATEGORY_FORMAT: dict[str, str] = {
    "hero":      "png",
    "lifestyle": "jpeg",
    "detail":    "jpeg",
}

# Target status for products with no hero image (Step 5)
DEFAULT_NO_HERO_STATUS = os.getenv(
    "NO_HERO_STATUS", os.getenv("LIFESTYLE_STATUS", "unlisted")
).strip()
DEFAULT_LIFESTYLE_STATUS = DEFAULT_NO_HERO_STATUS

# Padding applied when placing a product cutout on the hero canvas
HERO_MARGIN_RATIO = 0.85

# ---------------------------------------------------------------------------
# Strict AI classification prompt (furniture / interior focused)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ImageRecord:
    """Single Shopify product image -- carries its full lifecycle state."""
    product_id: int
    product_title: str
    shopify_image_id: int
    position: int
    src_url: str
    # After AI classification
    label: str = ""
    confidence: float = 0.0
    reason: str = ""
    classification_error: str = ""
    # After resize (bytes cleared from memory after successful upload)
    resized_bytes: bytes = field(default_factory=bytes, repr=False)
    resize_error: str = ""
    resized_dims: tuple[int, int] = (0, 0)
    # After Shopify upload
    uploaded: bool = False
    upload_error: str = ""


@dataclass
class ProductRecord:
    """Shopify product with aggregated image results."""
    product_id: int
    title: str
    handle: str
    shopify_status: str
    vendor: str
    created_at: str
    images: list[ImageRecord] = field(default_factory=list)
    # After Step 5
    has_hero: bool = False
    is_missing_hero: bool = False
    is_lifestyle_only: bool = False
    status_updated: bool = False
    new_shopify_status: str = ""
    status_error: str = ""


# ---------------------------------------------------------------------------
# Step 2: AI classification
# ---------------------------------------------------------------------------

def _parse_ai_json(text: str) -> dict[str, Any]:
    """Extract and validate the JSON classification object from AI output."""
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
        raise ValueError(f"Invalid label: {label!r}")
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "label": label,
        "confidence": conf,
        "reason": str(data.get("reason", "")).strip(),
    }


def _call_openrouter(
    *,
    image_url: str,
    product_title: str,
    api_key: str,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    """Single OpenRouter vision API call. Returns parsed label/confidence/reason dict."""
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://binnen.local",
            "X-Title": "Binnen image pipeline",
        },
        json={
            "model": model,
            "temperature": 0.0,  # deterministic output -- critical for strict classification
            "messages": [
                {"role": "system", "content": STRICT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Product: {product_title}\n"
                                "Classify this image strictly per the rules."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:400]}")
    payload = resp.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError(f"No choices in response: {payload}")
    content = choices[0].get("message", {}).get("content", "")
    return _parse_ai_json(content)


def classify_image_strict(
    *,
    image_url: str,
    product_title: str,
    api_key: str,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    """
    Strict two-pass classification.

    Pass 1 is always made. If confidence < CONFIDENCE_THRESHOLD a second
    pass is made. The result with the higher confidence wins. This catches
    edge-cases and prevents misclassification on ambiguous images.
    """
    first = _call_openrouter(
        image_url=image_url,
        product_title=product_title,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
    if first["confidence"] >= CONFIDENCE_THRESHOLD:
        return first
    try:
        second = _call_openrouter(
            image_url=image_url,
            product_title=product_title,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
    except Exception:
        return first  # second pass failed -- stick with first
    return second if second["confidence"] > first["confidence"] else first


# ---------------------------------------------------------------------------
# Step 3: Image resize functions
# ---------------------------------------------------------------------------

def _download_image(url: str, timeout: float = 30.0) -> bytes:
    resp = requests.get(url, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"Download failed ({resp.status_code}): {url[:120]}")
    if not resp.content:
        raise RuntimeError(f"Empty response: {url[:120]}")
    return resp.content


def _is_transparent_bg(img: Image.Image) -> bool:
    """Return True if any corner/edge pixel of the image is fully transparent."""
    if img.mode not in ("RGBA", "LA"):
        return False
    rgba = img.convert("RGBA")
    w, h = rgba.size
    for x, y in [
        (0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
        (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2),
    ]:
        if rgba.getpixel((x, y))[3] == 0:
            return True
    return False


def _resize_hero(img_bytes: bytes, target_w: int, target_h: int) -> bytes:
    """
    Resize hero image onto a fully transparent canvas (target_w x target_h).

    - Bounding-box crop applied when image already has transparency.
    - Product is centred with HERO_MARGIN_RATIO padding on all sides.
    - Output: PNG (transparent background guaranteed).
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    if _is_transparent_bg(img):
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
    avail_w = int(target_w * HERO_MARGIN_RATIO)
    avail_h = int(target_h * HERO_MARGIN_RATIO)
    scale = min(avail_w / img.width, avail_h / img.height)
    nw = max(1, int(img.width * scale))
    nh = max(1, int(img.height * scale))
    scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    px = (target_w - nw) // 2
    py = (target_h - nh) // 2
    mask = scaled.split()[3] if "A" in scaled.getbands() else None
    canvas.paste(scaled, (px, py), mask)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _resize_lifestyle(img_bytes: bytes, target_w: int, target_h: int) -> bytes:
    """
    Resize lifestyle image to target_w x target_h JPEG.

    Strategy: scale then center-crop to fill the target frame exactly.
    The room/scene background is preserved COMPLETELY -- never removed.
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    src_r = img.width / img.height
    dst_r = target_w / target_h
    if src_r > dst_r:
        # Wider source -- fit height, crop sides
        scale = target_h / img.height
        scaled = img.resize((int(img.width * scale), target_h), Image.Resampling.LANCZOS)
        left = (scaled.width - target_w) // 2
        final = scaled.crop((left, 0, left + target_w, target_h))
    else:
        # Taller source -- fit width, center-crop vertically
        scale = target_w / img.width
        scaled = img.resize((target_w, int(img.height * scale)), Image.Resampling.LANCZOS)
        top = (scaled.height - target_h) // 2
        final = scaled.crop((0, top, target_w, top + target_h))
    buf = io.BytesIO()
    final.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()


def _resize_detail(img_bytes: bytes, target_w: int, target_h: int) -> bytes:
    """
    Resize detail image to target_w x target_h JPEG (1760 x 1100 / 16:10).

    Strategy: scale then center-crop to fill the target frame exactly.
    Background preserved -- same approach as lifestyle since both share the
    same 16:10 canvas dimensions.
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    src_r = img.width / img.height
    dst_r = target_w / target_h
    if src_r > dst_r:
        # Wider source -- fit height, crop sides
        scale = target_h / img.height
        scaled = img.resize((int(img.width * scale), target_h), Image.Resampling.LANCZOS)
        left = (scaled.width - target_w) // 2
        final = scaled.crop((left, 0, left + target_w, target_h))
    else:
        # Taller source -- fit width, center-crop vertically
        scale = target_w / img.width
        scaled = img.resize((target_w, int(img.height * scale)), Image.Resampling.LANCZOS)
        top = (scaled.height - target_h) // 2
        final = scaled.crop((0, top, target_w, top + target_h))
    buf = io.BytesIO()
    final.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()


def resize_image(img_bytes: bytes, label: str) -> tuple[bytes, tuple[int, int], str]:
    """Resize image bytes according to the label's category spec.
    Returns (resized_bytes, (width, height), file_extension).
    """
    target_w, target_h = CATEGORY_DIMS[label]
    fmt = CATEGORY_FORMAT[label]
    if label == "hero":
        return _resize_hero(img_bytes, target_w, target_h), (target_w, target_h), fmt
    if label == "lifestyle":
        return _resize_lifestyle(img_bytes, target_w, target_h), (target_w, target_h), fmt
    if label == "detail":
        return _resize_detail(img_bytes, target_w, target_h), (target_w, target_h), fmt
    raise ValueError(f"Unknown label: {label!r}")


# ---------------------------------------------------------------------------
# Step 4: Background removal (hero images only) via fal.ai
# ---------------------------------------------------------------------------

def fal_remove_background(image_url_or_bytes: str | bytes, *, fal_key: str) -> bytes:
    """
    Remove background using fal.ai (fal-ai/imageutils/rembg).
    Accepts public image URL or raw image bytes (converted to base64 data URI).
    Returns transparent PNG bytes.
    """
    if isinstance(image_url_or_bytes, bytes):
        b64 = base64.b64encode(image_url_or_bytes).decode("ascii")
        img_input = f"data:image/png;base64,{b64}"
    else:
        img_input = str(image_url_or_bytes).strip()

    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json",
    }
    url = "https://fal.run/fal-ai/imageutils/rembg"
    payload = {"image_url": img_input}

    resp = requests.post(url, headers=headers, json=payload, timeout=90)
    if not resp.ok:
        raise RuntimeError(f"FAL RemBG Error ({resp.status_code}): {resp.text[:400]}")

    data = resp.json()
    out_url = (data.get("image") or {}).get("url")
    if not out_url:
        raise RuntimeError(f"FAL RemBG returned no image URL: {data}")

    dl_resp = requests.get(out_url, timeout=60)
    if not dl_resp.ok or not dl_resp.content:
        raise RuntimeError(f"Failed to download FAL result from {out_url}")

    return dl_resp.content


def ensure_hero_bg_removed(
    img_bytes: bytes,
    *,
    source_url: str = "",
    fal_key: str,
) -> bytes:
    """
    Ensure a hero image has a transparent (removed) background using fal.ai.

    If the image already has transparent edge pixels it is returned UNCHANGED
    -- existing background removal is preserved perfectly as-is.
    Otherwise fal.ai rembg is called and the transparent PNG is returned.

    Lifestyle and detail images are NEVER passed to this function.
    Their backgrounds must always remain exactly as they are.
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    if _is_transparent_bg(img):
        return img_bytes  # already transparent -- nothing to do

    # Use public source_url if available for direct processing, else bytes
    input_target = source_url if (source_url and source_url.startswith("http")) else img_bytes
    return fal_remove_background(input_target, fal_key=fal_key)


# ---------------------------------------------------------------------------
# Shopify integration helpers
# ---------------------------------------------------------------------------

def _fetch_all_products(client: Any, *, status: str) -> list[dict[str, Any]]:
    """Fetch ALL products from Shopify via paginated REST API (250/page)."""
    fields = "id,title,handle,status,vendor,created_at,tags,images"
    print(f"  Fetching products (status={status or 'all'})...")
    products = client.iter_products(status=status, fields=fields)
    print(f"  Loaded {len(products)} products from Shopify.")
    return products


def _parse_product_ids(raw: str) -> list[int]:
    """Parse single or comma/space-separated product IDs from CLI string."""
    if not raw:
        return []
    ids: list[int] = []
    for part in re.split(r"[\s,]+", str(raw).strip()):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def _apply_no_hero_status(
    client: Any,
    product: ProductRecord,
    *,
    target_status: str,
    dry_run: bool,
) -> None:
    """
    Update Shopify product status for a product missing a hero image.
    No tags and no metafields are modified.
    """
    if dry_run:
        print(
            f"    [DRY-RUN] Would change product {product.product_id} "
            f"({product.title!r}) status from '{product.shopify_status}' to '{target_status}' (No hero image)"
        )
        return

    try:
        client.set_product_status(product.product_id, target_status)
        product.status_updated = True
        product.new_shopify_status = target_status
        print(
            f"    [OK] Status updated product {product.product_id} "
            f"({product.title!r}): '{product.shopify_status}' -> '{target_status}'"
        )
    except Exception as exc:  # noqa: BLE001
        product.status_error = str(exc)
        print(f"    [ERR] Status update failed for {product.product_id}: {exc}")


_apply_lifestyle_only_status = _apply_no_hero_status


def _upload_resized_image(
    client: Any,
    rec: ImageRecord,
    *,
    fmt: str,
    dry_run: bool,
) -> None:
    """Replace the Shopify product image in-place with the resized bytes (base64)."""
    if dry_run or not rec.resized_bytes:
        return
    try:
        filename = f"{rec.label}_{rec.product_id}_{rec.shopify_image_id}.{fmt}"
        client.replace_product_image(
            rec.product_id,
            rec.shopify_image_id,
            image_bytes=rec.resized_bytes,
            filename=filename,
            position=rec.position,
        )
        rec.uploaded = True
        rec.resized_bytes = b""  # free memory after upload
    except Exception as exc:  # noqa: BLE001
        rec.upload_error = str(exc)


# ---------------------------------------------------------------------------
# Core pipeline orchestration
# ---------------------------------------------------------------------------

def _build_image_records(products: list[dict[str, Any]]) -> list[ImageRecord]:
    """Flatten Shopify product dicts into a list of ImageRecord objects."""
    records: list[ImageRecord] = []
    for product in products:
        pid = int(product["id"])
        title = str(product.get("title") or "").strip()
        for img in product.get("images") or []:
            src = str(img.get("src") or "").strip()
            image_id = img.get("id")
            if not src or not image_id:
                continue
            records.append(
                ImageRecord(
                    product_id=pid,
                    product_title=title,
                    shopify_image_id=int(image_id),
                    position=int(img.get("position") or 1),
                    src_url=src,
                )
            )
    return records


def _step2_classify(
    records: list[ImageRecord],
    *,
    api_key: str,
    model: str,
    timeout: float,
    workers: int,
) -> None:
    """AI-classify every image in parallel. Mutates records in-place."""
    total = len(records)
    done = [0]

    def _classify_one(rec: ImageRecord) -> None:
        try:
            result = classify_image_strict(
                image_url=rec.src_url,
                product_title=rec.product_title,
                api_key=api_key,
                model=model,
                timeout=timeout,
            )
            rec.label = result["label"]
            rec.confidence = result["confidence"]
            rec.reason = result["reason"]
        except Exception as exc:  # noqa: BLE001
            rec.classification_error = str(exc)[:400]
            # Default to lifestyle on error -- safer than a false hero classification
            rec.label = "lifestyle"
            rec.confidence = 0.0
            rec.reason = f"ERROR-defaulted-to-lifestyle: {exc}"

    print(f"\n[Step 2] Classifying {total} images ({workers} AI workers)...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_classify_one, rec): rec for rec in records}
        for future in as_completed(futures):
            done[0] += 1
            try:
                future.result()
            except Exception:
                pass
            if done[0] % 25 == 0 or done[0] == total:
                print(f"  classified {done[0]}/{total}...", flush=True)


def _step3_4_resize(
    records: list[ImageRecord],
    *,
    workers: int,
    http_timeout: float,
    fal_key: str,
    skip_bg_removal: bool,
) -> None:
    """Download, BG-remove if hero (via fal.ai), and resize all images. Mutates records in-place."""
    total = len(records)
    done = [0]

    def _process_one(rec: ImageRecord) -> None:
        if not rec.label:
            rec.resize_error = "No label -- skipped"
            return

        # Download original
        try:
            img_bytes = _download_image(rec.src_url, timeout=http_timeout)
        except Exception as exc:  # noqa: BLE001
            rec.resize_error = f"Download: {exc}"
            return

        # Step 4: BG removal for hero images ONLY using fal.ai
        # Lifestyle and detail images NEVER go through BG removal.
        if rec.label == "hero" and not skip_bg_removal and fal_key:
            try:
                img_bytes = ensure_hero_bg_removed(
                    img_bytes, source_url=rec.src_url, fal_key=fal_key
                )
            except Exception as exc:  # noqa: BLE001
                # Non-fatal -- note the warning and continue with original bytes
                rec.resize_error = f"[BG-WARN] {exc}"

        # Step 3: Resize per category spec
        try:
            out, dims, _ = resize_image(img_bytes, rec.label)
            rec.resized_bytes = out
            rec.resized_dims = dims
        except Exception as exc:  # noqa: BLE001
            suffix = f"Resize: {exc}"
            rec.resize_error = f"{rec.resize_error} | {suffix}".lstrip(" | ")

    print(f"\n[Step 3+4] Download + resize {total} images ({workers} workers)...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, rec): rec for rec in records}
        for future in as_completed(futures):
            done[0] += 1
            try:
                future.result()
            except Exception:
                pass
            if done[0] % 25 == 0 or done[0] == total:
                print(f"  processed {done[0]}/{total}...", flush=True)


def _step3_upload(
    records: list[ImageRecord],
    client: Any,
    *,
    dry_run: bool,
    workers: int,
) -> None:
    """Upload resized images back to Shopify (capped at 4 concurrent threads)."""
    uploadable = [r for r in records if r.resized_bytes and not r.resize_error]
    total = len(uploadable)
    if not total:
        print("\n[Upload] No images ready to upload.")
        return
    done = [0]
    print(f"\n[Upload] Uploading {total} resized images to Shopify...")

    def _upload_one(rec: ImageRecord) -> None:
        _upload_resized_image(client, rec, fmt=CATEGORY_FORMAT.get(rec.label, "jpeg"),
                              dry_run=dry_run)

    with ThreadPoolExecutor(max_workers=min(workers, 4)) as pool:
        futures = {pool.submit(_upload_one, rec): rec for rec in uploadable}
        for future in as_completed(futures):
            done[0] += 1
            try:
                future.result()
            except Exception:
                pass
            if done[0] % 10 == 0 or done[0] == total:
                print(f"  uploaded {done[0]}/{total}...", flush=True)


def _step5_flag_no_hero_products(
    product_records: list[ProductRecord],
    image_records: list[ImageRecord],
    client: Any,
    *,
    target_status: str,
    dry_run: bool,
) -> None:
    """
    Step 5: Change Shopify status to target_status (default: 'unlisted') for products
    that do NOT have any image classified as 'hero' (e.g. lifestyle-only, detail-only, or lifestyle+detail).
    """
    pid_to_labels: dict[int, list[str]] = {}
    for rec in image_records:
        pid_to_labels.setdefault(rec.product_id, []).append(rec.label)

    count = 0
    print(f"\n[Step 5] Scanning {len(product_records)} products for missing hero images...")
    for prod in product_records:
        labels = pid_to_labels.get(prod.product_id, [])
        if not labels:
            continue
        has_hero = "hero" in labels
        prod.has_hero = has_hero
        prod.is_lifestyle_only = bool(labels) and all(lb == "lifestyle" for lb in labels)

        # If there is NO hero image (only lifestyle, detail, or mix)
        if not has_hero:
            prod.is_missing_hero = True
            count += 1
            _apply_no_hero_status(
                client, prod,
                target_status=target_status,
                dry_run=dry_run,
            )

    verb = "would be" if dry_run else "were"
    print(
        f"  {count} products without hero image {verb} updated to status='{target_status}'."
    )


_step5_flag_lifestyle_only = _step5_flag_no_hero_products


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _build_report(
    product_records: list[ProductRecord],
    image_records: list[ImageRecord],
    *,
    shop: str,
    model: str,
    dry_run: bool,
    elapsed: float,
) -> dict[str, Any]:
    label_counts: dict[str, int] = {"hero": 0, "lifestyle": 0, "detail": 0}
    classify_errors = resize_errors = upload_ok = upload_errors = 0
    for rec in image_records:
        if rec.label in label_counts:
            label_counts[rec.label] += 1
        if rec.classification_error:
            classify_errors += 1
        if rec.resize_error:
            resize_errors += 1
        if rec.uploaded:
            upload_ok += 1
        if rec.upload_error:
            upload_errors += 1

    pid_to_imgs: dict[int, list[ImageRecord]] = {}
    for rec in image_records:
        pid_to_imgs.setdefault(rec.product_id, []).append(rec)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shop": shop,
        "model": model,
        "dry_run": dry_run,
        "elapsed_seconds": round(elapsed, 2),
        "totals": {
            "products": len(product_records),
            "images": len(image_records),
            "labels": label_counts,
            "lifestyle_only_products": sum(
                1 for p in product_records if p.is_lifestyle_only
            ),
            "no_hero_products": sum(
                1 for p in product_records if p.is_missing_hero
            ),
            "status_updated_products": sum(
                1 for p in product_records if p.status_updated
            ),
            "classify_errors": classify_errors,
            "resize_errors": resize_errors,
            "upload_ok": upload_ok,
            "upload_errors": upload_errors,
        },
        "category_dimensions": {k: list(v) for k, v in CATEGORY_DIMS.items()},
        "products": [
            {
                "product_id": prod.product_id,
                "title": prod.title,
                "handle": prod.handle,
                "shopify_status": prod.shopify_status,
                "new_shopify_status": prod.new_shopify_status or prod.shopify_status,
                "vendor": prod.vendor,
                "created_at": prod.created_at,
                "has_hero": prod.has_hero,
                "is_missing_hero": prod.is_missing_hero,
                "is_lifestyle_only": prod.is_lifestyle_only,
                "status_updated": prod.status_updated,
                "status_error": prod.status_error,
                "images": [
                    {
                        "image_id": r.shopify_image_id,
                        "position": r.position,
                        "src": r.src_url,
                        "label": r.label,
                        "confidence": round(r.confidence, 3),
                        "reason": r.reason,
                        "target_dims": list(CATEGORY_DIMS.get(r.label, (0, 0))),
                        "uploaded": r.uploaded,
                        "classification_error": r.classification_error,
                        "resize_error": r.resize_error,
                        "upload_error": r.upload_error,
                    }
                    for r in sorted(
                        pid_to_imgs.get(prod.product_id, []),
                        key=lambda x: x.position,
                    )
                ],
            }
            for prod in sorted(product_records, key=lambda p: p.title.lower())
        ],
    }


def _print_summary(r: dict[str, Any]) -> None:
    t = r["totals"]
    lbl = t["labels"]
    print("\n" + "=" * 70)
    print("PIPELINE SUMMARY")
    print("=" * 70)
    print(f"  Shop:               {r['shop']}")
    print(f"  Model:              {r['model']}")
    print(f"  Mode:               {'DRY RUN' if r['dry_run'] else 'APPLIED'}")
    print(f"  Elapsed:            {r['elapsed_seconds']}s")
    print(f"  Products scanned:   {t['products']}")
    print(f"  Images total:       {t['images']}")
    print(f"    hero:             {lbl.get('hero', 0)}")
    print(f"    lifestyle:        {lbl.get('lifestyle', 0)}")
    print(f"    detail:           {lbl.get('detail', 0)}")
    print(
        f"  No-Hero products:   {t.get('no_hero_products', 0)} products "
        f"(lifestyle-only: {t.get('lifestyle_only_products', 0)}, status updated: {t.get('status_updated_products', 0)})"
    )
    print(f"  Classify errors:    {t['classify_errors']}")
    print(f"  Resize errors:      {t['resize_errors']}")
    print(f"  Uploads OK:         {t['upload_ok']}")
    print(f"  Upload errors:      {t['upload_errors']}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Shopify Product Image Categorization & Processing Pipeline\n"
            "Steps: fetch -> AI classify -> resize -> BG verify -> tag lifestyle-only"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--apply", action="store_true",
        help="Apply all changes to Shopify (upload resized images, update status).",
    )
    grp.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Classify and resize locally only -- do NOT write to Shopify. (default)",
    )
    parser.add_argument(
        "--product-id",
        "--product-ids",
        "--id",
        "--ids",
        dest="product_ids",
        default="",
        help="Process specific product ID(s). Comma- or space-separated (e.g. --id 12345 or --ids 123,456).",
    )
    parser.add_argument(
        "--status", default="",
        help="Shopify product status filter: active|draft|archived|'' = all (default: all)",
    )
    parser.add_argument(
        "--no-hero-status",
        "--lifestyle-status",
        dest="no_hero_status",
        default=DEFAULT_NO_HERO_STATUS,
        choices=["unlisted", "draft", "active", "archived"],
        help=f"Shopify status to set when product has no hero image (default: {DEFAULT_NO_HERO_STATUS}). Choices: unlisted, draft, active, archived.",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max products to process (0 = no limit).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"OpenRouter vision model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--ai-workers", type=int,
        default=int(os.getenv("PIPELINE_AI_WORKERS", "3")),
        help="Parallel AI classification threads (default: 3; keep low to avoid rate limits).",
    )
    parser.add_argument(
        "--ai-timeout", type=float, default=90.0,
        help="Per-image AI call timeout in seconds (default: 90).",
    )
    parser.add_argument(
        "--workers", type=int,
        default=int(os.getenv("PIPELINE_WORKERS", "8")),
        help="Parallel image download/resize threads (default: 8).",
    )
    parser.add_argument(
        "--skip-resize", action="store_true",
        help="Skip resize + upload (classify and status update only).",
    )
    parser.add_argument(
        "--skip-bg-removal", action="store_true",
        help="Skip fal.ai BG removal for hero images (use existing BG state).",
    )
    parser.add_argument(
        "--fal-key",
        default="",
        help="fal.ai API key (overrides FAL_KEY env var).",
    )
    parser.add_argument(
        "--report", type=Path,
        default=Path("output") / "shopify_image_pipeline_report.json",
        help="JSON report output path.",
    )
    parser.add_argument(
        "--from-report", type=Path, default=None,
        help="Load a prior report JSON to skip fetch + classify (resume mode).",
    )

    args = parser.parse_args()
    dry_run = not args.apply
    target_pids = _parse_product_ids(args.product_ids)

    # --- Initialise Shopify client ------------------------------------------
    try:
        from shopify_client import ShopifyClient, load_shopify_config
        config = load_shopify_config()
        client = ShopifyClient(config)
    except Exception as exc:
        print(f"ERROR: Shopify client init failed: {exc}", file=sys.stderr)
        return 1

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"')
    if not openrouter_key and args.from_report is None:
        print("ERROR: OPENROUTER_API_KEY not set in .env", file=sys.stderr)
        return 1

    fal_key = (args.fal_key or os.getenv("FAL_KEY", "") or os.getenv("FAL_API_KEY", "")).strip().strip('"')
    if not fal_key and not args.skip_bg_removal:
        print("[WARN] FAL_KEY not set in .env -- hero BG removal will be skipped.", file=sys.stderr)

    print("=" * 70)
    print("Shopify Image Categorization & Processing Pipeline")
    print("=" * 70)
    print(f"  Shop:             {config.shop_host}")
    print(f"  Mode:             {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"  Model:            {args.model}")
    if target_pids:
        print(f"  Target ID(s):     {target_pids}")
    else:
        print(f"  Status Filter:    {args.status or 'all'}")
    print(f"  No-Hero Status:   {args.no_hero_status}")
    bg_label = (
        "SKIP"
        if (args.skip_bg_removal or not fal_key)
        else "fal.ai rembg (hero images only)"
    )
    print(f"  BG removal:       {bg_label}")
    print()

    t0 = time.perf_counter()

    # --- Step 1: Fetch -------------------------------------------------------
    products_raw: list[dict[str, Any]] = []
    image_records: list[ImageRecord] = []
    product_records: list[ProductRecord] = []

    if args.from_report:
        print(f"[Step 1] Loading from prior report: {args.from_report}")
        prior = json.loads(args.from_report.read_text(encoding="utf-8"))
        for p in prior.get("products") or []:
            pid = int(p["product_id"])
            if target_pids and pid not in target_pids:
                continue
            pr = ProductRecord(
                product_id=pid,
                title=str(p.get("title") or ""),
                handle=str(p.get("handle") or ""),
                shopify_status=str(p.get("shopify_status") or ""),
                vendor=str(p.get("vendor") or ""),
                created_at=str(p.get("created_at") or ""),
            )
            product_records.append(pr)
            products_raw.append({"id": pid})
            for img in p.get("images") or []:
                rec = ImageRecord(
                    product_id=pid,
                    product_title=pr.title,
                    shopify_image_id=int(img.get("image_id") or 0),
                    position=int(img.get("position") or 1),
                    src_url=str(img.get("src") or ""),
                    label=str(img.get("label") or ""),
                    confidence=float(img.get("confidence") or 0.0),
                    reason=str(img.get("reason") or ""),
                )
                image_records.append(rec)
                pr.images.append(rec)
        print(
            f"  {len(product_records)} products, "
            f"{len(image_records)} images loaded from report."
        )
    elif target_pids:
        print(f"[Step 1] Fetching {len(target_pids)} specific product(s) by ID...")
        for pid in target_pids:
            try:
                p = client.get_product(pid)
                products_raw.append(p)
                print(f"  Loaded product {pid}: {p.get('title')!r} (status: {p.get('status')})")
            except Exception as exc:
                print(f"  [ERROR] Could not fetch product {pid}: {exc}", file=sys.stderr)

        product_records = [
            ProductRecord(
                product_id=int(p["id"]),
                title=str(p.get("title") or ""),
                handle=str(p.get("handle") or ""),
                shopify_status=str(p.get("status") or ""),
                vendor=str(p.get("vendor") or ""),
                created_at=str(p.get("created_at") or ""),
            )
            for p in products_raw
        ]
        pid_to_prod = {pr.product_id: pr for pr in product_records}
        image_records = _build_image_records(products_raw)
        for rec in image_records:
            pr = pid_to_prod.get(rec.product_id)
            if pr:
                pr.images.append(rec)
        print(
            f"  {len(product_records)} products, "
            f"{len(image_records)} images collected.\n"
        )
        if not image_records:
            print("No images found. Exiting.")
            return 0

        # --- Step 2: AI classify --------------------------------------------
        _step2_classify(
            image_records,
            api_key=openrouter_key,
            model=args.model,
            timeout=args.ai_timeout,
            workers=args.ai_workers,
        )
    else:
        print("[Step 1] Fetching all products from Shopify...")
        try:
            products_raw = _fetch_all_products(client, status=args.status)
        except Exception as exc:
            print(f"ERROR fetching products: {exc}", file=sys.stderr)
            return 1

        if args.limit > 0:
            products_raw = products_raw[: args.limit]
            print(f"  Limited to first {args.limit} products.")

        product_records = [
            ProductRecord(
                product_id=int(p["id"]),
                title=str(p.get("title") or ""),
                handle=str(p.get("handle") or ""),
                shopify_status=str(p.get("status") or ""),
                vendor=str(p.get("vendor") or ""),
                created_at=str(p.get("created_at") or ""),
            )
            for p in products_raw
        ]
        pid_to_prod = {pr.product_id: pr for pr in product_records}
        image_records = _build_image_records(products_raw)
        for rec in image_records:
            pr = pid_to_prod.get(rec.product_id)
            if pr:
                pr.images.append(rec)
        print(
            f"  {len(product_records)} products, "
            f"{len(image_records)} images collected.\n"
        )
        if not image_records:
            print("No images found. Exiting.")
            return 0

        # --- Step 2: AI classify --------------------------------------------
        _step2_classify(
            image_records,
            api_key=openrouter_key,
            model=args.model,
            timeout=args.ai_timeout,
            workers=args.ai_workers,
        )

    # --- Step 3 + 4: Resize + BG removal + Upload ----------------------------
    if not args.skip_resize:
        classifiable = [r for r in image_records if r.label]
        _step3_4_resize(
            classifiable,
            workers=args.workers,
            http_timeout=float(os.getenv("HTTP_TIMEOUT", "30")),
            fal_key=fal_key,
            skip_bg_removal=args.skip_bg_removal,
        )
        _step3_upload(classifiable, client, dry_run=dry_run, workers=args.workers)
    else:
        print("\n[Step 3+4] Skipped (--skip-resize passed).")

    # --- Step 5: Update status of products without hero image ---------------
    _step5_flag_no_hero_products(
        product_records,
        image_records,
        client,
        target_status=args.no_hero_status,
        dry_run=dry_run,
    )

    # --- Write JSON report ---------------------------------------------------
    elapsed = time.perf_counter() - t0
    report = _build_report(
        product_records, image_records,
        shop=config.shop_host,
        model=args.model,
        dry_run=dry_run,
        elapsed=elapsed,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _print_summary(report)
    print(f"\nReport: {args.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
