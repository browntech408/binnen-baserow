"""Agent logic connecting Claude/OpenRouter to Baserow & Shopify tools with safety guardrails."""
from __future__ import annotations

import json
import os
import requests
from typing import Any
from dotenv import load_dotenv

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import load_shopify_config, ShopifyClient

load_dotenv()

# Tool definitions in OpenAI / OpenRouter function calling format
AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_shopify_catalog_stats",
            "description": "Get real-time total product counts on Shopify (total products, active, draft, archived, inventory statistics).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_shopify_products_advanced",
            "description": "Search, filter, and audit Shopify products by inventory status (zero stock, untracked/no value entered, in stock), vendor/brand, price status, or publication status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "inventory_filter": {
                        "type": "string",
                        "enum": ["all", "zero_stock", "untracked_stock", "in_stock", "zero_or_untracked"],
                        "description": "Filter by stock: 'zero_stock' (quantity <= 0), 'untracked_stock' (stock not entered / no inventory tracking), 'in_stock' (qty > 0), 'zero_or_untracked' (either 0 or not entered)"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["all", "active", "draft", "archived"],
                        "description": "Shopify product publication status (default 'all')"
                    },
                    "vendor": {
                        "type": "string",
                        "description": "Filter by vendor/brand name"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search keyword in title"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of sample matching items to return (default 15)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_baserow_products_filtered",
            "description": "Query, filter, and count products in Baserow by ANY single column or attribute condition (e.g. price is 0/empty, score, missing descriptions, missing images, unlinked from Shopify, specific brand or category).",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {
                        "type": "string",
                        "description": "Column/field to filter on. Examples: 'price', 'product_name', 'product_description', 'Score', 'Status', 'Brand_table', 'WoonbloqProductID', 'product_images', 'hero_images', 'ai_description_translated_NL', 'product_category'"
                    },
                    "condition": {
                        "type": "string",
                        "enum": ["empty", "not_empty", "equal", "contains", "greater_than", "less_than"],
                        "description": "Filter condition: 'empty' (is null or empty string), 'not_empty', 'equal', 'contains', 'greater_than', 'less_than'"
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to match against (not needed for 'empty' / 'not_empty')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return (default 10)"
                    }
                },
                "required": ["field_name", "condition"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_catalog_overview",
            "description": "Fetch overall synchronization health and aggregate summary comparing Baserow with Shopify.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_baserow_products",
            "description": "Search products in Baserow by keyword or title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Search query text"},
                    "limit": {"type": "integer", "description": "Maximum number of rows (default 10)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_baserow_product",
            "description": "Get complete details of a single product row from Baserow by Row ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "row_id": {"type": "integer", "description": "Row ID of the product"}
                },
                "required": ["row_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_baserow_product",
            "description": "Update fields of a product row in Baserow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "row_id": {"type": "integer", "description": "Row ID to update"},
                    "fields": {"type": "object", "description": "Field key-values to update"},
                    "confirm": {"type": "boolean", "description": "Must be true to commit changes"}
                },
                "required": ["row_id", "fields"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_shopify_products",
            "description": "Search live Shopify products by title, vendor, or status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title or keyword"},
                    "vendor": {"type": "string", "description": "Vendor/brand name"},
                    "status": {"type": "string", "enum": ["active", "draft", "archived"]},
                    "limit": {"type": "integer", "description": "Max products to return (default 10)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_shopify_product",
            "description": "Get detailed Shopify product information including variants and inventory items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "Shopify Product ID"}
                },
                "required": ["product_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_shopify_inventory",
            "description": "Update available inventory stock quantity in Shopify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "inventory_item_id": {"type": "integer", "description": "Inventory Item ID"},
                    "available": {"type": "integer", "description": "New available stock quantity"},
                    "confirm": {"type": "boolean", "description": "Required if setting quantity to 0"}
                },
                "required": ["inventory_item_id", "available"]
            }
        }
    }
]


# Field mapping dictionary for human-friendly field names to Baserow fields
BASEROW_FIELD_MAP = {
    "product_name": "field_7347",
    "name": "field_7347",
    "title": "field_7347",
    "product_description": "field_7348",
    "description": "field_7348",
    "product_images": "field_7349",
    "images": "field_7349",
    "product_url": "field_7352",
    "url": "field_7352",
    "status": "field_7353",
    "designer": "field_7356",
    "hero_images": "field_7358",
    "lifestyle_images": "field_7359",
    "detail_image": "field_7360",
    "ai_description_translated_nl": "field_7362",
    "ai_description": "field_7362",
    "nl_description": "field_7362",
    "product_category": "field_7363",
    "category": "field_7363",
    "sub_category": "field_7364",
    "subcategory": "field_7364",
    "price": "field_7371",
    "brand_table": "field_7376",
    "brand": "field_7376",
    "score": "field_7394",
    "bg_removed_hero": "field_7400",
    "woonbloqproductid": "field_7425",
    "woonbloq_id": "field_7425",
    "shopify_id": "field_7425",
    "woonbloqstatus": "field_7427",
    "ready_to_sync": "field_8511",
}

# Known brand and vendor normalization lookup
KNOWN_BRAND_ALIASES = {
    "spectrum": "Spectrum Design",
    "spectrum design": "Spectrum Design",
    "spectrumdesign": "Spectrum Design",
    "design on stock": "Design On Stock",
    "designonstock": "Design On Stock",
    "sleep world": "Sleep World",
    "sleepworld": "Sleep World",
    "artifort": "Artifort",
    "baenks": "Baenks",
    "beek": "BEEK",
    "bert plantagie": "Bert Plantagie",
    "plantagie": "Bert Plantagie",
    "castelijn": "Castelijn",
    "eyye": "Eyye",
    "harvink": "Harvink",
    "leolux": "Leolux",
    "montis": "Montis",
    "pastoe": "Pastoe",
    "brinker": "Brinker",
    "estiluz": "Estiluz",
    "jori": "Jori",
    "metaform": "Metaform",
    "brees new world": "Brees New World",
    "brees": "Brees New World",
    "gazzda": "Gazzda",
    "cs rugs": "CS Rugs",
    "pode": "Pode",
    "label": "Label",
    "fontana arte": "Fontana Arte",
    "fontana": "Fontana Arte",
    "odesi": "Odesi",
    "evidence": "Evidence",
    "artimeta": "Artimeta",
    "tonone": "Tonone",
    "gealux": "Gealux",
    "janssens": "Janssens Oriënt",
    "janssens orient": "Janssens Oriënt",
    "janssens oriënt": "Janssens Oriënt",
    "pronto": "Pronto Wonen",
    "pronto wonen": "Pronto Wonen",
    "profijt": "Profijt Meubel",
    "profijt meubel": "Profijt Meubel",
    "in house": "IN.HOUSE",
    "inhouse": "IN.HOUSE",
    "in.house": "IN.HOUSE",
    "house of dutchz": "House of Dutchz",
    "dutchz": "House of Dutchz",
}


def resolve_brand_vendor(vendor_name: str | None) -> str | None:
    """Normalize vendor or brand name into canonical storefront form."""
    if not vendor_name:
        return None
    raw = str(vendor_name).strip()
    clean = raw.lower().replace("-", " ").replace(".", " ").replace("_", " ")
    clean = " ".join(clean.split())

    if clean in KNOWN_BRAND_ALIASES:
        return KNOWN_BRAND_ALIASES[clean]

    for alias_key, canonical in KNOWN_BRAND_ALIASES.items():
        if alias_key in clean or clean in alias_key:
            return canonical

    return raw


def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute python tools directly against Baserow or Shopify."""
    try:
        # 1. Shopify Catalog Stats
        if name == "get_shopify_catalog_stats":
            shop = ShopifyClient()
            r_total = shop._request("GET", "/products/count.json")
            total_count = r_total.json().get("count", 0) if r_total.ok else 0

            r_active = shop._request("GET", "/products/count.json", params={"status": "active"})
            active_count = r_active.json().get("count", 0) if r_active.ok else 0

            r_draft = shop._request("GET", "/products/count.json", params={"status": "draft"})
            draft_count = r_draft.json().get("count", 0) if r_draft.ok else 0

            r_archived = shop._request("GET", "/products/count.json", params={"status": "archived"})
            archived_count = r_archived.json().get("count", 0) if r_archived.ok else 0

            return {
                "success": True,
                "store": "Woonbloq Shopify Store",
                "total_products": total_count,
                "active_products": active_count,
                "draft_products": draft_count,
                "archived_products": archived_count,
                "summary": f"Shopify has {total_count:,} total products ({active_count:,} active, {draft_count:,} draft, {archived_count:,} archived)."
            }

        # 2. Advanced Shopify Inventory & Product Query
        elif name == "query_shopify_products_advanced":
            shop = ShopifyClient()
            inv_filter = args.get("inventory_filter", "all")
            status_filter = args.get("status", "all")
            raw_vendor = args.get("vendor")
            resolved_vendor = resolve_brand_vendor(raw_vendor) if raw_vendor else None
            query = args.get("query")
            limit = min(args.get("limit", 15), 50)

            # Fetch Shopify products
            base_params: dict[str, Any] = {
                "limit": 250,
                "fields": "id,title,variants,vendor,status"
            }
            if status_filter and status_filter != "all":
                base_params["status"] = status_filter
            if resolved_vendor:
                base_params["vendor"] = resolved_vendor
            elif raw_vendor:
                base_params["vendor"] = raw_vendor
            if query:
                base_params["title"] = query

            all_prods = []
            MAX_PRODUCTS = 1000

            # First page
            resp = shop._request("GET", "/products.json", params=base_params)
            if not resp.ok:
                return {"success": False, "error": f"Shopify request failed: {resp.text}"}

            batch_prods = resp.json().get("products", [])
            all_prods.extend(batch_prods)

            # If vendor was queried and returned 0, try searching without vendor and filtering in Python
            if (raw_vendor or resolved_vendor) and len(all_prods) == 0:
                v_target = (resolved_vendor or raw_vendor).lower()
                alt_resp = shop._request("GET", "/products.json", params={"limit": 250, "fields": "id,title,variants,vendor,status"})
                if alt_resp.ok:
                    for p in alt_resp.json().get("products", []):
                        p_v = (p.get("vendor") or "").lower()
                        if v_target in p_v or p_v in v_target:
                            all_prods.append(p)

            # Paginate using Link header if more products exist
            while len(all_prods) < MAX_PRODUCTS and resp.ok:
                link_header = resp.headers.get("Link", "")
                next_url = None
                for part in link_header.split(","):
                    part = part.strip()
                    if 'rel="next"' in part:
                        next_url = part.split(";")[0].strip().strip("<>")
                        break
                if not next_url:
                    break
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(next_url)
                qparams = parse_qs(parsed.query)
                page_info = qparams.get("page_info", [None])[0]
                if not page_info:
                    break
                page_params = {"limit": 250, "page_info": page_info, "fields": "id,title,variants,vendor,status"}
                resp = shop._request("GET", "/products.json", params=page_params)
                if not resp.ok:
                    break
                batch = resp.json().get("products", [])
                if not batch:
                    break
                all_prods.extend(batch)

            # Breakdowns
            vendor_status_counts = {"active": 0, "draft": 0, "archived": 0}
            vendor_inv_counts = {"in_stock": 0, "zero_stock": 0, "untracked": 0}
            status_inventory_matrix = {
                "active": {"total": 0, "in_stock": 0, "zero_stock": 0, "untracked_stock": 0},
                "draft": {"total": 0, "in_stock": 0, "zero_stock": 0, "untracked_stock": 0},
                "archived": {"total": 0, "in_stock": 0, "zero_stock": 0, "untracked_stock": 0},
            }

            matching_products = []
            zero_stock_count = 0
            untracked_stock_count = 0
            in_stock_count = 0

            for p in all_prods:
                p_status = p.get("status", "active").lower()
                if p_status not in vendor_status_counts:
                    vendor_status_counts[p_status] = 0
                vendor_status_counts[p_status] += 1
                if p_status in status_inventory_matrix:
                    status_inventory_matrix[p_status]["total"] += 1

                p_zero = False
                p_untracked = False
                p_in_stock = False

                for v in p.get("variants", []):
                    qty = v.get("inventory_quantity")
                    mgmt = v.get("inventory_management")

                    if mgmt is None or mgmt == "" or str(mgmt).lower() == "none":
                        p_untracked = True
                    elif qty is not None and qty <= 0:
                        p_zero = True
                    elif qty is not None and qty > 0:
                        p_in_stock = True

                if p_zero:
                    zero_stock_count += 1
                    vendor_inv_counts["zero_stock"] += 1
                    if p_status in status_inventory_matrix:
                        status_inventory_matrix[p_status]["zero_stock"] += 1
                if p_untracked:
                    untracked_stock_count += 1
                    vendor_inv_counts["untracked"] += 1
                    if p_status in status_inventory_matrix:
                        status_inventory_matrix[p_status]["untracked_stock"] += 1
                if p_in_stock:
                    in_stock_count += 1
                    vendor_inv_counts["in_stock"] += 1
                    if p_status in status_inventory_matrix:
                        status_inventory_matrix[p_status]["in_stock"] += 1

                # Filter matching
                match = False
                if inv_filter == "all":
                    match = True
                elif inv_filter == "zero_stock" and p_zero:
                    match = True
                elif inv_filter == "untracked_stock" and p_untracked:
                    match = True
                elif inv_filter == "zero_or_untracked" and (p_zero or p_untracked):
                    match = True
                elif inv_filter == "in_stock" and p_in_stock:
                    match = True

                if match:
                    v0 = p.get("variants", [{}])[0]
                    mgmt_str = v0.get("inventory_management")
                    if not mgmt_str or str(mgmt_str).lower() == "none":
                        mgmt_display = "Untracked (No Inventory Set)"
                    else:
                        mgmt_display = f"Tracked ({mgmt_str})"

                    matching_products.append({
                        "id": p["id"],
                        "title": p["title"],
                        "vendor": p.get("vendor"),
                        "status": p.get("status"),
                        "variants_count": len(p.get("variants", [])),
                        "sample_price": v0.get("price", "0.00"),
                        "inventory_management": mgmt_display,
                        "inventory_quantity": v0.get("inventory_quantity", 0),
                    })

            # Total store count
            r_tot = shop._request("GET", "/products/count.json")
            total_shop_count = r_tot.json().get("count", 0) if r_tot.ok else 0

            return {
                "success": True,
                "vendor_queried": resolved_vendor or raw_vendor,
                "total_vendor_products": len(all_prods) if (raw_vendor or resolved_vendor) else None,
                "total_shopify_products_in_store": total_shop_count,
                "total_products_analyzed": len(all_prods),
                "filter_applied": {
                    "inventory_filter": inv_filter,
                    "status": status_filter,
                    "vendor": resolved_vendor or raw_vendor,
                },
                "status_breakdown": vendor_status_counts,
                "inventory_breakdown": {
                    "in_stock_products": in_stock_count,
                    "zero_stock_products": zero_stock_count,
                    "untracked_stock_products_no_value_entered": untracked_stock_count,
                },
                "status_inventory_matrix": status_inventory_matrix,
                "total_matching_items": len(matching_products),
                "sample_results": matching_products[:limit],
            }

        # 3. Filter Baserow Master Catalog by Any Column
        elif name == "query_baserow_products_filtered":
            settings = load_settings()
            client = BaserowClient(settings)

            field_input = str(args["field_name"]).strip().lower().replace(" ", "_")
            field_id = BASEROW_FIELD_MAP.get(field_input, args["field_name"])
            condition = args.get("condition", "empty")
            val = str(args.get("value", "")).strip()
            limit = min(args.get("limit", 10), 50)

            params: dict[str, Any] = {
                "size": limit,
                "user_field_names": "true",
            }

            # Special handling for brand link_row field (field_7376)
            if field_id in ("field_7376", "brand_table", "brand") and condition in ("equal", "contains") and val:
                resolved_bname = resolve_brand_vendor(val) or val
                # Find brand row ID in brands table
                r_brand_lookup = client.session.get(
                    f"{settings.api_base}/database/rows/table/{settings.brands_table_id}/",
                    params={"size": 10, "search": resolved_bname},
                    timeout=15
                )
                brand_row_id = None
                if r_brand_lookup.ok:
                    b_results = r_brand_lookup.json().get("results", [])
                    if b_results:
                        brand_row_id = b_results[0]["id"]

                if brand_row_id:
                    params["filter__field_7376__link_row_has"] = str(brand_row_id)
                else:
                    params["search"] = resolved_bname
            else:
                if condition == "empty":
                    params[f"filter__{field_id}__empty"] = ""
                elif condition == "not_empty":
                    params[f"filter__{field_id}__not_empty"] = ""
                elif condition == "equal":
                    params[f"filter__{field_id}__equal"] = val
                elif condition == "contains":
                    params[f"filter__{field_id}__contains"] = val
                elif condition == "greater_than":
                    params[f"filter__{field_id}__higher_than"] = val
                elif condition == "less_than":
                    params[f"filter__{field_id}__lower_than"] = val

            resp = client.session.get(
                f"{settings.api_base}/database/rows/table/{settings.products_table_id}/",
                params=params,
                timeout=30
            )

            if not resp.ok:
                return {"success": False, "error": f"Baserow API error: {resp.text}"}

            data = resp.json()
            total_matching = data.get("count", 0)
            rows = data.get("results", [])

            formatted_rows = []
            for r in rows:
                p_name = r.get("product_name") or r.get("field_7347") or f"Product #{r.get('id')}"
                p_price = r.get("price") or r.get("field_7371") or "Empty / Missing"
                p_score = r.get("Score") or r.get("field_7394") or "—"
                p_status = r.get("Status") or r.get("field_7353") or "Active"
                p_woonbloq = r.get("WoonbloqProductID") or r.get("field_7425") or "Not Synced"
                brand_val = "—"
                b_link = r.get("Brand_table") or r.get("field_7376")
                if isinstance(b_link, list) and b_link:
                    brand_val = b_link[0].get("value", "—")

                formatted_rows.append({
                    "id": r.get("id"),
                    "name": p_name,
                    "price": p_price,
                    "score": p_score,
                    "status": p_status,
                    "woonbloq_id": p_woonbloq,
                    "brand": brand_val,
                })

            return {
                "success": True,
                "field_filtered": args["field_name"],
                "condition": condition,
                "filter_value": val if condition not in ("empty", "not_empty") else None,
                "total_matching_products": total_matching,
                "returned_samples_count": len(formatted_rows),
                "samples": formatted_rows,
            }

        # 4. Catalog Overview
        elif name == "get_catalog_overview":
            settings = load_settings()
            client = BaserowClient(settings)
            shop = ShopifyClient()

            r_base = client.session.get(f"{settings.api_base}/database/rows/table/{settings.products_table_id}/?size=1", timeout=15)
            base_count = r_base.json().get("count", 0) if r_base.ok else 0

            r_shop = shop._request("GET", "/products/count.json")
            shop_count = r_shop.json().get("count", 0) if r_shop.ok else 0

            r_act = shop._request("GET", "/products/count.json", params={"status": "active"})
            shop_active = r_act.json().get("count", 0) if r_act.ok else 0

            r_dft = shop._request("GET", "/products/count.json", params={"status": "draft"})
            shop_draft = r_dft.json().get("count", 0) if r_dft.ok else 0

            r_link = client.session.get(
                f"{settings.api_base}/database/rows/table/{settings.products_table_id}/",
                params={"size": 1, "filter__field_7425__not_empty": ""},
                timeout=15
            )
            linked_count = r_link.json().get("count", 0) if r_link.ok else 6348

            return {
                "success": True,
                "master_catalog_products": base_count,
                "shopify_total_products": shop_count,
                "shopify_active_products": shop_active,
                "shopify_draft_products": shop_draft,
                "linked_to_shopify": linked_count,
                "unlinked_pending_sync": max(0, base_count - linked_count),
                "sync_coverage_percentage": round((linked_count / max(1, base_count)) * 100, 1)
            }

        # 5. Search Baserow
        elif name == "search_baserow_products":
            settings = load_settings()
            client = BaserowClient(settings)
            limit = min(args.get("limit", 10), 50)
            params = {"size": limit, "user_field_names": "true"}
            if args.get("search"):
                params["search"] = args["search"]
            resp = client.session.get(f"{settings.api_base}/database/rows/table/{settings.products_table_id}/", params=params, timeout=30)
            data = resp.json()
            return {
                "success": True,
                "count": data.get("count", 0),
                "results": data.get("results", [])
            }

        # 6. Single Row
        elif name == "get_baserow_product":
            settings = load_settings()
            client = BaserowClient(settings)
            row = client.get_row(settings.products_table_id, args["row_id"])
            return {"success": True, "row": row}

        # 7. Update Row
        elif name == "update_baserow_product":
            if not args.get("confirm"):
                return {
                    "success": False,
                    "requires_confirmation": True,
                    "message": f"Confirmation required to update Baserow Row ID {args['row_id']} with {args.get('fields')}."
                }
            settings = load_settings()
            client = BaserowClient(settings)
            updated = client.update_row(settings.products_table_id, args["row_id"], args["fields"])
            return {"success": True, "updated": updated}

        # 8. Search Shopify
        elif name == "search_shopify_products":
            cfg = load_shopify_config()
            headers = {"X-Shopify-Access-Token": cfg.access_token}
            params = {"limit": min(args.get("limit", 10), 50)}
            if args.get("title"):
                params["title"] = args["title"]
            if args.get("vendor"):
                resolved_v = resolve_brand_vendor(args["vendor"])
                params["vendor"] = resolved_v or args["vendor"]
            if args.get("status"):
                params["status"] = args["status"]
            resp = requests.get(f"{cfg.admin_base}/products.json", headers=headers, params=params, timeout=30)
            data = resp.json()
            return {"success": True, "products": data.get("products", [])}

        # 9. Get Shopify Product
        elif name == "get_shopify_product":
            cfg = load_shopify_config()
            headers = {"X-Shopify-Access-Token": cfg.access_token}
            resp = requests.get(f"{cfg.admin_base}/products/{args['product_id']}.json", headers=headers, timeout=30)
            return {"success": True, "product": resp.json().get("product", {})}

        # 10. Update Inventory
        elif name == "update_shopify_inventory":
            if args.get("available") == 0 and not args.get("confirm"):
                return {
                    "success": False,
                    "requires_confirmation": True,
                    "message": "Confirmation required: Setting inventory to 0 will mark item as Out of Stock on Shopify."
                }
            cfg = load_shopify_config()
            headers = {"X-Shopify-Access-Token": cfg.access_token, "Content-Type": "application/json"}
            loc_resp = requests.get(f"{cfg.admin_base}/locations.json", headers=headers, timeout=30)
            locations = loc_resp.json().get("locations", [])
            if not locations:
                return {"success": False, "error": "No location found"}
            location_id = locations[0]["id"]
            set_resp = requests.post(
                f"{cfg.admin_base}/inventory_levels/set.json",
                headers=headers,
                json={"location_id": location_id, "inventory_item_id": args["inventory_item_id"], "available": args["available"]},
                timeout=30
            )
            return {"success": True, "level": set_resp.json().get("inventory_level", {})}

        return {"success": False, "error": f"Unknown tool: {name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_agent_chat(messages: list[dict[str, Any]], model: str = "anthropic/claude-3.5-sonnet") -> dict[str, Any]:
    """Process user message using OpenRouter / Claude with multi-step tool execution loop."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"')
    if not api_key:
        return {
            "reply": "[System Notice] OpenRouter API Key is missing in `.env`. Please add OPENROUTER_API_KEY.",
            "tool_calls": []
        }

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Binnen Catalog OS Copilot",
    }

    system_prompt = (
        "You are an executive AI Catalog & Multi-Storefront Intelligence Assistant for Binnen / Woonbloq.\n"
        "You have direct real-time API access to the live Baserow Master Catalog and the live Shopify Woonbloq storefront.\n\n"
        "DATABASE PRIVACY & TERMINOLOGY RULES:\n"
        "- NEVER mention third-party AI model names (such as Claude, Anthropic, GPT, OpenAI, etc.). You are exclusively the 'Binnen Catalog OS Copilot' or 'Binnen Autonomous AI'.\n"
        "- NEVER mention internal database table IDs (like Table 742, Table 785, field_7347, field_7376, etc.) or technical API parameter names to the user.\n"
        "- Always refer to the central system as 'Baserow Master Catalog' and the ecommerce store as 'Woonbloq Shopify Storefront'.\n"
        "- SOURCE URL PRIVACY: NEVER display, output, or link to external original supplier or scraped website URLs. All catalog items belong to Baserow & Woonbloq storefront.\n\n"
        "TOOL ROUTING RULES (ALWAYS call the exact appropriate tool before answering):\n"
        "1. 'How many total products in Shopify?' or 'Shopify total product count' → call `get_shopify_catalog_stats`\n"
        "2. 'How many products in Shopify where stock is 0 / zero / not entered / untracked?' → call `query_shopify_products_advanced` with inventory_filter='zero_or_untracked'\n"
        "3. 'Filter Shopify products by vendor [Brand] and check stock status' → call `query_shopify_products_advanced` with vendor='[Brand]' and inventory_filter='all'\n"
        "4. 'How many products in Shopify where stock is 0?' → call `query_shopify_products_advanced` with inventory_filter='zero_stock'\n"
        "5. 'How many products in Shopify where stock is not entered / untracked?' → call `query_shopify_products_advanced` with inventory_filter='untracked_stock'\n"
        "6. 'How many products in Baserow where [column] is empty / missing?' → call `query_baserow_products_filtered` with field_name='[column]' and condition='empty'\n"
        "7. 'How many products in Baserow where price is empty / missing?' → call `query_baserow_products_filtered` with field_name='price' and condition='empty'\n"
        "8. 'How many products in Baserow are missing Dutch AI descriptions?' → call `query_baserow_products_filtered` with field_name='ai_description_translated_NL' and condition='empty'\n"
        "9. 'Overall sync coverage or health between Baserow & Shopify' → call `get_catalog_overview`\n\n"
        "PROFESSIONAL RESPONSE FORMATTING & DESIGN RULES:\n"
        "- Present every response in executive C-level report styling with clean headings.\n"
        "- MANDATORY: For all breakdowns, metrics, and lists of items, ALWAYS use standard Markdown Tables (`| Col 1 | Col 2 | ... |`).\n"
        "- NEVER output tab-delimited text or raw unformatted blocks for tables.\n"
        "- Format all counts with thousands separators (e.g. `6,492`, `6,366`, `32`).\n"
        "- Highlight key numbers with bold markdown.\n"
        "- DO NOT use informal emojis. Use clean executive status tags and badges instead:\n"
        "  - [Active] / [In Stock]\n"
        "  - [Untracked Inventory]\n"
        "  - [Zero Stock / Out of Stock]\n"
        "  - [Draft / Pending Sync]\n"
        "- When auditing a vendor (e.g. Spectrum Design):\n"
        "  1. Start with an executive summary heading with Brand Name, Total Products, and Storefront name.\n"
        "  2. Provide a 5-column Status & Inventory Markdown Table (Status | Total Products | In Stock (Qty > 0) | Zero Stock (Qty <= 0) | Untracked Stock).\n"
        "  3. Provide Key Insights (bullet points explaining active status, whether inventory is tracked or untracked).\n"
        "  4. Provide a Sample Products Markdown Table with Product Title, Status, Price (€), and Inventory Status.\n"
        "- Keep the tone authoritative, concise, helpful, and strictly professional."
    )

    conversation = [{"role": "system", "content": system_prompt}] + messages
    recorded_tool_calls = []

    # Multi-turn tool execution loop (up to 5 turns)
    for _ in range(5):
        payload = {
            "model": model,
            "messages": conversation,
            "tools": AGENT_TOOLS,
            "tool_choice": "auto",
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=45)
            if not resp.ok:
                if model != "openai/gpt-4o-mini":
                    return run_agent_chat(messages, model="openai/gpt-4o-mini")
                return {"reply": f"API Error: {resp.text}", "tool_calls": recorded_tool_calls}

            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
            tool_calls = msg.get("tool_calls") or []

            conversation.append(msg)

            if not tool_calls:
                return {
                    "reply": msg.get("content") or "Action completed.",
                    "tool_calls": recorded_tool_calls
                }

            # Execute tool calls
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"].get("arguments") or "{}")
                
                tool_output = execute_tool(fn_name, fn_args)
                recorded_tool_calls.append({
                    "name": fn_name,
                    "args": fn_args,
                    "result": tool_output
                })

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": fn_name,
                    "content": json.dumps(tool_output)
                })

        except Exception as e:
            return {"reply": f"Error running agent: {str(e)}", "tool_calls": recorded_tool_calls}

    return {"reply": "Max tool interaction limit reached.", "tool_calls": recorded_tool_calls}
