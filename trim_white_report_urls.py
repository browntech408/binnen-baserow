"""Trim white_urls in classify report: keep first N, drop the rest.

For white-section products not yet in pixelbin checkpoint, with 5+ white_urls,
keeps first 2 URLs so Pixelbin only processes 2 images per product.

  python trim_white_report_urls.py --dry-run
  python trim_white_report_urls.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

DEFAULT_REPORT = Path("output") / "shopify_before_june10_report.json"
DEFAULT_CHECKPOINT = Path("output") / "pixelbin_white_checkpoint.json"


def load_checkpoint(path: Path) -> set[int]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(x) for x in (data.get("done_product_ids") or []) if x}


def main() -> int:
    parser = argparse.ArgumentParser(description="Trim white_urls in classify report.")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--keep", type=int, default=2, help="Keep first N white_urls (default 2).")
    parser.add_argument(
        "--min-urls",
        type=int,
        default=5,
        help="Only trim products with at least this many white_urls (default 5).",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if not args.report.exists():
        print(f"ERROR: report not found: {args.report}")
        return 1

    done = load_checkpoint(args.checkpoint)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    white_section = report.get("white") or []

    trimmed: list[dict] = []
    for row in white_section:
        pid = int(row.get("id") or 0)
        if not pid or pid in done:
            continue
        urls = list(row.get("white_urls") or [])
        if len(urls) < args.min_urls:
            continue
        if len(urls) <= args.keep:
            continue
        removed = urls[args.keep :]
        trimmed.append(
            {
                "id": pid,
                "title": row.get("title"),
                "before": len(urls),
                "after": args.keep,
                "removed": len(removed),
            }
        )
        if not dry_run:
            row["white_urls"] = urls[: args.keep]

    print(f"Report: {args.report}")
    print(f"Checkpoint skip: {len(done)} done products")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Rule: white section, not done, >={args.min_urls} white_urls -> keep first {args.keep}")
    print(f"Products to trim: {len(trimmed)}")
    if trimmed:
        removed_total = sum(t["removed"] for t in trimmed)
        print(f"URLs removed total: {removed_total}")
        print()
        for t in trimmed[:20]:
            print(
                f"  {t['title']} (id={t['id']}): "
                f"{t['before']} -> {t['after']} urls (-{t['removed']})"
            )
        if len(trimmed) > 20:
            print(f"  ... +{len(trimmed) - 20} more")

    if dry_run:
        print()
        print("Dry run. Add --apply to update the report.")
        return 0

    if not trimmed:
        print("Nothing to change.")
        return 0

    backup = args.report.with_suffix(
        f".backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    shutil.copy2(args.report, backup)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print()
    print(f"Backup: {backup}")
    print(f"Updated: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
