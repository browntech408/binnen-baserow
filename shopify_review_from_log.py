"""Open Shopify admin URLs from a pixelbin run log for manual image review.

Does not run Pixelbin or change Shopify — opens browser tabs as you go.

  python shopify_review_from_log.py --run-log output/pixelbin/pixelbin_white_run_log.jsonl
  python shopify_review_from_log.py --run-log output/pixelbin_white_run_log.jsonl --skip 146 --pause
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from shopify_client import load_shopify_config


def _load_log_entries(path: Path) -> list[dict]:
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


def _admin_url(entry: dict, *, store_slug: str) -> str:
    if url := str(entry.get("url") or "").strip():
        return url
    pid = int(entry["product_id"])
    return f"https://admin.shopify.com/store/{store_slug}/products/{pid}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Review products from pixelbin run log.")
    parser.add_argument(
        "--run-log",
        type=Path,
        default=Path("output") / "pixelbin_white_run_log.jsonl",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Skip first N entries (already reviewed).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max entries to review this session (0 = all remaining).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print URLs only; do not open browser.",
    )
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Wait for Enter before opening the next tab.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Seconds between tab opens (default 0.3).",
    )
    args = parser.parse_args()

    if not args.run_log.exists():
        print(f"ERROR: log not found: {args.run_log}")
        return 1

    store_slug = load_shopify_config().store_slug
    entries = _load_log_entries(args.run_log)
    if args.skip:
        entries = entries[args.skip :]
    if args.limit > 0:
        entries = entries[: args.limit]

    if not entries:
        print("Nothing to review.")
        return 0

    print(f"Log: {args.run_log}")
    print(f"Opening {len(entries)} tab(s)...")
    print()

    for i, entry in enumerate(entries, 1):
        title = str(entry.get("title") or "").strip()
        pid = entry.get("product_id")
        url = _admin_url(entry, store_slug=store_slug)
        print(f"[{i}/{len(entries)}] {title} (id={pid})")
        print(f"  {url}")
        if not args.no_open:
            webbrowser.open(url, new=2)
        if args.pause:
            try:
                input("  Press Enter for next product (Ctrl+C to stop)... ")
            except (EOFError, KeyboardInterrupt):
                print("\nStopped.")
                return 0
        elif i < len(entries) and args.delay > 0:
            time.sleep(args.delay)
        print()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
