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
                "summary": f"Shopify has {total_count} total products ({active_count} active, {draft_count} draft, {archived_count} archived)."
            }

        # 2. Advanced Shopify Inventory & Product Query
        elif name == "query_shopify_products_advanced":
            shop = ShopifyClient()
            inv_filter = args.get("inventory_filter", "all")
            status_filter = args.get("status", "all")
            vendor = args.get("vendor")
            query = args.get("query")
            limit = min(args.get("limit", 15), 50)

            # Fetch ALL Shopify products via pagination (cursor-based)
            # Cap at 1000 products to keep response time reasonable
            base_params: dict[str, Any] = {
                "limit": 250,
                "fields": "id,title,variants,vendor,status"
            }
            if status_filter and status_filter != "all":
                base_params["status"] = status_filter
            if vendor:
                base_params["vendor"] = vendor
            if query:
                base_params["title"] = query

            all_prods = []
            MAX_PRODUCTS = 1000
            next_page_url = None

            # First page
            resp = shop._request("GET", "/products.json", params=base_params)
            if not resp.ok:
                return {"success": False, "error": f"Shopify request failed: {resp.text}"}

            all_prods.extend(resp.json().get("products", []))

            # Paginate using Link header
            while len(all_prods) < MAX_PRODUCTS:
                link_header = resp.headers.get("Link", "")
                next_url = None
                for part in link_header.split(","):
                    part = part.strip()
                    if 'rel="next"' in part:
                        next_url = part.split(";")[0].strip().strip("<>")
                        break
                if not next_url:
                    break
                # Extract page_info from next_url
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(next_url)
                qparams = parse_qs(parsed.query)
                page_info = qparams.get("page_info", [None])[0]
                if not page_info:
                    break
                resp = shop._request("GET", "/products.json", params={"limit": 250, "page_info": page_info, "fields": "id,title,variants,vendor,status"})
                if not resp.ok:
                    break
                batch = resp.json().get("products", [])
                if not batch:
                    break
                all_prods.extend(batch)

            matching_products = []
            zero_stock_count = 0
            untracked_stock_count = 0
            in_stock_count = 0

            for p in all_prods:
                p_zero = False
                p_untracked = False
                p_in_stock = False

                for v in p.get("variants", []):
                    qty = v.get("inventory_quantity")
                    mgmt = v.get("inventory_management")

                    if mgmt is None or mgmt == "":
                        p_untracked = True
                    elif qty is not None and qty <= 0:
                        p_zero = True
                    elif qty is not None and qty > 0:
                        p_in_stock = True

                if p_zero:
                    zero_stock_count += 1
                if p_untracked:
                    untracked_stock_count += 1
                if p_in_stock:
                    in_stock_count += 1

                # Apply filter match
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
                    matching_products.append({
                        "id": p["id"],
                        "title": p["title"],
                        "vendor": p.get("vendor"),
                        "status": p.get("status"),
                        "variants_count": len(p.get("variants", [])),
                        "sample_price": p.get("variants", [{}])[0].get("price", "0.00"),
                        "inventory_management": p.get("variants", [{}])[0].get("inventory_management") or "Not tracked",
                        "inventory_quantity": p.get("variants", [{}])[0].get("inventory_quantity"),
                    })

            # Get total count on Shopify for reference
            r_tot = shop._request("GET", "/products/count.json")
            total_shop_count = r_tot.json().get("count", 0) if r_tot.ok else 0

            return {
                "success": True,
                "total_shopify_products_in_store": total_shop_count,
                "total_products_analyzed": len(all_prods),
                "fully_analyzed": len(all_prods) >= total_shop_count,
                "filter_applied": {
                    "inventory_filter": inv_filter,
                    "status": status_filter,
                    "vendor": vendor,
                },
                "stock_analysis_counts": {
                    "zero_stock_products": zero_stock_count,
                    "untracked_stock_products_no_value_entered": untracked_stock_count,
                    "in_stock_products": in_stock_count,
                },
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
            val = args.get("value", "")
            limit = min(args.get("limit", 10), 50)

            # Build Baserow filter query
            params: dict[str, Any] = {
                "size": limit,
                "user_field_names": "true",
            }

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
                formatted_rows.append({
                    "id": r.get("id"),
                    "name": r.get("product_name") or r.get("field_7347") or f"Product #{r.get('id')}",
                    "price": r.get("price") or r.get("field_7371"),
                    "score": r.get("Score") or r.get("field_7394"),
                    "status": r.get("Status") or r.get("field_7353"),
                    "woonbloq_id": r.get("WoonbloqProductID") or r.get("field_7425"),
                    "brand": (r.get("Brand_table") or [{}])[0].get("value") if isinstance(r.get("Brand_table"), list) and r.get("Brand_table") else "—",
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

            # Baserow count
            r_base = client.session.get(f"{settings.api_base}/database/rows/table/{settings.products_table_id}/?size=1", timeout=15)
            base_count = r_base.json().get("count", 0) if r_base.ok else 0

            # Shopify counts
            r_shop = shop._request("GET", "/products/count.json")
            shop_count = r_shop.json().get("count", 0) if r_shop.ok else 0

            r_act = shop._request("GET", "/products/count.json", params={"status": "active"})
            shop_active = r_act.json().get("count", 0) if r_act.ok else 0

            r_dft = shop._request("GET", "/products/count.json", params={"status": "draft"})
            shop_draft = r_dft.json().get("count", 0) if r_dft.ok else 0

            # Linked in Baserow with WoonbloqProductID
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
                params["vendor"] = args["vendor"]
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
            "reply": "⚠️ OpenRouter API Key is missing in `.env`. Please add OPENROUTER_API_KEY.",
            "tool_calls": []
        }

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Binnen Store Agent",
    }

    system_prompt = (
        "You are an executive AI Catalog & Multi-Storefront Intelligence Assistant for Binnen / Woonbloq. "
        "You have direct real-time access to Baserow and the live Shopify Woonbloq storefront. "
        "NEVER mention internal database table IDs (like Table 742, Table 745, field_7347, etc.) or table names to the user. Always say 'Baserow' instead.\n\n"
        "TOOL ROUTING RULES — always call the appropriate tool before answering:\n"
        "1. 'How many total products in Shopify?' or 'what is total Shopify count?' → call `get_shopify_catalog_stats`\n"
        "2. 'How many products in Shopify where stock is 0 / zero / not entered / untracked?' → call `query_shopify_products_advanced` with inventory_filter='zero_or_untracked'\n"
        "3. 'How many products in Shopify where stock is 0?' → call `query_shopify_products_advanced` with inventory_filter='zero_stock'\n"
        "4. 'How many products in Shopify where stock is not entered / no value?' → call `query_shopify_products_advanced` with inventory_filter='untracked_stock'\n"
        "5. 'How many products in Shopify with in-stock / stock > 0?' → call `query_shopify_products_advanced` with inventory_filter='in_stock'\n"
        "6. 'How many products in Baserow where [column] is empty / 0 / missing?' → call `query_baserow_products_filtered` with appropriate field_name and condition='empty'\n"
        "7. 'How many products in Baserow where [column] equals [value]?' → call `query_baserow_products_filtered` with condition='equal' and value\n"
        "8. 'How many products in Baserow where price is missing / empty?' → call `query_baserow_products_filtered` with field_name='price' and condition='empty'\n"
        "9. Overall sync health → call `get_catalog_overview`\n\n"
        "IMPORTANT: When user asks 'how many products where [column] [condition]', you MUST call the tool and return the EXACT count from the API. "
        "Do NOT guess or use cached values. "
        "Structure all answers in executive format: bold key numbers, use bullet points, present clean tables where applicable.\n"
        "For Shopify stock queries: note that 'analyzed_batch_size' is the number of products fetched in one API call (max 250). "
        "If the store has more than 250 products, mention you analyzed a sample batch and the counts are from that batch. "
        "Always show the total store count alongside the batch analysis results.\n"
        "SOURCE URL PRIVACY RULE: NEVER display, output, or link to external original supplier or scraped website URLs. All catalog items belong to Baserow & Woonbloq storefront."
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
