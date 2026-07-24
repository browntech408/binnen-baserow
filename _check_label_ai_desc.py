"""List Label products in Baserow missing ai_description_translated_NL."""
from __future__ import annotations

from pathlib import Path

from baserow_client import BaserowClient
from config import load_settings
from product_baserow import find_brand_row_id


def main() -> None:
    settings = load_settings()
    client = BaserowClient(settings)
    brand_id = find_brand_row_id(client, settings, "Label")
    ai_field = settings.field_ai_description_nl
    desc_field = settings.field_product_description
    name_field = settings.field_product_name
    url_field = settings.field_product_url

    total = 0
    has_ai = 0
    missing: list[dict] = []

    for row in client.list_table_rows(settings.products_table_id):
        links = row.get(settings.field_brand_link) or []
        if not any(int(x.get("id") or 0) == brand_id for x in links):
            continue
        total += 1
        ai = (row.get(ai_field) or "").strip()
        desc = (row.get(desc_field) or "").strip()
        name = row.get(name_field) or ""
        url = row.get(url_field) or ""
        if ai:
            has_ai += 1
        else:
            missing.append(
                {
                    "id": row["id"],
                    "name": name,
                    "url": url,
                    "has_desc": bool(desc),
                }
            )

    out = Path("output/label_missing_ai_description.txt")
    lines = [
        f"Brand: Label",
        f"Table: productsDetails ({settings.products_table_id})",
        f"AI field: {ai_field}",
        f"Total: {total}",
        f"With AI description: {has_ai}",
        f"Missing AI description: {len(missing)}",
        "",
        "=== Missing ai_description_translated_NL ===",
    ]
    for r in sorted(missing, key=lambda x: str(x["name"]).lower()):
        flag = "" if r["has_desc"] else " [NO product_description]"
        lines.append(f"{r['id']}\t{r['name']}{flag}\t{r['url']}")

    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
