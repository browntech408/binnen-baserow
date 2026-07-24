"""Build Woonbloq n8n workflow from Binnen Design template."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "output" / "Binnen Design Baserow → Shopify Sync.json"
DST = ROOT / "output" / "Woonbloq Baserow → Shopify Sync.json"

WOONBLOQ_STORE_ROW_ID = 1
SHOPIFY_SHOP = "ibz6u3-ss.myshopify.com"


def main() -> None:
    wf = json.loads(SRC.read_text(encoding="utf-8"))
    wf["name"] = "Woonbloq Baserow → Shopify Sync"
    wf["active"] = False

    for node in wf["nodes"]:
        node["id"] = str(uuid.uuid4())

    text = json.dumps(wf, ensure_ascii=False, indent=2)
    text = text.replace(
        "filter__stores__link_row_has=3&filter__BinnenProductID__empty",
        f"filter__stores__link_row_has={WOONBLOQ_STORE_ROW_ID}&filter__WoonbloqProductID__empty",
    )
    text = text.replace(
        "https://ddvwt8-0k.myshopify.com/admin/api/2025-10/products.json",
        f"https://{SHOPIFY_SHOP}/admin/api/2025-10/products.json",
    )
    text = text.replace('"name": "Binnen Design Shopify"', '"name": "Woonbloq Shopify"')
    text = text.replace("if (row.BinnenProductID) continue;", "if (row.WoonbloqProductID) continue;")
    text = text.replace(
        "BinnenProductID: 'gid://shopify/Product/' + $('Create Shopify Product').item.json.product.id, "
        "BinnenStatus: $('Collect Images For Shopify').item.json.shopify_status === 'active' ? 'Added' : 'Draft'",
        "WoonbloqProductID: 'gid://shopify/Product/' + $('Create Shopify Product').item.json.product.id, "
        "WoonbloqStatus: $('Collect Images For Shopify').item.json.shopify_status === 'active' ? 'Added' : 'Draft'",
    )

    DST.write_text(text, encoding="utf-8")
    print("Wrote", DST.name)


if __name__ == "__main__":
    main()
