"""Diagnose products skipped with 'no matching images on live Shopify'."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from shopify_client import ShopifyClient, load_shopify_config
from shopify_process_white_nobg_from_report import (
    REPORT_SECTIONS,
    _map_shopify_images,
    _url_key,
)

DEFAULT_REPORT = Path("output") / "shopify_before_june10_report.json"
DEFAULT_OUT_JSON = Path("output") / "pixelbin_skipped_no_match.json"
DEFAULT_OUT_TXT = Path("output") / "pixelbin_skipped_no_match.txt"

# From current terminal session (parse or pass --ids)
SESSION_SKIPPED = [
    10376464597339,
    10376581775707,
    10376532132187,
    10376337523035,
    10376359805275,
    10376331919707,
    10376629813595,
    10376730739035,
    10376587641179,
    10376551498075,
    10376683848027,
    10376633024859,
]


def find_row(report: dict, pid: int) -> dict | None:
    for key in REPORT_SECTIONS:
        for row in report.get(key) or []:
            if int(row.get("id") or 0) == pid:
                return row
    return None


def diagnose_product(client: ShopifyClient, row: dict) -> dict:
    pid = int(row["id"])
    white_urls = list(row.get("white_urls") or [])
    product = client.get_product(pid)
    live_images = list(product.get("images") or [])
    img_map = _map_shopify_images(product)

    matched = []
    missing = []
    for url in white_urls:
        key = _url_key(url)
        img = img_map.get(key)
        if img:
            matched.append(
                {
                    "report_url": url,
                    "shopify_image_id": int(img["id"]),
                    "position": int(img.get("position") or 0),
                    "live_src": str(img.get("src") or ""),
                }
            )
        else:
            missing.append(url)

    live_list = [
        {
            "shopify_image_id": int(img["id"]),
            "position": int(img.get("position") or 0),
            "src": str(img.get("src") or ""),
            "path_key": _url_key(str(img.get("src") or "")),
        }
        for img in live_images
    ]

    if not white_urls:
        reason = "report has no white_urls"
    elif not live_images:
        reason = "product has zero images on Shopify"
    elif not missing:
        reason = "should not skip (all report URLs match)"
    elif matched and missing:
        reason = (
            "partial match: some report white_urls no longer on live product "
            "(deleted, replaced after pixelbin, or stale classify report)"
        )
    else:
        reason = (
            "no report white_url matches any live Shopify image — URLs changed "
            "(likely after image delete/replace or report is outdated)"
        )

    return {
        "product_id": pid,
        "title": row.get("title"),
        "category": row.get("category"),
        "skip_reason": reason,
        "report_white_urls_count": len(white_urls),
        "live_images_count": len(live_images),
        "matched_count": len(matched),
        "missing_report_urls_count": len(missing),
        "admin_url": client.config.admin_product_url(pid),
        "report_white_urls": white_urls,
        "missing_report_urls": missing,
        "matched": matched,
        "live_images": live_list,
    }


def format_txt_entry(d: dict) -> str:
    lines = [
        f"Product ID: {d['product_id']}",
        f"Title: {d['title']}",
        f"Category: {d.get('category')}",
        f"Skip reason: {d['skip_reason']}",
        f"Report white_urls: {d['report_white_urls_count']}",
        f"Live Shopify images: {d['live_images_count']}",
        f"Matched: {d['matched_count']}",
        f"Missing from live: {d['missing_report_urls_count']}",
        f"Admin: {d['admin_url']}",
        "",
        "Report white_urls (classify report):",
    ]
    for u in d.get("report_white_urls") or []:
        lines.append(f"  - {u}")
    lines.append("")
    lines.append("Live Shopify images now:")
    for img in d.get("live_images") or []:
        lines.append(f"  pos={img['position']} id={img['shopify_image_id']}")
        lines.append(f"    {img['src']}")
    if d.get("missing_report_urls"):
        lines.append("")
        lines.append("Report URLs NOT found on live product:")
        for u in d["missing_report_urls"]:
            lines.append(f"  - {u}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("")
    return "\n".join(lines)


def parse_terminal_skips(path: Path) -> list[int]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    ids: list[int] = []
    for m in re.finditer(
        r"\[\d+/\d+\] .+ \(id=(\d+), \d+ white_urls\)\.\.\.\s*\n\s*skip: no matching",
        text,
    ):
        ids.append(int(m.group(1)))
    return ids


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--terminal", type=Path, default=None)
    parser.add_argument("--product-id", type=int, action="append", dest="product_ids")
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-txt", type=Path, default=DEFAULT_OUT_TXT)
    args = parser.parse_args()

    pids = args.product_ids or []
    if args.terminal:
        pids = parse_terminal_skips(args.terminal)
    if not pids:
        pids = SESSION_SKIPPED

    report = json.loads(args.report.read_text(encoding="utf-8"))
    client = ShopifyClient(load_shopify_config())
    results: list[dict] = []

    for pid in pids:
        row = find_row(report, pid)
        if not row:
            results.append(
                {
                    "product_id": pid,
                    "title": "?",
                    "skip_reason": "not found in classify report",
                }
            )
            continue
        results.append(diagnose_product(client, row))

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(
            {
                "reason_code": "no_matching_images_on_live_shopify",
                "description": (
                    "Script matches report white_urls to live Shopify image src "
                    "(path only, no query string). Skip when zero matches."
                ),
                "count": len(results),
                "products": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    txt_parts = [
        "# Pixelbin skipped products — no matching images on live Shopify",
        f"# Total: {len(results)}",
        "#",
        "",
    ]
    for d in results:
        txt_parts.append(format_txt_entry(d))
    args.out_txt.write_text("\n".join(txt_parts), encoding="utf-8")

    print(f"Diagnosed: {len(results)} products")
    print(f"JSON: {args.out_json}")
    print(f"TXT:  {args.out_txt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
