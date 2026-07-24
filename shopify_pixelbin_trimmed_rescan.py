"""Re-scan trimmed products (pixelbin already done): classify live images, BG-remove white.

Targets products where report white_urls were trimmed (3/4/5 -> 2) AND product is
already in pixelbin_white_checkpoint.json. Fetches live Shopify images, classifies each;
if still white, runs Pixelbin.

  python shopify_pixelbin_trimmed_rescan.py --dry-run
  python shopify_pixelbin_trimmed_rescan.py --apply --limit 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pixelbin_bg import load_pixelbin_settings, remove_background_to_bytes
from shopify_client import ShopifyClient, load_shopify_config, shopify_preview_url
from shopify_delete_extra_images_trimmed import find_trimmed_product_ids
from shopify_image_classify import ImageKind, classify_image_url
from shopify_process_white_nobg_from_report import (
    _append_run_log,
    _load_checkpoint,
    _safe_filename,
    _save_checkpoint,
)

DEFAULT_REPORT = Path("output") / "shopify_before_june10_report.json"
DEFAULT_ORIGINAL = Path("output") / "shopify_before_june10_report.backup_20260626_114915.json"
DEFAULT_PIXELBIN_CKPT = Path("output") / "pixelbin_white_checkpoint.json"
DEFAULT_RESCAN_CKPT = Path("output") / "pixelbin_trimmed_rescan_checkpoint.json"
DEFAULT_RUN_LOG = Path("output") / "pixelbin_trimmed_rescan_log.jsonl"
DEFAULT_REPORT_OUT = Path("output") / "pixelbin_trimmed_rescan_report.json"


@dataclass
class BgJob:
    product_id: int
    product_title: str
    shopify_image_id: int
    position: int
    source_url: str
    kind: ImageKind
    output_path: Path = field(default_factory=Path)
    done: bool = False
    credits: int = 0
    error: str = ""


def _classify_live_images(
    product: dict,
    *,
    timeout: float,
    max_position: int,
) -> list[tuple[dict, ImageKind]]:
    out: list[tuple[dict, ImageKind]] = []
    for img in product.get("images") or []:
        pos = int(img.get("position") or 0)
        if max_position > 0 and pos > max_position:
            continue
        src = str(img.get("src") or "").strip()
        if not src:
            continue
        kind = classify_image_url(
            src,
            timeout,
            preview_url=shopify_preview_url(src),
        )
        out.append((img, kind))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pixelbin rescan: trimmed + already-done products with live white images."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--original-report", type=Path, default=DEFAULT_ORIGINAL)
    parser.add_argument("--pixelbin-checkpoint", type=Path, default=DEFAULT_PIXELBIN_CKPT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_RESCAN_CKPT)
    parser.add_argument("--run-log", type=Path, default=DEFAULT_RUN_LOG)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--limit", type=int, default=0, help="Max products this run (0=all).")
    parser.add_argument(
        "--max-position",
        type=int,
        default=2,
        help="Only check Shopify images at position <= N (default 2).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "pixelbin" / "trimmed_rescan",
    )
    parser.add_argument("--time-limit-minutes", type=int, default=0)
    args = parser.parse_args()
    dry_run = not args.apply
    timeout = float(os.getenv("HTTP_TIMEOUT", "30"))

    if not args.report.exists() or not args.original_report.exists():
        print("ERROR: report or original backup not found.")
        return 1

    original = json.loads(args.original_report.read_text(encoding="utf-8"))
    current = json.loads(args.report.read_text(encoding="utf-8"))
    trimmed = find_trimmed_product_ids(original, current)
    pixelbin_done = _load_checkpoint(args.pixelbin_checkpoint)
    rescan_done = _load_checkpoint(args.checkpoint) if args.checkpoint else set()

    targets = [t for t in trimmed if int(t["id"]) in pixelbin_done]
    targets = [t for t in targets if int(t["id"]) not in rescan_done]

    if args.product_id:
        targets = [t for t in targets if int(t["id"]) == args.product_id]
        if not targets:
            print(f"Product {args.product_id} not in trimmed+pixelbin-done set.")
            return 1
    if args.limit > 0:
        targets = targets[: args.limit]

    client = ShopifyClient(load_shopify_config())
    print(f"Shop: {client.config.shop_host}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Trimmed products: {len(trimmed)}")
    print(f"Pixelbin done: {len(pixelbin_done)}")
    print(f"Target (trimmed + pixelbin done): {len(trimmed) and sum(1 for t in trimmed if int(t['id']) in pixelbin_done)}")
    print(f"Rescan checkpoint skip: {len(rescan_done)}")
    print(f"Remaining this run: {len(targets)}")
    print(f"Check live images position 1-{args.max_position}; Pixelbin if kind=white")
    print()

    if not targets:
        print("Nothing to do.")
        return 0

    if not dry_run:
        load_pixelbin_settings()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    deadline = t0 + args.time_limit_minutes * 60 if args.time_limit_minutes > 0 else None
    done_ids = set(rescan_done)
    all_jobs: list[BgJob] = []
    session_ok = 0
    session_skip = 0
    session_fail = 0
    session_white_imgs = 0

    for i, row in enumerate(targets, 1):
        if deadline and time.perf_counter() >= deadline:
            print(f"\nTime limit ({args.time_limit_minutes} min) reached.")
            break

        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        print(f"[{i}/{len(targets)}] {title} (id={pid})...", flush=True)

        try:
            product = client.get_product(pid)
        except Exception as exc:  # noqa: BLE001
            session_fail += 1
            print(f"  ERROR fetch: {exc}", flush=True)
            continue

        classified = _classify_live_images(
            product, timeout=timeout, max_position=args.max_position
        )
        kinds = {k: 0 for k in ("white", "no_bg", "lifestyle", "unknown")}
        for _, kind in classified:
            kinds[kind] = kinds.get(kind, 0) + 1
        print(
            f"  live: {len(classified)} image(s) — "
            f"white={kinds['white']} no_bg={kinds['no_bg']} "
            f"lifestyle={kinds['lifestyle']} unknown={kinds['unknown']}",
            flush=True,
        )

        white_imgs = [img for img, kind in classified if kind == "white"]
        if not white_imgs:
            session_skip += 1
            done_ids.add(pid)
            if not dry_run and args.checkpoint:
                _save_checkpoint(args.checkpoint, done_ids)
            print("  skip: no white images on Shopify", flush=True)
            continue

        jobs: list[BgJob] = []
        for img in white_imgs:
            jobs.append(
                BgJob(
                    product_id=pid,
                    product_title=title,
                    shopify_image_id=int(img["id"]),
                    position=int(img.get("position") or 0),
                    source_url=str(img.get("src") or ""),
                    kind="white",
                )
            )

        session_white_imgs += len(jobs)
        for job in jobs:
            print(
                f"    {'would pixelbin' if dry_run else 'pixelbin'} "
                f"pos={job.position} image_id={job.shopify_image_id}",
                flush=True,
            )
            if dry_run:
                job.done = True
                continue
            out_dir = args.output_dir / str(pid)
            out_dir.mkdir(parents=True, exist_ok=True)
            fname = f"rescan_{job.position:02d}_{_safe_filename(title)}.png"
            job.output_path = out_dir / fname
            try:
                png, px = remove_background_to_bytes(job.source_url)
                job.output_path.write_bytes(png)
                job.credits = px.consumed_credits
                client.replace_product_image(
                    job.product_id,
                    job.shopify_image_id,
                    image_bytes=png,
                    filename=fname,
                    position=job.position,
                )
                job.done = True
            except Exception as exc:  # noqa: BLE001
                job.error = f"{type(exc).__name__}: {exc}".strip(": ")
                print(f"      ERROR: {job.error}", flush=True)

        all_jobs.extend(jobs)
        product_ok = all(j.done for j in jobs)
        if product_ok:
            session_ok += 1
            done_ids.add(pid)
            if not dry_run and args.checkpoint:
                _save_checkpoint(args.checkpoint, done_ids)
            if args.run_log and not dry_run:
                _append_run_log(
                    args.run_log,
                    {
                        "product_id": pid,
                        "title": title,
                        "source": "trimmed_rescan",
                        "images_ok": len(jobs),
                        "credits": sum(j.credits for j in jobs),
                        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "url": client.config.admin_product_url(pid),
                        "actions": [
                            {
                                "shopify_image_id": j.shopify_image_id,
                                "position": j.position,
                                "source_url": j.source_url,
                                "done": j.done,
                                "credits": j.credits,
                                "error": j.error,
                            }
                            for j in jobs
                        ],
                    },
                )
            print(f"  OK {len(jobs)} white image(s) processed", flush=True)
        else:
            session_fail += 1
            print("  PARTIAL FAIL — not checkpointed", flush=True)

    ok = sum(1 for j in all_jobs if j.done)
    fail = sum(1 for j in all_jobs if j.error)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(
        json.dumps(
            {
                "session_ok": session_ok,
                "session_skip_no_white": session_skip,
                "session_fail": session_fail,
                "white_images_found": session_white_imgs,
                "images_ok": ok,
                "images_failed": fail,
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Products: {session_ok} pixelbin OK, {session_skip} skip (no white), {session_fail} fail")
    print(f"White images processed: {ok} ok, {fail} failed")
    print(f"Report: {args.report_out}")
    if args.checkpoint:
        print(f"Rescan checkpoint: {args.checkpoint} ({len(done_ids)} products)")
    if dry_run:
        print()
        print("Dry run. Add --apply to run Pixelbin on white images.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
