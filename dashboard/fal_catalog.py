"""Live fal.ai model catalog — fetch models per eval task from Platform API."""
from __future__ import annotations

import re
import time
from typing import Any

import requests

FAL_PLATFORM_API = "https://api.fal.ai/v1"

# Production endpoints used in CLI pipelines — always pinned in eval catalog
PRODUCTION_ENDPOINTS: dict[str, list[str]] = {
    "outpaint": ["fal-ai/bria/expand"],
    "rembg": ["fal-ai/imageutils/rembg"],
    "detail": ["fal-ai/flux-2-pro/edit"],
}

PRODUCTION_SEARCH_ALIASES: dict[str, list[str]] = {
    "fal-ai/bria/expand": ["bria expand", "production", "outpaint production"],
    "fal-ai/imageutils/rembg": ["rembg", "rmbg v1.4", "production", "background remove production"],
    "fal-ai/flux-2-pro/edit": ["flux 2 pro edit", "flux-2-pro", "production", "detail production", "macro production"],
}

# Endpoints with tuned payload builders in fal_eval.py
OPTIMIZED_ENDPOINTS: set[str] = {
    "fal-ai/bria/expand",
    "fal-ai/flux-2-pro/outpaint",
    "fal-ai/image-apps-v2/outpaint",
    "fal-ai/imageutils/rembg",
    "fal-ai/birefnet/v2",
    "fal-ai/bria/background/remove",
    "fal-ai/flux-2-pro/edit",
    "fal-ai/flux-2-max/edit",
    "fal-ai/flux-pro/kontext",
    "fal-ai/flux/schnell",
}

LEGACY_ID_MAP: dict[str, str] = {
    "fal_bria_expand": "fal-ai/bria/expand",
    "fal_flux2_outpaint": "fal-ai/flux-2-pro/outpaint",
    "fal_image_outpaint": "fal-ai/image-apps-v2/outpaint",
    "fal_rembg": "fal-ai/imageutils/rembg",
    "fal_isnet_rembg": "fal-ai/imageutils/rembg",
    "fal_birefnet": "fal-ai/birefnet/v2",
    "fal_bria_rmbg": "fal-ai/bria/background/remove",
    "fal_flux2_pro": "fal-ai/flux-2-pro/edit",
    "fal_flux2_max": "fal-ai/flux-2-max/edit",
    "fal_kontext_detail": "fal-ai/flux-pro/kontext",
    "fal_flux_schnell_detail": "fal-ai/flux/schnell",
}

TASK_SEARCH: dict[str, dict[str, Any]] = {
    "outpaint": {
        "queries": ["outpaint", "expand image", "reframe extend"],
        "category": "image-to-image",
        "include": re.compile(
            r"(outpaint|expand|reframe|extend|canvas)",
            re.I,
        ),
        "exclude": re.compile(
            r"(text-to-image|video|audio|3d|training|thumbnail)",
            re.I,
        ),
        "default_endpoints": [
            "fal-ai/bria/expand",
            "fal-ai/flux-2-pro/outpaint",
        ],
    },
    "rembg": {
        "queries": ["background remove", "rembg", "rmbg", "matting"],
        "category": "image-to-image",
        "include": re.compile(
            r"(background|rembg|rmbg|birefnet|matting|cutout|segment)",
            re.I,
        ),
        "exclude": re.compile(
            r"(text-removal|object-removal|video|audio|training|inpaint|outpaint|expand)",
            re.I,
        ),
        "default_endpoints": [
            "fal-ai/imageutils/rembg",
            "fal-ai/birefnet/v2",
        ],
    },
    "detail": {
        "queries": ["image edit", "edit image", "flux-2-pro edit", "inpaint edit"],
        "category": "image-to-image",
        "include": re.compile(
            r"(edit|kontext|inpaint|macro|detail)",
            re.I,
        ),
        "exclude": re.compile(
            r"(background.?remove|rembg|rmbg|outpaint|expand|reframe|video|audio|training|text-to-image)",
            re.I,
        ),
        "default_endpoints": [
            "fal-ai/flux-2-pro/edit",
            "fal-ai/flux-pro/kontext",
        ],
    },
}

_CACHE: dict[str, Any] = {"fetched_at": 0.0, "tasks": {}, "meta_index": {}}
_CACHE_TTL = 3600  # 1 hour
_OPENAPI_CACHE: dict[str, tuple[float, dict]] = {}


def normalize_endpoint_id(model_id: str) -> str:
    if "/" in model_id:
        return model_id
    return LEGACY_ID_MAP.get(model_id, model_id)


def _headers(fal_key: str) -> dict[str, str]:
    h = {"User-Agent": "binnen-dashboard/1.0"}
    if fal_key:
        h["Authorization"] = f"Key {fal_key}"
    return h


def _search_models(query: str, fal_key: str, category: str, limit: int = 50) -> list[dict]:
    models: list[dict] = []
    cursor: str | None = None
    pages = 0
    while pages < 3:
        params: dict[str, Any] = {
            "q": query,
            "limit": limit,
            "status": "active",
            "category": category,
        }
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{FAL_PLATFORM_API}/models",
            params=params,
            headers=_headers(fal_key),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        models.extend(data.get("models") or [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        pages += 1
    return models


def _matches_task(endpoint_id: str, meta: dict[str, Any], task_cfg: dict[str, Any]) -> bool:
    hay = f"{endpoint_id} {meta.get('display_name', '')} {meta.get('description', '')}"
    # Image-edit endpoints are valid for detail even when API copy mentions text-to-image
    if "/edit" in endpoint_id and task_cfg is TASK_SEARCH.get("detail"):
        return True
    if task_cfg["exclude"].search(hay):
        return False
    return bool(task_cfg["include"].search(hay))


def _fetch_model_by_endpoint(endpoint_id: str, fal_key: str) -> dict[str, Any] | None:
    try:
        resp = requests.get(
            f"{FAL_PLATFORM_API}/models",
            params={"endpoint_id": endpoint_id, "status": "active"},
            headers=_headers(fal_key),
            timeout=30,
        )
        if not resp.ok:
            return None
        models = resp.json().get("models") or []
        return models[0] if models else None
    except Exception:
        return None


def _fetch_pricing(endpoint_ids: list[str], fal_key: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    chunk_size = 40
    for i in range(0, len(endpoint_ids), chunk_size):
        chunk = endpoint_ids[i : i + chunk_size]
        params: list[tuple[str, str]] = [("endpoint_id", eid) for eid in chunk]
        try:
            resp = requests.get(
                f"{FAL_PLATFORM_API}/models/pricing",
                params=params,
                headers=_headers(fal_key),
                timeout=30,
            )
            if not resp.ok:
                continue
            for item in resp.json().get("prices") or []:
                eid = item.get("endpoint_id")
                unit = item.get("unit_price")
                if eid and unit is not None:
                    prices[eid] = float(unit)
        except Exception:
            continue
    return prices


def _to_catalog_entry(
    raw: dict[str, Any],
    task: str,
    price: float | None,
    default_selected: bool,
    *,
    production: bool = False,
) -> dict[str, Any]:
    endpoint_id = raw.get("endpoint_id", "")
    meta = raw.get("metadata") or {}
    name = meta.get("display_name") or endpoint_id.split("/")[-1]
    optimized = endpoint_id in OPTIMIZED_ENDPOINTS
    if production:
        badge = "Production"
    elif optimized:
        badge = "Optimized"
    else:
        badge = "fal.ai"
    return {
        "id": endpoint_id,
        "name": name,
        "provider": endpoint_id.split("/")[0] if "/" in endpoint_id else "fal.ai",
        "endpoint": endpoint_id,
        "cost_usd": price if price is not None else 0.01,
        "cost_unit": "per run",
        "badge": badge,
        "description": (meta.get("description") or "").strip(),
        "default_selected": default_selected,
        "thumbnail_url": meta.get("thumbnail_url"),
        "category": meta.get("category"),
        "source": "fal_api",
        "optimized": optimized,
        "production": production,
        "search_aliases": PRODUCTION_SEARCH_ALIASES.get(endpoint_id, []),
    }


def fetch_task_models(task: str, fal_key: str) -> list[dict[str, Any]]:
    """Fetch and normalize all fal.ai models relevant to an eval task."""
    cfg = TASK_SEARCH.get(task)
    if not cfg:
        return []

    seen: dict[str, dict] = {}
    for query in cfg["queries"]:
        for raw in _search_models(query, fal_key, cfg["category"]):
            endpoint_id = raw.get("endpoint_id")
            meta = raw.get("metadata") or {}
            if not endpoint_id or endpoint_id in seen:
                continue
            if not _matches_task(endpoint_id, meta, cfg):
                continue
            seen[endpoint_id] = raw

    # Always include production + default endpoints even if search/filter missed them
    pinned = list(dict.fromkeys(
        (PRODUCTION_ENDPOINTS.get(task) or []) + (cfg.get("default_endpoints") or [])
    ))
    for endpoint_id in pinned:
        if endpoint_id in seen:
            continue
        raw = _fetch_model_by_endpoint(endpoint_id, fal_key)
        if raw:
            seen[endpoint_id] = raw

    endpoint_ids = list(seen.keys())
    prices = _fetch_pricing(endpoint_ids, fal_key)
    defaults = set(cfg.get("default_endpoints") or [])
    production_set = set(PRODUCTION_ENDPOINTS.get(task) or [])

    entries = [
        _to_catalog_entry(
            seen[eid],
            task,
            prices.get(eid),
            default_selected=eid in defaults,
            production=eid in production_set,
        )
        for eid in endpoint_ids
    ]
    # Production first, then optimized, then alphabetical
    entries.sort(
        key=lambda m: (
            0 if m.get("production") else 1,
            0 if m["optimized"] else 1,
            m["name"].lower(),
        )
    )
    return entries


def get_dynamic_catalog(fal_key: str = "", force: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Return full task-grouped catalog from fal.ai (cached)."""
    now = time.time()
    if not force and _CACHE["tasks"] and (now - _CACHE["fetched_at"]) < _CACHE_TTL:
        return _CACHE["tasks"]

    tasks: dict[str, list[dict[str, Any]]] = {}
    meta_index: dict[str, dict[str, Any]] = {}

    for task in TASK_SEARCH:
        try:
            models = fetch_task_models(task, fal_key)
            tasks[task] = models
            for m in models:
                meta_index[m["id"]] = {**m, "task": task}
        except Exception as exc:
            print(f"[fal_catalog] Failed to fetch {task} models: {exc}")
            tasks[task] = _CACHE["tasks"].get(task, [])

    if any(tasks.values()):
        _CACHE["tasks"] = tasks
        _CACHE["meta_index"] = meta_index
        _CACHE["fetched_at"] = now

    return _CACHE["tasks"]


def find_model_meta(endpoint_id: str, task_type: str | None = None) -> dict[str, Any]:
    """Look up model metadata from cached catalog."""
    endpoint_id = normalize_endpoint_id(endpoint_id)
    cached = _CACHE["meta_index"].get(endpoint_id)
    if cached:
        return cached
    if task_type:
        for m in _CACHE["tasks"].get(task_type, []):
            if m["id"] == endpoint_id:
                return m
    slug = endpoint_id.split("/")[-1]
    return {
        "id": endpoint_id,
        "name": slug.replace("-", " ").title(),
        "provider": "fal.ai",
        "endpoint": endpoint_id,
        "cost_usd": 0.01,
        "badge": "fal.ai",
        "description": "",
    }


def fetch_openapi(endpoint_id: str, fal_key: str) -> dict[str, Any]:
    """Fetch and cache OpenAPI schema for a model endpoint."""
    endpoint_id = normalize_endpoint_id(endpoint_id)
    now = time.time()
    cached = _OPENAPI_CACHE.get(endpoint_id)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    resp = requests.get(
        f"{FAL_PLATFORM_API}/models",
        params={"endpoint_id": endpoint_id, "expand": "openapi-3.0"},
        headers=_headers(fal_key),
        timeout=45,
    )
    resp.raise_for_status()
    models = resp.json().get("models") or []
    if not models:
        raise RuntimeError(f"No OpenAPI schema for {endpoint_id}")
    openapi = models[0].get("openapi") or {}
    _OPENAPI_CACHE[endpoint_id] = (now, openapi)
    return openapi


def get_input_schema(openapi: dict[str, Any]) -> dict[str, Any]:
    schemas = openapi.get("components", {}).get("schemas", {})
    for schema in schemas.values():
        if isinstance(schema, dict) and schema.get("properties"):
            name = schema.get("title", "") or ""
            if name.endswith("Input") or "image_url" in schema.get("properties", {}):
                return schema
    return {}


def _enum_default(prop: dict[str, Any]) -> Any | None:
    if "default" in prop:
        return prop["default"]
    enum = prop.get("enum")
    if enum:
        return enum[0]
    return None


def build_generic_payload(
    schema: dict[str, Any],
    *,
    task_type: str,
    image_url: str,
    prompt: str,
    aspect_ratio: str,
    canvas_size: tuple[int, int],
    expand_px: int,
    outpaint_prompt: str,
    detail_prompt: str,
) -> dict[str, Any] | None:
    """Build a best-effort fal.run payload from an OpenAPI input schema."""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    if "mask_url" in required or "mask" in required:
        return None
    if required == {"prompt"} or (required <= {"prompt", "seed"} and "image_url" not in props):
        return None

    tw, th = canvas_size
    payload: dict[str, Any] = {}

    def set_field(key: str, value: Any) -> bool:
        if key not in props or value is None:
            return key not in required
        payload[key] = value
        return True

    # Image inputs
    if not set_field("image_url", image_url):
        return None
    set_field("image", image_url)
    if "image_urls" in props:
        payload["image_urls"] = [image_url]

    # Prompts
    if task_type == "outpaint":
        set_field("prompt", outpaint_prompt)
    elif task_type == "detail":
        if not set_field("prompt", detail_prompt):
            return None
    else:
        set_field("prompt", prompt or outpaint_prompt)

    # Outpaint / expand fields
    set_field("canvas_size", [tw, th])
    for side in ("expand_left", "expand_right", "expand_top", "expand_bottom"):
        set_field(side, expand_px)
    set_field("num_images", 1)
    set_field("output_format", _enum_default(props.get("output_format", {})) or "jpeg")
    set_field("mode", _enum_default(props.get("mode", {})) or "high")

    # Aspect ratio (enum-safe)
    if "aspect_ratio" in props:
        enum = props["aspect_ratio"].get("enum") or []
        ar = aspect_ratio if aspect_ratio in enum else None
        if not ar and enum:
            mapping = {"16:10": "16:9", "4:3": "4:3", "1:1": "1:1", "16:9": "16:9"}
            ar = mapping.get(aspect_ratio)
            if ar not in enum and enum:
                ar = enum[0]
        if ar:
            payload["aspect_ratio"] = ar

    # Fill remaining required fields from defaults / enums
    for field in required:
        if field in payload:
            continue
        prop = props.get(field, {})
        default = _enum_default(prop)
        if default is not None:
            payload[field] = default
        elif field in ("sync_mode",):
            payload[field] = False
        else:
            return None

    return payload
