"""Quick scrape quality report for output/products_*.json."""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "output/products_Gealux.json")
data = json.loads(path.read_text(encoding="utf-8"))
ok = [p for p in data if p.get("scrape_ok", True)]
print(f"=== Quality: {path.name} ===")
print(f"total={len(data)} ok={len(ok)}")
print(f"missing name: {sum(1 for p in ok if not (p.get('product_name') or '').strip())}")
print(f"missing images: {sum(1 for p in ok if not p.get('product_images'))}")
print(f"short description (<30): {sum(1 for p in ok if len(p.get('product_description') or '') < 30)}")
print(f"missing category: {sum(1 for p in ok if not (p.get('product_category') or '').strip())}")
for p in data[:8]:
    print(
        f"- {p.get('product_name')}: imgs={len(p.get('product_images', []))}, "
        f"cat={p.get('product_category')}/{p.get('sub_category')}, "
        f"desc={len(p.get('product_description') or '')}"
    )
