import os
import io
import json
import requests
from PIL import Image

FAL_KEY = os.getenv("FAL_KEY", "").strip()

DETAIL_CANVAS_W = 1760
DETAIL_CANVAS_H = 1100

def fal_call(endpoint: str, payload: dict) -> dict:
    if not FAL_KEY:
        raise ValueError("FAL_KEY is not set.")
    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json"
    }
    url = f"https://fal.run/{endpoint}"
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        print(f"FAL API Error: {resp.text}")
        resp.raise_for_status()
    return resp.json()
def is_transparent_bg(img: Image.Image) -> bool:
    """Detects if an image already has a transparent background by checking its corners."""
    if img.mode not in ('RGBA', 'LA') and not (img.mode == 'P' and 'transparency' in img.info):
        return False
    
    img_rgba = img.convert("RGBA")
    w, h = img_rgba.size
    # Check corners and midpoints of edges
    points = [
        (0,0), (w-1,0), (0,h-1), (w-1,h-1),
        (w//2, 0), (w//2, h-1), (0, h//2), (w-1, h//2)
    ]
    # If any of these edge points are fully transparent, it's likely a transparent BG
    for x, y in points:
        if img_rgba.getpixel((x, y))[3] == 0:
            return True
    return False

def _process_transparent_on_white(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Takes a transparent image, scales it, and places it on a WHITE canvas."""
    bbox = img.getbbox()
    if not bbox:
        img_cropped = img
    else:
        img_cropped = img.crop(bbox)
        
    margin_ratio = 0.85
    avail_w = int(target_w * margin_ratio)
    avail_h = int(target_h * margin_ratio)
    
    scale = min(avail_w / img_cropped.width, avail_h / img_cropped.height)
    new_w = int(img_cropped.width * scale)
    new_h = int(img_cropped.height * scale)
    
    img_scaled = img_cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    # White canvas!
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    
    # Use alpha channel as mask to paste correctly
    mask = None
    if 'A' in img_scaled.getbands():
        mask = img_scaled.split()[3]
    canvas.paste(img_scaled, (paste_x, paste_y), mask)
    return canvas.convert("RGB")

def process_master(image_url: str, img_class: str) -> tuple[Image.Image, str]:
    """
    Main processor.
    Returns: (PIL Image, file_extension)
    """
    resp = requests.get(image_url)
    resp.raise_for_status()
    orig = Image.open(io.BytesIO(resp.content))
    
    target_w, target_h = 1000, 880
    if img_class in ("detail", "lifestyle"):
        target_w, target_h = 1760, 1100
        
    is_transparent = is_transparent_bg(orig)
    
    if img_class == "hero":
        if is_transparent:
            print("  [+] Image already without BG (HERO). Keeping transparent.")
            img_no_bg = orig.convert("RGBA")
        else:
            print("  [-] Image has BG (HERO). Removing BG with fal.ai...")
            res = fal_call("fal-ai/imageutils/rembg", {"image_url": image_url})
            img_data = requests.get(res["image"]["url"]).content
            img_no_bg = Image.open(io.BytesIO(img_data)).convert("RGBA")
            
        # Scale and place on TRANSPARENT canvas
        bbox = img_no_bg.getbbox()
        if bbox:
            img_cropped = img_no_bg.crop(bbox)
        else:
            img_cropped = img_no_bg
            
        margin_ratio = 0.85
        avail_w = int(target_w * margin_ratio)
        avail_h = int(target_h * margin_ratio)
        
        scale = min(avail_w / img_cropped.width, avail_h / img_cropped.height)
        new_w = int(img_cropped.width * scale)
        new_h = int(img_cropped.height * scale)
        
        img_scaled = img_cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        # Transparent canvas!
        canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
        paste_x = (target_w - new_w) // 2
        paste_y = (target_h - new_h) // 2
        
        canvas.paste(img_scaled, (paste_x, paste_y), img_scaled)
        return canvas, "png"
        
    else:
        if is_transparent:
            print(f"  [+] Image already without BG ({img_class.upper()}). Placing on WHITE background ({target_w}x{target_h}).")
            final_img = _process_transparent_on_white(orig.convert("RGBA"), target_w, target_h)
            return final_img, "jpg"
        else:
            print(f"  [-] Image has BG ({img_class.upper()}). Cropping/Padding smartly without removing BG...")
            final_img = _smart_crop_center(image_url, target_w, target_h)
            return final_img.convert("RGB"), "jpg"

def _smart_crop_center(image_url: str, target_w: int, target_h: int) -> Image.Image:
    """Keeps background, finds subject to center it, and crops/pads to target size."""
    resp = requests.get(image_url)
    resp.raise_for_status()
    orig = Image.open(io.BytesIO(resp.content)).convert("RGB")
    
    try:
        # Call fal to just get the bbox for centering
        res = fal_call("fal-ai/imageutils/rembg", {"image_url": image_url})
        img_data = requests.get(res["image"]["url"]).content
        mask_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        bbox = mask_img.getbbox()
    except Exception as e:
        bbox = None
        
    if not bbox:
        bbox = (0, 0, orig.width, orig.height)
        
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    
    target_ratio = target_w / float(target_h)
    orig_ratio = orig.width / orig.height
    
    if orig_ratio > target_ratio:
        new_h = orig.height
        new_w = int(new_h * target_ratio)
    else:
        new_w = orig.width
        new_h = int(new_w / target_ratio)
        
    left = cx - new_w / 2
    top = cy - new_h / 2
    right = cx + new_w / 2
    bottom = cy + new_h / 2
    
    if left < 0:
        right -= left
        left = 0
    if right > orig.width:
        left -= (right - orig.width)
        right = orig.width
        
    if top < 0:
        bottom -= top
        top = 0
    if bottom > orig.height:
        top -= (bottom - orig.height)
        bottom = orig.height
        
    left = max(0, left)
    top = max(0, top)
    right = min(orig.width, right)
    bottom = min(orig.height, bottom)
    
    cropped = orig.crop((left, top, right, bottom))
    return cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)


import base64

# ----------------------------------------------------------------
# True Close-Up Detail Image Generation (Feature Regions)
# ----------------------------------------------------------------

DETAIL_FEATURE_CATEGORIES = [
    "Material/texture close-up (fabric weave, leather grain, wood grain, marble veining, wool pile)",
    "Joinery / construction detail (leg-to-frame joint, stitching seams, welding points, hinges, zippers)",
    "Base / feet / legs close-up (material, shape, how it meets the floor)",
    "Edge or corner detail (piping, edge banding, rounded vs. sharp edges, trim)",
    "Functional hardware close-up (handles, buttons, cushions/tufting, drawer pulls, switches for lighting)",
    "Surface finish close-up (matte/gloss sheen, grain direction, pattern repeat on rugs/carpets)",
]

_DETAIL_CATEGORY_EXAMPLES = (
    "Category examples (apply the logic, not a fixed list):\n"
    "- Sofa/Chair -> upholstery texture close-up, stitching/tufting detail, leg/base close-up\n"
    "- Table -> tabletop material/edge detail, leg joinery close-up\n"
    "- Rug -> fiber/weave close-up, edge binding detail\n"
    "- Lighting -> fixture material/finish close-up, switch/hardware detail\n"
)

# Appended to every detail fal prompt — forbids full-product re-render + identity lock.
_DETAIL_FAL_PROMPT_SUFFIX = (
    f"EXTREME CLOSE-UP / MACRO only — isolate this ONE physical feature filling most of the frame. "
    f"This must visually read as zoomed-in from a different part of the SAME product — "
    f"NOT a full product shot, NOT a packshot, NOT a different product. "
    f"Do NOT invent parts, materials, or hardware that are not on this product "
    f"(e.g. no office-chair star base, no unrelated metal pedestal). "
    f"Feature perfectly centered. Seamless pure white (#FFFFFF) studio background. "
    f"Soft diffuse commercial catalog lighting, ultra-sharp macro focus, crisp material texture, "
    f"exact same materials/colors as the reference and product description. "
    f"Native {DETAIL_CANVAS_W}x{DETAIL_CANVAS_H} landscape composition."
)


def _clean_product_title(product_name: str) -> str:
    """Strip pipeline suffixes so fal/Vision see the real product title."""
    name = (product_name or "").strip()
    for suffix in (" - COPY", " – COPY", " — COPY"):
        if name.upper().endswith(suffix.upper()):
            name = name[: -len(suffix)].strip()
    return name or "Unknown product"


def _truncate_description(product_description: str, max_len: int = 450) -> str:
    desc = " ".join((product_description or "").split())
    if len(desc) <= max_len:
        return desc
    return desc[: max_len - 1].rstrip() + "…"


def _product_identity_block(
    product_name: str,
    product_description: str,
    product_category_hint: str = "",
) -> str:
    """Title + description (+ optional category) for every Vision/fal prompt."""
    title = _clean_product_title(product_name)
    desc = _truncate_description(product_description)
    cat = (product_category_hint or "").strip()
    lines = [
        f'PRODUCT TITLE: "{title}"',
    ]
    if cat:
        lines.append(f"PRODUCT CATEGORY / TYPE: {cat}")
    if desc:
        lines.append(f"PRODUCT DESCRIPTION: {desc}")
    else:
        lines.append("PRODUCT DESCRIPTION: (none provided — rely only on title + reference images)")
    lines.append(
        "IDENTITY RULE: Generate details ONLY for this exact product. "
        "Never mix in parts from other furniture (office chairs, unrelated metal bases, etc.)."
    )
    return "\n".join(lines)


def identify_detail_feature_regions(
    reference_urls: list[str] | str,
    product_name: str,
    product_description: str,
    openrouter_key: str,
    num_features: int = 3,
    product_category_hint: str = "",
) -> list[dict]:
    """
    Uses GPT-4o Vision to identify 2-3 distinct feature regions for TRUE detail close-ups.

    Product TITLE + DESCRIPTION are required context so features match the real product
    (e.g. voetenbank / footstool must not invent an office-chair metal base).
    """
    if isinstance(reference_urls, str):
        urls_list = [reference_urls] if reference_urls else []
    else:
        urls_list = [u for u in reference_urls if u and isinstance(u, str)]

    if not urls_list:
        return []

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
    }

    checklist_text = "\n".join(f"  - {cat}" for cat in DETAIL_FEATURE_CATEGORIES)
    identity = _product_identity_block(product_name, product_description, product_category_hint)
    title = _clean_product_title(product_name)

    sys_prompt = (
        f"You are an expert commercial product photographer for high-end furniture & home products.\n"
        f"You are given {len(urls_list)} reference image(s) of ONE product.\n\n"
        f"{identity}\n\n"
        "DEFINITION OF A DETAIL IMAGE:\n"
        "A detail image is NOT a full product shot. It is a close-up, zoomed-in image that isolates\n"
        "ONE specific physical feature of the product to show craftsmanship, material, and build quality.\n"
        "It must visually read as \"zoomed in from a different part of the same object\" —\n"
        "not the whole product re-rendered.\n\n"
        "CRITICAL — USE TITLE + DESCRIPTION:\n"
        f"  - Read the product title \"{title}\" and description carefully BEFORE choosing features.\n"
        "  - Only select features that physically exist on THIS product type (e.g. a voetenbank/ottoman\n"
        "    footstool may have upholstery texture, wood/metal legs that match the photos, piping —\n"
        "    but MUST NOT invent an office-chair star pedestal, swivel base, or unrelated hardware).\n"
        "  - If title/description conflict with a generic furniture stereotype, TRUST title + description + photos.\n\n"
        f"TASK: Identify exactly {num_features} DISTINCT feature regions from this checklist\n"
        "(only those physically present on THIS product):\n"
        f"{checklist_text}\n\n"
        f"{_DETAIL_CATEGORY_EXAMPLES}\n"
        "RULES FOR EACH FEATURE:\n"
        f"  1. Choose the best reference image index (0 to {len(urls_list) - 1}) where that feature is sharpest.\n"
        "  2. Provide a TIGHT normalized bounding box [ymin, xmin, ymax, xmax] (0.0–1.0) framing ONLY that feature.\n"
        "  3. Write a fal.ai generation prompt that ALWAYS starts with the product title and a short\n"
        "     description summary, then describes the ONE feature close-up. The prompt MUST say:\n"
        f"       - Product: \"{title}\" (exact product — do not change identity)\n"
        "       - extreme close-up / macro of this ONE feature only\n"
        "       - NOT a full product shot / NOT a packshot / NOT parts from another product\n"
        "       - materials/colors matching title, description, and the reference crop\n"
        "       - feature centered; pure white studio background; sharp material texture\n"
        f"       - native {DETAIL_CANVAS_W}x{DETAIL_CANVAS_H} landscape\n"
        "  4. STRICT FIDELITY: Do NOT invent features. Only what is visible in the references AND\n"
        "     consistent with the product title/description.\n"
        "  5. Each feature MUST target a completely different physical section of the product.\n"
        "  6. Set feature_type to one of: material_texture | joinery_construction | base_feet_legs |\n"
        "     edge_corner | functional_hardware | surface_finish\n\n"
        "Return ONLY a JSON object with key 'detail_features' = array of objects with keys:\n"
        "feature_name, feature_type, best_image_index, crop_box_normalized, detail_prompt."
    )

    content: list[dict] = [{"type": "text", "text": sys_prompt}]
    for u in urls_list[:6]:
        content.append({"type": "image_url", "image_url": {"url": u}})

    payload = {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=90)
    if not resp.ok:
        print(f"  [!] GPT-4o Vision feature analysis error: {resp.text}")
        return []

    try:
        data = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = json.loads(data)
        features = parsed.get("detail_features", [])
        return features[:num_features]
    except Exception as exc:
        print(f"  [!] Failed to parse feature regions JSON: {exc}")
        return []


def _crop_and_prepare_region(img_url: str, crop_box: list[float]) -> str:
    """
    Downloads image, tightly crops to normalized [ymin, xmin, ymax, xmax]
    with a small safety margin, white-fills transparency, returns JPEG data URL.
    Used as fal reference so generation starts from a true close-up, not a full packshot.
    """
    resp = requests.get(img_url, timeout=60)
    resp.raise_for_status()
    orig = Image.open(io.BytesIO(resp.content))
    w, h = orig.size

    ymin, xmin, ymax, xmax = crop_box
    # Small context margin only — keep it a tight close-up
    pad_y = (ymax - ymin) * 0.05
    pad_x = (xmax - xmin) * 0.05

    ymin = max(0.0, ymin - pad_y)
    xmin = max(0.0, xmin - pad_x)
    ymax = min(1.0, ymax + pad_y)
    xmax = min(1.0, xmax + pad_x)

    box = (int(xmin * w), int(ymin * h), int(xmax * w), int(ymax * h))
    # Guard against degenerate boxes
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        box = (0, 0, w, h)
    cropped = orig.crop(box)

    if cropped.mode in ("RGBA", "LA") or (cropped.mode == "P" and "transparency" in cropped.info):
        cropped_rgba = cropped.convert("RGBA")
        white_bg = Image.new("RGBA", cropped_rgba.size, (255, 255, 255, 255))
        white_bg.paste(cropped_rgba, (0, 0), cropped_rgba)
        cropped = white_bg.convert("RGB")
    else:
        cropped = cropped.convert("RGB")

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=95)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _build_detail_closeup_prompt(
    product_name: str,
    product_description: str,
    feature_name: str,
    feature_type: str,
    raw_prompt: str,
    product_category_hint: str = "",
) -> str:
    """Always lead fal prompts with product TITLE + DESCRIPTION for identity lock."""
    title = _clean_product_title(product_name)
    desc = _truncate_description(product_description, max_len=320)
    cat = (product_category_hint or "").strip()

    identity = f'Product title: "{title}".'
    if cat:
        identity += f" Product type/category: {cat}."
    if desc:
        identity += f" Product description: {desc}."

    base = (raw_prompt or "").strip().rstrip(".")
    # Avoid duplicating a weak feature-only prompt without title
    if not base or title.lower() not in base.lower():
        feature_bit = (
            f"Extreme macro close-up of \"{feature_name}\" ({feature_type}) "
            f"on product \"{title}\", isolating only this feature from the same object"
        )
        if base:
            base = f"{feature_bit}. {base}"
        else:
            base = feature_bit

    return (
        f"{identity} "
        f"{base}. "
        f"Stay faithful to title + description + reference images only. "
        f"{_DETAIL_FAL_PROMPT_SUFFIX}"
    )


def _fallback_detail_features(
    product_name: str,
    product_description: str,
    num_images: int,
    product_category_hint: str = "",
) -> list[dict]:
    """
    Safe fallbacks when Vision fails — prefer material/edge over inventing exotic bases.
    Uses title+description so we don't default to office-chair metal pedestals.
    """
    title = _clean_product_title(product_name)
    blob = f"{title} {product_description} {product_category_hint}".lower()
    is_footstool = any(k in blob for k in ("voetenbank", "footstool", "ottoman", "poef", "pouf"))
    is_sofa_chair = any(
        k in blob for k in ("bank", "sofa", "stoel", "chair", "fauteuil", "armchair")
    )

    features = [
        {
            "feature_name": "Material / texture close-up",
            "feature_type": "material_texture",
            "best_image_index": 0,
            "crop_box_normalized": [0.28, 0.28, 0.72, 0.72],
            "detail_prompt": (
                f'Product "{title}": extreme macro of the actual upholstery or surface material '
                f"visible on this product only — weave/grain/texture, not the full product"
            ),
        },
        {
            "feature_name": "Edge / corner / piping detail",
            "feature_type": "edge_corner",
            "best_image_index": 0,
            "crop_box_normalized": [0.15, 0.15, 0.55, 0.55],
            "detail_prompt": (
                f'Product "{title}": extreme close-up of an edge, corner, piping or trim '
                f"that exists on this product — not parts from another furniture type"
            ),
        },
    ]

    # Only add base/legs if product type typically has visible legs matching photos —
    # still describe via title so fal doesn't invent star pedestals.
    if is_footstool or is_sofa_chair or "tafel" in blob or "table" in blob:
        features.append(
            {
                "feature_name": "Legs / feet close-up (only if present on this product)",
                "feature_type": "base_feet_legs",
                "best_image_index": 0,
                "crop_box_normalized": [0.60, 0.20, 1.0, 0.80],
                "detail_prompt": (
                    f'Product "{title}": extreme close-up of the ACTUAL legs or feet of this '
                    f"product as in the reference photos only. Do not invent office-chair "
                    f"star bases, swivel pedestals, or unrelated metal bases."
                ),
            }
        )
    else:
        features.append(
            {
                "feature_name": "Surface finish close-up",
                "feature_type": "surface_finish",
                "best_image_index": 0,
                "crop_box_normalized": [0.30, 0.20, 0.70, 0.60],
                "detail_prompt": (
                    f'Product "{title}": extreme close-up of surface finish/sheen/grain '
                    f"matching this product only"
                ),
            }
        )

    return features[:num_images]


# Packshot angle variants — full product always visible (optional --mode packshot only).
_PACKSHOT_ANGLE_VARIANTS = [
    "straight-on front commercial catalog view",
    "subtle three-quarter angle from the front-left",
    "subtle three-quarter angle from the front-right",
]

_FAL_ENGINE_ENDPOINTS = {
    "flux2-pro": "fal-ai/flux-2-pro/edit",
    "flux2-max": "fal-ai/flux-2-max/edit",
    "kontext": "fal-ai/flux-pro/kontext",
    "img2img": "fal-ai/flux/dev/image-to-image",
    "redux": "fal-ai/flux/dev/redux",
}


def _build_packshot_prompt(product_name: str, product_description: str, angle: str) -> str:
    title = _clean_product_title(product_name)
    desc_bit = _truncate_description(product_description, max_len=220)
    desc_clause = f" Product description: {desc_bit}." if desc_bit else ""
    return (
        f'Professional commercial product photograph of the exact product titled "{title}".'
        f"{desc_clause} "
        f"Keep exact product identity, shape, materials, colors, and proportions — do not invent or alter design. "
        f"Camera: {angle}. "
        f"CRITICAL FRAMING: the FULL product must be completely visible in frame — do NOT crop any part "
        f"(no cutting legs, top, sides, or edges). Product perfectly centered horizontally and vertically. "
        f"Leave approximately 12-15% even pure-white margin on all four sides around the product. "
        f"Seamless pure white (#FFFFFF) studio background, no props, no people, no text, no logos, no shadows spilling off-frame. "
        f"Soft diffuse catalog lighting, ultra-sharp focus, high-end e-commerce packshot quality. "
        f"Native output composition {DETAIL_CANVAS_W}x{DETAIL_CANVAS_H} landscape (16:10)."
    )


def _collect_ref_pool(
    reference_url: str,
    all_image_urls: list[str] | None,
    prefer_image_urls: list[str] | None = None,
) -> list[str]:
    """Build ordered reference pool: preferred heroes first, then primary ref, then others."""
    pool: list[str] = []

    def _add(u: str | None) -> None:
        if u and u not in pool:
            pool.append(u)

    for u in prefer_image_urls or []:
        _add(u)
    _add(reference_url)
    for u in all_image_urls or []:
        _add(u)
    return pool


def _download_fal_image(gen_meta: dict, request_id: str) -> Image.Image:
    """Download fal output as-is — never PIL.resize to force canvas size."""
    gen_url = gen_meta["url"]
    reported_w = gen_meta.get("width")
    reported_h = gen_meta.get("height")
    img_resp = requests.get(gen_url, timeout=60)
    img_resp.raise_for_status()
    gen_img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
    out_w, out_h = gen_img.size
    if (out_w, out_h) != (DETAIL_CANVAS_W, DETAIL_CANVAS_H):
        print(
            "    [!] fal returned %dx%d (requested %dx%d); keeping fal output as-is "
            "(no manual resize)." % (out_w, out_h, DETAIL_CANVAS_W, DETAIL_CANVAS_H)
        )
    elif reported_w and reported_h and (int(reported_w), int(reported_h)) != (out_w, out_h):
        print(
            "    [!] fal meta size %sx%s != downloaded %dx%d; keeping download as-is."
            % (reported_w, reported_h, out_w, out_h)
        )
    return gen_img


def _fal_post(endpoint: str, payload: dict, fal_key: str, timeout: int = 180) -> tuple[dict, str]:
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json",
    }
    url = f"https://fal.run/{endpoint}"
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if not resp.ok:
        print(f"  [!] FAL API error ({endpoint}): {resp.text}")
        resp.raise_for_status()
    data = resp.json()
    request_id = data.get("request_id") or resp.headers.get("x-fal-request-id", "unknown")
    return data, request_id


def _generate_packshot_images(
    ref_pool: list[str],
    product_name: str,
    product_description: str,
    num_images: int,
    engine: str,
    fal_key: str,
) -> list[tuple]:
    """
    Full-product packshots via FLUX.2 edit / Kontext.
    No local crop/resize — fal receives full reference URLs and image_size.
    """
    engine_l = engine.lower()
    endpoint = _FAL_ENGINE_ENDPOINTS.get(engine_l, _FAL_ENGINE_ENDPOINTS["flux2-pro"])
    # Up to 3 packshot refs (multi-ref on FLUX.2); skip lifestyle-heavy pools by preferring heroes first.
    image_urls = ref_pool[:3]
    print(
        f"  [detail-gen] PACKSHOT mode via {endpoint} | refs={len(image_urls)} | "
        f"target={DETAIL_CANVAS_W}x{DETAIL_CANVAS_H} | no crop / no resize"
    )
    for i, u in enumerate(image_urls):
        print(f"    ref[{i}]: {u[:90]}...")

    results: list[tuple] = []
    for i in range(num_images):
        angle = _PACKSHOT_ANGLE_VARIANTS[i % len(_PACKSHOT_ANGLE_VARIANTS)]
        prompt = _build_packshot_prompt(product_name, product_description, angle)
        print(f"\n  [detail-gen] Packshot {i + 1}/{num_images}: {angle}")
        print(f"    Prompt: {prompt[:160]}...")

        try:
            if engine_l == "kontext":
                payload = {
                    "prompt": prompt,
                    "image_url": image_urls[0],
                    "aspect_ratio": "16:10",
                    "output_format": "jpeg",
                    "num_images": 1,
                    "safety_tolerance": "2",
                }
                # Kontext uses aspect_ratio; also request custom size when supported.
                payload["image_size"] = {"width": DETAIL_CANVAS_W, "height": DETAIL_CANVAS_H}
            else:
                # flux2-pro / flux2-max edit
                payload = {
                    "prompt": prompt,
                    "image_urls": image_urls,
                    "image_size": {"width": DETAIL_CANVAS_W, "height": DETAIL_CANVAS_H},
                    "output_format": "jpeg",
                    "safety_tolerance": "2",
                    "enable_safety_checker": True,
                }

            data, request_id = _fal_post(endpoint, payload, fal_key)
            gen_meta = data["images"][0]
            gen_img = _download_fal_image(gen_meta, request_id)
            results.append((gen_img, request_id, prompt))
            print(
                f"    [OK] Packshot via {engine_l} "
                f"(size={gen_img.size[0]}x{gen_img.size[1]}, request_id={request_id})"
            )
        except Exception as exc:
            print(f"  [!] Error generating packshot {i + 1}: {exc}")

    return results


def _generate_detail_closeups(
    vision_ref_pool: list[str],
    product_name: str,
    product_description: str,
    openrouter_key: str,
    num_images: int,
    strength: float,
    engine: str,
    fal_key: str,
    product_category_hint: str = "",
) -> list[tuple]:
    """
    TRUE detail close-ups: Vision picks 2-3 feature regions from the checklist,
    soft-crops that region as fal reference, then FLUX.2 / img2img renders a
    macro of ONE feature only (never a full-product packshot). No output resize.
    """
    engine_l = engine.lower()
    detail_engines = {"flux2-pro", "flux2-max", "kontext", "img2img", "redux"}
    if engine_l not in detail_engines:
        engine_l = "flux2-pro"
        print(f"  [!] Unknown engine; using flux2-pro for detail close-ups.")

    title = _clean_product_title(product_name)
    print(
        f"  [detail-gen] DETAIL close-up mode | analyzing {len(vision_ref_pool)} ref(s) "
        f"for '{title}' (engine={engine_l})..."
    )
    print(
        "    Identity:\n%s"
        % _product_identity_block(
            product_name, product_description, product_category_hint
        ).replace("\n", "\n    ")
    )

    features = identify_detail_feature_regions(
        vision_ref_pool,
        product_name,
        product_description,
        openrouter_key,
        num_features=num_images,
        product_category_hint=product_category_hint,
    )

    if not features:
        print("  [!] Vision could not identify feature regions. Using title/description-aware fallbacks.")
        features = _fallback_detail_features(
            product_name, product_description, num_images, product_category_hint
        )

    endpoint = _FAL_ENGINE_ENDPOINTS.get(engine_l, _FAL_ENGINE_ENDPOINTS["flux2-pro"])
    results: list[tuple] = []
    identity_ref_url = vision_ref_pool[0] if vision_ref_pool else ""

    for i, feat in enumerate(features):
        feat_name = feat.get("feature_name") or feat.get("feature") or ("Feature %d" % (i + 1))
        feat_type = feat.get("feature_type") or "material_texture"
        best_idx = feat.get("best_image_index") if "best_image_index" in feat else feat.get("image_index", 0)
        if not isinstance(best_idx, int) or best_idx < 0 or best_idx >= len(vision_ref_pool):
            best_idx = 0
        img_source_url = vision_ref_pool[best_idx]
        crop_box = (
            feat.get("crop_box_normalized")
            or feat.get("bounding_box")
            or feat.get("crop_box")
            or [0.25, 0.25, 0.75, 0.75]
        )
        raw_prompt = feat.get("detail_prompt") or feat.get("prompt") or ""
        prompt = _build_detail_closeup_prompt(
            product_name,
            product_description,
            feat_name,
            feat_type,
            raw_prompt,
            product_category_hint=product_category_hint,
        )

        print("\n  [detail-gen] Detail %d/%d: '%s' (%s)" % (i + 1, len(features), feat_name, feat_type))
        print("    Product: %s" % title)
        print("    Source [%d]: %s..." % (best_idx, img_source_url[:75]))
        print("    Crop Box: %s" % crop_box)
        print("    Prompt: %s..." % prompt[:240])
        print("    fal image_size: %dx%d (no manual resize)" % (DETAIL_CANVAS_W, DETAIL_CANVAS_H))

        try:
            cropped_data_uri = _crop_and_prepare_region(img_source_url, crop_box)

            if engine_l in ("flux2-pro", "flux2-max"):
                # (1) feature crop for framing, (2) full product URL for identity
                image_urls = [cropped_data_uri]
                if identity_ref_url:
                    image_urls.append(identity_ref_url)
                prompt_with_refs = (
                    prompt
                    + " Reference image 1 = tight crop of the target feature. "
                    + "Reference image 2 = full product for identity — "
                    + "match materials/colors from this product only; "
                    + "do not invent office-chair bases or parts from other furniture."
                )
                payload = {
                    "prompt": prompt_with_refs,
                    "image_urls": image_urls[:2],
                    "image_size": {"width": DETAIL_CANVAS_W, "height": DETAIL_CANVAS_H},
                    "output_format": "jpeg",
                    "safety_tolerance": "2",
                    "enable_safety_checker": True,
                }
            elif engine_l == "kontext":
                payload = {
                    "prompt": prompt,
                    "image_url": cropped_data_uri,
                    "image_size": {"width": DETAIL_CANVAS_W, "height": DETAIL_CANVAS_H},
                    "aspect_ratio": "16:10",
                    "output_format": "jpeg",
                    "num_images": 1,
                    "safety_tolerance": "2",
                }
            else:
                payload = {
                    "prompt": prompt,
                    "image_url": cropped_data_uri,
                    "image_size": {"width": DETAIL_CANVAS_W, "height": DETAIL_CANVAS_H},
                    "num_inference_steps": 40,
                    "num_images": 1,
                    "output_format": "jpeg",
                    "acceleration": "none",
                    "enable_safety_checker": True,
                }
                if engine_l == "redux":
                    payload["guidance_scale"] = 3.5
                else:
                    payload["strength"] = strength
                    payload["guidance_scale"] = 7.5

            data, request_id = _fal_post(endpoint, payload, fal_key)
            gen_img = _download_fal_image(data["images"][0], request_id)
            results.append((gen_img, request_id, prompt))
            print(
                f"    [OK] Detail close-up via {engine_l} "
                f"(size={gen_img.size[0]}x{gen_img.size[1]}, request_id={request_id})"
            )
        except Exception as exc:
            print(f"  [!] Error generating detail close-up {i + 1}: {exc}")

    return results


def generate_detail_images(
    reference_url: str,
    product_name: str,
    product_description: str,
    openrouter_key: str,
    num_images: int = 3,
    strength: float = 0.30,
    all_image_urls: list[str] | None = None,
    prefer_image_urls: list[str] | None = None,
    engine: str = "flux2-pro",
    mode: str = "detail",
    product_category_hint: str = "",
) -> list[tuple]:
    """
    Generate catalog images via fal.ai.

    Modes:
      detail (default): TRUE close-ups of 2-3 distinct physical features
        (material, joinery, base, edge, hardware, finish). NOT full product.
        Uses Vision feature regions + FLUX.2 Pro edit (or img2img/redux).
      macro: alias for detail
      packshot: full product centered white-BG shot (optional; not a detail image)

    No manual PIL resize of fal output — size comes from image_size 1760x1100.
    """
    fal_key_local = os.getenv("FAL_KEY", "").strip() or FAL_KEY
    if not fal_key_local:
        raise ValueError("FAL_KEY is not set.")

    mode_l = (mode or "detail").lower().strip()
    if mode_l == "macro":
        mode_l = "detail"
    engine_l = (engine or "flux2-pro").lower().strip()

    ref_pool = _collect_ref_pool(reference_url, all_image_urls, prefer_image_urls)
    if not ref_pool:
        print("  [!] No reference images available.")
        return []

    if mode_l == "packshot":
        packshot_engines = {"flux2-pro", "flux2-max", "kontext"}
        if engine_l not in packshot_engines:
            print(
                f"  [!] Packshot mode works best with flux2-pro/flux2-max/kontext; "
                f"upgrading engine from '{engine_l}' -> 'flux2-pro'."
            )
            engine_l = "flux2-pro"
        return _generate_packshot_images(
            ref_pool=ref_pool,
            product_name=product_name,
            product_description=product_description,
            num_images=num_images,
            engine=engine_l,
            fal_key=fal_key_local,
        )

    # Detail / close-up mode (default)
    if not openrouter_key:
        raise ValueError("openrouter_key is required for detail close-up mode.")
    return _generate_detail_closeups(
        vision_ref_pool=ref_pool,
        product_name=product_name,
        product_description=product_description,
        openrouter_key=openrouter_key,
        num_images=num_images,
        strength=strength,
        engine=engine_l,
        fal_key=fal_key_local,
        product_category_hint=product_category_hint,
    )
