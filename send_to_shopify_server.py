"""Webhook server for Baserow 'SendToShopify' / 'Send To Shop' buttons.

Listens on port 5678 (matching the existing Baserow formula URLs).
When a button is clicked in Baserow, the browser opens the webhook URL,
this server handles the request, syncs the product to Shopify, and
returns a result page.

Start:
  python send_to_shopify_server.py
  python send_to_shopify_server.py --port 5678
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import threading
import time
import traceback
from typing import Any

import requests
from flask import Flask, request, jsonify
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config

# Import the core logic from send_to_shopify
from send_to_shopify import (
    TABLE_MAP,
    TableFieldMap,
    METAFIELD_NS,
    _extract_product_data,
    _build_shopify_images,
    _build_metafields,
    _extract_shopify_numeric_id,
    _pick_download_url,
    _unique_file_images,
    _image_file_urls,
)

app = Flask(__name__)

# Globals initialized at startup
_baserow: BaserowClient | None = None
_shopify: ShopifyClient | None = None
_settings: Any = None
_session: requests.Session | None = None


def _init_clients() -> None:
    global _baserow, _shopify, _settings, _session
    _settings = load_settings()
    _baserow = BaserowClient(_settings)
    _shopify = ShopifyClient(load_shopify_config())
    _session = requests.Session()
    _session.headers.update({"Authorization": f"Token {_settings.baserow_token}"})


def _find_row(record_id: int) -> tuple[dict, TableFieldMap] | None:
    """Try to find a row in table 742 first, then 802."""
    for table_id in [742, 802]:
        try:
            row = _baserow.get_row(table_id, record_id)
            if row:
                return row, TABLE_MAP[table_id]
        except Exception:
            continue
    return None


def _html_response(title: str, message: str, status: str = "success", details: str = "") -> str:
    """Generate a styled HTML response page."""
    colors = {
        "success": ("#10b981", "#064e3b", "#d1fae5"),
        "error": ("#ef4444", "#7f1d1d", "#fee2e2"),
        "info": ("#3b82f6", "#1e3a5f", "#dbeafe"),
        "processing": ("#f59e0b", "#78350f", "#fef3c7"),
    }
    accent, dark, light = colors.get(status, colors["info"])
    icon = {"success": "✅", "error": "❌", "info": "ℹ️", "processing": "⏳"}.get(status, "📦")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 40px;
      max-width: 600px;
      width: 100%;
      box-shadow: 0 25px 50px rgba(0,0,0,0.4);
    }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    .title {{
      font-size: 24px;
      font-weight: 700;
      color: #f1f5f9;
      margin-bottom: 12px;
    }}
    .message {{
      font-size: 16px;
      color: #94a3b8;
      line-height: 1.6;
      margin-bottom: 20px;
    }}
    .badge {{
      display: inline-block;
      background: {light};
      color: {dark};
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 20px;
    }}
    .details {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 16px;
      font-family: 'Cascadia Code', 'Fira Code', monospace;
      font-size: 13px;
      color: #94a3b8;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 300px;
      overflow-y: auto;
    }}
    .close-hint {{
      margin-top: 24px;
      text-align: center;
      color: #475569;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <div class="badge">{status.upper()}</div>
    <div class="title">{title}</div>
    <div class="message">{message}</div>
    {"<div class='details'>" + details + "</div>" if details else ""}
    <div class="close-hint">You can close this tab now.</div>
  </div>
</body>
</html>"""


def _sync_product(record_id: int, table_id: int | None = None) -> tuple[str, int]:
    """Run the sync logic for a single product row. Returns (html, http_status)."""
    if _baserow is None:
        _init_clients()

    # Find the row
    row = None
    fm = None
    if table_id and table_id in TABLE_MAP:
        try:
            row = _baserow.get_row(table_id, record_id)
            fm = TABLE_MAP[table_id]
        except Exception:
            pass

    if row is None:
        # Try both tables
        result = _find_row(record_id)
        if result is None:
            return _html_response(
                "Row Not Found",
                f"Could not find row {record_id} in table 742 or 802.",
                status="error",
            ), 404
        row, fm = result

    # Extract product data
    data = _extract_product_data(row, fm)
    details_lines = []

    try:
        if data["woonbloq_product_id"]:
            # ============ UPDATE FLOW ============
            gid = data["woonbloq_product_id"]
            pid = _extract_shopify_numeric_id(gid)
            details_lines.append(f"Mode: UPDATE (existing product)")
            details_lines.append(f"Shopify ID: {pid}")
            details_lines.append(f"Table: {fm.table_id}, Row: {record_id}")

            # Update product fields (no images)
            update_fields: dict[str, Any] = {
                "title": data["title"],
                "body_html": data["description"],
                "vendor": data["brand"],
                "product_type": data["product_type"],
                "tags": "" if not data["missing"] else ", ".join(data["missing"]),
                "status": data["shopify_status"],
            }
            price = data["price"]
            if price and price != "0.00":
                update_fields["variants"] = [{"price": price}]

            _shopify.update_product(pid, update_fields)
            details_lines.append(f"Title: {data['title']}")
            details_lines.append(f"Vendor: {data['brand']}")
            details_lines.append(f"Type: {data['product_type']}")
            details_lines.append(f"Price: {price}")
            details_lines.append(f"Description: {'set' if data['description'] else 'empty'}")
            details_lines.append(f"Status: {data['shopify_status']}")

            # Update metafields
            metafields = _build_metafields(data)
            if metafields:
                mf_ok, mf_fail, mf_errors = _shopify.set_metafields_graphql(gid, metafields)
                details_lines.append(f"Metafields: {mf_ok} saved, {mf_fail} failed")
                for err in mf_errors:
                    details_lines.append(f"  Error: {err}")
            else:
                details_lines.append("Metafields: none to update")

            # Lifestyle images
            if data["lifestyle_urls"]:
                try:
                    file_gids = _shopify.create_image_files_from_urls(data["lifestyle_urls"])
                    if file_gids:
                        _shopify.set_product_list_file_reference_metafield(
                            pid, METAFIELD_NS, "lifestyle_images", file_gids,
                        )
                        details_lines.append(f"Lifestyle images: {len(file_gids)} uploaded")
                except RuntimeError as exc:
                    details_lines.append(f"Lifestyle images error: {exc}")

            details_lines.append(f"\nImages: NOT UPDATED (keeping existing)")

            # Update Baserow Status
            _baserow.update_row(
                fm.table_id,
                int(row["id"]),
                {
                    fm.woonbloq_status: "Added" if data["shopify_status"] == "active" else "Draft",
                },
            )

            return _html_response(
                f"Product Updated: {data['title']}",
                f"Successfully updated on Shopify (ID: {pid}).",
                status="success",
                details="\n".join(details_lines),
            ), 200

        else:
            # ============ CREATE FLOW ============
            details_lines.append(f"Mode: CREATE (new product)")
            details_lines.append(f"Table: {fm.table_id}, Row: {record_id}")

            # Download and encode images
            images, skipped = _build_shopify_images(
                data["image_source"], _session, timeout=120.0
            )
            details_lines.append(f"Images: {len(images)} uploaded, {len(skipped)} skipped")

            # Build product payload
            payload: dict[str, Any] = {
                "title": data["title"],
                "body_html": data["description"],
                "vendor": data["brand"],
                "product_type": data["product_type"],
                "tags": "" if not data["missing"] else ", ".join(data["missing"]),
                "status": data["shopify_status"],
                "variants": [{"price": data["price"]}],
            }
            if images:
                payload["images"] = images

            # Create on Shopify
            created = _shopify.create_product(payload)
            pid = int(created["id"])
            gid = f"gid://shopify/Product/{pid}"
            details_lines.append(f"Shopify ID: {pid}")
            details_lines.append(f"Status: {data['shopify_status']}")
            details_lines.append(f"Title: {data['title']}")
            details_lines.append(f"Vendor: {data['brand']}")
            details_lines.append(f"Type: {data['product_type']}")

            # Set metafields
            metafields = _build_metafields(data)
            if metafields:
                mf_ok, mf_fail, mf_errors = _shopify.set_metafields_graphql(gid, metafields)
                details_lines.append(f"Metafields: {mf_ok} saved, {mf_fail} failed")
                for err in mf_errors:
                    details_lines.append(f"  Error: {err}")

            # Lifestyle images
            if data["lifestyle_urls"]:
                try:
                    file_gids = _shopify.create_image_files_from_urls(data["lifestyle_urls"])
                    if file_gids:
                        _shopify.set_product_list_file_reference_metafield(
                            pid, METAFIELD_NS, "lifestyle_images", file_gids,
                        )
                        details_lines.append(f"Lifestyle images: {len(file_gids)} uploaded")
                except RuntimeError as exc:
                    details_lines.append(f"Lifestyle images error: {exc}")

            # Update Baserow
            _baserow.update_row(
                fm.table_id,
                int(row["id"]),
                {
                    fm.woonbloq_product_id: gid,
                    fm.woonbloq_status: "Added" if data["shopify_status"] == "active" else "Draft",
                },
            )
            details_lines.append(f"Baserow updated: {fm.woonbloq_product_id} = {gid}")

            return _html_response(
                f"Product Created: {data['title']}",
                f"Successfully created on Shopify (ID: {pid}, {data['shopify_status']}).",
                status="success",
                details="\n".join(details_lines),
            ), 200

    except Exception as exc:
        details_lines.append(f"\nError: {exc}")
        details_lines.append(traceback.format_exc())
        return _html_response(
            f"Sync Failed: {data.get('title', 'Unknown')}",
            f"An error occurred while syncing to Shopify.",
            status="error",
            details="\n".join(details_lines),
        ), 500


# ---------------------------------------------------------------------------
# Routes — match the existing Baserow formula URLs
# ---------------------------------------------------------------------------

# "Send To Shop" button — has record_id in query params
# URL pattern: /webhook-test/<uuid>?database_id=77&table_id=329&record_id=XXX
@app.route("/webhook-test/<path:webhook_id>", methods=["GET", "POST"])
def handle_webhook_test(webhook_id: str):
    record_id = request.args.get("record_id")
    table_id = request.args.get("table_id")

    if not record_id:
        return _html_response(
            "Missing Record ID",
            "No record_id found in the URL. Make sure the Baserow button formula includes record_id.",
            status="error",
            details=f"URL: {request.url}\nWebhook ID: {webhook_id}",
        ), 400

    try:
        record_id_int = int(record_id)
    except ValueError:
        return _html_response("Invalid Record ID", f"record_id '{record_id}' is not a number.", status="error"), 400

    # table_id in the URL might be stale (329), try to detect real table
    real_table_id = None
    if table_id:
        try:
            tid = int(table_id)
            if tid in TABLE_MAP:
                real_table_id = tid
        except ValueError:
            pass

    html, status_code = _sync_product(record_id_int, real_table_id)
    return html, status_code


# Clean and professional endpoint
# URL pattern: /sync?row_id=XXX
@app.route("/sync", methods=["GET", "POST"])
def handle_sync_clean():
    row_id = request.args.get("row_id") or request.args.get("record_id")

    if not row_id:
        return _html_response(
            "Missing Row ID",
            "Provide row_id as a query parameter (e.g., ?row_id=123).",
            status="error",
        ), 400

    try:
        row_id_int = int(row_id)
    except ValueError:
        return _html_response("Invalid Row ID", f"row_id '{row_id}' is not a number.", status="error"), 400

    html, status_code = _sync_product(row_id_int, None)
    return html, status_code


# Direct endpoint — for manual/future use
# URL pattern: /webhook/send-to-shopify?table_id=742&row_id=XXX
@app.route("/webhook/send-to-shopify", methods=["GET", "POST"])
def handle_direct_webhook():
    row_id = request.args.get("row_id") or request.args.get("record_id")
    table_id = request.args.get("table_id")

    if not row_id:
        return _html_response(
            "Missing Row ID",
            "Provide row_id (or record_id) as a query parameter.",
            status="error",
        ), 400

    try:
        row_id_int = int(row_id)
    except ValueError:
        return _html_response("Invalid Row ID", f"row_id '{row_id}' is not a number.", status="error"), 400

    real_table_id = None
    if table_id:
        try:
            tid = int(table_id)
            if tid in TABLE_MAP:
                real_table_id = tid
        except ValueError:
            pass

    html, status_code = _sync_product(row_id_int, real_table_id)
    return html, status_code


# Health check
@app.route("/", methods=["GET"])
def health():
    return _html_response(
        "Send to Shopify Server",
        "Webhook server is running. Click a 'SendToShopify' button in Baserow to sync a product.",
        status="info",
        details=(
            "Endpoints:\n"
            "  /sync?row_id=XXX  (Clean & Professional URL)\n"
            "  /webhook-test/<id>?record_id=XXX  (Legacy fallback)\n"
            "\n"
            "Tables supported: 742, 802\n"
            "Target store: Woonbloq"
        ),
    ), 200


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Webhook server for Baserow SendToShopify buttons")
    parser.add_argument("--port", type=int, default=5678, help="Port to listen on (default: 5678)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    print("=" * 60)
    print("  Send to Shopify — Webhook Server")
    print("=" * 60)
    print(f"  Host: {args.host}")
    print(f"  Port: {args.port}")
    print(f"  URL:  http://localhost:{args.port}/")
    print()
    print("  Professional Baserow Formula:")
    print(f"    button(concat('http://localhost:{args.port}/sync?row_id=', totext(row_id())), 'Send to Shopify')")
    print("=" * 60)

    # Pre-initialize clients
    try:
        _init_clients()
        print(f"\n  Baserow: {_settings.baserow_url}")
        print(f"  Shopify: {_shopify.config.shop_host}")
        print(f"\n  Server ready! Waiting for button clicks...\n")
    except Exception as exc:
        print(f"\n  WARNING: Client init failed: {exc}")
        print("  Clients will be initialized on first request.\n")

    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
