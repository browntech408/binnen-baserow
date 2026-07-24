"""Delete Shopify images beyond position 2 for pixelbin-done products after a log ID.

Reads pixelbin_white_run_log.jsonl in completion order. After --from-id, keeps
Shopify position 1-2 and deletes position 3+.

  python shopify_delete_extra_after_log_id.py --from-id 10376422687067 --dry-run
  python shopify_delete_extra_after_log_id.py --from-id 10376422687067 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from export_pixelbin_products_txt import load_log_entries, merge_entries
from shopify_client import ShopifyClient, load_shopify_config
from shopify_delete_extra_images_trimmed import images_to_delete

DEFAULT_RUN_LOGS = [
    Path("output") / "pixelbin_white_run_log.jsonl",
    Path("output") / "pixelbin" / "pixelbin_white_run_log.jsonl",
]
DEFAULT_CHECKPOINT = Path("output") / "delete_extra_after_log_id_checkpoint.json"
DEFAULT_REPORT_OUT = Path("output") / "shopify_delete_extra_after_log_id_report.json"


def _load_checkpoint(path: Path) -> set[int]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {int(x) for x in (data.get("done_product_ids") or []) if x}


def _save_checkpoint(path: Path, done_product_ids: set[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "done_product_ids": sorted(done_product_ids),
                "total_done": len(done_product_ids),
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def products_after_log_id(
    from_id: int,
    *,
    run_logs: list[Path],
    include_from: bool = False,
    min_images_ok: int = 0,
) -> tuple[list[dict], dict | None]:
    paths = [p for p in run_logs if p.exists()]
    if not paths:
        return [], None
    entries = merge_entries(paths)
    anchor_idx = next(
        (i for i, e in enumerate(entries) if int(e["product_id"]) == from_id),
        None,
    )
    if anchor_idx is None:
        return [], None
    start = anchor_idx if include_from else anchor_idx + 1
    after = entries[start:]
    if min_images_ok > 0:
        after = [e for e in after if int(e.get("images_ok") or 0) >= min_images_ok]
    return after, entries[anchor_idx]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete Shopify images pos 3+ for products after a pixelbin log ID."
    )
    parser.add_argument("--from-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-log", type=Path, action="append", default=None)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--product-limit", type=int, default=0)
    parser.add_argument("--keep-positions", type=int, default=2)
    parser.add_argument(
        "--min-images-ok",
        type=int,
        default=0,
        help="Only products with >= N images_ok in log (e.g. 3 for 3+ pixelbin images).",
    )
    parser.add_argument("--include-from-id", action="store_true")
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    args = parser.parse_args()
    dry_run = not args.apply

    run_logs = args.run_log or DEFAULT_RUN_LOGS
    targets, anchor = products_after_log_id(
        args.from_id,
        run_logs=run_logs,
        include_from=args.include_from_id,
        min_images_ok=args.min_images_ok,
    )
    if anchor is None:
        print(f"ERROR: --from-id {args.from_id} not found in pixelbin run log.")
        return 1

    done = _load_checkpoint(args.checkpoint) if args.checkpoint else set()
    targets = [e for e in targets if int(e["product_id"]) not in done]
    if args.product_limit > 0:
        targets = targets[: args.product_limit]

    client = ShopifyClient(load_shopify_config())
    print(f"Shop: {client.config.shop_host}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"From ID: {args.from_id} — {anchor.get('title')}")
    print(f"Completed: {anchor.get('completed_at')}")
    if args.min_images_ok > 0:
        print(f"Filter: images_ok >= {args.min_images_ok} in log")
    print(f"Checkpoint skip: {len(done)}")
    print(f"Products this run: {len(targets)}")
    print(f"Rule: keep Shopify position 1-{args.keep_positions}, delete higher")
    print()

    if not targets:
        print("Nothing to do.")
        return 0

    products_ok = 0
    products_skip = 0
    products_fail = 0
    images_deleted = 0
    images_failed = 0
    done_ids = set(done)
    actions: list[dict] = []

    for i, entry in enumerate(targets, 1):
        pid = int(entry["product_id"])
        title = str(entry.get("title") or "").strip()
        images_ok = int(entry.get("images_ok") or 0)
        print(
            f"[{i}/{len(targets)}] {title} (id={pid}, log images_ok={images_ok})...",
            flush=True,
        )

        try:
            product = client.get_product(pid)
        except Exception as exc:  # noqa: BLE001
            products_fail += 1
            print(f"  ERROR fetch: {exc}", flush=True)
            continue

        live_n = len(product.get("images") or [])
        to_delete = images_to_delete(product, keep_positions=args.keep_positions)
        if not to_delete:
            products_skip += 1
            done_ids.add(pid)
            if not dry_run and args.checkpoint:
                _save_checkpoint(args.checkpoint, done_ids)
            print(f"  skip: live has {live_n} image(s), nothing above pos {args.keep_positions}", flush=True)
            continue

        jobs_ok = True
        for img in to_delete:
            pos = int(img.get("position") or 0)
            img_id = int(img["id"])
            print(f"  delete pos={pos} image_id={img_id}", flush=True)
            action = {
                "product_id": pid,
                "title": title,
                "position": pos,
                "shopify_image_id": img_id,
                "done": False,
                "error": "",
            }
            if dry_run:
                action["done"] = True
                images_deleted += 1
            else:
                try:
                    client.delete_product_image(pid, img_id)
                    action["done"] = True
                    images_deleted += 1
                except Exception as exc:  # noqa: BLE001
                    action["error"] = str(exc)
                    images_failed += 1
                    jobs_ok = False
                    print(f"    ERROR: {exc}", flush=True)
            actions.append(action)

        if jobs_ok:
            products_ok += 1
            done_ids.add(pid)
            if not dry_run and args.checkpoint:
                _save_checkpoint(args.checkpoint, done_ids)
            print(f"  OK deleted {len(to_delete)} image(s)", flush=True)
        else:
            products_fail += 1
            print("  PARTIAL FAIL — not checkpointed", flush=True)

    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(
        json.dumps(
            {
                "from_id": args.from_id,
                "products_ok": products_ok,
                "products_skip": products_skip,
                "products_fail": products_fail,
                "images_deleted": images_deleted,
                "images_failed": images_failed,
                "actions": actions,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Products: {products_ok} deleted extras, {products_skip} skip, {products_fail} fail")
    print(f"Images: {images_deleted} deleted, {images_failed} failed")
    print(f"Report: {args.report_out}")
    if args.checkpoint:
        print(f"Checkpoint: {args.checkpoint} ({len(done_ids)} products)")
    if dry_run:
        print()
        print("Dry run. Add --apply to delete on Shopify.")
    return 0 if images_failed == 0 and products_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
