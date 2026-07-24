"""Export pixelbin run log entries to text file (pixelbin_white_products format)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CUTOFF_DEFAULT = "2026-06-25T21:08:31"
DEFAULT_REPORT = Path("output") / "shopify_before_june10_report.json"


def count_report_white_products(report_path: Path) -> int:
    if not report_path.exists():
        return 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    seen: set[int] = set()
    total = 0
    for key in ("products", "white", "no_bg", "lifestyle", "unknown", "no_images"):
        for row in report.get(key) or []:
            pid = int(row.get("id") or 0)
            if not pid or pid in seen:
                continue
            if row.get("white_urls"):
                seen.add(pid)
                total += 1
    return total


def load_log_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    entries: list[dict] = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        if idx >= len(text):
            break
        obj, end = decoder.raw_decode(text, idx)
        entries.append(obj)
        idx = end
    return entries


def merge_entries(paths: list[Path]) -> list[dict]:
    by_id: dict[int, dict] = {}
    for path in paths:
        for entry in load_log_entries(path):
            pid = int(entry["product_id"])
            existing = by_id.get(pid)
            if not existing or str(entry.get("completed_at") or "") > str(
                existing.get("completed_at") or ""
            ):
                by_id[pid] = entry
    return sorted(by_id.values(), key=lambda e: str(e.get("completed_at") or ""))


def format_block(entry: dict, *, include_images: bool = True) -> str:
    pid = int(entry["product_id"])
    title = str(entry.get("title") or "").strip()
    lines = [str(pid), title]
    if include_images:
        lines.append(str(int(entry.get("images_ok") or 0)))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-log",
        type=Path,
        action="append",
        default=None,
        help="Run log JSONL (repeatable).",
    )
    parser.add_argument(
        "--until",
        default=CUTOFF_DEFAULT,
        help=f"Include entries with completed_at <= this (default {CUTOFF_DEFAULT}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "pixelbin" / "pixelbin_white_products_until_2026-06-25.txt",
    )
    parser.add_argument(
        "--all-done",
        action="store_true",
        help="Export all products in pixelbin checkpoint (from run logs).",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("output") / "pixelbin_white_checkpoint.json",
    )
    parser.add_argument(
        "--categories-report",
        type=Path,
        default=DEFAULT_REPORT,
        help="Classify report for total product count (--all-done).",
    )
    args = parser.parse_args()

    log_paths = args.run_log or [
        Path("output") / "pixelbin_white_run_log.jsonl",
        Path("output") / "pixelbin" / "pixelbin_white_run_log.jsonl",
    ]

    entries = merge_entries(log_paths)

    if args.all_done:
        if not args.checkpoint.exists():
            print(f"ERROR: checkpoint not found: {args.checkpoint}")
            return 1
        done = {
            int(x)
            for x in json.loads(args.checkpoint.read_text(encoding="utf-8")).get(
                "done_product_ids", []
            )
        }
        entries = [e for e in entries if int(e["product_id"]) in done]
        if args.output == Path("output") / "pixelbin" / "pixelbin_white_products_until_2026-06-25.txt":
            args.output = Path("output") / "pixelbin" / "pixelbin_white_products_all_done.txt"
    else:
        entries = [e for e in entries if str(e.get("completed_at") or "") <= args.until]

    include_images = True
    if args.all_done:
        total_products = count_report_white_products(args.categories_report)
        header = (
            f"# Total products: {total_products}\n"
            f"# Done products: {len(entries)}\n"
            "#\n"
            "# Done products - per block:\n"
            "#   line 1 = Product ID\n"
            "#   line 2 = Product title\n"
            "#   line 3 = Number of images\n"
            "#   blank line\n"
            "#\n"
        )
    else:
        header = (
            "# Per product block:\n"
            "#   line 1 = Product ID\n"
            "#   line 2 = Product title\n"
            "#   line 3 = Total images (BG removed)\n"
            "#   blank line\n"
            f"# Total products: {len(entries)}\n"
            "#\n"
        )
    blocks = [format_block(e, include_images=include_images) for e in entries]
    body = header + "\n".join(blocks)
    if body and not body.endswith("\n"):
        body += "\n"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")

    credits = sum(int(e.get("credits") or 0) for e in entries)
    print(f"Logs: {[str(p) for p in log_paths if p.exists()]}")
    if args.all_done:
        print(f"Source: checkpoint ({args.checkpoint})")
    else:
        print(f"Until: {args.until}")
    print(f"Products: {len(entries)}")
    print(f"Total images (credits): {credits}")
    if entries:
        print(f"First: {entries[0].get('completed_at')} — {entries[0].get('title')}")
        print(f"Last:  {entries[-1].get('completed_at')} — {entries[-1].get('title')}")
    print(f"Written: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
