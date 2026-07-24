"""Generate and save ai_description_translated_NL for specific Baserow product rows."""
from __future__ import annotations

import argparse
import sys

from baserow_client import BaserowClient
from config import load_settings
from description_ai import enhance_product_description_nl


def _link_names(value) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        parts = [str(v.get("value") or v.get("name") or "").strip() for v in value if v]
        return ", ".join(p for p in parts if p)
    return str(value).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("row_ids", nargs="+", type=int, help="Baserow product row IDs")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set in .env", file=sys.stderr)
        return 1

    client = BaserowClient(settings)
    ai_field = settings.field_ai_description_nl

    for row_id in args.row_ids:
        row = client.get_row(settings.products_table_id, row_id)
        name = str(row.get(settings.field_product_name) or "").strip()
        raw = str(row.get(settings.field_product_description) or "").strip()
        if not raw:
            print(f"SKIP row {row_id} {name}: no product_description")
            continue

        brands = _link_names(row.get(settings.field_brand_link))
        categories = _link_names(row.get(settings.field_product_category))
        designer = str(row.get(settings.field_designer) or "").strip()

        print(f"AI generate: row {row_id} — {name}...")
        try:
            enhanced = enhance_product_description_nl(
                product_name=name,
                raw_description=raw,
                designer=designer,
                category=categories,
                brand=brands or "Label",
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
                timeout=max(settings.http_timeout, 60),
            )
        except Exception as exc:
            print(f"  FAILED row {row_id}: {exc}", file=sys.stderr)
            continue

        ai_text = enhanced.product_description
        client.update_row(
            settings.products_table_id,
            row_id,
            {ai_field: ai_text},
        )
        print(f"  SAVED row {row_id} ({len(ai_text)} chars)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
