"""Open Shopify admin product pages from a classify report, with configurable delay.

Examples:
  python shopify_open_from_report.py --report output/shopify_after_june10_report.json --category lifestyle --delay 10
  python shopify_open_from_report.py --report output/shopify_after_june10_report.json --category lifestyle --delay 20
  python shopify_open_from_report.py --report output/shopify_after_june10_report.json --category white --delay 5 --limit 10
  python shopify_open_from_report.py --report output/shopify_after_june10_report.json --category lifestyle --delay 10 --skip 50
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

CATEGORIES = ("lifestyle", "white", "no_bg", "unknown", "no_images", "all")


def _load_rows(report: dict, category: str) -> list[dict]:
    if category == "all":
        rows = list(report.get("products") or [])
        if rows:
            return rows
        out: list[dict] = []
        seen: set[int] = set()
        for key in ("lifestyle", "white", "no_bg", "unknown", "no_images"):
            for row in report.get(key) or []:
                pid = int(row.get("id") or 0)
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                out.append(row)
        return out

    section = report.get(category)
    if isinstance(section, list) and section:
        return section

    return [
        p
        for p in (report.get("products") or [])
        if str(p.get("category") or "") == category
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open report products in browser with a delay between each."
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Classify report JSON path.",
    )
    parser.add_argument(
        "--category",
        default="lifestyle",
        choices=CATEGORIES,
        help="Which category to open (default: lifestyle).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=10,
        help="Seconds between opening products (default: 10). Use 10, 20, etc.",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Skip first N products (already reviewed).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max products to open (0 = all remaining).",
    )
    parser.add_argument(
        "--status",
        default="",
        help="Optional status filter: active, draft, archived (default: all).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print URLs only; do not open browser.",
    )
    parser.add_argument(
        "--shop",
        default="ibz6u3-ss",
        help="Shopify store slug for admin URLs (default: ibz6u3-ss).",
    )
    args = parser.parse_args()

    if not args.report.is_file():
        print(f"ERROR: report not found: {args.report}")
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = _load_rows(report, args.category)
    status = str(args.status or "").strip().lower()
    if status:
        rows = [r for r in rows if str(r.get("status") or "").lower() == status]

    if args.skip > 0:
        rows = rows[args.skip :]
    if args.limit > 0:
        rows = rows[: args.limit]

    if not rows:
        print("Nothing to open.")
        return 0

    print(f"Report: {args.report}")
    print(f"Category: {args.category}")
    if status:
        print(f"Status: {status}")
    print(f"Delay: {args.delay}s between products")
    print(f"Opening: {len(rows)} product(s) (Ctrl+C to stop)")
    print()

    for i, row in enumerate(rows, 1):
        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        url = f"https://admin.shopify.com/store/{args.shop}/products/{pid}"
        print(f"[{i}/{len(rows)}] {title} (id={pid})", flush=True)
        print(f"  {url}", flush=True)
        if not args.no_open:
            webbrowser.open(url, new=2)
        if i < len(rows) and args.delay > 0:
            time.sleep(args.delay)
        print()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
