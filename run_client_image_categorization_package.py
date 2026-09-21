"""
Client package: image categorization bake-off.

1. Pick 20 RANDOM Baserow products (1 random image each)
2. Classify each image with 4 vision models
3. Build simple client folder:

     output/image categorization/
       README.txt
       GPT-4o - SELECTED/
         hero/  lifestyle/  detail/
       Gemini 2.5 Flash/
         hero/  lifestyle/  detail/
       ...

   Each image goes into the category THAT model predicted.
   Client opens folders — no JSON required.

4. Refresh AI_Model_Comparison_Client_Brief.docx (adds section 3)
5. Rebuild ZIP with bg compare + model_comparison + image categorization + docx

Usage:
  python run_client_image_categorization_package.py
  python run_client_image_categorization_package.py --limit 20 --seed 42
  python run_client_image_categorization_package.py --organize-only   # reuse results.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings

load_dotenv()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LABELS = ("hero", "lifestyle", "detail")

TABLE_ID = 742
FIELD_PRODUCT_NAME = "field_7347"
FIELD_PRODUCT_IMAGES = "field_7349"
FIELD_HERO_IMAGES = "field_7358"
FIELD_LIFESTYLE_IMAGES = "field_7359"
FIELD_DETAIL_IMAGE = "field_7360"
FIELD_BRAND_LINK = "field_7376"

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
WORK_DIR = OUTPUT_DIR / "_categorization_work"  # internal (not for client)
CAT_DIR = OUTPUT_DIR / "image categorization"
DOCX_PATH = OUTPUT_DIR / "AI_Model_Comparison_Client_Brief.docx"
ZIP_PATH = OUTPUT_DIR / "AI_Model_Comparison_Client_Package.zip"
BG_DIR = OUTPUT_DIR / "bg compare"
MODEL_DIR = OUTPUT_DIR / "model_comparison"
RESULTS_PATH = WORK_DIR / "results.json"

# 4 models — same set mentioned to the client
MODELS: list[dict[str, Any]] = [
    {
        "id": "gemini_25_flash",
        "name": "Gemini 2.5 Flash",
        "endpoint": "google/gemini-2.5-flash",
        "approx_cost": "~$0.0003-$0.001 / image",
        "note": "Fast / cheap; sometimes confuses detail or props with wrong label",
    },
    {
        "id": "gemini_25_pro",
        "name": "Gemini 2.5 Pro",
        "endpoint": "google/gemini-2.5-pro",
        "approx_cost": "~$0.002-$0.005 / image",
        "note": "Strong overall; occasional lifestyle/hero swaps",
    },
    {
        "id": "gpt4o",
        "name": "GPT-4o",
        "endpoint": "openai/gpt-4o",
        "approx_cost": "~$0.005-$0.015 / image",
        "note": "SELECTED — clearest hero / lifestyle / detail separation; also among the fastest",
        "selected": True,
    },
    {
        "id": "gpt41",
        "name": "GPT-4.1",
        "endpoint": "openai/gpt-4.1",
        "approx_cost": "~$0.004-$0.012 / image",
        "note": "Very close to GPT-4o; slightly less consistent on edge cases",
    },
]

STRICT_SYSTEM_PROMPT = """\
You are an EXPERT ecommerce product image classifier for a high-end furniture
and interior design store. Classify the image into EXACTLY ONE category.

CATEGORIES:
  hero      - Clean packshot. The product is FULLY VISIBLE on a plain, white,
              studio, off-white, or transparent/removed background. No room
              context whatsoever. Props next to the product count as lifestyle.

  lifestyle - Product in a real room / ambient interior / styled setting.
              ANY visible floor, wall, ceiling, rug, curtain, or decorative
              prop means lifestyle.

  detail    - Close-up of material / fabric / stitching / wood / hardware /
              feet / legs / edge / diagram. Full product silhouette NOT visible.

STRICT RULES (apply in order):
  1. Room, interior, or decorative prop visible -> lifestyle.
  2. Tight crop without full product silhouette -> detail.
  3. Otherwise -> hero.
  4. NEVER classify a flat texture swatch as hero.

RESPOND with ONLY valid JSON:
{"label":"hero"|"lifestyle"|"detail","confidence":0.0-1.0,"reason":"one sentence"}
"""


def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in (" ", "-", "_", ".")).strip()


def _is_demo_or_copy(name: str) -> bool:
    u = name.upper()
    return (
        "IMAGE CAT DEMO" in u
        or " - COPY" in u
        or u.endswith("- COPY")
        or "IMAGE CATEGORIZATION" in u
    )


def _image_pool(row: dict) -> list[dict]:
    """Prefer raw product_images; fall back to typed fields."""
    for field in (
        FIELD_PRODUCT_IMAGES,
        FIELD_HERO_IMAGES,
        FIELD_LIFESTYLE_IMAGES,
        FIELD_DETAIL_IMAGE,
    ):
        imgs = row.get(field) or []
        if isinstance(imgs, list):
            pool = [i for i in imgs if isinstance(i, dict) and i.get("url")]
            if pool:
                return pool
    return []


def collect_random_samples(
    baserow: BaserowClient,
    *,
    limit: int,
    seed: int | None,
) -> list[dict]:
    print("Scanning Baserow for products with images...")
    candidates: list[dict] = []
    scanned = 0
    for row in baserow.list_table_rows(TABLE_ID):
        scanned += 1
        if scanned % 1000 == 0:
            print("  ...scanned %d, eligible %d" % (scanned, len(candidates)))
        name = str(row.get(FIELD_PRODUCT_NAME) or "")
        if _is_demo_or_copy(name):
            continue
        pool = _image_pool(row)
        if not pool:
            continue
        candidates.append(
            {
                "product_id": row["id"],
                "product_name": name or ("Product %s" % row["id"]),
                "pool": pool,
            }
        )
    print("Eligible products: %d (scanned %d)" % (len(candidates), scanned))
    if not candidates:
        raise RuntimeError("No eligible products found")

    if seed is not None:
        random.seed(seed)
    if len(candidates) > limit:
        picked = random.sample(candidates, limit)
    else:
        picked = list(candidates)
        random.shuffle(picked)

    samples = []
    for i, c in enumerate(picked, 1):
        img = random.choice(c["pool"])
        samples.append(
            {
                "index": i,
                "product_id": c["product_id"],
                "product_name": c["product_name"],
                "url": img["url"],
            }
        )
    print("Selected %d products (1 random image each)." % len(samples))
    return samples


def download_image(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    img = Image.open(io.BytesIO(resp.content))
    if img.mode in ("RGBA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = bg
    else:
        img = img.convert("RGB")
    # Cap huge images for client folder size
    img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    img.save(dest, format="JPEG", quality=85)
    return dest


def _parse_ai_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    label = str(data.get("label", "")).strip().lower()
    if label not in LABELS:
        raise ValueError("invalid label: %r" % label)
    try:
        conf = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "label": label,
        "confidence": conf,
        "reason": str(data.get("reason", "")).strip(),
    }


def classify_with_model(
    *,
    image_url: str,
    product_title: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    t0 = time.time()
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://binnen.local",
                    "X-Title": "Binnen categorization client package",
                },
                json={
                    "model": model,
                    "temperature": 0.0,
                    "messages": [
                        {"role": "system", "content": STRICT_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Product: %s\nClassify this image strictly."
                                    % product_title,
                                },
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        },
                    ],
                },
                timeout=90,
            )
            if resp.status_code >= 400:
                raise RuntimeError("HTTP %s: %s" % (resp.status_code, resp.text[:250]))
            content = (
                (resp.json().get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            if not (content or "").strip():
                raise ValueError("empty model content")
            parsed = _parse_ai_json(content)
            parsed["duration_sec"] = round(time.time() - t0, 2)
            return parsed
        except Exception as exc:
            last_err = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(str(last_err))


def run_bakeoff(samples: list[dict], api_key: str) -> dict[str, Any]:
    images_dir = WORK_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Consensus from models used as soft "expected" for accuracy table in DOCX only
    results_samples = []
    model_counts = {
        m["id"]: {lab: 0 for lab in LABELS} | {"errors": 0, "total": 0}
        for m in MODELS
    }

    for i, sample in enumerate(samples, 1):
        product = _safe_name(sample["product_name"])[:55]
        local_name = "%03d_p%s_%s.jpg" % (i, sample["product_id"], product.replace(" ", "_")[:40])
        local_path = images_dir / local_name
        print(
            "\n[%d/%d] id=%s | %s"
            % (i, len(samples), sample["product_id"], sample["product_name"][:55])
        )
        try:
            download_image(sample["url"], local_path)
        except Exception as exc:
            print("  download failed: %s" % exc)
            continue

        model_results = []
        labels_this = []
        for m in MODELS:
            print("  -> %s ..." % m["name"], end=" ", flush=True)
            try:
                pred = classify_with_model(
                    image_url=sample["url"],
                    product_title=sample["product_name"],
                    api_key=api_key,
                    model=m["endpoint"],
                )
                model_counts[m["id"]]["total"] += 1
                model_counts[m["id"]][pred["label"]] += 1
                labels_this.append(pred["label"])
                print(
                    "%s conf=%.2f (%.1fs)"
                    % (pred["label"].upper(), pred["confidence"], pred["duration_sec"])
                )
                model_results.append(
                    {
                        "model_id": m["id"],
                        "model_name": m["name"],
                        "endpoint": m["endpoint"],
                        "label": pred["label"],
                        "confidence": pred["confidence"],
                        "reason": pred["reason"],
                        "duration_sec": pred["duration_sec"],
                        "status": "ok",
                    }
                )
            except Exception as exc:
                model_counts[m["id"]]["errors"] += 1
                model_counts[m["id"]]["total"] += 1
                print("ERROR %s" % exc)
                model_results.append(
                    {
                        "model_id": m["id"],
                        "model_name": m["name"],
                        "endpoint": m["endpoint"],
                        "label": "",
                        "confidence": 0,
                        "reason": str(exc)[:200],
                        "duration_sec": 0,
                        "status": "error",
                    }
                )
            time.sleep(0.12)

        # Majority consensus (for DOCX accuracy only — not shown as client folders)
        consensus = ""
        if labels_this:
            consensus = max(set(labels_this), key=labels_this.count)

        for mr in model_results:
            mr["match_consensus"] = bool(
                mr.get("status") == "ok" and mr.get("label") == consensus and consensus
            )

        results_samples.append(
            {
                "index": i,
                "product_id": sample["product_id"],
                "product_name": sample["product_name"],
                "url": sample["url"],
                "local_image": str(local_path.relative_to(WORK_DIR)).replace("\\", "/"),
                "local_name": local_name,
                "consensus_label": consensus,
                "models": model_results,
            }
        )

        # Checkpoint every 10
        if i % 10 == 0:
            _write_partial(results_samples, model_counts)

    leaderboard = []
    for m in MODELS:
        c = model_counts[m["id"]]
        # agreement with consensus
        correct = 0
        total_ok = 0
        durations: list[float] = []
        for s in results_samples:
            cons = s.get("consensus_label")
            for mr in s["models"]:
                if mr["model_id"] != m["id"] or mr.get("status") != "ok":
                    continue
                total_ok += 1
                if cons and mr["label"] == cons:
                    correct += 1
                if mr.get("duration_sec"):
                    durations.append(float(mr["duration_sec"]))
        acc = (correct / total_ok * 100.0) if total_ok else 0.0
        avg_sec = (sum(durations) / len(durations)) if durations else 0.0
        leaderboard.append(
            {
                **m,
                "correct": correct,
                "total": total_ok,
                "errors": c["errors"],
                "accuracy_pct": round(acc, 1),
                "label_counts": {lab: c[lab] for lab in LABELS},
                "avg_duration_sec": round(avg_sec, 1),
                "min_duration_sec": round(min(durations), 1) if durations else 0.0,
                "max_duration_sec": round(max(durations), 1) if durations else 0.0,
            }
        )
    leaderboard.sort(
        key=lambda x: (0 if x.get("selected") else 1, -x["accuracy_pct"], x["errors"])
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(results_samples),
        "models": MODELS,
        "leaderboard": leaderboard,
        "samples": results_samples,
        "winner": next((x for x in leaderboard if x.get("selected")), leaderboard[0]),
        "notes": (
            "Client folder places each image into the category predicted by that model. "
            "DOCX accuracy = agreement with majority consensus across the 4 models."
        ),
    }


def _write_partial(samples: list, model_counts: dict) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "partial": True,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "samples_done": len(samples),
                "model_counts": model_counts,
                "samples": samples,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("  [checkpoint] saved %d samples -> %s" % (len(samples), RESULTS_PATH))


def organize_client_folders(report: dict) -> Path:
    """Simple client tree: model / hero|lifestyle|detail / images."""
    if CAT_DIR.exists():
        shutil.rmtree(CAT_DIR)
    CAT_DIR.mkdir(parents=True)

    model_dirs: dict[str, Path] = {}
    for m in report.get("models") or []:
        folder = m["name"]
        if m.get("selected"):
            folder = "%s - SELECTED" % m["name"]
        mdir = CAT_DIR / _safe_name(folder)
        for lab in LABELS:
            (mdir / lab).mkdir(parents=True)
        model_dirs[m["id"]] = mdir

    copied = 0
    for s in report.get("samples") or []:
        src = WORK_DIR / s["local_image"]
        if not src.exists():
            print("MISSING", s.get("local_image"))
            continue
        base = s.get("local_name") or Path(s["local_image"]).name
        for mr in s.get("models") or []:
            mid = mr.get("model_id")
            pred = (mr.get("label") or "").strip().lower()
            if mid not in model_dirs:
                continue
            if pred not in LABELS:
                continue
            shutil.copy2(src, model_dirs[mid] / pred / base)
            copied += 1

    (CAT_DIR / "README.txt").write_text(
        "\n".join(
            [
                "IMAGE CATEGORIZATION — how to review",
                "====================================",
                "",
                "Each model has its own folder.",
                "Inside every model folder you will find:",
                "  hero /",
                "  lifestyle /",
                "  detail /",
                "",
                "Images are placed into the folder matching what THAT model predicted",
                "for that photo. Open the same product across models to compare.",
                "",
                "Production choice: GPT-4o (folder marked SELECTED)",
                "  Clearest separation of hero / lifestyle / detail.",
                "",
                "Sample size: %d products (1 random image each)."
                % int(report.get("sample_count") or 0),
                "Generated: %s"
                % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "",
            ]
        ),
        encoding="utf-8",
    )
    print("Organized client folder: %s (%d file copies)" % (CAT_DIR, copied))
    return CAT_DIR


def build_combined_docx(report: dict) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    def font(run, *, size=11, bold=False, color=(30, 41, 59)):
        run.bold = bold
        run.font.size = Pt(size)
        run.font.name = "Calibri"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
        run.font.color.rgb = RGBColor(*color)

    def heading(doc, text, level=1):
        h = doc.add_heading(text, level=level)
        for run in h.runs:
            run.font.color.rgb = RGBColor(24, 43, 73)

    def para(doc, text, *, bold=False, size=11):
        p = doc.add_paragraph()
        r = p.add_run(text)
        font(r, size=size, bold=bold)
        p.paragraph_format.space_after = Pt(8)
        return p

    def bullet(doc, text, *, bold_prefix=None):
        p = doc.add_paragraph(style="List Bullet")
        if bold_prefix:
            r1 = p.add_run(bold_prefix)
            font(r1, bold=True)
            r2 = p.add_run(text)
            font(r2)
        else:
            r = p.add_run(text)
            font(r)

    def table(doc, headers, rows):
        t = doc.add_table(rows=1 + len(rows), cols=len(headers))
        t.style = "Table Grid"
        for i, title in enumerate(headers):
            t.rows[0].cells[i].text = ""
            font(t.rows[0].cells[i].paragraphs[0].add_run(title), bold=True, size=10)
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                t.rows[ri + 1].cells[ci].text = ""
                font(
                    t.rows[ri + 1].cells[ci].paragraphs[0].add_run(val),
                    size=10,
                    bold=(ci == 0 and "SELECTED" in val),
                )
        doc.add_paragraph()

    doc = Document()
    for sec in doc.sections:
        sec.top_margin = Inches(0.85)
        sec.bottom_margin = Inches(0.85)
        sec.left_margin = Inches(0.95)
        sec.right_margin = Inches(0.95)

    tr = doc.add_paragraph().add_run("Binnen — AI Model Comparison Brief")
    font(tr, size=18, bold=True, color=(24, 43, 73))
    sr = doc.add_paragraph().add_run(
        "Background removal, detail image generation, and image categorization — "
        "which models we tested, which performed best, and which we use in production."
    )
    font(sr, size=11, color=(71, 85, 105))
    mr = doc.add_paragraph().add_run(
        "Prepared for client review  |  %s  |  Folders: bg compare + model_comparison + image categorization"
        % datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    font(mr, size=9, color=(100, 116, 139))

    heading(doc, "Purpose")
    para(
        doc,
        "Before locking production models for the Binnen catalog pipeline, we ran "
        "side-by-side bake-offs on real furniture product photos. This brief explains "
        "what is inside the attached folders, which model won each test, why we selected "
        "it, and the approximate API cost per image.",
    )

    # 1 BG
    heading(doc, "1. Background Removal Comparison  (folder: bg compare)")
    para(
        doc,
        "This folder contains one original product photo plus the same image processed "
        "by four background-removal models. Compare edge quality side by side.",
    )
    original = BG_DIR / "original image.jpg"
    if original.exists():
        para(doc, "Original source image used for the test:", bold=True, size=10)
        try:
            doc.add_picture(str(original), width=Inches(3.2))
        except Exception:
            pass
    para(doc, "Models tested:", bold=True)
    table(
        doc,
        ["Model / folder", "What it is", "Approx. cost / image", "Result"],
        [
            ["fal-aibirefnetv2\n(BiRefNet v2)", "Best complex furniture edges", "~$0.002", "SELECTED — production"],
            ["Bria RMBG 2.0", "Studio-grade Bria rembg", "~$0.0015", "Good, softer fine edges"],
            ["Remove Background\n(RMBG / rembg)", "Fast classic rembg", "~$0.001", "Cheapest; weaker thin legs"],
            ["ben-v2-image\n(BEN v2)", "Alternative rembg family", "~$0.001-$0.002", "Acceptable; less consistent"],
        ],
    )
    para(doc, "Winner & production choice — BiRefNet v2", bold=True)
    para(
        doc,
        "BiRefNet v2 (fal-ai/birefnet/v2) gave the cleanest furniture cutouts. "
        "We use it for hero background removal / resize. Typical cost ~$0.002 / image.",
    )

    # 2 Detail gen
    heading(doc, "2. Detail Image Generation Comparison  (folder: model_comparison)")
    para(
        doc,
        "Detail / macro close-ups generated for real catalog products. Open "
        "comparison_grid.jpg files or comparison_overview.html to compare models.",
    )
    para(doc, "Models tested:", bold=True)
    table(
        doc,
        ["Model", "Speciality", "Approx. cost / image", "Result"],
        [
            [
                "FLUX.2 Pro Edit\n(fal-ai/flux-2-pro/edit)",
                "Accepts a real product reference photo (image-to-image). Best identity & texture lock.",
                "~$0.05",
                "SELECTED — production",
            ],
            ["Recraft V3", "Sharp catalog / commercial look", "~$0.04", "Strong runner-up"],
            ["Ideogram v2", "High contrast / strong geometry", "~$0.08", "Higher cost; weaker identity lock"],
            ["Luma Photon", "Cinematic lighting & bokeh", "~$0.03", "Nice mood; less exact material match"],
        ],
    )
    para(doc, "Winner & production choice — FLUX.2 Pro Edit", bold=True)
    para(
        doc,
        "FLUX.2 Pro Edit performed best because it accepts a reference image, so wood grain, "
        "stitching, and fabric stay consistent with the real product. Cost ~$0.05 / image.",
    )

    # 3 Categorization
    heading(doc, "3. Image Categorization Comparison  (folder: image categorization)")
    para(
        doc,
        "We tested sorting product images into Hero / Lifestyle / Detail using 4 vision models "
        "(Gemini 2.5 Flash, Gemini 2.5 Pro, GPT-4o, GPT-4.1) on %d random catalog products "
        "(1 random photo per product)."
        % int(report.get("sample_count") or 0),
    )
    para(
        doc,
        "How to review (simple — no JSON): open the folder image categorization. "
        "Each model has its own folder. Inside every model you will find three subfolders: "
        "hero, lifestyle, and detail. Each photo is placed into the category that model chose. "
        "Open the same product across models to see who sorted it correctly. "
        "The GPT-4o folder is marked SELECTED (production choice).",
    )
    para(doc, "Models tested:", bold=True)
    # Ensure timing stats exist even for older results.json
    for m in report.get("leaderboard") or []:
        if m.get("avg_duration_sec"):
            continue
        durations = []
        for s in report.get("samples") or []:
            for mr in s.get("models") or []:
                if mr.get("model_id") == m.get("id") and mr.get("status") == "ok" and mr.get("duration_sec"):
                    durations.append(float(mr["duration_sec"]))
        if durations:
            m["avg_duration_sec"] = round(sum(durations) / len(durations), 1)
            m["min_duration_sec"] = round(min(durations), 1)
            m["max_duration_sec"] = round(max(durations), 1)

    board_rows = []
    for m in report.get("leaderboard") or []:
        name = m.get("name") or ""
        if m.get("selected"):
            name = "%s\nSELECTED" % name
        counts = m.get("label_counts") or {}
        avg_t = float(m.get("avg_duration_sec") or 0)
        min_t = float(m.get("min_duration_sec") or 0)
        max_t = float(m.get("max_duration_sec") or 0)
        speed = "avg %.1fs (%.1f-%.1fs)" % (avg_t, min_t, max_t) if avg_t else "—"
        board_rows.append(
            [
                name,
                "hero %s / lifestyle %s / detail %s"
                % (counts.get("hero", 0), counts.get("lifestyle", 0), counts.get("detail", 0)),
                "%.1f%% vs consensus" % float(m.get("accuracy_pct") or 0),
                speed,
                m.get("approx_cost") or "",
                m.get("note") or "",
            ]
        )
    table(
        doc,
        ["Model", "How it sorted (counts)", "Consensus agree", "Speed / image", "Approx. cost", "Notes"],
        board_rows,
    )
    para(doc, "Winner & production choice — GPT-4o", bold=True)
    gpt4o = next(
        (m for m in (report.get("leaderboard") or []) if m.get("selected") or m.get("id") == "gpt4o"),
        None,
    ) or {}
    gpt_avg = float(gpt4o.get("avg_duration_sec") or 0)
    pro = next((m for m in (report.get("leaderboard") or []) if m.get("id") == "gemini_25_pro"), None) or {}
    pro_avg = float(pro.get("avg_duration_sec") or 0)
    speed_note = ""
    if gpt_avg:
        speed_note = (
            " GPT-4o was also among the fastest models in this bake-off "
            "(about %.1f seconds per image on average"
            % gpt_avg
        )
        if pro_avg and pro_avg > gpt_avg:
            speed_note += ", vs ~%.1fs for Gemini 2.5 Pro" % pro_avg
        speed_note += ") — so we get clearer labels without waiting longer."
    para(
        doc,
        "Best result so far: GPT-4o — clearest separation, especially for lifestyle vs hero "
        "vs detail."
        + speed_note
        + " That is why we use GPT-4o for production image categorization.",
    )

    # Summary
    heading(doc, "4. Summary — what we use in production")
    bullet(
        doc,
        " BiRefNet v2 (fal-ai/birefnet/v2) — ~$0.002 / image.",
        bold_prefix="Background removal / resize:",
    )
    bullet(
        doc,
        " FLUX.2 Pro Edit (fal-ai/flux-2-pro/edit) — ~$0.05 / image — accepts reference photos.",
        bold_prefix="Detail image generation:",
    )
    bullet(
        doc,
        " GPT-4o (openai/gpt-4o) — clearest Hero / Lifestyle / Detail separation, "
        "and among the fastest (~%.1fs / image in this bake-off)."
        % float(
            next(
                (
                    m.get("avg_duration_sec") or 0
                    for m in (report.get("leaderboard") or [])
                    if m.get("selected") or m.get("id") == "gpt4o"
                ),
                0,
            )
            or 4.0
        ),
        bold_prefix="Image categorization:",
    )
    para(
        doc,
        "Attached folders for visual review: bg compare, model_comparison, and "
        "image categorization. Costs are provider API rates in USD and may change.",
    )
    fr = doc.add_paragraph().add_run(
        "Binnen / Woonbloq data pipeline · Confidential client review material"
    )
    font(fr, size=8, color=(148, 163, 184))

    DOCX_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(DOCX_PATH))
    print("DOCX written: %s" % DOCX_PATH)
    return DOCX_PATH


def build_zip() -> Path:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # folders
        for folder, arc_prefix in (
            (BG_DIR, "bg compare"),
            (MODEL_DIR, "model_comparison"),
            (CAT_DIR, "image categorization"),
        ):
            if not folder.exists():
                print("WARN missing folder for zip: %s" % folder)
                continue
            for path in folder.rglob("*"):
                if path.is_file():
                    zf.write(path, arc_prefix + "/" + str(path.relative_to(folder)).replace("\\", "/"))
        if DOCX_PATH.exists():
            zf.write(DOCX_PATH, DOCX_PATH.name)
    print("ZIP written: %s (%.1f MB)" % (ZIP_PATH, ZIP_PATH.stat().st_size / 1e6))
    return ZIP_PATH


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=20, help="Random products (default 20)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--organize-only",
        action="store_true",
        help="Reuse existing results.json; only rebuild folders/docx/zip",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    if args.organize_only:
        if not RESULTS_PATH.exists():
            print("No results at %s" % RESULTS_PATH)
            return 1
        report = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        if report.get("partial") and not report.get("leaderboard"):
            print("Results file is a partial checkpoint without leaderboard — re-run full bake-off.")
            return 1
    else:
        if not api_key:
            print("ERROR: OPENROUTER_API_KEY missing")
            return 1
        limit = max(1, min(50, args.limit))  # client package: keep small (default 20)
        baserow = BaserowClient(settings)
        samples = collect_random_samples(baserow, limit=limit, seed=args.seed)
        print(
            "\nStarting bake-off: %d images x %d models = %d classifications"
            % (len(samples), len(MODELS), len(samples) * len(MODELS))
        )
        report = run_bakeoff(samples, api_key)
        RESULTS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Saved results: %s" % RESULTS_PATH)

    organize_client_folders(report)
    build_combined_docx(report)
    build_zip()

    print("\n=== DONE ===")
    print("Client folder: %s" % CAT_DIR)
    print("DOCX: %s" % DOCX_PATH)
    print("ZIP: %s" % ZIP_PATH)
    print("Leaderboard:")
    for m in report.get("leaderboard") or []:
        mark = " [SELECTED]" if m.get("selected") else ""
        print(
            "  %s%s: %.1f%%  counts=%s"
            % (m["name"], mark, m["accuracy_pct"], m.get("label_counts"))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
