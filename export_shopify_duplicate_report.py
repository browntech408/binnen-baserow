"""Export Baserow->Shopify skip-duplicate report with admin links."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from baserow_client import BaserowClient
from baserow_shopify_sync import STORE_TARGETS, _iter_candidate_rows, _normalize_title
from config import load_settings
from shopify_client import ShopifyClient, load_shopify_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand-id", type=int, required=True)
    parser.add_argument("--brand-name", default="")
    parser.add_argument("--target", choices=tuple(STORE_TARGETS), default="woonbloq")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "shopify_duplicate_skip_report.json",
    )
    args = parser.parse_args()

    settings = load_settings()
    target = STORE_TARGETS[args.target]
    baserow = BaserowClient(settings)
    shopify = ShopifyClient(load_shopify_config())
    cfg = shopify.config

    existing: dict[str, list[dict]] = {}
    for p in shopify.iter_products(status="", fields="id,title,status,handle"):
        t = _normalize_title(p.get("title"))
        if not t:
            continue
        pid = int(p["id"])
        existing.setdefault(t, []).append(
            {
                "id": pid,
                "title": str(p.get("title") or ""),
                "status": str(p.get("status") or ""),
                "handle": str(p.get("handle") or ""),
                "admin_url": cfg.admin_product_url(pid),
            }
        )

    candidates = _iter_candidate_rows(
        baserow,
        settings,
        target,
        brand_id=args.brand_id,
        store_id=None,
        skip_synced=True,
    )

    duplicates: list[dict] = []
    batch_dups: list[dict] = []
    will_push: list[dict] = []
    queued: dict[str, int] = {}

    for row in candidates:
        title = str(row.get(settings.field_product_name) or "").strip()
        tnorm = _normalize_title(title)
        woonbloq_id = str(row.get(target.product_id_field) or "").strip()
        if not tnorm:
            continue

        if tnorm in existing:
            matches = existing[tnorm]
            duplicates.append(
                {
                    "baserow_row_id": int(row["id"]),
                    "baserow_title": title,
                    "normalized_title": tnorm,
                    "skip_reason": "title_match_existing_shopify",
                    "reason_detail": (
                        "Baserow product_name ko normalize karke "
                        f"('{tnorm}') Shopify ke existing product title se match mila. "
                        "Script same title se naya product create nahi karti. "
                        f"Matches: {len(matches)}."
                    ),
                    "woonbloq_product_id_in_baserow": woonbloq_id or None,
                    "shopify_matches": matches,
                    "shopify_admin_urls": [m["admin_url"] for m in matches],
                }
            )
            continue

        if tnorm in queued:
            first = queued[tnorm]
            batch_dups.append(
                {
                    "baserow_row_id": int(row["id"]),
                    "baserow_title": title,
                    "normalized_title": tnorm,
                    "skip_reason": "duplicate_title_in_baserow_batch",
                    "reason_detail": (
                        "Isi brand ke Baserow push batch mein pehle se same "
                        f"normalized title hai (pehli row {first}). "
                        "Sirf pehli row push hoti hai."
                    ),
                    "first_baserow_row_id": first,
                    "shopify_matches": [],
                    "shopify_admin_urls": [],
                }
            )
            continue

        queued[tnorm] = int(row["id"])
        will_push.append(
            {
                "baserow_row_id": int(row["id"]),
                "baserow_title": title,
                "normalized_title": tnorm,
            }
        )

    report = {
        "brand_id": args.brand_id,
        "brand_name": args.brand_name or None,
        "store": cfg.shop_host,
        "store_slug": cfg.store_slug,
        "duplicate_check_logic": {
            "method": "normalized_title_match",
            "normalize": "strip + lowercase + collapse whitespace (\\s+ -> single space)",
            "compare_against": "ALL Shopify products (any status)",
            "also_skips": [
                "rows with WoonbloqProductID already set (skip_synced=True) — not in candidate list",
                "same normalized title appearing twice in current Baserow push batch",
            ],
            "does_not_compare": "SKU, URL, images, or Baserow row id — ONLY product title",
        },
        "summary": {
            "baserow_candidates_unsynced": len(candidates),
            "skipped_shopify_title_duplicate": len(duplicates),
            "skipped_baserow_batch_duplicate": len(batch_dups),
            "will_push": len(will_push),
        },
        "skipped_shopify_duplicates": duplicates,
        "skipped_baserow_batch_duplicates": batch_dups,
        "will_push": will_push,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {args.report}")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
