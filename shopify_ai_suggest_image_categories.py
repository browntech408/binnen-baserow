"""
Suggest Hero / Lifestyle / Detail labels for latest Shopify product images
via OpenRouter vision (dry-run — does NOT change Shopify).

Recommended model (cheap + good vision):
  google/gemini-2.5-flash

Alternatives to A/B:
  google/gemini-2.0-flash-001   — cheaper/faster
  openai/gpt-4o                — higher quality, more expensive
  openai/gpt-4o-mini           — OK for text; vision often costly (avoid for 15k)

Usage:
  python shopify_ai_suggest_image_categories.py
  python shopify_ai_suggest_image_categories.py --limit 10 --max-images 6
  python shopify_ai_suggest_image_categories.py --model google/gemini-2.0-flash-001
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"
LABELS = ("hero", "lifestyle", "detail")

SYSTEM_PROMPT = """You classify ecommerce furniture / interior product photos.

Labels (pick exactly one):
- hero: clean packshot — full product visible, plain/studio/solid background, suitable as main PDP gallery image
- lifestyle: product in a room / ambient interior scene / styled setting
- detail: close-up of material, texture, stitching, edge, hardware, partial product zoom

Rules:
- If unsure between hero and lifestyle, prefer lifestyle when any room context is visible.
- If unsure between hero and detail, prefer detail when the crop is tight / texture-focused.
- Respond with JSON only: {"label":"hero"|"lifestyle"|"detail","confidence":0.0-1.0,"reason":"one short sentence"}
"""


def _parse_json_content(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # tolerate leading prose
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    label = str(data.get("label", "")).strip().lower()
    if label not in LABELS:
        raise ValueError(f"invalid label: {label!r}")
    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(data.get("reason", "")).strip()
    return {"label": label, "confidence": confidence, "reason": reason}


def classify_image_url(
    *,
    image_url: str,
    product_title: str,
    api_key: str,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://binnen.local",
            "X-Title": "Binnen image category suggest",
        },
        json={
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Product title: {product_title}\n"
                                "Classify this product image."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        },
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter {response.status_code}: {response.text[:400]}"
        )
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError(f"No choices: {payload}")
    content = choices[0].get("message", {}).get("content", "")
    parsed = _parse_json_content(content)
    usage = payload.get("usage") or {}
    parsed["usage"] = {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    return parsed


def fetch_latest_products(
    client: ShopifyClient, *, limit: int, status: str
) -> list[dict[str, Any]]:
    """Newest products by created_at (Shopify REST order)."""
    params: dict[str, Any] = {
        "limit": min(max(limit, 1), 250),
        "order": "created_at desc",
        "fields": "id,title,status,vendor,created_at,images,handle",
    }
    if status:
        params["status"] = status
    resp = client._request("GET", "/products.json", params=params)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"List products failed ({resp.status_code}): {resp.text[:500]}"
        )
    products = list(resp.json().get("products") or [])
    products.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    return products[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI-suggest hero/lifestyle/detail for latest Shopify product images"
    )
    parser.add_argument("--limit", type=int, default=10, help="Latest N products")
    parser.add_argument(
        "--max-images",
        type=int,
        default=8,
        help="Max images per product to analyze (cost control)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenRouter model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--status",
        default="",
        help="Shopify status filter: active|draft|archived (empty = all)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "shopify_ai_image_suggest_latest10.json",
    )
    args = parser.parse_args()

    settings = load_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        print("OPENROUTER_API_KEY is not set in .env", file=sys.stderr)
        return 1

    client = ShopifyClient(load_shopify_config())
    products = fetch_latest_products(
        client, limit=args.limit, status=args.status.strip()
    )
    if not products:
        print("No Shopify products found.")
        return 1

    print(f"Model: {args.model}")
    print(f"Products: {len(products)} (latest by created_at)")
    print("Mode: suggest only — Shopify NOT modified\n")

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "limit": args.limit,
        "max_images": args.max_images,
        "products": [],
        "totals": {"images": 0, "hero": 0, "lifestyle": 0, "detail": 0, "errors": 0},
    }

    timeout = max(settings.http_timeout, 90)

    for product in products:
        pid = int(product["id"])
        title = str(product.get("title") or "")
        images = list(product.get("images") or [])[: max(args.max_images, 0)]
        print(f"=== {title} (id {pid}, {product.get('created_at')}) ===")
        print(f"    images to analyze: {len(images)}")

        entry: dict[str, Any] = {
            "product_id": pid,
            "title": title,
            "vendor": product.get("vendor"),
            "status": product.get("status"),
            "created_at": product.get("created_at"),
            "handle": product.get("handle"),
            "images": [],
        }

        for img in images:
            src = str(img.get("src") or "").strip()
            position = img.get("position")
            image_id = img.get("id")
            if not src:
                continue
            report["totals"]["images"] += 1
            try:
                result = classify_image_url(
                    image_url=src,
                    product_title=title,
                    api_key=api_key,
                    model=args.model,
                    timeout=timeout,
                )
                label = result["label"]
                report["totals"][label] = report["totals"].get(label, 0) + 1
                row = {
                    "image_id": image_id,
                    "position": position,
                    "src": src,
                    "label": label,
                    "confidence": result["confidence"],
                    "reason": result["reason"],
                    "usage": result.get("usage"),
                    "error": "",
                }
                print(
                    f"  [{position}] {label:10s} "
                    f"conf={result['confidence']:.2f} — {result['reason'][:80]}"
                )
            except Exception as exc:
                report["totals"]["errors"] += 1
                row = {
                    "image_id": image_id,
                    "position": position,
                    "src": src,
                    "label": "",
                    "confidence": 0,
                    "reason": "",
                    "usage": None,
                    "error": str(exc)[:400],
                }
                print(f"  [{position}] ERROR: {exc}")
            entry["images"].append(row)

        report["products"].append(entry)
        print()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    t = report["totals"]
    print("---- Summary ----")
    print(
        f"images={t['images']} hero={t['hero']} lifestyle={t['lifestyle']} "
        f"detail={t['detail']} errors={t['errors']}"
    )
    print(f"Report: {args.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
