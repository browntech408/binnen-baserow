"""Build client demo: 4 lifestyle metafield images → Claid expand → before/after folders.

Skips the newest 600 Shopify products (by created_at), then picks 4 different
older products that have custom.lifestyle_images metafield URLs.

Usage:
  python claid_demo_lifestyle_before_after.py
  python claid_demo_lifestyle_before_after.py --skip-latest 600 --count 4
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from claid_client import ClaidClient, load_claid_settings
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (text or "").strip())[:50].strip("_") or "product"


def _download(url: str, dest: Path, timeout: float = 120) -> None:
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        for chunk in resp.iter_content(65536):
            if chunk:
                handle.write(chunk)


def _parse_next_link(link_header: str) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">")
        if start >= 0 and end > start:
            return part[start + 1 : end]
    return None


def iter_products_newest_first(
    client: ShopifyClient, *, status: str = "active"
) -> list[dict[str, Any]]:
    """All products newest-first (created_at desc)."""
    products: list[dict[str, Any]] = []
    params: dict[str, Any] = {
        "limit": 250,
        "order": "created_at desc",
        "fields": "id,title,status,created_at,handle,vendor",
    }
    if status:
        params["status"] = status

    next_url: str | None = None
    while True:
        if next_url:
            resp = client._get_with_retry(next_url)
        else:
            resp = client._request("GET", "/products.json", params=params)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"List products failed ({resp.status_code}): {resp.text[:500]}"
            )
        batch = list(resp.json().get("products") or [])
        products.extend(batch)
        next_url = _parse_next_link(resp.headers.get("Link", ""))
        print(f"  fetched {len(products)} products...", flush=True)
        if not next_url or not batch:
            break
    return products


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-latest", type=int, default=600)
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument(
        "--scan-pool",
        type=int,
        default=800,
        help="How many products after skip to scan for lifestyle metafields",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("output") / "claid_client_demo_lifestyle",
    )
    parser.add_argument("--status", default="active")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    settings = load_settings()
    ns = settings.shopify_metafield_namespace or "custom"
    key = settings.shopify_metafield_lifestyle_images or "lifestyle_images"

    shopify = ShopifyClient(load_shopify_config())
    print(f"Metafield: {ns}.{key}")
    print(f"Loading Shopify products (newest first), skip first {args.skip_latest}...")
    all_products = iter_products_newest_first(shopify, status=args.status)
    print(f"Total products loaded: {len(all_products)}")

    if len(all_products) <= args.skip_latest:
        print(
            f"Not enough products ({len(all_products)}) to skip {args.skip_latest}",
            file=sys.stderr,
        )
        return 1

    candidates = all_products[args.skip_latest : args.skip_latest + args.scan_pool]
    print(
        f"Scanning pool of {len(candidates)} products "
        f"(indexes {args.skip_latest + 1}..{args.skip_latest + len(candidates)}) "
        f"for {ns}.{key}..."
    )

    with_lifestyle: list[dict[str, Any]] = []
    for i, product in enumerate(candidates, 1):
        pid = int(product["id"])
        title = str(product.get("title") or "")
        try:
            mf = shopify.get_product_list_file_reference_metafield(pid, ns, key)
        except Exception as exc:
            print(f"  skip {pid} metafield error: {exc}")
            continue
        urls = [u for u in (mf.get("urls") or []) if str(u).startswith("http")]
        if not urls:
            continue
        with_lifestyle.append(
            {
                "product": product,
                "product_id": pid,
                "title": title,
                "lifestyle_url": urls[0],
                "lifestyle_count": len(urls),
            }
        )
        print(
            f"  [{i}/{len(candidates)}] FOUND {title!r} "
            f"({len(urls)} lifestyle) id={pid}"
        )
        # Enough pool to randomize from
        if len(with_lifestyle) >= max(args.count * 4, 12):
            print(f"  collected {len(with_lifestyle)} candidates — stopping scan early")
            break
        time.sleep(0.15)

    if len(with_lifestyle) < args.count:
        print(
            f"Only found {len(with_lifestyle)} products with lifestyle metafield "
            f"(need {args.count}). Try larger --scan-pool.",
            file=sys.stderr,
        )
        if not with_lifestyle:
            return 1
        picked = with_lifestyle
    else:
        picked = random.sample(with_lifestyle, args.count)

    print(f"\nPicked {len(picked)} products for Claid demo:")
    for row in picked:
        print(f"  - {row['title']} ({row['product_id']})")

    try:
        claid_settings = load_claid_settings()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    claid = ClaidClient(claid_settings)

    if args.outdir.exists():
        # keep old demos; use timestamped subfolder if non-empty
        existing = list(args.outdir.iterdir())
        if existing:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            args.outdir = args.outdir.parent / f"{args.outdir.name}_{stamp}"

    args.outdir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skip_latest": args.skip_latest,
        "metafield": f"{ns}.{key}",
        "items": [],
        "ok": 0,
        "failed": 0,
    }

    for idx, row in enumerate(picked, 1):
        title = row["title"]
        pid = row["product_id"]
        src = row["lifestyle_url"]
        folder = args.outdir / f"{idx:02d}_{_safe_name(title)}"
        folder.mkdir(parents=True, exist_ok=True)
        before_path = folder / "01_before.jpg"
        after_path = folder / "02_after_claid.jpg"
        meta_path = folder / "info.json"

        print(f"\n[{idx}/{len(picked)}] {title}")
        print(f"  before URL: {src[:100]}")
        item: dict[str, Any] = {
            "index": idx,
            "product_id": pid,
            "title": title,
            "created_at": row["product"].get("created_at"),
            "handle": row["product"].get("handle"),
            "vendor": row["product"].get("vendor"),
            "source_url": src,
            "folder": str(folder),
            "before": str(before_path),
            "after": str(after_path),
            "claid_output_url": "",
            "error": "",
        }
        try:
            _download(src, before_path, timeout=claid_settings.timeout)
            result = claid.expand_lifestyle_url(src)
            item["claid_output_url"] = result.output_url
            _download(result.output_url, after_path, timeout=claid_settings.timeout)
            meta_path.write_text(
                json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            report["ok"] += 1
            print(f"  saved: {folder}")
        except Exception as exc:
            item["error"] = str(exc)[:500]
            report["failed"] += 1
            meta_path.write_text(
                json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"  ERROR: {exc}", file=sys.stderr)

        report["items"].append(item)

    report_path = args.outdir / "_demo_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDone: ok={report['ok']} failed={report['failed']}")
    print(f"Demo folder: {args.outdir.resolve()}")
    print(f"Report: {report_path.resolve()}")
    return 0 if report["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
