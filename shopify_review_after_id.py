"""Open Shopify admin tabs for products after a given ID (from pixelbin live log).

Reads output/pixelbin_white_run_log.jsonl in completion order, skips up to --from-id,
then opens each product in Chrome every --delay seconds.

  python shopify_review_after_id.py --from-id 10376472363355 --limit 30
  python shopify_review_after_id.py --from-id 10376472363355 --limit 30 --delay 2

Next batch: use the printed "Last ID" as the new --from-id.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from shopify_client import load_shopify_config

DEFAULT_RUN_LOG = Path("output") / "pixelbin_white_run_log.jsonl"


def load_log_entries(path: Path) -> list[dict]:
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


def merge_by_product_id(entries: list[dict]) -> list[dict]:
    by_id: dict[int, dict] = {}
    for entry in entries:
        pid = int(entry["product_id"])
        existing = by_id.get(pid)
        if not existing or str(entry.get("completed_at") or "") >= str(
            existing.get("completed_at") or ""
        ):
            by_id[pid] = entry
    return sorted(by_id.values(), key=lambda e: str(e.get("completed_at") or ""))


def admin_url(entry: dict, *, store_slug: str) -> str:
    if url := str(entry.get("url") or "").strip():
        return url
    pid = int(entry["product_id"])
    return f"https://admin.shopify.com/store/{store_slug}/products/{pid}"


def entries_after_id(
    entries: list[dict],
    from_id: int,
    *,
    include_from: bool = False,
) -> tuple[list[dict], int | None]:
    """Return entries after from_id in completion order. Returns (slice, index or None)."""
    index: int | None = None
    for i, entry in enumerate(entries):
        if int(entry["product_id"]) == from_id:
            index = i
            break
    if index is None:
        return [], None
    start = index if include_from else index + 1
    return entries[start:], index


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open Shopify admin tabs for log products after a product ID."
    )
    parser.add_argument(
        "--from-id",
        type=int,
        required=True,
        help="Start after this product ID (last one you already reviewed).",
    )
    parser.add_argument(
        "--run-log",
        type=Path,
        default=DEFAULT_RUN_LOG,
        help=f"Pixelbin live log (default: {DEFAULT_RUN_LOG}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Max products to open this run (default: 30).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between opening each tab (default: 2).",
    )
    parser.add_argument(
        "--include-from-id",
        action="store_true",
        help="Also open --from-id itself (default: start from the next product).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print URLs only; do not open browser.",
    )
    args = parser.parse_args()

    if not args.run_log.exists():
        print(f"ERROR: log not found: {args.run_log}")
        return 1
    if args.limit <= 0:
        print("ERROR: --limit must be > 0")
        return 1

    all_entries = merge_by_product_id(load_log_entries(args.run_log))
    batch, from_index = entries_after_id(
        all_entries,
        args.from_id,
        include_from=args.include_from_id,
    )

    if from_index is None:
        print(f"ERROR: product ID {args.from_id} not found in {args.run_log}")
        print(f"Log has {len(all_entries)} unique completed products.")
        if all_entries:
            print(f"First ID: {all_entries[0]['product_id']} ({all_entries[0].get('title')})")
            print(f"Last ID:  {all_entries[-1]['product_id']} ({all_entries[-1].get('title')})")
        return 1

    remaining_after = len(batch)
    batch = batch[: args.limit]

    if not batch:
        print(f"Nothing after ID {args.from_id} — you are at the end of the log.")
        print(f"Log total: {len(all_entries)} products")
        return 0

    store_slug = load_shopify_config().store_slug
    anchor = all_entries[from_index]

    print(f"Log: {args.run_log}")
    print(f"From ID: {args.from_id} — {anchor.get('title')}")
    print(f"Completed: {anchor.get('completed_at')}")
    print(f"Remaining after this ID: {remaining_after}")
    print(f"Opening this run: {len(batch)} (limit {args.limit}, delay {args.delay}s)")
    print()

    last_pid: int | None = None
    for i, entry in enumerate(batch, 1):
        pid = int(entry["product_id"])
        title = str(entry.get("title") or "").strip()
        url = admin_url(entry, store_slug=store_slug)
        print(f"[{i}/{len(batch)}] {title}")
        print(f"  id={pid}  completed={entry.get('completed_at')}")
        print(f"  {url}")
        if not args.no_open:
            webbrowser.open(url, new=2)
        last_pid = pid
        if i < len(batch) and args.delay > 0:
            time.sleep(args.delay)
        print()

    still_left = remaining_after - len(batch)
    print("=" * 60)
    print(f"Opened: {len(batch)} product(s)")
    if last_pid is not None:
        print(f"Last ID: {last_pid}")
        print()
        print("Next batch:")
        print(
            f"  python shopify_review_after_id.py --from-id {last_pid} "
            f"--limit {args.limit} --delay {args.delay}"
        )
    if still_left > 0:
        print(f"Still remaining after last opened: {still_left}")
    else:
        print("No more products after this batch in the log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
