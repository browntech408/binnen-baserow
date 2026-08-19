"""FastAPI Server powering the Binnen AI Catalog & Multi-Storefront OS."""
from __future__ import annotations

import os
import re
import math
from pathlib import Path
from typing import Any
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config
from dashboard.agent import run_agent_chat, execute_tool

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


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    model: str = "anthropic/claude-3.5-sonnet"


class ConfirmActionRequest(BaseModel):
    tool_name: str
    args: dict[str, Any]


class SyncProductRequest(BaseModel):
    row_id: int
    dry_run: bool = False


class GenerateAIDescRequest(BaseModel):
    row_id: int


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stats")
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

        # 4. Count linked products in Baserow
        r_linked = client.session.get(
            f"{settings.api_base}/database/rows/table/{settings.products_table_id}/",
            params={
                "size": 1,
                f"filter__{settings.field_woonbloq_product_id if hasattr(settings, 'field_woonbloq_product_id') else 'field_7425'}__empty": "false",
            },
            timeout=15,
        )
        linked_count = r_linked.json().get("count", 0) if r_linked.ok else 0
        sync_ratio = round((linked_count / max(baserow_count, 1)) * 100, 1)

        return {
            "baserow_products": baserow_count,
            "baserow_brands": brands_count,
            "linked_products": linked_count,
            "unlinked_products": max(0, baserow_count - linked_count),
            "sync_ratio": sync_ratio,
            "shopify": shopify_stats,
            "active_tables": {
                "products": settings.products_table_id,
                "brands": settings.brands_table_id,
                "categories": settings.category_table_id,
                "subcategories": settings.subcategory_table_id,
            },
            "agent_status": "Active (Claude 3.5 Sonnet + MCP)",
        }
    except Exception as e:
        return {"error": str(e), "baserow_products": 0, "baserow_brands": 0, "shopify": {}}


@app.get("/api/brands")
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


@app.get("/api/baserow/products")
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

        # Apply server-side filters if feasible
        if filter_type == "linked":
            params[f"filter__{w_field}__empty"] = "false"
        elif filter_type == "unlinked":
            params[f"filter__{w_field}__empty"] = "true"
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


@app.get("/api/product/{row_id}")
async def get_single_product(row_id: int):
    """Get complete row detail from Table 742."""
    try:
        settings = load_settings()
        client = BaserowClient(settings)
        row = client.get_row(settings.products_table_id, row_id)
        return {"ok": True, "product": row}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Product row not found: {e}")


@app.post("/api/sync/product")
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
            # Refresh row to get assigned ID
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


@app.post("/api/chat")
async def handle_chat(req: ChatRequest):
    """Run interactive Claude / OpenRouter agent loop with Baserow and Shopify tools."""
    try:
        result = run_agent_chat(req.messages, model=req.model)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/action/confirm")
async def handle_confirm_action(req: ConfirmActionRequest):
    """Directly execute confirmed write tool action."""
    try:
        args = req.args.copy()
        args["confirm"] = True
        result = execute_tool(req.tool_name, args)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Mount static directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=8000, reload=True)
