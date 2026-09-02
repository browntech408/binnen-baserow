"""fal.ai Model Evaluation Engine — catalog registry + multi-method benchmark runner."""
from __future__ import annotations

import base64
import io
import re
import time
from typing import Any

import requests
from PIL import Image

from dashboard.fal_catalog import (
    OPTIMIZED_ENDPOINTS,
    build_generic_payload,
    fetch_openapi,
    find_model_meta,
    get_dynamic_catalog,
    get_input_schema,
    normalize_endpoint_id,
)

# Static fallback when fal API is unreachable
FALLBACK_CATALOG: dict[str, list[dict[str, Any]]] = {
    "outpaint": [
        {
            "id": "fal_bria_expand",
            "name": "Bria Expand",
            "provider": "fal.ai",
            "endpoint": "fal-ai/bria/expand",
            "cost_usd": 0.040,
            "badge": "Best AI Outpaint",
            "description": "Generative canvas expansion to target aspect ratio",
            "default_selected": True,
        },
        {
            "id": "fal_flux2_outpaint",
            "name": "FLUX 2 Pro Outpaint",
            "provider": "fal.ai",
            "endpoint": "fal-ai/flux-2-pro/outpaint",
            "cost_usd": 0.050,
            "badge": "Photoreal Extend",
            "description": "High-fidelity directional scene extension per edge",
            "default_selected": False,
        },
        {
            "id": "fal_image_outpaint",
            "name": "Image Outpaint v2",
            "provider": "fal.ai",
            "endpoint": "fal-ai/image-apps-v2/outpaint",
            "cost_usd": 0.030,
            "badge": "Fast Extend",
            "description": "Directional outpainting with per-edge pixel control",
            "default_selected": False,
        },
    ],
    "rembg": [
        {
            "id": "fal_rembg",
            "name": "RMBG v1.4",
            "provider": "fal.ai",
            "endpoint": "fal-ai/imageutils/rembg",
            "cost_usd": 0.001,
            "badge": "Fast & Cheap",
            "description": "Classic rembg cutout with alpha channel",
            "default_selected": True,
        },
        {
            "id": "fal_birefnet",
            "name": "BiRefNet v2",
            "provider": "fal.ai",
            "endpoint": "fal-ai/birefnet/v2",
            "cost_usd": 0.002,
            "badge": "High Accuracy",
            "description": "State-of-the-art segmentation for complex edges",
            "default_selected": True,
        },
        {
            "id": "fal_bria_rmbg",
            "name": "Bria Background Remove",
            "provider": "fal.ai",
            "endpoint": "fal-ai/bria/background/remove",
            "cost_usd": 0.0015,
            "badge": "Studio Quality",
            "description": "Bria studio-grade background removal",
            "default_selected": False,
        },
        {
            "id": "fal_isnet_rembg",
            "name": "ISNet General",
            "provider": "fal.ai",
            "endpoint": "fal-ai/imageutils/rembg",
            "cost_usd": 0.001,
            "badge": "General Use",
            "description": "ISNet segmentation — good for furniture silhouettes",
            "default_selected": False,
        },
    ],
    "detail": [
        {
            "id": "fal_flux2_pro",
            "name": "FLUX.2 Pro Edit",
            "provider": "fal.ai",
            "endpoint": "fal-ai/flux-2-pro/edit",
            "cost_usd": 0.05,
            "badge": "Best Detail",
            "description": "High-res detail close-up from product reference",
            "default_selected": True,
        },
        {
            "id": "fal_flux2_max",
            "name": "FLUX.2 Max Edit",
            "provider": "fal.ai",
            "endpoint": "fal-ai/flux-2-max/edit",
            "cost_usd": 0.08,
            "badge": "Max Quality",
            "description": "Maximum quality macro texture synthesis",
            "default_selected": False,
        },
        {
            "id": "fal_kontext_detail",
            "name": "FLUX Pro Kontext",
            "provider": "fal.ai",
            "endpoint": "fal-ai/flux-pro/kontext",
            "cost_usd": 0.04,
            "badge": "Context Macro",
            "description": "Kontext-based detail feature crop",
            "default_selected": True,
        },
        {
            "id": "fal_flux_schnell_detail",
            "name": "FLUX Schnell (Detail)",
            "provider": "fal.ai",
            "endpoint": "fal-ai/flux/schnell",
            "cost_usd": 0.003,
            "badge": "Budget Pick",
            "description": "Fast detail generation from macro prompt",
            "default_selected": False,
        },
    ],
}

DIM_MAP = {
    "16:10": (1760, 1100),
    "1:1": (1200, 1200),
    "4:3": (1600, 1200),
    "16:9": (1920, 1080),
}

DIM_LABEL = {
    "16:10": "1760 × 1100",
    "1:1": "1200 × 1200",
    "4:3": "1600 × 1200",
    "16:9": "1920 × 1080",
}

# Bria Expand only accepts these aspect_ratio enum values (16:10 is not supported).
BRIA_ASPECT_RATIO = {
    "1:1": "1:1",
    "4:3": "4:3",
    "16:9": "16:9",
}


def get_catalog(fal_key: str = "", force: bool = False) -> dict[str, Any]:
    """Return task-grouped model catalog — live from fal.ai API with static fallback."""
    try:
        tasks = get_dynamic_catalog(fal_key, force=force)
        if any(tasks.values()):
            return {"ok": True, "tasks": tasks, "source": "fal_api"}
    except Exception as exc:
        print(f"[fal_eval] Dynamic catalog failed: {exc}")
    fallback: dict[str, list[dict[str, Any]]] = {}
    for task, models in FALLBACK_CATALOG.items():
        fallback[task] = [
            {
                **m,
                "id": normalize_endpoint_id(m["id"]),
                "endpoint": normalize_endpoint_id(m.get("endpoint", m["id"])),
            }
            for m in models
        ]
    return {"ok": True, "tasks": fallback, "source": "fallback"}


def _meta(method_id: str, task_type: str) -> dict[str, Any]:
    endpoint_id = normalize_endpoint_id(method_id)
    meta = find_model_meta(endpoint_id, task_type)
    if meta:
        return meta
    return {
        "id": endpoint_id,
        "name": endpoint_id.split("/")[-1].replace("-", " ").title(),
        "provider": "fal.ai",
        "cost_usd": 0.01,
        "badge": "fal.ai",
        "endpoint": endpoint_id,
    }


def _fal_post(endpoint: str, payload: dict, fal_key: str, timeout: int = 90) -> dict:
    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
    resp = requests.post(f"https://fal.run/{endpoint}", headers=headers, json=payload, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"fal HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def _extract_image_url(data: dict) -> str | None:
    if data.get("image", {}).get("url"):
        return data["image"]["url"]
    images = data.get("images") or []
    if images and images[0].get("url"):
        return images[0]["url"]
    return None


def _image_to_data_url(image_url: str) -> str:
    if image_url.startswith("data:"):
        return image_url
    resp = requests.get(image_url, timeout=30)
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "image/jpeg")
    b64 = base64.b64encode(resp.content).decode("ascii")
    return f"data:{ct};base64,{b64}"


def _parse_percent(value: str) -> float:
    """Parse UI values like '15%' into 0.15."""
    if not value:
        return 0.15
    match = re.search(r"([\d.]+)", str(value))
    if not match:
        return 0.15
    pct = float(match.group(1))
    return pct / 100.0 if pct > 1 else pct


def _load_image_bytes(image_url: str) -> bytes:
    image_url = (image_url or "").strip()
    if image_url.startswith("[") or "Local Upload" in image_url:
        raise ValueError("Invalid image reference — please re-upload the file or select a product image")
    if image_url.startswith("data:"):
        header, _, payload = image_url.partition(",")
        if ";base64" in header:
            return base64.b64decode(payload)
        return payload.encode("utf-8")
    resp = requests.get(image_url, timeout=30)
    resp.raise_for_status()
    return resp.content


def ensure_public_image_url(image_url: str, fal_key: str) -> str:
    """Return an https URL fal.ai can fetch — upload data URIs to fal CDN."""
    image_url = (image_url or "").strip()
    if not image_url:
        raise ValueError("Missing image URL")
    if image_url.startswith("[") or "Local Upload" in image_url or "Dropped:" in image_url:
        raise ValueError("Invalid image reference — please re-upload the file or select a product image")
    if image_url.startswith(("http://", "https://")):
        return image_url
    if image_url.startswith("data:"):
        if not fal_key:
            raise RuntimeError("FAL_KEY not configured — cannot process uploaded images")
        import fal_client

        raw = _load_image_bytes(image_url)
        content_type = "image/jpeg"
        header = image_url.split(",", 1)[0]
        if ";" in header:
            content_type = header.replace("data:", "").split(";")[0] or content_type
        ext = "jpg" if "jpeg" in content_type or "jpg" in content_type else content_type.split("/")[-1]
        return fal_client.upload(raw, content_type, file_name=f"binnen-playground.{ext}")
    raise ValueError(f"Unsupported image URL format: {image_url[:80]}")


def _get_image_dimensions(image_url: str) -> tuple[int, int]:
    img = Image.open(io.BytesIO(_load_image_bytes(image_url)))
    return img.size


def _expand_pixels(image_url: str, outpaint_percent: str, max_px: int = 700) -> int:
    """Convert expansion % to per-edge pixels (capped for API limits)."""
    iw, ih = _get_image_dimensions(image_url)
    pct = _parse_percent(outpaint_percent)
    px = int(min(iw, ih) * pct)
    return max(16, min(max_px, px))


def _bria_expand_placement(
    image_url: str,
    canvas_w: int,
    canvas_h: int,
    outpaint_percent: str,
) -> tuple[list[int], list[int]]:
    """Center source image in canvas, scaled down to leave room for expansion."""
    iw, ih = _get_image_dimensions(image_url)
    pct = _parse_percent(outpaint_percent)
    margin = min(0.45, max(0.05, pct))
    inner_w = max(64, int(canvas_w * (1 - 2 * margin)))
    inner_h = max(64, int(canvas_h * (1 - 2 * margin)))
    scale = min(inner_w / iw, inner_h / ih)
    placed_w = max(32, int(iw * scale))
    placed_h = max(32, int(ih * scale))
    loc_x = max(0, (canvas_w - placed_w) // 2)
    loc_y = max(0, (canvas_h - placed_h) // 2)
    return [placed_w, placed_h], [loc_x, loc_y]


def _outpaint_prompt(prompt: str) -> str:
    return prompt or "Seamless studio background extension, photorealistic product photography"


def _score_result(method_id: str, latency: float, cost: float, ok: bool) -> int:
    if not ok:
        return 0
    base = 85
    if "bria" in method_id or "flux2" in method_id or "birefnet" in method_id:
        base += 8
    if "schnell" in method_id or method_id == "fal_rembg":
        base += 3
    if latency < 3:
        base += 4
    elif latency < 8:
        base += 2
    if cost > 0.03:
        base += 3
    return min(100, max(50, base))


def _recommendation(score: int, cost: float, latency: float) -> str:
    if score >= 95 and cost <= 0.01:
        return "Best ROI"
    if score >= 97:
        return "Highest Quality"
    if latency < 3:
        return "Fastest"
    if cost < 0.005:
        return "Budget Pick"
    return "Qualified"


def _detail_prompt(prompt: str, tw: int, th: int) -> str:
    return prompt or (
        f"Extreme macro close-up detail shot of this product's material texture "
        f"and craftsmanship, studio lighting, {tw}x{th} catalog quality"
    )


def _run_known_endpoint(
    endpoint: str,
    task_type: str,
    image_url: str,
    prompt: str,
    outpaint_percent: str,
    aspect_ratio: str,
    fal_key: str,
) -> tuple[str, str]:
    """Run optimized handler for a known endpoint. Returns (output_url, dimensions_label)."""
    tw, th = DIM_MAP.get(aspect_ratio, (1760, 1100))

    if endpoint == "fal-ai/bria/expand":
        payload: dict[str, Any] = {
            "image_url": image_url,
            "canvas_size": [tw, th],
            "prompt": _outpaint_prompt(prompt),
        }
        bria_ratio = BRIA_ASPECT_RATIO.get(aspect_ratio)
        if bria_ratio:
            payload["aspect_ratio"] = bria_ratio
        else:
            orig_size, orig_loc = _bria_expand_placement(image_url, tw, th, outpaint_percent)
            payload["original_image_size"] = orig_size
            payload["original_image_location"] = orig_loc
        data = _fal_post(endpoint, payload, fal_key)
        url = _extract_image_url(data)
        return url or image_url, f"{tw} × {th} (Bria Expand)"

    if endpoint == "fal-ai/flux-2-pro/outpaint":
        expand_px = _expand_pixels(image_url, outpaint_percent, max_px=512)
        data = _fal_post(
            endpoint,
            {
                "image_url": image_url,
                "expand_left": expand_px,
                "expand_right": expand_px,
                "expand_top": expand_px,
                "expand_bottom": expand_px,
                "mode": "high",
                "output_format": "jpeg",
            },
            fal_key,
            timeout=180,
        )
        url = _extract_image_url(data)
        return url or image_url, f"+{expand_px}px per edge (FLUX 2 Pro Outpaint)"

    if endpoint == "fal-ai/image-apps-v2/outpaint":
        expand_px = _expand_pixels(image_url, outpaint_percent, max_px=700)
        data = _fal_post(
            endpoint,
            {
                "image_url": image_url,
                "expand_left": expand_px,
                "expand_right": expand_px,
                "expand_top": expand_px,
                "expand_bottom": expand_px,
                "prompt": _outpaint_prompt(prompt),
                "num_images": 1,
                "output_format": "png",
            },
            fal_key,
            timeout=120,
        )
        url = _extract_image_url(data)
        return url or image_url, f"+{expand_px}px per edge (Image Outpaint)"

    if endpoint == "fal-ai/imageutils/rembg":
        data = _fal_post(endpoint, {"image_url": image_url}, fal_key)
        url = _extract_image_url(data)
        return url or image_url, "PNG Alpha"

    if endpoint == "fal-ai/birefnet/v2":
        data = _fal_post(
            endpoint,
            {"image_url": image_url, "model": "General Use (Heavy)"},
            fal_key,
        )
        url = _extract_image_url(data)
        return url or image_url, "PNG Alpha (BiRefNet)"

    if endpoint == "fal-ai/bria/background/remove":
        data = _fal_post(endpoint, {"image_url": image_url}, fal_key)
        url = _extract_image_url(data)
        return url or image_url, "PNG Alpha (Bria)"

    if endpoint == "fal-ai/flux/schnell":
        scene_prompt = prompt or (
            "Extreme macro close-up of luxury furniture material texture, "
            "wood grain and stitching detail, studio lighting, 8k"
        )
        data = _fal_post(
            endpoint,
            {
                "prompt": scene_prompt,
                "image_size": "landscape_16_9",
                "num_inference_steps": 4,
                "num_images": 1,
            },
            fal_key,
            timeout=60,
        )
        url = _extract_image_url(data)
        return url or image_url, "1024 × 576 (FLUX Schnell)"

    if endpoint == "fal-ai/flux-pro/kontext":
        edit_prompt = prompt or "Extreme macro close-up of material texture and craftsmanship detail"
        data = _fal_post(
            endpoint,
            {
                "prompt": edit_prompt,
                "image_url": image_url,
                "aspect_ratio": "16:9" if aspect_ratio == "16:9" else "16:10",
                "output_format": "jpeg",
                "num_images": 1,
            },
            fal_key,
            timeout=120,
        )
        url = _extract_image_url(data)
        return url or image_url, "Kontext Edit"

    if endpoint in ("fal-ai/flux-2-pro/edit", "fal-ai/flux-2-max/edit"):
        detail_prompt = _detail_prompt(prompt, tw, th)
        data = _fal_post(
            endpoint,
            {
                "prompt": detail_prompt,
                "image_urls": [image_url],
                "image_size": {"width": tw, "height": th},
                "output_format": "jpeg",
                "num_images": 1,
            },
            fal_key,
            timeout=180,
        )
        url = _extract_image_url(data)
        label = "FLUX 2 Max Edit" if "max" in endpoint else "FLUX 2 Pro Edit"
        return url or image_url, f"{tw} × {th} ({label})"

    raise KeyError(endpoint)


def _run_generic_endpoint(
    endpoint: str,
    task_type: str,
    image_url: str,
    prompt: str,
    outpaint_percent: str,
    aspect_ratio: str,
    fal_key: str,
) -> tuple[str, str]:
    """Build payload from OpenAPI schema and run any fal.ai image model."""
    tw, th = DIM_MAP.get(aspect_ratio, (1760, 1100))
    expand_px = _expand_pixels(image_url, outpaint_percent)
    openapi = fetch_openapi(endpoint, fal_key)
    schema = get_input_schema(openapi)
    if not schema:
        raise RuntimeError(f"No input schema for {endpoint}")

    payload = build_generic_payload(
        schema,
        task_type=task_type,
        image_url=image_url,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        canvas_size=(tw, th),
        expand_px=expand_px,
        outpaint_prompt=_outpaint_prompt(prompt),
        detail_prompt=_detail_prompt(prompt, tw, th),
    )
    if not payload:
        raise RuntimeError(f"Cannot auto-build payload for {endpoint} (may require mask or special inputs)")

    data = _fal_post(endpoint, payload, fal_key, timeout=180)
    url = _extract_image_url(data)
    return url or image_url, f"{endpoint}"


def eval_image_method(
    method_id: str,
    task_type: str,
    image_url: str,
    prompt: str,
    outpaint_percent: str,
    aspect_ratio: str,
    fal_key: str,
) -> dict[str, Any]:
    """Run a single fal.ai method and return benchmark metrics."""
    endpoint = normalize_endpoint_id(method_id)
    meta = _meta(endpoint, task_type)
    t0 = time.perf_counter()
    output_url = image_url
    output_dimensions = DIM_LABEL.get(aspect_ratio, "1760 × 1100")
    ok = True
    error = None
    status_note = "OK"

    try:
        if not fal_key:
            raise RuntimeError("FAL_KEY not configured in .env")

        image_url = ensure_public_image_url(image_url, fal_key)

        if endpoint in OPTIMIZED_ENDPOINTS:
            output_url, output_dimensions = _run_known_endpoint(
                endpoint, task_type, image_url, prompt, outpaint_percent, aspect_ratio, fal_key
            )
        else:
            output_url, output_dimensions = _run_generic_endpoint(
                endpoint, task_type, image_url, prompt, outpaint_percent, aspect_ratio, fal_key
            )

    except Exception as exc:
        ok = False
        error = str(exc)
        status_note = f"Failed: {exc}"
        output_url = image_url

    elapsed = round(time.perf_counter() - t0, 3)
    cost = float(meta.get("cost_usd", 0))
    score = _score_result(endpoint, elapsed, cost, ok)

    return {
        "method_id": endpoint,
        "model_id": endpoint,
        "method_name": meta["name"],
        "model_label": meta["name"],
        "provider": meta.get("provider", "fal.ai"),
        "tier_badge": meta.get("badge", ""),
        "description": meta.get("description", ""),
        "endpoint": meta.get("endpoint", endpoint),
        "ok": ok,
        "error": error,
        "output_url": output_url,
        "output_dimensions": output_dimensions,
        "latency_sec": elapsed,
        "cost_usd": cost,
        "cost_per_1k": round(cost * 1000, 3),
        "score": score,
        "status_note": status_note,
        "recommendation": _recommendation(score, cost, elapsed),
    }
