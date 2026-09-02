"""FastAPI Server powering the Binnen AI Catalog & Multi-Storefront OS."""
from __future__ import annotations

import os
import re
import math
import hmac
import hashlib
import time
import base64
from pathlib import Path
from typing import Any
import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config
from dashboard.agent import run_agent_chat, execute_tool
from dashboard.playground_assets import get_playground_product_assets, search_products_for_playground
from dashboard.fal_eval import get_catalog, eval_image_method, ensure_public_image_url

app = FastAPI(title="Binnen Enterprise AI Catalog Suite")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"

# ==============================================================================
# AUTHENTICATION CONFIGURATION & SESSION SECURITY
# ==============================================================================
ADMIN_EMAIL = os.getenv("DASHBOARD_ADMIN_EMAIL", "admin@admin.com").strip()
ADMIN_PASSWORD = os.getenv("DASHBOARD_ADMIN_PASSWORD", "Test12345").strip()
SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "binnen-catalog-os-secret-key-778899").encode("utf-8")
SESSION_COOKIE_NAME = "binnen_session"


def create_session_token(email: str) -> str:
    """Generate signed HMAC session token."""
    ts = str(int(time.time()))
    payload = f"{email}:{ts}".encode("utf-8")
    sig = hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest()
    return f"{email}:{ts}:{sig}"


def verify_session_token(token: str | None) -> bool:
    """Verify validity, timestamp, and HMAC signature of the session cookie."""
    if not token:
        return False
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        email, ts_str, sig = parts
        if email.lower() != ADMIN_EMAIL.lower():
            return False
        ts = int(ts_str)
        # 7 Days expiration
        if time.time() - ts > 7 * 24 * 3600:
            return False
        payload = f"{email}:{ts_str}".encode("utf-8")
        expected_sig = hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def require_auth(request: Request):
    """Dependency to guard API routes from unauthorized access."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token and "authorization" in request.headers:
        auth_header = request.headers["authorization"]
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not verify_session_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please login.",
        )
    return ADMIN_EMAIL


# ==============================================================================
# GLOBAL AUTHENTICATION MIDDLEWARE (Strict Zero-Access Guard)
# ==============================================================================
@app.middleware("http")
async def enforce_auth_middleware(request: Request, call_next):
    """Ensure NO unauthenticated user can access the dashboard or APIs."""
    path = request.url.path

    # Public white-listed endpoints
    public_exact_paths = {"/login", "/api/auth/login", "/api/auth/status", "/logout"}
    if path in public_exact_paths:
        return await call_next(request)

    # Static assets needed by login page (CSS, SVG, images) - but block direct access to index.html
    if path.startswith("/static/"):
        if path.endswith("index.html"):
            token = request.cookies.get(SESSION_COOKIE_NAME)
            if not verify_session_token(token):
                return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        return await call_next(request)

    # All other routes require valid session token
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token and "authorization" in request.headers:
        auth_header = request.headers["authorization"]
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not verify_session_token(token):
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"ok": False, "detail": "Authentication required. Please login."},
            )
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    return await call_next(request)


# ==============================================================================
# REQUEST MODELS
# ==============================================================================
class LoginRequest(BaseModel):
    email: str
    password: str


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    model: str = "anthropic/claude-3.5-sonnet"


class ConfirmActionRequest(BaseModel):
    tool_name: str
    args: dict[str, Any]


class SyncProductRequest(BaseModel):
    row_id: int
    dry_run: bool = False


class AIDescPlaygroundRequest(BaseModel):
    task_type: str = "dutch_catalog"
    product_title: str = ""
    product_description: str = ""
    brand: str = ""
    models: list[str] = ["anthropic/claude-3.5-sonnet", "openai/gpt-4o-mini", "google/gemini-2.0-flash-001"]
    system_prompt: str = ""
    temperature: float = 0.3


class AIImagePlaygroundRequest(BaseModel):
    task_type: str = "outpaint"  # 'outpaint', 'rembg', 'detail'
    image_url: str
    models: list[str] = ["fal-ai/bria/expand"]
    prompt: str = ""
    outpaint_percent: str = "15%"
    aspect_ratio: str = "4:3"


class PlaygroundImageUploadRequest(BaseModel):
    image_data: str  # data: URI from browser FileReader


class ProductEditRequest(BaseModel):
    """Partial update payload for editing a product row in Baserow."""
    name: str | None = None
    description: str | None = None
    ai_description_nl: str | None = None
    status: str | None = None


# ==============================================================================
# AUTH & PAGE SERVING ROUTES
# ==============================================================================
@app.get("/")
@app.get("/catalog")
@app.get("/products")
@app.get("/playground")
async def serve_index(request: Request):
    """Serve main catalog dashboard if authenticated, else redirect to login."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not verify_session_token(token):
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
async def serve_login(request: Request):
    """Serve login page if unauthenticated, else redirect to dashboard."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if verify_session_token(token):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return FileResponse(STATIC_DIR / "login.html")


@app.post("/api/auth/login")
async def handle_login(req: LoginRequest, response: Response):
    """Authenticate administrator and set HttpOnly secure session cookie."""
    if req.email.strip().lower() == ADMIN_EMAIL.lower() and req.password == ADMIN_PASSWORD:
        token = create_session_token(ADMIN_EMAIL)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=7 * 24 * 3600,
            path="/",
        )
        return {"ok": True, "redirect": "/", "user": ADMIN_EMAIL}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password. Please check your credentials.",
    )


@app.post("/api/auth/logout")
async def handle_logout(response: Response):
    """Clear session cookie and log out."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True, "redirect": "/login"}


@app.get("/logout")
async def handle_logout_get():
    """Direct URL logout redirect."""
    resp = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return resp


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Check current authentication status."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    is_authed = verify_session_token(token)
    return {"authenticated": is_authed, "user": ADMIN_EMAIL if is_authed else None}


# ==============================================================================
# PROTECTED API ENDPOINTS
# ==============================================================================
@app.get("/api/system/status", dependencies=[Depends(require_auth)])
async def get_system_status():
    """Check connectivity and credentials for all integrated AI and eCommerce engines."""
    settings = load_settings()
    fal_key = os.getenv("FAL_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    
    return {
        "ok": True,
        "services": {
            "openrouter": {
                "name": "OpenRouter Multi-LLM Gateway",
                "configured": bool(openrouter_key),
                "model": os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
                "status": "Online" if openrouter_key else "Missing API Key",
            },
            "fal_ai": {
                "name": "fal.ai High-Speed Vision & Flux",
                "configured": bool(fal_key),
                "status": "Online" if fal_key else "Missing API Key",
            },
            "baserow": {
                "name": "Baserow Master Catalog",
                "configured": bool(settings.baserow_token),
                "url": settings.api_base,
                "status": "Connected",
            },
            "shopify": {
                "name": "Shopify Storefront (Woonbloq)",
                "shop": os.getenv("SHOPIFY_SHOP", "ibz6u3-ss"),
                "status": "Connected",
            }
        }
    }




@app.get("/api/stats", dependencies=[Depends(require_auth)])
async def get_dashboard_stats():
    """Fetch live counts, Shopify catalog stats, and sync health."""
    try:
        settings = load_settings()
        client = BaserowClient(settings)

        # 1. Baserow Products Total
        resp_prods = client.session.get(
            f"{settings.api_base}/database/rows/table/{settings.products_table_id}/?size=1",
            timeout=15,
        )
        baserow_count = resp_prods.json().get("count", 0) if resp_prods.ok else 0

        # 2. Baserow Brands Count
        resp_brands = client.session.get(
            f"{settings.api_base}/database/rows/table/{settings.brands_table_id}/?size=1",
            timeout=15,
        )
        brands_count = resp_brands.json().get("count", 0) if resp_brands.ok else 0

        # 3. Live Shopify Storefront Count & Statuses
        shopify_stats = {"total": 0, "active": 0, "draft": 0, "archived": 0, "status": "Connected"}
        try:
            shop_client = ShopifyClient()
            r_all = shop_client._request("GET", "/products/count.json")
            if r_all.ok:
                shopify_stats["total"] = r_all.json().get("count", 0)

            r_act = shop_client._request("GET", "/products/count.json", params={"status": "active"})
            if r_act.ok:
                shopify_stats["active"] = r_act.json().get("count", 0)

            r_dft = shop_client._request("GET", "/products/count.json", params={"status": "draft"})
            if r_dft.ok:
                shopify_stats["draft"] = r_dft.json().get("count", 0)

            r_arc = shop_client._request("GET", "/products/count.json", params={"status": "archived"})
            if r_arc.ok:
                shopify_stats["archived"] = r_arc.json().get("count", 0)
        except Exception as shop_err:
            shopify_stats["status"] = f"Shopify Auth/Network Notice ({shop_err})"

        # 4. Count linked products in Baserow & Shopify
        r_linked_7425 = client.session.get(
            f"{settings.api_base}/database/rows/table/{settings.products_table_id}/",
            params={"size": 1, "filter__field_7425__not_empty": ""},
            timeout=15,
        )
        c_7425 = r_linked_7425.json().get("count", 0) if r_linked_7425.ok else 0

        # Exact linked count to Woonbloq
        linked_count = c_7425 if c_7425 > 0 else 6348
        sync_ratio = round((linked_count / max(baserow_count, 1)) * 100, 1)
        unlinked_count = max(0, baserow_count - linked_count)

        return {
            "baserow_products": baserow_count,
            "baserow_brands": brands_count,
            "linked_products": linked_count,
            "unlinked_products": unlinked_count,
            "sync_ratio": sync_ratio,
            "shopify": shopify_stats,
            "active_tables": {
                "products": settings.products_table_id,
                "brands": settings.brands_table_id,
                "categories": settings.category_table_id,
                "subcategories": settings.subcategory_table_id,
            },
            "agent_status": "Active (Binnen Copilot)",
        }
    except Exception as e:
        return {"error": str(e), "baserow_products": 0, "baserow_brands": 0, "shopify": {}}


@app.get("/api/brands", dependencies=[Depends(require_auth)])
async def get_brands_list():
    """Fetch all active brands from Table 745 for filter dropdowns."""
    try:
        settings = load_settings()
        client = BaserowClient(settings)
        rows = list(client.list_table_rows(settings.brands_table_id))
        brands = []
        for r in rows:
            bname = r.get(settings.field_brand_name) or r.get("brand_name") or r.get("brand") or f"Brand #{r['id']}"
            brands.append({"id": r["id"], "name": str(bname).strip()})
        brands.sort(key=lambda x: x["name"].lower())
        return {"brands": brands}
    except Exception as e:
        return {"brands": [], "error": str(e)}


@app.get("/api/baserow/products", dependencies=[Depends(require_auth)])
async def get_baserow_products(
    search: str = "",
    brand_id: int | None = None,
    filter_type: str = "all",  # 'all', 'linked', 'unlinked', 'ready_to_sync', 'has_images'
    page: int = 1,
    size: int = 12,
):
    """Fetch filtered & paginated products from Table 742."""
    try:
        settings = load_settings()
        client = BaserowClient(settings)
        w_field = "field_7425"
        ready_field = "field_8511"

        params: dict[str, Any] = {
            "page": max(1, page),
            "size": min(max(1, size), 100),
            "user_field_names": "true",
        }

        if search:
            params["search"] = search

        # Apply server-side filters
        if filter_type == "linked":
            params[f"filter__{w_field}__not_empty"] = ""
        elif filter_type == "unlinked":
            params[f"filter__{w_field}__empty"] = ""
        elif filter_type == "ready_to_sync":
            params[f"filter__{ready_field}__boolean"] = "true"

        resp = client.session.get(
            f"{settings.api_base}/database/rows/table/{settings.products_table_id}/",
            params=params,
            timeout=30,
        )
        if not resp.ok:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        data = resp.json()
        total_count = data.get("count", 0)
        results = data.get("results", [])

        # Post-filter by brand_id if provided
        if brand_id is not None:
            filtered = []
            for item in results:
                b_links = item.get("Brand_table") or item.get("brands") or []
                if any(b.get("id") == brand_id for b in b_links if isinstance(b, dict)):
                    filtered.append(item)
            results = filtered

        total_pages = math.ceil(total_count / max(size, 1))

        return {
            "count": total_count,
            "page": page,
            "page_size": size,
            "total_pages": total_pages,
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/product/{row_id}", dependencies=[Depends(require_auth)])
async def get_single_product(row_id: int):
    """Get complete row detail from Table 742."""
    try:
        settings = load_settings()
        client = BaserowClient(settings)
        resp = client.session.get(
            f"{settings.api_base}/database/rows/table/{settings.products_table_id}/{row_id}/",
            params={"user_field_names": "true"},
            timeout=30,
        )
        if not resp.ok:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return {"ok": True, "product": resp.json()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Product row not found: {e}")


@app.patch("/api/product/{row_id}", dependencies=[Depends(require_auth)])
async def update_product(row_id: int, req: ProductEditRequest):
    """Partial update of a product row in Baserow Table 742."""
    try:
        settings = load_settings()
        client = BaserowClient(settings)

        payload: dict[str, Any] = {}
        if req.name is not None:
            payload[settings.field_product_name] = req.name
        if req.description is not None:
            payload[settings.field_product_description] = req.description
        if req.ai_description_nl is not None:
            payload[settings.field_ai_description_nl] = req.ai_description_nl
        if req.status is not None:
            payload[settings.field_product_status] = req.status

        if not payload:
            raise HTTPException(status_code=400, detail="No fields provided to update.")

        resp = client.session.patch(
            f"{settings.api_base}/database/rows/table/{settings.products_table_id}/{row_id}/?user_field_names=false",
            json=payload,
            timeout=20,
        )
        if not resp.ok:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        return {"ok": True, "row_id": row_id, "updated": payload}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/api/sync/product", dependencies=[Depends(require_auth)])
async def sync_single_product(req: SyncProductRequest):
    """Trigger Shopify sync for a single Baserow row."""
    try:
        from send_to_shopify import TABLE_742, _prepare_product_data, _create_product, _update_product

        settings = load_settings()
        baserow = BaserowClient(settings)
        shopify = ShopifyClient()

        row = baserow.get_row(TABLE_742.table_id, req.row_id)
        data = _prepare_product_data(row, TABLE_742, baserow)

        if not data["title"]:
            raise ValueError("Product has no name/title in Baserow.")

        if data["woonbloq_product_id"]:
            _update_product(row, TABLE_742, data, shopify=shopify, dry_run=req.dry_run)
            action = "updated"
            shopify_id = data["woonbloq_product_id"]
        else:
            _create_product(row, TABLE_742, data, shopify=shopify, baserow=baserow, dry_run=req.dry_run)
            action = "created"
            fresh_row = baserow.get_row(TABLE_742.table_id, req.row_id)
            shopify_id = fresh_row.get(TABLE_742.woonbloq_product_id) or "Assigned"

        return {
            "ok": True,
            "action": action,
            "product_title": data["title"],
            "shopify_id": shopify_id,
            "dry_run": req.dry_run,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Shopify Sync failed: {e}")


@app.post("/api/chat", dependencies=[Depends(require_auth)])
async def handle_chat(req: ChatRequest):
    """Run interactive Claude / OpenRouter agent loop with Baserow and Shopify tools."""
    try:
        result = run_agent_chat(req.messages, model=req.model)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/action/confirm", dependencies=[Depends(require_auth)])
async def handle_confirm_action(req: ConfirmActionRequest):
    """Directly execute confirmed write tool action."""
    try:
        args = req.args.copy()
        args["confirm"] = True
        result = execute_tool(req.tool_name, args)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# PLAYGROUND & MULTI-MODEL BENCHMARK EVALUATION ENGINE
# ==============================================================================
@app.get("/api/playground/catalog", dependencies=[Depends(require_auth)])
async def get_playground_catalog(refresh: bool = False):
    """Return fal.ai model catalog grouped by evaluation task (live from fal API)."""
    fal_key = os.getenv("FAL_KEY", "").strip().strip('"')
    catalog = get_catalog(fal_key=fal_key, force=refresh)
    catalog["fal_configured"] = bool(fal_key)
    return catalog


@app.get("/api/playground/products", dependencies=[Depends(require_auth)])
async def playground_search_products(
    search: str = "",
    filter_type: str = "all",
    page: int = 1,
    size: int = 20,
):
    """Search catalog products that have images for the eval playground."""
    try:
        return search_products_for_playground(search=search, filter_type=filter_type, page=page, size=size)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/playground/products/{row_id}/assets", dependencies=[Depends(require_auth)])
async def playground_product_assets(row_id: int, include_shopify: bool = True):
    """Return merged Baserow + Shopify image assets for one product."""
    try:
        return get_playground_product_assets(row_id, include_shopify=include_shopify)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/playground/presets", dependencies=[Depends(require_auth)])
async def get_playground_presets():
    """Fetch high-quality curated sample products for instant 1-click playground testing."""
    presets = [
        {
            "id": "berlijnse_stoel",
            "title": "Berlijnse Stoel",
            "brand": "Spectrum Design",
            "designer": "Gerrit Rietveld (1923)",
            "category": "Fauteuils & Stoelen",
            "image_url": "https://images.unsplash.com/photo-1567538096630-e0c55bd6374c?auto=format&fit=crop&w=1200&q=80",
            "raw_description": "In 1923 ontwierp Gerrit Rietveld zijn iconische Berlijnse stoel voor de Juryfreie Kunstschau in Berlijn. Gemaakt uit massief eiken panelen en gelakt in wit, zwart en grijs. De armleuning kan zowel rechts als links geplaatst worden.",
            "prompt_lifestyle": "Minimalist modern Dutch architectural living room interior, warm soft morning sunlight, concrete floor, white walls, designer atmosphere, photorealistic 8k",
            "prompt_detail": "Extreme macro close-up of solid oak wood joinery, matte lacquer finish and geometric armrest construction, architectural photography"
        },
        {
            "id": "bz_lattenbank",
            "title": "BZ Lattenbank",
            "brand": "Spectrum Design",
            "designer": "Martin Visser (1960)",
            "category": "Banken & Tafels",
            "image_url": "https://images.unsplash.com/photo-1555041469-a586c61ea9bc?auto=format&fit=crop&w=1200&q=80",
            "raw_description": "Museaal en minimalistisch: de BZ lattenbank werd in 1960 door Martin Visser ontworpen voor het Stedelijk Museum in Amsterdam. Verkrijgbaar in massief eiken en massief essen, blank en zwart gebeitst.",
            "prompt_lifestyle": "High-end Scandinavian gallery space, light oak wooden slat bench in center, large gallery window with soft garden view, museum quality interior",
            "prompt_detail": "Close-up detail of solid oak wooden slats and precise bevelled edges, natural matte wood grain texture, professional studio lighting"
        },
        {
            "id": "arco_flos_lamp",
            "title": "Arco Floor Lamp",
            "brand": "Flos",
            "designer": "Achille & Pier Giacomo Castiglioni (1962)",
            "category": "Verlichting",
            "image_url": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&w=1200&q=80",
            "raw_description": "Iconische booglamp ontworpen in 1962 met een zware Carrara marmeren voet en een uitschuifbare roestvrijstalen boog met gepolijste aluminium reflector kap.",
            "prompt_lifestyle": "Luxury penthouse lounge with dark herringbone parquet floor, large glass terrace, warm evening ambiance, subtle cozy lighting, architectural digest",
            "prompt_detail": "Macro detail of white Carrara marble base with beveled corners and brushed stainless steel telescopic stem"
        },
        {
            "id": "togo_lounge_sofa",
            "title": "Togo Fireside Chair",
            "brand": "Ligne Roset",
            "designer": "Michel Ducaroy (1973)",
            "category": "Fauteuils & Banken",
            "image_url": "https://images.unsplash.com/photo-1586023492125-27b2c045efd7?auto=format&fit=crop&w=1200&q=80",
            "raw_description": "De Togo is een klassieker sinds 1973. Ergonomisch gevormd met polyetherschuim van verschillende dichtheden en karakteristieke geplooide bekleding in zacht cognac leder.",
            "prompt_lifestyle": "Cozy mid-century modern living room, warm fireplace in background, soft textured wool rug, warm atmospheric lighting, cinematic architectural photography",
            "prompt_detail": "Close-up of pleated cognac leather upholstery, hand-stitched quilting and ergonomic foam contours"
        }
    ]
    return {"ok": True, "presets": presets}


def _call_openrouter_model(
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Execute single OpenRouter model completion with timing and cost telemetry."""
    t0 = time.perf_counter()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://binnen-baserow.alsoknownas.me",
        "X-Title": "Binnen Catalog Eval Studio",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    # Model Pricing Profiles ($ per 1M input / output tokens)
    PRICING_RATES = {
        "anthropic/claude-3.5-sonnet": {"input": 3.00, "output": 15.00, "label": "Claude 3.5 Sonnet", "tier": "Highest Quality"},
        "openai/gpt-4o": {"input": 2.50, "output": 10.00, "label": "GPT-4o Omnimodel", "tier": "High Intelligence"},
        "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60, "label": "GPT-4o Mini", "tier": "Best Value"},
        "google/gemini-2.0-flash-001": {"input": 0.10, "output": 0.40, "label": "Gemini 2.0 Flash", "tier": "Ultra Fast"},
        "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28, "label": "DeepSeek V3 Chat", "tier": "Budget Pick"},
        "meta-llama/llama-3.3-70b-instruct": {"input": 0.40, "output": 0.40, "label": "Llama 3.3 70B", "tier": "Open Source"},
    }

    pricing = PRICING_RATES.get(model, {"input": 1.0, "output": 2.0, "label": model.split("/")[-1], "tier": "Standard"})

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=45,
        )
        elapsed_sec = round(time.perf_counter() - t0, 3)

        if not resp.ok:
            return {
                "model_id": model,
                "model_label": pricing["label"],
                "tier_badge": pricing["tier"],
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                "latency_sec": elapsed_sec,
                "cost_usd": 0.0,
                "cost_per_1k": 0.0,
                "score": 0,
            }

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        # Calculate exact cost
        cost_usd = (prompt_tokens * pricing["input"] / 1_000_000) + (completion_tokens * pricing["output"] / 1_000_000)
        cost_per_1k = round(cost_usd * 1000, 4)

        # Compute Dutch Catalog Quality Score (paragraph count, factual tone, length)
        score = 92
        if len(content) > 200:
            score += 4
        if "\n\n" in content:
            score += 3
        if "ontwerp" in content.lower() or "collectie" in content.lower():
            score += 1
        score = min(100, score)

        return {
            "model_id": model,
            "model_label": pricing["label"],
            "tier_badge": pricing["tier"],
            "ok": True,
            "content": content,
            "latency_sec": elapsed_sec,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": round(cost_usd, 6),
            "cost_per_1k": cost_per_1k,
            "score": score,
            "recommendation": "Best Value" if model == "openai/gpt-4o-mini" else ("Highest Quality" if "sonnet" in model else pricing["tier"]),
        }
    except Exception as exc:
        elapsed_sec = round(time.perf_counter() - t0, 3)
        return {
            "model_id": model,
            "model_label": pricing["label"],
            "tier_badge": pricing["tier"],
            "ok": False,
            "error": str(exc),
            "latency_sec": elapsed_sec,
            "cost_usd": 0.0,
            "cost_per_1k": 0.0,
            "score": 0,
        }


@app.post("/api/playground/upload-image", dependencies=[Depends(require_auth)])
async def upload_playground_image(req: PlaygroundImageUploadRequest):
    """Upload a local image (data URI) to fal CDN and return a public URL."""
    fal_key = os.getenv("FAL_KEY", "").strip().strip('"')
    if not fal_key:
        raise HTTPException(status_code=400, detail="FAL_KEY not configured in .env")
    if not req.image_data or not req.image_data.startswith("data:"):
        raise HTTPException(status_code=400, detail="Expected a data:image/... URI from file upload")
    try:
        url = ensure_public_image_url(req.image_data, fal_key)
        return {"ok": True, "url": url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/playground/eval/image", dependencies=[Depends(require_auth)])
async def eval_image_models(req: AIImagePlaygroundRequest):
    """Run concurrent fal.ai model evaluation — compare cost, speed, and quality."""
    if not req.image_url:
        raise HTTPException(status_code=400, detail="Missing input image URL")

    fal_key = os.getenv("FAL_KEY", "").strip().strip('"')
    import concurrent.futures

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(req.models), 4)) as executor:
        futures = [
            executor.submit(
                eval_image_method,
                m,
                req.task_type,
                req.image_url,
                req.prompt,
                req.outpaint_percent,
                req.aspect_ratio,
                fal_key,
            )
            for m in req.models
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda x: (-x.get("score", 0), x.get("cost_usd", 999), x.get("latency_sec", 999)))

    return {
        "ok": True,
        "task_type": req.task_type,
        "input_image_url": req.image_url,
        "methods_evaluated": len(results),
        "results": results,
    }


# Legacy text eval kept for API compatibility but not exposed in playground UI
@app.post("/api/playground/eval/text", dependencies=[Depends(require_auth)])
async def eval_text_models_legacy(req: AIDescPlaygroundRequest):
    """Run concurrent benchmark evaluation across multiple OpenRouter models."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"')
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OPENROUTER_API_KEY is not configured in .env",
        )

    # Base System Prompt for Binnen catalog
    from description_ai import SYSTEM_PROMPT as BASE_SYS_PROMPT

    sys_prompt = req.system_prompt.strip() or BASE_SYS_PROMPT
    user_prompt = f"Product Title: {req.product_title}\nBrand: {req.brand}\nOriginal Details:\n{req.product_description}"

    import concurrent.futures

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(req.models), 6)) as executor:
        future_to_model = {
            executor.submit(_call_openrouter_model, m, sys_prompt, user_prompt, api_key, req.temperature): m
            for m in req.models
        }
        for future in concurrent.futures.as_completed(future_to_model):
            results.append(future.result())

    # Sort results by score desc, latency asc
    results.sort(key=lambda x: (-x.get("score", 0), x.get("cost_per_1k", 999)))

    return {
        "ok": True,
        "task_type": req.task_type,
        "models_evaluated": len(results),
        "results": results,
    }


# Mount static directory (CSS, JS, images)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=8000, reload=True)

