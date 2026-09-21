"""
compare_detail_image_models.py
==============================
Standalone comparison tool for AI-generated product detail & macro images.

Selects 2 products from Baserow, extracts 2 distinct detail features per product
via OpenRouter Vision (GPT-4o), generates each detail feature across 4 top-tier
AI model families (FLUX.2 Pro Edit, Recraft V3, Ideogram v2, Luma Photon),
and saves the results model-wise with side-by-side comparison grids and an
interactive HTML report.

Usage:
  python compare_detail_image_models.py
  python compare_detail_image_models.py --num-products 2 --features-per-product 2
  python compare_detail_image_models.py --ids 2,4
  python compare_detail_image_models.py --output-dir output/model_comparison
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from baserow_client import BaserowClient
from config import load_settings

# Set standard output to UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Load environment variables
load_dotenv()

FAL_KEY = os.getenv("FAL_KEY", "").strip().strip('"').strip("'")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"').strip("'")

TARGET_WIDTH = 1760
TARGET_HEIGHT = 1100
OUTPUT_DIR_DEFAULT = Path(__file__).resolve().parent / "output" / "model_comparison"


# Model Definitions & Specifications
@dataclass
class ModelSpec:
    id: str
    name: str
    provider: str
    badge: str
    cost_usd: float
    endpoint: str
    speciality: str
    why_use: str

    def build_payload(self, prompt: str, hero_url: str) -> dict[str, Any]:
        """Construct payload specific to this model's fal.ai API format."""
        if self.id == "flux2_pro":
            return {
                "prompt": (
                    f"{prompt} Focus tightly on this exact feature as an extreme macro close-up. "
                    f"Match the colors, materials, and textures seen in the reference photo. "
                    f"Clean pure white studio background (#FFFFFF), no distracting props."
                ),
                "image_urls": [hero_url],
                "image_size": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT},
                "output_format": "jpeg",
                "safety_tolerance": "2",
                "enable_safety_checker": True,
            }
        elif self.id == "recraft_v3":
            return {
                "prompt": (
                    f"{prompt}, extreme macro detail, professional commercial catalog photography, "
                    f"razor-sharp focus, studio lighting, clean white background"
                ),
                "style": "realistic_image",
                "image_size": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT},
            }
        elif self.id == "ideogram_v2":
            return {
                "prompt": (
                    f"{prompt}, extreme macro close-up, sharp joinery and texture, "
                    f"clean commercial studio lighting, pure white background, high contrast, 8k resolution"
                ),
                "aspect_ratio": "16:9",
                "style_type": "REALISTIC",
            }
        elif self.id == "luma_photon":
            return {
                "prompt": (
                    f"{prompt}, extreme macro photography, soft commercial lighting, "
                    f"bokeh depth of field, premium luxury product finish, clean studio backdrop"
                ),
                "aspect_ratio": "16:9",
            }
        else:
            return {
                "prompt": prompt,
                "image_size": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT},
            }


MODELS: list[ModelSpec] = [
    ModelSpec(
        id="flux2_pro",
        name="FLUX.2 Pro Edit",
        provider="Black Forest Labs",
        badge="Identity & Texture Lock",
        cost_usd=0.050,
        endpoint="fal-ai/flux-2-pro/edit",
        speciality="Direct Image-to-Image reference and exact texture continuity",
        why_use="Best for matching real-world wood grains, leather pores, and fabric weaves directly from the product photo.",
    ),
    ModelSpec(
        id="recraft_v3",
        name="Recraft V3",
        provider="Recraft AI",
        badge="Commercial Sharpness",
        cost_usd=0.040,
        endpoint="fal-ai/recraft-v3",
        speciality="Ranked #1 for catalog precision, vector sharpness, and clean metallic finishes",
        why_use="Best for ultra-crisp joinery edges, polished metals, hardware, and modern industrial catalog images.",
    ),
    ModelSpec(
        id="ideogram_v2",
        name="Ideogram v2",
        provider="Ideogram AI (ex-Google Brain)",
        badge="Graphic & Structural Contrast",
        cost_usd=0.080,
        endpoint="fal-ai/ideogram/v2",
        speciality="Strongest geometric boundary definition and high-contrast studio fidelity",
        why_use="Best for distinct structural parts, hardware fixtures, clean seam contrast, and sharp geometry.",
    ),
    ModelSpec(
        id="luma_photon",
        name="Luma Photon",
        provider="Luma AI",
        badge="Cinematic Lighting & Bokeh",
        cost_usd=0.030,
        endpoint="fal-ai/luma-photon",
        speciality="Photorealistic neural rendering with natural light reflection & shallow depth of field",
        why_use="Best for luxury catalog aesthetic, warm ambient room light, soft bokeh, and premium finishes.",
    ),
]


# Helper Image Functions
def download_image(url: str, timeout: int = 30) -> Image.Image:
    """Download an image from a URL and return a PIL RGB image."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def fit_to_canvas(img: Image.Image, target_w: int = TARGET_WIDTH, target_h: int = TARGET_HEIGHT) -> Image.Image:
    """Pad image onto a pure white canvas preserving aspect ratio without stretching."""
    img = img.convert("RGB")
    w, h = img.size
    if (w, h) == (target_w, target_h):
        return img

    scale = min(target_w / w, target_h / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    if (new_w, new_h) != (w, h):
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    canvas.paste(img, (paste_x, paste_y))
    return canvas


def slugify(text: str) -> str:
    """Create a safe filesystem slug."""
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "_", text)[:40] or "product"


# OpenRouter Vision Feature Identification
def identify_features_via_openrouter(
    hero_url: str,
    product_name: str,
    product_desc: str,
    openrouter_key: str,
    num_features: int = 2,
) -> list[dict[str, str]]:
    """
    Use GPT-4o Vision to inspect the product hero image and identify 2 distinct
    macro detail features (e.g. material/texture vs joinery/hardware).
    """
    if not openrouter_key:
        print("  [!] OPENROUTER_API_KEY missing. Using fallback feature definitions.")
        return _fallback_features(product_name, product_desc, num_features)

    system_prompt = (
        "You are a professional luxury commercial furniture & product photographer and AI director. "
        "Your task is to analyze the product photo and generate extreme macro close-up detail prompts "
        "for AI image generators. Each feature must represent a distinct physical close-up."
    )

    user_prompt = f"""Analyze this product photo:
Product Title: {product_name}
Description: {product_desc[:300] if product_desc else 'N/A'}

Identify EXACTLY {num_features} distinct, physical close-up features (e.g., Feature 1: Material/upholstery/wood texture weave close-up; Feature 2: Joinery, legs, stitching, edge, or hardware close-up).

Return ONLY a valid JSON object matching this schema:
{{
  "features": [
    {{
      "feature_name": "Material & Texture Weave",
      "feature_type": "material_texture",
      "macro_prompt": "Extreme commercial macro photograph close-up of the upholstery fabric texture of {product_name}, showing fine woven threads, rich tactile grain, soft directional studio lighting, 8k resolution, crisp detail, seamless pure white background"
    }},
    {{
      "feature_name": "Joinery & Structural Edge",
      "feature_type": "joinery_structure",
      "macro_prompt": "Extreme macro studio close-up of the precision joinery and finish of {product_name}, showing craftsman seam, smooth bevelled edge, subtle reflection, pure white background, commercial catalog quality"
    }}
  ]
}}
"""

    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": hero_url}},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=45,
        )
        if resp.ok:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            features = parsed.get("features", [])
            if len(features) >= num_features:
                return features[:num_features]
    except Exception as exc:
        print(f"  [!] OpenRouter Vision error: {exc}. Using fallback prompts.")

    return _fallback_features(product_name, product_desc, num_features)


def _fallback_features(product_name: str, product_desc: str, num_features: int) -> list[dict[str, str]]:
    """Fallback detail features when OpenRouter is unavailable."""
    return [
        {
            "feature_name": "Material & Texture Close-up",
            "feature_type": "material_texture",
            "macro_prompt": (
                f"Commercial studio macro photograph close-up of the material and texture of {product_name}, "
                f"showing fine tactile surface grain, subtle studio lighting, extreme high resolution, "
                f"crisp focus on texture, pure white background"
            ),
        },
        {
            "feature_name": "Craftsmanship & Joinery Detail",
            "feature_type": "joinery_structure",
            "macro_prompt": (
                f"Commercial macro photograph close-up of the edge, seam, and precision joinery of {product_name}, "
                f"highlighting premium finish and craftsmanship, soft diffuse studio lighting, "
                f"extreme detail, pure white background"
            ),
        },
    ][:num_features]


# Fal.ai Multi-Model Caller
def generate_with_model(
    model: ModelSpec,
    prompt: str,
    hero_url: str,
    fal_key: str,
) -> tuple[Image.Image | None, float, str]:
    """
    Call a specific Fal.ai model, track latency, and return the padded PIL Image.
    Returns: (PIL Image or None, duration_seconds, request_id_or_error)
    """
    if not fal_key:
        return None, 0.0, "FAL_KEY missing"

    url = f"https://fal.run/{model.endpoint}"
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json",
    }
    payload = model.build_payload(prompt, hero_url)

    start_time = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=90)
        elapsed = round(time.time() - start_time, 2)
        if not resp.ok:
            err_msg = f"HTTP {resp.status_code}: {resp.text[:120]}"
            print(f"    [!] Error from {model.name}: {err_msg}")
            return None, elapsed, err_msg

        data = resp.json()
        req_id = data.get("request_id") or resp.headers.get("x-fal-request-id", "req_unknown")

        # Extract image URL depending on response format
        img_url = ""
        if "images" in data and data["images"]:
            img_url = data["images"][0].get("url", "")
        elif "image" in data and isinstance(data["image"], dict):
            img_url = data["image"].get("url", "")

        if not img_url:
            return None, elapsed, "No image URL in response"

        # Download and fit canvas
        pil_img = download_image(img_url)
        fitted_img = fit_to_canvas(pil_img, TARGET_WIDTH, TARGET_HEIGHT)
        return fitted_img, elapsed, req_id

    except Exception as exc:
        elapsed = round(time.time() - start_time, 2)
        print(f"    [!] Exception calling {model.name}: {exc}")
        return None, elapsed, str(exc)


# Image Grid Composer
def create_comparison_grid(
    hero_img: Image.Image,
    results: list[tuple[ModelSpec, Image.Image | None, float, str]],
    feature_title: str,
    product_name: str,
) -> Image.Image:
    """
    Creates a visual side-by-side comparison poster.
    Layout: 1 Hero Image + 4 AI Model Results with clear badges, titles, and latency.
    """
    cell_w, cell_h = 560, 350
    header_h = 80
    footer_h = 30
    cols = 5
    padding = 16
    total_w = cols * cell_w + (cols + 1) * padding
    total_h = cell_h + header_h + footer_h + padding * 2

    canvas = Image.new("RGB", (total_w, total_h), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)

    # Title Banner
    draw.rectangle([(0, 0), (total_w, header_h)], fill=(18, 24, 38))
    title_text = f"AI DETAIL MODEL COMPARISON  |  {product_name.upper()}  |  {feature_title.upper()}"
    draw.text((padding + 10, 26), title_text, fill=(255, 255, 255))

    # Cells to draw: [0] = Original Hero, [1..4] = 4 AI Models
    cells = [
        ("ORIGINAL HERO REF", "Baserow Source Photo", "Reference", hero_img, 0.0)
    ]
    for model, img, dur, _ in results:
        cells.append((model.name, model.badge, f"${model.cost_usd:.3f} | {dur:.1f}s", img, dur))

    for idx, (head, subhead, tag, c_img, _) in enumerate(cells):
        x = padding + idx * (cell_w + padding)
        y = header_h + padding

        # Card Background
        draw.rectangle([(x, y), (x + cell_w, y + cell_h)], fill=(255, 255, 255), outline=(220, 226, 235), width=2)

        # Image
        if c_img:
            thumb = c_img.resize((cell_w - 4, cell_h - 60), Image.Resampling.LANCZOS)
            canvas.paste(thumb, (x + 2, y + 2))
        else:
            draw.rectangle([(x + 2, y + 2), (x + cell_w - 2, y + cell_h - 58)], fill=(235, 238, 242))
            draw.text((x + cell_w // 4, y + cell_h // 3), "Generation Failed", fill=(180, 50, 50))

        # Bottom label bar
        draw.rectangle([(x, y + cell_h - 56), (x + cell_w, y + cell_h)], fill=(248, 250, 252))
        draw.text((x + 10, y + cell_h - 50), head, fill=(15, 23, 42))
        draw.text((x + 10, y + cell_h - 30), subhead[:38], fill=(100, 116, 139))
        draw.text((x + cell_w - 110, y + cell_h - 30), tag, fill=(14, 116, 144))

    return canvas


# Interactive HTML Dashboard Generator
def generate_html_report(
    products_data: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Generate a responsive HTML dashboard to compare models, prompts, and costs."""
    models_cards_html = ""
    for m in MODELS:
        models_cards_html += f"""
        <div class="model-card">
            <div class="model-card-header">
                <div>
                    <span class="model-provider">{m.provider}</span>
                    <h3 class="model-name">{m.name}</h3>
                </div>
                <span class="model-price">${m.cost_usd:.3f} / img</span>
            </div>
            <div class="model-badge">{m.badge}</div>
            <p class="model-desc"><strong>Speciality:</strong> {m.speciality}</p>
            <p class="model-why"><strong>Why Use:</strong> {m.why_use}</p>
        </div>
        """

    products_sections_html = ""
    for p in products_data:
        p_name = p["name"]
        p_id = p["id"]
        hero_rel = p["hero_relative_path"]

        features_html = ""
        for f in p["features"]:
            f_name = f["feature_name"]
            prompt = f["prompt"]
            grid_rel = f.get("grid_relative_path", "")

            model_images_html = ""
            for m_res in f["models"]:
                m_name = m_res["name"]
                m_cost = m_res["cost"]
                m_dur = m_res["duration"]
                img_rel = m_res.get("relative_path", "")
                m_badge = m_res.get("badge", "")

                if img_rel:
                    model_images_html += f"""
                    <div class="result-card">
                        <div class="result-img-wrapper">
                            <img src="{img_rel}" alt="{m_name} - {f_name}" loading="lazy" onclick="openLightbox(this.src, '{m_name} ({f_name})')">
                        </div>
                        <div class="result-meta">
                            <div class="result-title">{m_name}</div>
                            <div class="result-badge">{m_badge}</div>
                            <div class="result-stats">⏱ {m_dur}s &nbsp;|&nbsp; 💵 ${m_cost:.3f}</div>
                        </div>
                    </div>
                    """
                else:
                    model_images_html += f"""
                    <div class="result-card error">
                        <div class="error-box">Generation Failed: {m_res.get('error', 'Unknown error')}</div>
                        <div class="result-meta">
                            <div class="result-title">{m_name}</div>
                        </div>
                    </div>
                    """

            features_html += f"""
            <div class="feature-block">
                <div class="feature-header">
                    <h4>🔍 {f_name}</h4>
                    <div class="prompt-box">
                        <strong>Prompt:</strong> <code>{prompt}</code>
                    </div>
                </div>

                <div class="models-grid">
                    <div class="result-card hero-ref">
                        <div class="result-img-wrapper">
                            <img src="{hero_rel}" alt="Original Hero Photo" onclick="openLightbox(this.src, 'Original Hero Reference')">
                        </div>
                        <div class="result-meta">
                            <div class="result-title">Original Hero Photo</div>
                            <div class="result-badge">Baserow Source</div>
                            <div class="result-stats">Reference Image</div>
                        </div>
                    </div>
                    {model_images_html}
                </div>

                {f'<div class="grid-link-box"><a href="{grid_rel}" target="_blank" class="view-grid-btn">🖼 Open Side-by-Side Composite Poster</a></div>' if grid_rel else ''}
            </div>
            """

        products_sections_html += f"""
        <section class="product-section">
            <div class="product-header">
                <div>
                    <span class="product-id-tag">Baserow Product #{p_id}</span>
                    <h2 class="product-title">{p_name}</h2>
                </div>
            </div>
            {features_html}
        </section>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Detail Image Model Comparison | Binnen Baserow</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #0B0F19;
            --surface: #131B2E;
            --surface-hover: #1A243D;
            --card-border: #232F4B;
            --text: #F1F5F9;
            --text-muted: #94A3B8;
            --accent: #38BDF8;
            --accent-glow: rgba(56, 189, 248, 0.2);
            --badge-bg: #1E293B;
            --success: #10B981;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2.5rem 1.5rem;
        }}
        .container {{ max-width: 1440px; margin: 0 auto; }}
        header {{ margin-bottom: 3rem; text-align: center; }}
        .header-tag {{
            display: inline-block;
            background: linear-gradient(135deg, rgba(56, 189, 248, 0.15), rgba(168, 85, 247, 0.15));
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: var(--accent);
            font-size: 0.85rem;
            font-weight: 700;
            padding: 0.35rem 1rem;
            border-radius: 9999px;
            margin-bottom: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        h1 {{ font-size: 2.5rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 0.5rem; }}
        p.subtitle {{ color: var(--text-muted); font-size: 1.1rem; max-width: 800px; margin: 0 auto; }}

        /* Model Comparison Guide Cards */
        .guide-section {{ margin-bottom: 3.5rem; }}
        .guide-title {{ font-size: 1.35rem; font-weight: 700; margin-bottom: 1.2rem; color: #E2E8F0; }}
        .models-overview-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.25rem;
        }}
        .model-card {{
            background: var(--surface);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            padding: 1.4rem;
            transition: transform 0.2s, border-color 0.2s;
        }}
        .model-card:hover {{ transform: translateY(-3px); border-color: var(--accent); }}
        .model-card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.8rem; }}
        .model-provider {{ font-size: 0.75rem; text-transform: uppercase; color: var(--accent); font-weight: 700; letter-spacing: 0.05em; }}
        .model-name {{ font-size: 1.2rem; font-weight: 700; color: #FFF; }}
        .model-price {{ background: rgba(16, 185, 129, 0.15); color: var(--success); font-weight: 700; font-size: 0.85rem; padding: 0.25rem 0.6rem; border-radius: 6px; }}
        .model-badge {{ display: inline-block; background: var(--badge-bg); color: #CBD5E1; font-size: 0.78rem; font-weight: 600; padding: 0.25rem 0.6rem; border-radius: 6px; margin-bottom: 0.75rem; }}
        .model-desc, .model-why {{ font-size: 0.88rem; color: var(--text-muted); margin-bottom: 0.5rem; }}
        .model-desc strong, .model-why strong {{ color: #E2E8F0; }}

        /* Product Comparison Sections */
        .product-section {{
            background: rgba(19, 27, 46, 0.6);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 2rem;
            margin-bottom: 3.5rem;
        }}
        .product-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; border-bottom: 1px solid var(--card-border); padding-bottom: 1rem; }}
        .product-id-tag {{ color: var(--accent); font-size: 0.85rem; font-weight: 700; text-transform: uppercase; }}
        .product-title {{ font-size: 1.8rem; font-weight: 800; }}

        /* Feature Block */
        .feature-block {{
            background: var(--surface);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.75rem;
            margin-bottom: 2rem;
        }}
        .feature-header {{ margin-bottom: 1.5rem; }}
        .feature-header h4 {{ font-size: 1.25rem; font-weight: 700; color: #FFF; margin-bottom: 0.5rem; }}
        .prompt-box {{
            background: #0B0F19;
            border: 1px solid #1E293B;
            border-radius: 8px;
            padding: 0.75rem 1rem;
            font-size: 0.85rem;
            color: #CBD5E1;
        }}
        .prompt-box code {{ color: #7DD3FC; font-family: monospace; word-break: break-word; }}

        /* Results Grid */
        .models-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 1.25rem;
            margin-bottom: 1.25rem;
        }}
        .result-card {{
            background: #0B0F19;
            border: 1px solid var(--card-border);
            border-radius: 12px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .result-card:hover {{ transform: scale(1.02); box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }}
        .result-card.hero-ref {{ border-color: rgba(56, 189, 248, 0.4); }}
        .result-img-wrapper {{ width: 100%; aspect-ratio: 16/10; background: #FFF; overflow: hidden; cursor: zoom-in; }}
        .result-img-wrapper img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
        .result-meta {{ padding: 0.9rem; flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between; }}
        .result-title {{ font-size: 0.95rem; font-weight: 700; color: #FFF; margin-bottom: 0.25rem; }}
        .result-badge {{ font-size: 0.75rem; color: var(--accent); font-weight: 600; margin-bottom: 0.4rem; }}
        .result-stats {{ font-size: 0.78rem; color: var(--text-muted); font-weight: 600; }}
        .grid-link-box {{ text-align: right; margin-top: 0.5rem; }}
        .view-grid-btn {{
            display: inline-block;
            background: rgba(56, 189, 248, 0.1);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: var(--accent);
            text-decoration: none;
            font-size: 0.85rem;
            font-weight: 600;
            padding: 0.4rem 0.9rem;
            border-radius: 6px;
            transition: all 0.2s;
        }}
        .view-grid-btn:hover {{ background: var(--accent); color: #000; }}

        /* Lightbox Modal */
        .lightbox {{
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.9);
            z-index: 9999;
            justify-content: center;
            align-items: center;
            flex-direction: column;
            padding: 1.5rem;
        }}
        .lightbox.active {{ display: flex; }}
        .lightbox img {{ max-width: 90vw; max-height: 85vh; border-radius: 8px; box-shadow: 0 20px 40px rgba(0,0,0,0.8); }}
        .lightbox-title {{ margin-top: 1rem; color: #FFF; font-size: 1.1rem; font-weight: 600; }}
        .lightbox-close {{ position: absolute; top: 1.5rem; right: 2rem; color: #FFF; font-size: 2rem; cursor: pointer; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <span class="header-tag">Evaluation Lab</span>
            <h1>AI Detail Image Model Comparison</h1>
            <p class="subtitle">Comparing 4 leading AI model families on authentic Baserow products and hero images to determine the best model for e-commerce macro detail photography.</p>
        </header>

        <section class="guide-section">
            <h2 class="guide-title">Model Specifications, Specialities & Costs</h2>
            <div class="models-overview-grid">
                {models_cards_html}
            </div>
        </section>

        {products_sections_html}
    </div>

    <div class="lightbox" id="lightbox" onclick="closeLightbox()">
        <span class="lightbox-close">&times;</span>
        <img id="lightbox-img" src="" alt="Zoomed view">
        <div class="lightbox-title" id="lightbox-title"></div>
    </div>

    <script>
        function openLightbox(src, title) {{
            document.getElementById('lightbox-img').src = src;
            document.getElementById('lightbox-title').innerText = title;
            document.getElementById('lightbox').classList.add('active');
        }}
        function closeLightbox() {{
            document.getElementById('lightbox').classList.remove('active');
        }}
        document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') closeLightbox(); }});
    </script>
</body>
</html>
"""
    output_path.write_text(html_content, encoding="utf-8")
    print(f"\n[+] Interactive HTML Comparison Report generated: {output_path}")


# Main Execution Pipeline
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare 4 distinct AI models for product detail image generation from Baserow products."
    )
    parser.add_argument(
        "--num-products", type=int, default=2,
        help="Number of random Baserow products to evaluate (default: 2)."
    )
    parser.add_argument(
        "--features-per-product", type=int, default=2,
        help="Number of distinct detail features to generate per product (default: 2)."
    )
    parser.add_argument(
        "--ids", type=str, default="",
        help="Comma-separated Baserow row IDs to evaluate (e.g. '2,4'). Overrides random selection."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT,
        help="Output directory to save all generated images, grids, and reports."
    )
    args = parser.parse_args()

    settings = load_settings()
    baserow = BaserowClient(settings)
    fal_key = FAL_KEY
    openrouter_key = OPENROUTER_API_KEY or settings.openrouter_api_key

    if not fal_key:
        print("[!] ERROR: FAL_KEY is not set in environment or .env file.")
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 75)
    print("=== AI MODEL COMPARISON: PRODUCT DETAIL IMAGE GENERATION ===")
    print("=" * 75)
    print(f"  Products to test   : {args.num_products}")
    print(f"  Features / Product : {args.features_per_product}")
    print(f"  Models to evaluate : {len(MODELS)} ({', '.join(m.name for m in MODELS)})")
    print(f"  Output Directory   : {args.output_dir}")
    print("=" * 75)
    print()

    # Step 1: Select products from Baserow
    target_rows = []
    if args.ids:
        row_ids = [int(i.strip()) for i in args.ids.split(",") if i.strip().isdigit()]
        print(f"Fetching specific row IDs from Baserow Table {settings.products_table_id}: {row_ids}...")
        for r_id in row_ids:
            try:
                row = baserow.get_row(settings.products_table_id, r_id)
                target_rows.append(row)
            except Exception as e:
                print(f"  [!] Could not fetch row {r_id}: {e}")
    else:
        print(f"Querying Baserow Table {settings.products_table_id} for candidate products with Hero images...")
        all_candidates = []
        for row in baserow.list_table_rows(settings.products_table_id, size=100):
            hero_imgs = row.get(settings.field_hero_images) or []
            prod_imgs = row.get(settings.field_product_images) or []
            if hero_imgs or prod_imgs:
                all_candidates.append(row)
            if len(all_candidates) >= 30:
                break

        if not all_candidates:
            print("[!] ERROR: No candidate products with images found in Baserow.")
            return 1

        selected_count = min(args.num_products, len(all_candidates))
        # Random sample
        target_rows = random.sample(all_candidates, selected_count)
        print(f"Selected {len(target_rows)} random products for evaluation.")

    products_report_data = []

    # Step 2: Process each product
    for p_idx, row in enumerate(target_rows):
        row_id = row["id"]
        prod_name = str(row.get(settings.field_product_name) or f"Product_{row_id}")
        prod_desc = str(row.get(settings.field_product_description) or row.get(settings.field_ai_description_nl) or "")
        hero_imgs = row.get(settings.field_hero_images) or []
        prod_imgs = row.get(settings.field_product_images) or []

        # Find best hero image URL
        hero_url = ""
        for item in (hero_imgs + prod_imgs):
            if isinstance(item, dict) and item.get("url"):
                hero_url = item["url"]
                break

        if not hero_url:
            print(f"\n[!] Skipping row {row_id} ('{prod_name}'): No image URL found.")
            continue

        prod_slug = f"product_{row_id}_{slugify(prod_name)}"
        prod_dir = args.output_dir / prod_slug
        prod_dir.mkdir(parents=True, exist_ok=True)

        print("\n" + "-" * 75)
        print(f"[*] PRODUCT {p_idx + 1}/{len(target_rows)}: {prod_name} (ID: {row_id})")
        print(f"   Hero Image: {hero_url[:80]}...")
        print(f"   Target Dir: {prod_dir}")
        print("-" * 75)

        # Save Reference Hero Image
        hero_file = prod_dir / "00_hero_reference.jpg"
        try:
            hero_pil = download_image(hero_url)
            hero_pil.save(str(hero_file), quality=92)
            print(f"  [+] Saved Hero Reference: {hero_file.name}")
        except Exception as exc:
            print(f"  [!] Failed to download hero image: {exc}")
            continue

        # Step 3: Identify 2 distinct detail features via OpenRouter GPT-4o Vision
        print(f"\n  Analyzing product via OpenRouter GPT-4o Vision for {args.features_per_product} detail features...")
        features = identify_features_via_openrouter(
            hero_url=hero_url,
            product_name=prod_name,
            product_desc=prod_desc,
            openrouter_key=openrouter_key,
            num_features=args.features_per_product,
        )

        for f_i, feat in enumerate(features):
            print(f"    Feature {f_i + 1}: {feat.get('feature_name')} ({feat.get('feature_type')})")

        prod_record: dict[str, Any] = {
            "id": row_id,
            "name": prod_name,
            "hero_url": hero_url,
            "hero_relative_path": str(hero_file.relative_to(args.output_dir)).replace("\\", "/"),
            "features": [],
        }

        # Step 4: Generate each feature across all 4 models
        for f_idx, feat in enumerate(features):
            feat_name = feat.get("feature_name") or f"Feature {f_idx + 1}"
            feat_type = feat.get("feature_type") or f"feat_{f_idx + 1}"
            prompt = feat.get("macro_prompt") or feat.get("detail_prompt") or f"Macro close-up of {prod_name}"

            feat_slug = f"feature_{f_idx + 1}_{slugify(feat_type)}"
            feat_dir = prod_dir / feat_slug
            feat_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n  [+] Generating Feature {f_idx + 1}/{len(features)}: '{feat_name}'")
            print(f"     Prompt: {prompt[:120]}...")

            feature_results: list[tuple[ModelSpec, Image.Image | None, float, str]] = []
            models_record_list: list[dict[str, Any]] = []

            for model in MODELS:
                print(f"     -> Calling {model.name} ({model.provider})...", end="", flush=True)
                model_dir = feat_dir / model.id
                model_dir.mkdir(parents=True, exist_ok=True)

                gen_img, dur, req_or_err = generate_with_model(
                    model=model,
                    prompt=prompt,
                    hero_url=hero_url,
                    fal_key=fal_key,
                )

                if gen_img:
                    out_img_path = model_dir / "detail.jpg"
                    gen_img.save(str(out_img_path), quality=92)
                    rel_path = str(out_img_path.relative_to(args.output_dir)).replace("\\", "/")
                    print(f" Done ({dur}s) -> {out_img_path.name}")
                    feature_results.append((model, gen_img, dur, req_or_err))
                    models_record_list.append({
                        "id": model.id,
                        "name": model.name,
                        "provider": model.provider,
                        "badge": model.badge,
                        "cost": model.cost_usd,
                        "duration": dur,
                        "relative_path": rel_path,
                        "status": "success",
                    })
                else:
                    print(f" FAILED ({dur}s) - {req_or_err}")
                    feature_results.append((model, None, dur, req_or_err))
                    models_record_list.append({
                        "id": model.id,
                        "name": model.name,
                        "provider": model.provider,
                        "badge": model.badge,
                        "cost": model.cost_usd,
                        "duration": dur,
                        "error": req_or_err,
                        "status": "failed",
                    })

            # Create side-by-side comparison grid for this feature
            grid_img = create_comparison_grid(
                hero_img=hero_pil,
                results=feature_results,
                feature_title=feat_name,
                product_name=prod_name,
            )
            grid_file = feat_dir / "comparison_grid.jpg"
            grid_img.save(str(grid_file), quality=90)
            print(f"     [+] Created Feature Grid: {grid_file.name}")

            prod_record["features"].append({
                "feature_name": feat_name,
                "feature_type": feat_type,
                "prompt": prompt,
                "grid_relative_path": str(grid_file.relative_to(args.output_dir)).replace("\\", "/"),
                "models": models_record_list,
            })

        products_report_data.append(prod_record)

    # Step 5: Save Summary JSON & Generate Interactive HTML Report
    summary_json_path = args.output_dir / "summary_report.json"
    summary_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models_evaluated": [asdict(m) for m in MODELS],
        "products": products_report_data,
    }
    summary_json_path.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")
    print(f"\n[+] Saved Summary Metadata: {summary_json_path}")

    html_path = args.output_dir / "comparison_overview.html"
    generate_html_report(products_report_data, html_path)

    print("\n" + "=" * 75)
    print("=== MODEL COMPARISON COMPLETE ===")
    print("=" * 75)
    print(f"  Products processed : {len(products_report_data)}")
    print(f"  Total Images       : {len(products_report_data) * args.features_per_product * len(MODELS)}")
    print(f"  HTML Dashboard     : {html_path}")
    print(f"  Summary JSON       : {summary_json_path}")
    print("=" * 75 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
