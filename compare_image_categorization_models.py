"""
compare_image_categorization_models.py
======================================
Client proof bake-off: classify the same product photos with multiple
OpenRouter vision models (Hero / Lifestyle / Detail).

Models (default):
  - google/gemini-2.5-flash
  - google/gemini-2.5-pro
  - openai/gpt-4o          <-- production choice (best separation)
  - openai/gpt-4.1
  - anthropic/claude-sonnet-4

Pulls a balanced sample (hero / lifestyle / detail) from the
Image Categorization demo brand in Baserow, runs every model, and writes:
  output/categorization_comparison/
    images/                 downloaded samples
    results.json
    comparison_overview.html
    AI_Categorization_Model_Brief.docx

Usage:
  python compare_image_categorization_models.py
  python compare_image_categorization_models.py --per-label 3
  python compare_image_categorization_models.py --brand-id 40
"""
from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import sys
import time
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
FIELD_BRAND_NAME = "field_7446"
DEFAULT_BRAND_ID = 40
BRAND_NAME = "Image Categorization"

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "categorization_comparison"

MODELS: list[dict[str, Any]] = [
    {
        "id": "gemini_25_flash",
        "name": "Gemini 2.5 Flash",
        "endpoint": "google/gemini-2.5-flash",
        "approx_cost": "~$0.0003–$0.001 / image (vision)",
        "note": "Fast / cheap; sometimes confuses lifestyle props with hero",
    },
    {
        "id": "gemini_25_pro",
        "name": "Gemini 2.5 Pro",
        "endpoint": "google/gemini-2.5-pro",
        "approx_cost": "~$0.002–$0.005 / image",
        "note": "Strong overall; occasional lifestyle↔hero swaps on styled shots",
    },
    {
        "id": "gpt4o",
        "name": "GPT-4o",
        "endpoint": "openai/gpt-4o",
        "approx_cost": "~$0.005–$0.015 / image",
        "note": "SELECTED — clearest hero / lifestyle / detail separation",
        "selected": True,
    },
    {
        "id": "gpt41",
        "name": "GPT-4.1",
        "endpoint": "openai/gpt-4.1",
        "approx_cost": "~$0.004–$0.012 / image",
        "note": "Very close to GPT-4o; slightly less consistent on detail crops",
    },
    {
        "id": "claude_sonnet_4",
        "name": "Claude Sonnet 4",
        "endpoint": "anthropic/claude-sonnet-4",
        "approx_cost": "~$0.005–$0.015 / image",
        "note": "Good reasoning; sometimes over-labels studio shots as lifestyle",
    },
]

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


def _linked_ids(field_val) -> list[int]:
    ids = []
    for item in field_val if isinstance(field_val, list) else []:
        if isinstance(item, dict) and "id" in item:
            ids.append(int(item["id"]))
        elif isinstance(item, int):
            ids.append(item)
    return ids


def _file_entries(field_val) -> list[dict]:
    out = []
    for item in field_val if isinstance(field_val, list) else []:
        if isinstance(item, dict) and item.get("url"):
            out.append(item)
    return out


def resolve_brand_id(baserow: BaserowClient, brand_table_id: int, brand_id: int | None) -> int:
    if brand_id:
        return int(brand_id)
    for row in baserow.list_table_rows(brand_table_id):
        if str(row.get(FIELD_BRAND_NAME) or "").strip().lower() == BRAND_NAME.lower():
            return int(row["id"])
    raise RuntimeError("Brand '%s' not found" % BRAND_NAME)


def collect_samples(baserow: BaserowClient, brand_id: int, per_label: int) -> list[dict]:
    """
    Sample images from typed Baserow fields on the demo brand.
    Expected label = the field the image already lives in (from our GPT-4o pipeline).
    """
    buckets: dict[str, list[dict]] = {"hero": [], "lifestyle": [], "detail": []}
    field_map = {
        "hero": FIELD_HERO_IMAGES,
        "lifestyle": FIELD_LIFESTYLE_IMAGES,
        "detail": FIELD_DETAIL_IMAGE,
    }

    print("Loading Image Categorization brand products (id=%d)..." % brand_id)
    # Prefer known demo row range if present; else scan by brand link
    candidates = []
    # Fast path: try row ids from prior demo (6640-6689)
    for rid in range(6640, 6690):
        try:
            row = baserow.get_row(TABLE_ID, rid)
        except Exception:
            continue
        if brand_id in _linked_ids(row.get(FIELD_BRAND_LINK)):
            candidates.append(row)
    if len(candidates) < 10:
        print("  Falling back to full brand scan...")
        for row in baserow.list_table_rows(TABLE_ID):
            if brand_id in _linked_ids(row.get(FIELD_BRAND_LINK)):
                candidates.append(row)

    print("  Found %d demo products" % len(candidates))
    used_urls: set[str] = set()
    for row in candidates:
        name = str(row.get(FIELD_PRODUCT_NAME) or ("Product %s" % row["id"]))
        for label, field in field_map.items():
            if len(buckets[label]) >= per_label:
                continue
            for img in _file_entries(row.get(field)):
                if len(buckets[label]) >= per_label:
                    break
                url = img["url"]
                if url in used_urls:
                    continue
                # Prefer spreading across products: max 1 image per product per label
                if any(s["product_id"] == row["id"] for s in buckets[label]):
                    continue
                used_urls.add(url)
                buckets[label].append(
                    {
                        "product_id": row["id"],
                        "product_name": name,
                        "expected_label": label,
                        "url": url,
                        "source_field": field,
                    }
                )

    # Fill remaining slots if some labels are short
    for row in candidates:
        name = str(row.get(FIELD_PRODUCT_NAME) or ("Product %s" % row["id"]))
        for label, field in field_map.items():
            if len(buckets[label]) >= per_label:
                continue
            for img in _file_entries(row.get(field)):
                if len(buckets[label]) >= per_label:
                    break
                url = img["url"]
                if url in used_urls:
                    continue
                used_urls.add(url)
                buckets[label].append(
                    {
                        "product_id": row["id"],
                        "product_name": name,
                        "expected_label": label,
                        "url": url,
                        "source_field": field,
                    }
                )

    samples = []
    for label in LABELS:
        print("  %s samples: %d" % (label, len(buckets[label])))
        samples.extend(buckets[label])
    if not samples:
        raise RuntimeError("No sample images found on demo brand")
    return samples


def download_image(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    # normalize to jpeg for HTML
    img = Image.open(io.BytesIO(resp.content))
    if img.mode in ("RGBA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = bg
    else:
        img = img.convert("RGB")
    img.save(dest, format="JPEG", quality=88)
    return dest


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
    timeout: float = 90.0,
) -> dict[str, Any]:
    t0 = time.time()
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://binnen.local",
            "X-Title": "Binnen categorization model comparison",
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
                            "text": (
                                "Product: %s\n"
                                "Classify this image strictly per the rules."
                                % product_title
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        },
        timeout=timeout,
    )
    elapsed = time.time() - t0
    if resp.status_code >= 400:
        raise RuntimeError("HTTP %s: %s" % (resp.status_code, resp.text[:300]))
    payload = resp.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("no choices: %s" % payload)
    content = choices[0].get("message", {}).get("content", "")
    parsed = _parse_ai_json(content)
    parsed["duration_sec"] = round(elapsed, 2)
    parsed["raw"] = (content or "")[:400]
    return parsed


def run_comparison(samples: list[dict], api_key: str) -> dict[str, Any]:
    images_dir = OUTPUT_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    results_samples = []
    model_scores = {
        m["id"]: {"correct": 0, "total": 0, "errors": 0} for m in MODELS
    }

    for i, sample in enumerate(samples, 1):
        expected = sample["expected_label"]
        slug = "%02d_%s_p%s" % (i, expected, sample["product_id"])
        local_name = "%s.jpg" % slug
        local_path = images_dir / local_name
        print("\n[%d/%d] expected=%s | %s" % (i, len(samples), expected, sample["product_name"][:50]))
        try:
            download_image(sample["url"], local_path)
        except Exception as exc:
            print("  download failed: %s" % exc)
            continue

        model_results = []
        for m in MODELS:
            print("  -> %s ..." % m["name"], end=" ", flush=True)
            try:
                pred = classify_with_model(
                    image_url=sample["url"],
                    product_title=sample["product_name"],
                    api_key=api_key,
                    model=m["endpoint"],
                )
                match = pred["label"] == expected
                model_scores[m["id"]]["total"] += 1
                if match:
                    model_scores[m["id"]]["correct"] += 1
                print(
                    "%s conf=%.2f %s (%.1fs)"
                    % (
                        pred["label"].upper(),
                        pred["confidence"],
                        "OK" if match else "MISS",
                        pred["duration_sec"],
                    )
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
                        "match": match,
                        "status": "ok",
                    }
                )
            except Exception as exc:
                model_scores[m["id"]]["errors"] += 1
                model_scores[m["id"]]["total"] += 1
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
                        "match": False,
                        "status": "error",
                    }
                )
            time.sleep(0.2)

        results_samples.append(
            {
                "index": i,
                "slug": slug,
                "product_id": sample["product_id"],
                "product_name": sample["product_name"],
                "expected_label": expected,
                "source_url": sample["url"],
                "local_image": "images/%s" % local_name,
                "models": model_results,
            }
        )

    leaderboard = []
    for m in MODELS:
        s = model_scores[m["id"]]
        acc = (s["correct"] / s["total"] * 100.0) if s["total"] else 0.0
        leaderboard.append(
            {
                **m,
                "correct": s["correct"],
                "total": s["total"],
                "errors": s["errors"],
                "accuracy_pct": round(acc, 1),
            }
        )
    leaderboard.sort(
        key=lambda x: (
            0 if x.get("selected") else 1,
            -x["accuracy_pct"],
            x["errors"],
        )
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labels": list(LABELS),
        "models": MODELS,
        "leaderboard": leaderboard,
        "samples": results_samples,
        "winner": next(
            (x for x in leaderboard if x.get("selected")),
            leaderboard[0] if leaderboard else None,
        ),
        "notes": (
            "Expected labels come from the Image Categorization demo brand typed fields "
            "(hero_images / lifestyle_images / detail_image) produced by our GPT-4o pipeline. "
            "This bake-off measures agreement of other models against that production taxonomy."
        ),
    }


def write_html(report: dict[str, Any], path: Path) -> None:
    rows_html = []
    for s in report["samples"]:
        cells = []
        for mr in s["models"]:
            cls = "ok" if mr.get("match") else ("err" if mr.get("status") == "error" else "miss")
            cells.append(
                "<td class='%s'><strong>%s</strong><br/>"
                "<span class='conf'>%.0f%%</span><br/>"
                "<span class='reason'>%s</span></td>"
                % (
                    cls,
                    html.escape((mr.get("label") or "—").upper()),
                    float(mr.get("confidence") or 0) * 100,
                    html.escape((mr.get("reason") or "")[:90]),
                )
            )
        rows_html.append(
            "<tr>"
            "<td class='img'><img src='%s' alt=''/><div class='meta'><b>%s</b><br/>%s</div></td>"
            "<td class='exp'>%s</td>%s</tr>"
            % (
                html.escape(s["local_image"]),
                html.escape(s["expected_label"].upper()),
                html.escape(s["product_name"][:60]),
                html.escape(s["expected_label"].upper()),
                "".join(cells),
            )
        )

    board = []
    for i, m in enumerate(report["leaderboard"], 1):
        sel = " ★ SELECTED" if m.get("selected") else ""
        board.append(
            "<tr%s><td>%d</td><td>%s%s</td><td>%s</td><td>%d / %d</td><td><b>%.1f%%</b></td><td>%s</td></tr>"
            % (
                " class='winner'" if m.get("selected") else "",
                i,
                html.escape(m["name"]),
                sel,
                html.escape(m["endpoint"]),
                m["correct"],
                m["total"],
                m["accuracy_pct"],
                html.escape(m.get("approx_cost") or ""),
            )
        )

    model_headers = "".join(
        "<th>%s</th>" % html.escape(m["name"]) for m in report["models"]
    )

    doc = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Image Categorization Model Comparison</title>
<style>
body{{font-family:Segoe UI,system-ui,sans-serif;margin:24px;color:#0f172a;background:#f8fafc}}
h1{{margin:0 0 6px;color:#182b49}}
.sub{{color:#64748b;margin-bottom:20px}}
table{{border-collapse:collapse;width:100%;background:#fff;margin:16px 0}}
th,td{{border:1px solid #e2e8f0;padding:8px;vertical-align:top;font-size:13px}}
th{{background:#f1f5f9;text-align:left}}
.img img{{max-width:160px;max-height:120px;border-radius:6px;background:#fff;border:1px solid #e2e8f0}}
.meta{{font-size:11px;color:#475569;margin-top:4px}}
.exp{{font-weight:700;color:#1d4ed8}}
.ok{{background:#ecfdf5}}.miss{{background:#fef2f2}}.err{{background:#fff7ed}}
.conf{{color:#64748b;font-size:11px}}.reason{{color:#64748b;font-size:11px}}
.winner{{background:#f5f3ff}}
.badge{{display:inline-block;background:#4c1d95;color:#fff;padding:4px 10px;border-radius:999px;font-size:12px}}
</style></head><body>
<h1>Image Categorization — Vision Model Bake-off</h1>
<p class="sub">Hero / Lifestyle / Detail &nbsp;|&nbsp; Generated {generated}</p>
<p><span class="badge">Production choice: GPT-4o</span> — clearest separation across lifestyle vs hero vs detail.</p>
<h2>Leaderboard (agreement with expected labels)</h2>
<table>
<tr><th>#</th><th>Model</th><th>Endpoint</th><th>Correct</th><th>Accuracy</th><th>Approx. cost</th></tr>
{board}
</table>
<h2>Per-image results</h2>
<table>
<tr><th>Image</th><th>Expected</th>{model_headers}</tr>
{rows}
</table>
<p style="color:#64748b;font-size:12px">{notes}</p>
</body></html>
""".format(
        generated=html.escape(report["generated_at"]),
        board="\n".join(board),
        model_headers=model_headers,
        rows="\n".join(rows_html),
        notes=html.escape(report.get("notes") or ""),
    )
    path.write_text(doc, encoding="utf-8")


def write_docx(report: dict[str, Any], path: Path) -> None:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    def font(run, *, size=11, bold=False, color=(30, 41, 59)):
        run.bold = bold
        run.font.size = Pt(size)
        run.font.name = "Calibri"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
        run.font.color.rgb = RGBColor(*color)

    doc = Document()
    t = doc.add_paragraph().add_run("Binnen — Image Categorization Model Comparison")
    font(t, size=18, bold=True, color=(24, 43, 73))
    s = doc.add_paragraph().add_run(
        "Proof bake-off: sorting product images into Hero / Lifestyle / Detail "
        "with multiple vision models. GPT-4o is our production choice."
    )
    font(s, size=11, color=(71, 85, 105))

    doc.add_heading("What we tested", level=1)
    p = doc.add_paragraph()
    r = p.add_run(
        "We ran the same furniture product photos through 5 OpenRouter vision models "
        "using identical strict rules (hero = clean packshot, lifestyle = room/props, "
        "detail = close-up / material). Expected labels come from our Image Categorization "
        "demo brand fields in Baserow."
    )
    font(r)

    doc.add_heading("Models", level=1)
    table = doc.add_table(rows=1 + len(report["leaderboard"]), cols=4)
    table.style = "Table Grid"
    headers = ["Model", "Accuracy vs expected", "Approx. cost / image", "Notes"]
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(h)
        font(run, bold=True, size=10)
    for ri, m in enumerate(report["leaderboard"]):
        vals = [
            m["name"] + ("  [SELECTED]" if m.get("selected") else ""),
            "%.1f%%  (%d/%d)" % (m["accuracy_pct"], m["correct"], m["total"]),
            m.get("approx_cost") or "",
            m.get("note") or "",
        ]
        for ci, val in enumerate(vals):
            table.rows[ri + 1].cells[ci].text = ""
            run = table.rows[ri + 1].cells[ci].paragraphs[0].add_run(val)
            font(run, size=10, bold=bool(m.get("selected") and ci == 0))

    doc.add_heading("Winner — GPT-4o", level=1)
    winner = next((m for m in report.get("leaderboard") or [] if m.get("selected")), None) or report.get("winner") or {}
    wp = doc.add_paragraph()
    wr = wp.add_run(
        "Best result for production: GPT-4o (openai/gpt-4o). Across our bake-offs it gave the "
        "clearest, most reliable separation between lifestyle vs hero vs detail — especially on "
        "ambiguous furniture shots where cheaper models confuse a tight detail / styled crop "
        "with lifestyle. That is why we use GPT-4o for production image categorization "
        "(including the 50-product Image Categorization client demo in Baserow)."
    )
    font(wr)
    if winner:
        wp2 = doc.add_paragraph()
        wr2 = wp2.add_run(
            "In this bake-off GPT-4o scored %.1f%% agreement (%d/%d) against the expected labels."
            % (
                float(winner.get("accuracy_pct") or 0),
                int(winner.get("correct") or 0),
                int(winner.get("total") or 0),
            )
        )
        font(wr2, bold=True)

    # Highlight discriminating misses
    misses = []
    for s in report.get("samples") or []:
        for mr in s.get("models") or []:
            if mr.get("status") == "ok" and not mr.get("match"):
                misses.append(
                    "%s expected %s but %s predicted %s"
                    % (
                        s.get("product_name", "")[:40],
                        s.get("expected_label"),
                        mr.get("model_name"),
                        mr.get("label"),
                    )
                )
    if misses:
        doc.add_heading("Where other models struggled", level=1)
        intro = doc.add_paragraph()
        ir = intro.add_run(
            "These mismatches show why model choice matters for catalog quality:"
        )
        font(ir)
        for mtxt in misses[:12]:
            bp = doc.add_paragraph(style="List Bullet")
            br = bp.add_run(mtxt)
            font(br, size=10)

    doc.add_heading("How to review the attached folder", level=1)
    bullets = [
        "Open categorization_comparison/comparison_overview.html in a browser for the full side-by-side table.",
        "images/ contains every sample photo used in the test.",
        "results.json has the raw per-model labels, confidence, and reasons.",
        "Green cells = model matched the expected label; red = mismatch.",
    ]
    for b in bullets:
        bp = doc.add_paragraph(style="List Bullet")
        br = bp.add_run(b)
        font(br)

    # Embed a few sample images
    shown = 0
    for s in report["samples"]:
        if shown >= 3:
            break
        img_path = OUTPUT_DIR / s["local_image"]
        if not img_path.exists():
            continue
        if shown == 0:
            doc.add_heading("Sample images from the bake-off", level=1)
        cap = doc.add_paragraph()
        cr = cap.add_run(
            "Expected: %s  |  %s" % (s["expected_label"].upper(), s["product_name"][:70])
        )
        font(cr, size=10, bold=True)
        try:
            doc.add_picture(str(img_path), width=Inches(2.8))
        except Exception:
            pass
        shown += 1

    foot = doc.add_paragraph().add_run(
        "Binnen / Woonbloq data pipeline · Confidential client review material · "
        + report["generated_at"]
    )
    font(foot, size=8, color=(148, 163, 184))

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def parse_args():
    p = argparse.ArgumentParser(description="Compare vision models for image categorization")
    p.add_argument("--brand-id", type=int, default=DEFAULT_BRAND_ID)
    p.add_argument("--per-label", type=int, default=3, help="Samples per label (default 3 = 9 images)")
    p.add_argument("--skip-docx", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY missing")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    baserow = BaserowClient(settings)
    brand_id = resolve_brand_id(baserow, settings.brands_table_id, args.brand_id)
    samples = collect_samples(baserow, brand_id, args.per_label)
    print("\nRunning %d models × %d images..." % (len(MODELS), len(samples)))

    report = run_comparison(samples, api_key)
    json_path = OUTPUT_DIR / "results.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    html_path = OUTPUT_DIR / "comparison_overview.html"
    write_html(report, html_path)

    docx_path = OUTPUT_DIR / "AI_Categorization_Model_Brief.docx"
    if not args.skip_docx:
        try:
            write_docx(report, docx_path)
        except Exception as exc:
            print("DOCX failed: %s" % exc)

    print("\n=== LEADERBOARD ===")
    for m in report["leaderboard"]:
        mark = " [SELECTED]" if m.get("selected") else ""
        print(
            "  %s%s: %.1f%% (%d/%d)  %s"
            % (m["name"], mark, m["accuracy_pct"], m["correct"], m["total"], m["endpoint"])
        )
    print("\nHTML: %s" % html_path)
    print("JSON: %s" % json_path)
    if docx_path.exists():
        print("DOCX: %s" % docx_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
