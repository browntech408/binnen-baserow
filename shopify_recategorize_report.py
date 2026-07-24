"""Re-bucket products in a Shopify categories report JSON.

Rules:
  - lifestyle: ONLY lifestyle images (no white / no_bg / unknown)
  - mixed products: white vs no_bg — whichever count is higher wins
  - tie on white vs no_bg: white

Examples:
  python shopify_recategorize_report.py
  python shopify_recategorize_report.py --report output/shopify_all_products_report.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from shopify_image_classify import product_category

DEFAULT_REPORT = Path("output") / "shopify_image_categories_report.json"
SECTIONS = ("lifestyle", "white", "no_bg", "unknown", "no_images")


def _counts_for_row(row: dict) -> tuple[int, int, int, int]:
    ls = len(row.get("lifestyle_urls") or [])
    wh = len(row.get("white_urls") or [])
    nb = len(row.get("no_bg_urls") or [])
    un = len(row.get("unknown_urls") or [])
    return ls, wh, nb, un


def _status_counts(rows: list[dict]) -> dict[str, int]:
    return dict(Counter(str(r.get("status") or "unknown") for r in rows))


def _collect_rows(report: dict) -> dict[int, dict]:
    all_rows: dict[int, dict] = {}
    for key in SECTIONS:
        for row in report.get(key) or []:
            all_rows[int(row["id"])] = row
    # Canonical copy: products[] wins over stale category sections
    if report.get("products"):
        for row in report["products"]:
            all_rows[int(row["id"])] = row
    return all_rows


def recategorize_report(report: dict) -> tuple[dict, list[dict]]:
    all_rows = _collect_rows(report)

    buckets: dict[str, list[dict]] = {k: [] for k in SECTIONS}
    moved: list[dict] = []

    for row in all_rows.values():
        ls, wh, nb, un = _counts_for_row(row)
        image_count = ls + wh + nb + un
        old_cat = row.get("category", "?")
        new_cat = product_category(
            lifestyle=ls,
            white=wh,
            no_bg=nb,
            unknown=un,
            image_count=image_count,
        )
        row["category"] = new_cat
        buckets[new_cat].append(row)
        if old_cat != new_cat:
            moved.append(
                {
                    "id": row["id"],
                    "title": row.get("title"),
                    "status": row.get("status"),
                    "from": old_cat,
                    "to": new_cat,
                    "lifestyle": ls,
                    "white": wh,
                    "no_bg": nb,
                }
            )

    for key in SECTIONS:
        buckets[key].sort(key=lambda r: str(r.get("title") or "").lower())

    all_sorted = sorted(all_rows.values(), key=lambda r: str(r.get("title") or "").lower())

    report["counts"] = {k: len(buckets[k]) for k in SECTIONS}
    for key in SECTIONS:
        report[key] = buckets[key]
    report["products"] = all_sorted
    if "status_by_category" in report:
        report["status_by_category"] = {
            cat: _status_counts(rows) for cat, rows in buckets.items()
        }
    if "status_counts" in report:
        report["status_counts"] = _status_counts(all_sorted)

    report["recategorize_rules"] = (
        "lifestyle = only lifestyle images; "
        "mixed -> white if white > no_bg else no_bg (tie -> white)"
    )
    report["recategorize_moved"] = len(moved)
    return report, moved


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-bucket report by new category rules.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--moves-out",
        type=Path,
        default=None,
        help="Moves log path (default: output/recategorize_moves.json or *_moves.json).",
    )
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    old_counts = dict(report.get("counts") or {})

    report, moved = recategorize_report(report)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    moves_out = args.moves_out or args.report.with_name(
        args.report.stem.replace("_report", "") + "_recategorize_moves.json"
    )
    if moves_out.name == args.report.name:
        moves_out = Path("output") / "recategorize_moves.json"
    moves_out.parent.mkdir(parents=True, exist_ok=True)
    moves_out.write_text(
        json.dumps(moved, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    pure_out = args.report.with_name("products_only_lifestyle_images.json")
    if "all_products" in args.report.stem:
        pure_out = args.report.parent / "shopify_all_only_lifestyle.json"
    pure_out.write_text(
        json.dumps(
            {
                "count": report["counts"]["lifestyle"],
                "products": [
                    {
                        "id": r["id"],
                        "title": r.get("title"),
                        "status": r.get("status"),
                        "lifestyle_count": len(r.get("lifestyle_urls") or []),
                    }
                    for r in report["lifestyle"]
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"Updated: {args.report}")
    print(f"Moves log: {moves_out} ({len(moved)} products changed category)")
    print(f"Pure lifestyle list: {pure_out}")
    print()
    print("Before -> After:")
    for k in SECTIONS:
        print(f"  {k}: {old_counts.get(k, 0)} -> {report['counts'][k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
