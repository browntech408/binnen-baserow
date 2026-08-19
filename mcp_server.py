"""Binnen Agentic MCP Server (Python Native).

Model Context Protocol (MCP) server connecting Claude AI directly to
Baserow Database and Shopify Storefront using existing Python clients.
"""
from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyConfig, load_shopify_config
import requests

load_dotenv()

# Initialize MCPServer
mcp = MCPServer("Binnen Baserow & Shopify Suite")

# Initialize Baserow client
_settings = None
_baserow_client = None

def get_baserow() -> tuple[Any, BaserowClient]:
    global _settings, _baserow_client
    if _baserow_client is None:
        _settings = load_settings()
        _baserow_client = BaserowClient(_settings)
    return _settings, _baserow_client


# Initialize Shopify REST helper
def shopify_request(method: str, path: str, json_data: dict | None = None, params: dict | None = None) -> dict:
    cfg = load_shopify_config()
    headers = {
        "X-Shopify-Access-Token": cfg.access_token,
        "Content-Type": "application/json",
    }
    url = f"{cfg.admin_base}{path}"
    resp = requests.request(method, url, json=json_data, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


# ==============================================================================
# BASEROW MCP TOOLS
# ==============================================================================

@mcp.tool()
def get_baserow_schema(table_id: int | None = None) -> str:
    """Fetch table schema and field names from Baserow database.
    
    Args:
        table_id: Optional Baserow table ID (defaults to PRODUCTS_TABLE_ID).
    """
    try:
        settings, client = get_baserow()
        target_table = table_id or settings.products_table_id
        url = f"{settings.api_base}/database/fields/table/{target_table}/"
        resp = client.session.get(url, timeout=30)
        resp.raise_for_status()
        fields = resp.json()
        
        summary = [
            {"id": f.get("id"), "name": f.get("name"), "type": f.get("type"), "primary": f.get("primary", False)}
            for f in fields
        ]
        return json.dumps({"table_id": target_table, "total_fields": len(fields), "fields": summary}, indent=2)
    except Exception as e:
        return f"Error fetching Baserow schema: {e}"


@mcp.tool()
def search_baserow_products(search: str = "", limit: int = 10, table_id: int | None = None) -> str:
    """Search and filter products in the Baserow database table.
    
    Args:
        search: Keyword search string (matches product name, description, etc.).
        limit: Max rows to return (1 to 50, default 10).
        table_id: Optional Baserow table ID (defaults to PRODUCTS_TABLE_ID).
    """
    try:
        settings, client = get_baserow()
        target_table = table_id or settings.products_table_id
        safe_limit = max(1, min(limit, 50))
        
        params = {"size": safe_limit, "user_field_names": "true"}
        if search.strip():
            params["search"] = search.strip()
            
        url = f"{settings.api_base}/database/rows/table/{target_table}/"
        resp = client.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        return json.dumps({
            "count": data.get("count", 0),
            "returned": len(data.get("results", [])),
            "results": data.get("results", [])
        }, indent=2)
    except Exception as e:
        return f"Error searching Baserow products: {e}"


@mcp.tool()
def get_baserow_product(row_id: int, table_id: int | None = None) -> str:
    """Fetch complete details of a single product row from Baserow by its Row ID.
    
    Args:
        row_id: The Row ID of the product.
        table_id: Optional Baserow table ID (defaults to PRODUCTS_TABLE_ID).
    """
    try:
        settings, client = get_baserow()
        target_table = table_id or settings.products_table_id
        row = client.get_row(target_table, row_id)
        return json.dumps(row, indent=2)
    except Exception as e:
        return f"Error fetching Baserow row {row_id}: {e}"


@mcp.tool()
def update_baserow_product(row_id: int, fields: dict, confirm: bool = False, table_id: int | None = None) -> str:
    """Update field values in a Baserow product row.
    
    Args:
        row_id: The Row ID of the product to update.
        fields: Dictionary of field names/IDs and new values.
        confirm: Confirmation flag to approve update.
        table_id: Optional Baserow table ID (defaults to PRODUCTS_TABLE_ID).
    """
    if not confirm:
        return (
            f"CONFIRMATION REQUIRED: You are about to update Row ID {row_id} with fields: {fields}. "
            f"Please call this tool with confirm=True to apply changes."
        )
    try:
        settings, client = get_baserow()
        target_table = table_id or settings.products_table_id
        updated = client.update_row(target_table, row_id, fields)
        return json.dumps({"message": "Baserow row updated successfully", "row": updated}, indent=2)
    except Exception as e:
        return f"Error updating Baserow row {row_id}: {e}"


# ==============================================================================
# SHOPIFY MCP TOOLS
# ==============================================================================

@mcp.tool()
def search_shopify_products(title: str = "", vendor: str = "", status: str = "", limit: int = 10) -> str:
    """Search and filter live products in the Shopify store.
    
    Args:
        title: Filter by product title or keyword.
        vendor: Filter by vendor/brand name.
        status: Filter by status ('active', 'draft', 'archived').
        limit: Max products to return (default 10, max 50).
    """
    try:
        params = {"limit": max(1, min(limit, 50))}
        if title:
            params["title"] = title
        if vendor:
            params["vendor"] = vendor
        if status:
            params["status"] = status
            
        data = shopify_request("GET", "/products.json", params=params)
        products = data.get("products", [])
        
        simplified = []
        for p in products:
            first_var = p.get("variants", [{}])[0] if p.get("variants") else {}
            simplified.append({
                "id": p.get("id"),
                "title": p.get("title"),
                "status": p.get("status"),
                "vendor": p.get("vendor"),
                "variants_count": len(p.get("variants", [])),
                "first_variant": {
                    "id": first_var.get("id"),
                    "price": first_var.get("price"),
                    "inventory_quantity": first_var.get("inventory_quantity"),
                    "inventory_item_id": first_var.get("inventory_item_id"),
                } if first_var else None,
            })
            
        return json.dumps({"count": len(products), "products": simplified}, indent=2)
    except Exception as e:
        return f"Error searching Shopify products: {e}"


@mcp.tool()
def get_shopify_product(product_id: int) -> str:
    """Get full product record from Shopify (including variants, images, options).
    
    Args:
        product_id: Shopify Product ID.
    """
    try:
        data = shopify_request("GET", f"/products/{product_id}.json")
        return json.dumps(data.get("product", {}), indent=2)
    except Exception as e:
        return f"Error fetching Shopify product {product_id}: {e}"


@mcp.tool()
def update_shopify_product(
    product_id: int,
    title: str | None = None,
    body_html: str | None = None,
    status: str | None = None,
    tags: str | None = None,
    confirm: bool = False,
) -> str:
    """Update title, description, status, or tags of a Shopify product.
    
    Args:
        product_id: Shopify Product ID.
        title: New product title.
        body_html: New HTML description.
        status: New status ('active', 'draft', 'archived').
        tags: Comma-separated tags.
        confirm: Confirmation flag (required for status change or bulk edits).
    """
    if status in ("draft", "archived") and not confirm:
        return (
            f"CONFIRMATION REQUIRED: Setting status to '{status}' may hide this product from storefront. "
            f"Please call with confirm=True to proceed."
        )
    try:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body_html is not None:
            payload["body_html"] = body_html
        if status is not None:
            payload["status"] = status
        if tags is not None:
            payload["tags"] = tags

        data = shopify_request("PUT", f"/products/{product_id}.json", json_data={"product": payload})
        return json.dumps({"message": "Shopify product updated successfully", "product": data.get("product")}, indent=2)
    except Exception as e:
        return f"Error updating Shopify product {product_id}: {e}"


@mcp.tool()
def update_shopify_inventory(inventory_item_id: int, available: int, confirm: bool = False) -> str:
    """Update available stock quantity in Shopify.
    
    Args:
        inventory_item_id: Shopify Inventory Item ID.
        available: New available quantity.
        confirm: Required if setting available stock to 0.
    """
    if available == 0 and not confirm:
        return (
            f"CONFIRMATION REQUIRED: Setting available inventory to 0 will mark this item Out of Stock. "
            f"Please call with confirm=True to proceed."
        )
    try:
        # Get primary location
        loc_data = shopify_request("GET", "/locations.json")
        locations = loc_data.get("locations", [])
        if not locations:
            return "Error: No location found in Shopify store."
        location_id = locations[0]["id"]

        # Set inventory level
        payload = {
            "location_id": location_id,
            "inventory_item_id": inventory_item_id,
            "available": available,
        }
        res = shopify_request("POST", "/inventory_levels/set.json", json_data=payload)
        return json.dumps({"message": "Inventory updated successfully", "level": res.get("inventory_level")}, indent=2)
    except Exception as e:
        return f"Error updating Shopify inventory: {e}"


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())

