"""
test_detail_image_gen.py
========================
Test script for the AI-generated detail image pipeline.

Runs the full pipeline on a single Baserow product row and outputs
before/after comparison images for manual QA review.

Consistent with the pattern in test_unified_pipeline.py and
test_genai_lifestyle_to_hero.py.

Usage:
  python test_detail_image_gen.py --id 3182
  python test_detail_image_gen.py --id 5630 --num-images 2 --strength 0.55
  python test_detail_image_gen.py --id 5630 --save          (writes to Baserow)
  python test_detail_image_gen.py --id 5630 --output-dir output/my_test
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from fal_image_processor import (
    generate_detail_images,
    identify_detail_feature_regions,
    process_master,
)
from generate_detail_images_pipeline import (
    FIELD_CREATED_ON,
    FIELD_DETAIL_IMAGE,
    FIELD_DETAILED_IMAGE_GEN,
    FIELD_HERO_IMAGES,
    FIELD_LIFESTYLE_IMAGES,
    FIELD_PRODUCT_DESC,
    FIELD_PRODUCT_IMAGES,
    FIELD_PRODUCT_NAME,
    TABLE_ID,
    classify_images_batch,
    upload_pil_to_baserow,
)

OUTPUT_DIR_DEFAULT = Path(__file__).resolve().parent / "output" / "detail_gen_test"


def _download_image(url):
    """Download an image from a URL and return as PIL Image."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content))


def _save_image(img, path):
    """Save PIL image. Forces RGB for JPEG."""
    p = Path(path)
    if p.suffix.lower() in (".jpg", ".jpeg"):
        img = img.convert("RGB")
    img.save(str(p), quality=90)
    print("  Saved: %s" % p)


def _make_comparison_strip(before_img, after_img, label=""):
    """
    Creates a side-by-side comparison image.
    Left = original / reference, Right = AI-generated detail.
    """
    max_h = max(before_img.height, after_img.height)
    scale_b = max_h / before_img.height
    scale_a = max_h / after_img.height
    b = before_img.resize(
        (int(before_img.width * scale_b), max_h), Image.Resampling.LANCZOS
    )
    a = after_img.resize(
        (int(after_img.width * scale_a), max_h), Image.Resampling.LANCZOS
    )
    total_w = b.width + a.width + 20  # 20px separator
    strip = Image.new("RGB", (total_w, max_h), (200, 200, 200))
    strip.paste(b, (0, 0))
    strip.paste(a, (b.width + 20, 0))
    return strip


def main():
    parser = argparse.ArgumentParser(
        description="Test AI detail image generation on a single Baserow product."
    )
    parser.add_argument(
        "--id", type=int, required=True,
        help="Baserow Row ID in Table 742."
    )
    parser.add_argument(
        "--num-images", type=int, default=3,
        help="Number of AI image variations to generate (default: 3)."
    )
    parser.add_argument(
        "--strength", type=float, default=0.30,
        help="FLUX img2img denoising strength for --mode macro (default: 0.30).",
    )
    parser.add_argument(
        "--mode", choices=["detail", "macro", "packshot"], default="detail",
        help="detail (default: true feature close-ups) or packshot (full product). macro=detail.",
    )
    parser.add_argument(
        "--engine",
        choices=["flux2-pro", "flux2-max", "kontext", "img2img", "redux"],
        default="flux2-pro",
        help="fal.ai engine (default: flux2-pro).",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Write generated images to Baserow field_7401 (Detailed_image_gen)."
    )
    parser.add_argument(
        "--skip-classify", action="store_true",
        help="Skip image classification step (useful for quick generation tests)."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT,
        help="Directory to save before/after comparison images."
    )
    args = parser.parse_args()

    settings = load_settings()
    baserow = BaserowClient(settings)
    openrouter_key = settings.openrouter_api_key
    fal_key = os.getenv("FAL_KEY", "").strip()

    if not openrouter_key:
        print("ERROR: OPENROUTER_API_KEY not set.")
        return 1
    if not fal_key:
        print("ERROR: FAL_KEY not set.")
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Test: AI Detail Image Generation")
    print("=" * 60)
    print("  Row ID     : %d" % args.id)
    print("  Engine     : %s" % args.engine)
    print("  Num images : %d" % args.num_images)
    print("  Strength   : %.2f" % args.strength)
    print("  Save       : %s" % args.save)
    print("  Output dir : %s" % args.output_dir)
    print()

    # Fetch row
    print("Fetching row %d from Table %d..." % (args.id, TABLE_ID))
    try:
        row = baserow.get_row(TABLE_ID, args.id)
    except Exception as exc:
        print("ERROR: %s" % exc)
        return 1

    product_name = str(row.get(FIELD_PRODUCT_NAME) or "Product %d" % args.id)
    product_desc = str(row.get(FIELD_PRODUCT_DESC) or "")
    product_images = row.get(FIELD_PRODUCT_IMAGES) or []
    hero_images = row.get(FIELD_HERO_IMAGES) or []
    lifestyle_images = row.get(FIELD_LIFESTYLE_IMAGES) or []
    detail_images = row.get(FIELD_DETAIL_IMAGE) or []

    print("Product: %s" % product_name)
    print("  Raw images: %d | Hero: %d | Lifestyle: %d | Detail: %d" % (
        len(product_images), len(hero_images), len(lifestyle_images), len(detail_images)
    ))
    print()

    # Step 1: Classify images (unless skipped)
    classification_summary = {}
    if product_images and not args.skip_classify:
        image_urls = [img["url"] for img in product_images if img.get("url")]
        print("Classifying %d images via GPT-4o Vision..." % len(image_urls))
        classifications = classify_images_batch(image_urls, openrouter_key)
        for i, (img, cls) in enumerate(zip(product_images, classifications)):
            print("  [%d] %s -> %s" % (i + 1, cls.upper(), img.get("url", "")[:70]))
            classification_summary[cls] = classification_summary.get(cls, 0) + 1
        print("  Summary: %s" % json.dumps(classification_summary))
    else:
        print("Skipping classification step.")
    print()

    # Step 2: Pick reference image
    # Packshot: hero / raw only (avoid lifestyle). Macro: hero > lifestyle > raw.
    ref_img = ""
    ref_field = ""
    if args.mode == "packshot":
        ref_pools = [(hero_images, "hero"), (product_images, "raw")]
    else:
        ref_pools = [
            (hero_images, "hero"),
            (lifestyle_images, "lifestyle"),
            (product_images, "raw"),
        ]
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
        print("ERROR: No reference image found for row %d." % args.id)
        return 1

    print("Reference image (%s):" % ref_field)
    print("  %s" % ref_img)
    print()

    # Save reference image for comparison
    ref_path = args.output_dir / ("ref_%d.jpg" % args.id)
    try:
        ref_pil = _download_image(ref_img)
        _save_image(ref_pil, ref_path)
        print("  Reference saved to: %s" % ref_path)
    except Exception as exc:
        print("  [!] Could not download reference: %s" % exc)
        ref_pil = None
    print()

    # Prefer hero + product URLs for packshot multi-ref
    prefer_image_urls = []
    for pool in [hero_images, product_images]:
        for img_obj in pool:
            u = img_obj.get("url", "")
            if u and u not in prefer_image_urls:
                prefer_image_urls.append(u)

    all_image_urls = list(prefer_image_urls)
    if args.mode != "packshot":
        for pool in [lifestyle_images, detail_images]:
            for img_obj in pool:
                u = img_obj.get("url", "")
                if u and u not in all_image_urls:
                    all_image_urls.append(u)

    print("Total reference images for generation: %d" % len(all_image_urls))
    for i, u in enumerate(all_image_urls):
        print("  [%d] %s" % (i + 1, u[:75]))
    print()

    # Detail mode: show Vision feature regions. Packshot: skip crops.
    mode_l = "detail" if args.mode == "macro" else args.mode
    if mode_l == "detail":
        print("Identifying TRUE detail feature regions via GPT-4o Vision...")
        features = identify_detail_feature_regions(
            all_image_urls or ref_img,
            product_name,
            product_desc,
            openrouter_key,
            num_features=args.num_images,
        )
        print()
        print("[Identified Feature Regions & Sub-Prompts]")
        print("-" * 60)
        for i, feat in enumerate(features):
            feat_name = feat.get("feature_name") or feat.get("feature") or ("Feature %d" % (i + 1))
            feat_type = feat.get("feature_type") or feat.get("category") or "detail_feature"
            best_idx = feat.get("best_image_index") if "best_image_index" in feat else feat.get("image_index", 0)
            crop_box = feat.get("crop_box_normalized") or feat.get("bounding_box") or feat.get("crop_box") or [0.0, 0.0, 1.0, 1.0]
            prompt = feat.get("detail_prompt") or feat.get("prompt") or ("Commercial macro studio photograph close-up of %s" % product_name)
            print("  Crop %d: '%s' (%s)" % (i + 1, feat_name, feat_type))
            print("    Best Image: index %s" % best_idx)
            print("    Crop Box  : %s" % crop_box)
            print("    Prompt    : %s" % prompt)
            print()
        print("-" * 60)
        print()
    else:
        print("Packshot mode: skipping Vision crop boxes (full product, no local crop).")
        print()

    # Step 4: Generate via fal.ai
    print(
        "Generating %d image(s) via fal.ai (%s / %s)..."
        % (args.num_images, mode_l, args.engine)
    )
    results = generate_detail_images(
        reference_url=ref_img,
        product_name=product_name,
        product_description=product_desc,
        openrouter_key=openrouter_key,
        num_images=args.num_images,
        strength=args.strength,
        all_image_urls=all_image_urls,
        prefer_image_urls=prefer_image_urls,
        engine=args.engine,
        mode=mode_l,
    )

    if not results:
        print("ERROR: No detail images were generated.")
        return 1

    print()
    print("Generated %d image(s)." % len(results))

    # Step 5: Save comparison images
    detail_gen_files = []
    for var_idx, (pil_img, req_id, gen_prompt) in enumerate(results):
        # Save the generated detail image
        gen_path = args.output_dir / ("detail_gen_%d_%d.jpg" % (args.id, var_idx))
        _save_image(pil_img, gen_path)
        print("  [%d] request_id=%s -> %s" % (var_idx + 1, req_id, gen_path))

        # Save side-by-side comparison
        if ref_pil:
            try:
                comp = _make_comparison_strip(ref_pil, pil_img, label="ref vs detail_%d" % var_idx)
                comp_path = args.output_dir / ("comparison_%d_%d.jpg" % (args.id, var_idx))
                _save_image(comp, comp_path)
                print("  [%d] comparison -> %s" % (var_idx + 1, comp_path))
            except Exception as exc:
                print("  [!] Comparison failed: %s" % exc)

        # Upload to Baserow if --save
        if args.save:
            filename = "detail_ai_%d_%d.jpg" % (args.id, var_idx)
            try:
                uploaded = upload_pil_to_baserow(pil_img, filename, settings)
                detail_gen_files.append({"name": uploaded["name"]})
                print("  [%d] Uploaded to Baserow: %s" % (var_idx + 1, uploaded["name"]))
            except Exception as exc:
                print("  [!] Upload failed: %s" % exc)

    # Step 6: Update Baserow field_7401
    if detail_gen_files and args.save:
        existing_gen = row.get(FIELD_DETAILED_IMAGE_GEN) or []
        new_gen_list = list(existing_gen) + detail_gen_files
        print()
        print("Updating row %d field_7401 (Detailed_image_gen) with %d image(s)..." % (
            args.id, len(detail_gen_files)
        ))
        baserow.update_row(TABLE_ID, args.id, {FIELD_DETAILED_IMAGE_GEN: new_gen_list})
        print("Done! Check row %d in Baserow Table %d." % (args.id, TABLE_ID))

    # Print summary
    print()
    print("=" * 60)
    print("Test Complete")
    print("=" * 60)
    print("  Product         : %s (ID=%d)" % (product_name, args.id))
    print("  Images generated: %d" % len(results))
    print("  Saved to Baserow: %s" % args.save)
    print("  Output directory: %s" % args.output_dir)
    print("  Files:")
    for f in sorted(args.output_dir.glob("*_%d*" % args.id)):
        print("    %s" % f)
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
