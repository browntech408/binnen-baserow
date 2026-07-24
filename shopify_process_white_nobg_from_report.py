"""Pixelbin BG remove on white_urls from a classify report (no re-scan).

Flow for large catalogs (e.g. shopify_before_june10_report.json):
  1) Classify once (if needed): shopify_classify_since.py --before 2026-06-10 ...
  2) Optional: delete lifestyle on mixed products (--delete-lifestyle-only)
  3) Pixelbin white_urls ONLY — resume anytime (checkpoint + log per product):
     python shopify_process_white_nobg_from_report.py --apply --all-white-urls \\
       --categories-report output/shopify_before_june10_report.json \\
       --checkpoint output/pixelbin_white_checkpoint.json \\
       --run-log output/pixelbin_white_run_log.jsonl \\
       --time-limit-minutes 150
     # Kal wahi command dubara — done products skip, agy se continue
  4) White + lifestyle BOTH Pixelbin BG-remove (no lifestyle delete):
     python shopify_process_white_nobg_from_report.py --apply --all-bg-urls \\
       --categories-report output/shopify_after_july20_2026_report.json \\
       --checkpoint output/pixelbin_after_july20_checkpoint.json \\
       --run-log output/pixelbin_after_july20_run_log.jsonl
  5) Auto-open Shopify admin tab after each product (for manual review):
     python shopify_process_white_nobg_from_report.py --apply --all-white-urls \\
       ... --open-admin
     # Stale report URLs: live Shopify scan classifies white images automatically

Examples:
  python shopify_process_white_nobg_from_report.py --dry-run --all-white-urls \\
    --categories-report output/shopify_before_june10_report.json
  python shopify_process_white_nobg_from_report.py --apply --bg-only --category white \\
    --categories-report output/shopify_before_june10_report.json --limit 3
  python shopify_process_white_nobg_from_report.py --apply --delete-lifestyle-only \\
    --categories-report output/shopify_before_june10_report.json
  python shopify_process_white_nobg_from_report.py --apply --all-bg-urls \\
    --categories-report output/shopify_after_july20_2026_report.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from config import load_settings
from pixelbin_bg import load_pixelbin_settings, remove_background_to_bytes
from shopify_client import ShopifyClient, load_shopify_config, shopify_preview_url
from shopify_image_classify import classify_image_url

DEFAULT_CATEGORIES_REPORT = Path("output") / "shopify_before_june10_report.json"
REPORT_SECTIONS = ("products", "white", "no_bg", "lifestyle", "unknown", "no_images")


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text.strip())[:80].strip("_") or "product"


def _url_key(url: str) -> str:
    """Match report URLs to live Shopify image src (ignore query string)."""
    path = urlparse(url.strip()).path
    return path.rstrip("/").lower()


def _map_shopify_images(product: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for img in product.get("images") or []:
        src = str(img.get("src") or "").strip()
        if src:
            out[_url_key(src)] = img
    return out


@dataclass
class ImageAction:
    product_id: int
    product_title: str
    product_category: str
    action: str  # bg_remove | delete_lifestyle
    shopify_image_id: int
    position: int
    source_url: str
    output_path: Path = field(default_factory=Path)
    done: bool = False
    credits: int = 0
    error: str = ""


def _actions_for_row(
    row: dict,
    *,
    shopify_images: dict[str, dict],
    white_urls_only: bool = False,
    bg_lifestyle: bool = False,
) -> list[ImageAction]:
    pid = int(row["id"])
    title = str(row.get("title") or "").strip()
    cat = str(row.get("category") or "").strip()
    actions: list[ImageAction] = []

    def _lookup(url: str) -> dict | None:
        return shopify_images.get(_url_key(url))

    def _add_bg(url: str) -> None:
        img = _lookup(url)
        if not img:
            return
        actions.append(
            ImageAction(
                product_id=pid,
                product_title=title,
                product_category=cat,
                action="bg_remove",
                shopify_image_id=int(img["id"]),
                position=int(img.get("position") or 0),
                source_url=url,
            )
        )

    if bg_lifestyle:
        # Pixelbin on white + lifestyle (no lifestyle delete).
        seen: set[str] = set()
        for url in list(row.get("white_urls") or []) + list(
            row.get("lifestyle_urls") or []
        ):
            key = _url_key(url)
            if not key or key in seen:
                continue
            seen.add(key)
            _add_bg(url)
        return actions

    if not white_urls_only:
        for url in row.get("lifestyle_urls") or []:
            img = _lookup(url)
            if not img:
                continue
            actions.append(
                ImageAction(
                    product_id=pid,
                    product_title=title,
                    product_category=cat,
                    action="delete_lifestyle",
                    shopify_image_id=int(img["id"]),
                    position=int(img.get("position") or 0),
                    source_url=url,
                )
            )

    for url in row.get("white_urls") or []:
        _add_bg(url)

    return actions


def _actions_from_live_shopify(
    product: dict,
    row: dict,
    *,
    timeout: float,
    include_lifestyle: bool = False,
) -> list[ImageAction]:
    """Classify live Shopify images when report white_urls no longer match CDN URLs."""
    pid = int(row["id"])
    title = str(row.get("title") or product.get("title") or "").strip()
    cat = str(row.get("category") or "").strip()
    actions: list[ImageAction] = []
    for img in product.get("images") or []:
        src = str(img.get("src") or "").strip()
        if not src:
            continue
        kind = classify_image_url(
            src,
            timeout,
            preview_url=shopify_preview_url(src),
        )
        if kind == "white" or (include_lifestyle and kind == "lifestyle"):
            pass
        else:
            continue
        actions.append(
            ImageAction(
                product_id=pid,
                product_title=title,
                product_category=cat,
                action="bg_remove",
                shopify_image_id=int(img["id"]),
                position=int(img.get("position") or 0),
                source_url=src,
            )
        )
    return actions


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


def _append_run_log(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _action_to_dict(a: ImageAction) -> dict:
    return {
        "shopify_image_id": a.shopify_image_id,
        "position": a.position,
        "source_url": a.source_url,
        "done": a.done,
        "credits": a.credits,
        "error": a.error,
    }


def _process_one_product_bg(
    client: ShopifyClient,
    row: dict,
    *,
    output_dir: Path,
    white_urls_only: bool,
    http_timeout: float,
    bg_lifestyle: bool = False,
) -> tuple[list[ImageAction], str]:
    product = client.get_product(int(row["id"]))
    img_map = _map_shopify_images(product)
    jobs = _actions_for_row(
        row,
        shopify_images=img_map,
        white_urls_only=white_urls_only,
        bg_lifestyle=bg_lifestyle,
    )
    if white_urls_only or bg_lifestyle:
        jobs = [j for j in jobs if j.action == "bg_remove"]
    source = "report"
    if not jobs and (white_urls_only or bg_lifestyle):
        jobs = _actions_from_live_shopify(
            product,
            row,
            timeout=http_timeout,
            include_lifestyle=bg_lifestyle,
        )
        if jobs:
            source = "live_scan"
            kinds = "white+lifestyle" if bg_lifestyle else "white"
            print(
                f"    live scan: {len(jobs)} {kinds} image(s) "
                "(report URLs did not match Shopify)",
                flush=True,
            )
    if not jobs:
        return jobs, source

    for job in jobs:
        out_dir = output_dir / str(job.product_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{job.position:02d}_{_safe_filename(job.product_title)}.png"
        job.output_path = out_dir / fname
        try:
            print(
                f"    pixelbin image_id={job.shopify_image_id} pos={job.position}",
                flush=True,
            )
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
    return jobs, source


def _load_all_rows_with_white_urls(report: dict) -> list[dict]:
    """Every product in the report that has at least one white_urls entry."""
    seen: set[int] = set()
    out: list[dict] = []
    for key in REPORT_SECTIONS:
        for row in report.get(key) or []:
            pid = int(row.get("id") or 0)
            if not pid or pid in seen:
                continue
            if not row.get("white_urls"):
                continue
            seen.add(pid)
            out.append(row)
    out.sort(key=lambda r: str(r.get("title") or "").lower())
    return out


def _load_all_rows_for_bg(report: dict) -> list[dict]:
    """Products with white_urls and/or lifestyle_urls (BG-remove both)."""
    seen: set[int] = set()
    out: list[dict] = []
    for key in REPORT_SECTIONS:
        for row in report.get(key) or []:
            pid = int(row.get("id") or 0)
            if not pid or pid in seen:
                continue
            if not (row.get("white_urls") or row.get("lifestyle_urls")):
                continue
            seen.add(pid)
            out.append(row)
    out.sort(key=lambda r: str(r.get("title") or "").lower())
    return out


def _bg_url_count(row: dict) -> int:
    seen: set[str] = set()
    for url in list(row.get("white_urls") or []) + list(row.get("lifestyle_urls") or []):
        key = _url_key(url)
        if key:
            seen.add(key)
    return len(seen)


def _load_report_rows(
    report: dict,
    *,
    categories: tuple[str, ...],
    mixed_lifestyle_only: bool = False,
) -> list[dict]:
    rows: list[dict] = []
    for cat in categories:
        section = report.get(cat) or []
        if section:
            rows.extend(section)
            continue
        for row in report.get("products") or []:
            if str(row.get("category") or "") != cat:
                continue
            if mixed_lifestyle_only and not (row.get("lifestyle_urls") or []):
                continue
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BG-remove white images; delete lifestyle images (from report)."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--categories-report",
        type=Path,
        default=DEFAULT_CATEGORIES_REPORT,
    )
    parser.add_argument(
        "--category",
        choices=("white", "no_bg", "both"),
        default="both",
        help="Which report sections to process (default: white + no_bg).",
    )
    parser.add_argument("--product-id", type=int, help="Single product from report.")
    parser.add_argument(
        "--bg-only",
        action="store_true",
        help="Only Pixelbin BG remove on white_urls (skip lifestyle delete).",
    )
    parser.add_argument(
        "--delete-lifestyle-only",
        action="store_true",
        help="Only delete lifestyle_urls from Shopify (skip BG remove).",
    )
    parser.add_argument(
        "--all-white-urls",
        action="store_true",
        help="All report sections: every product with white_urls (Pixelbin white only).",
    )
    parser.add_argument(
        "--all-bg-urls",
        action="store_true",
        help=(
            "Pixelbin BG remove on white_urls AND lifestyle_urls "
            "(no lifestyle delete). Use when studio shots are classified as lifestyle."
        ),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Resume: skip product IDs already fully processed (saved after each product).",
    )
    parser.add_argument(
        "--run-log",
        type=Path,
        default=Path("output") / "pixelbin_white_run_log.jsonl",
        help="Append one JSON line per completed product (live log).",
    )
    parser.add_argument(
        "--time-limit-minutes",
        type=int,
        default=0,
        help="Stop after N minutes this session (0 = no limit). Re-run tomorrow to continue.",
    )
    parser.add_argument(
        "--product-limit",
        type=int,
        default=0,
        help="Max products to process this run (0 = all remaining).",
    )
    parser.add_argument("--limit", type=int, default=0, help="Alias for --product-limit.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "pixelbin",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "shopify_process_white_nobg_report.json",
    )
    parser.add_argument(
        "--open-admin",
        action="store_true",
        help="Open Shopify admin product page in browser after each successful BG remove.",
    )
    parser.add_argument(
        "--review-pause",
        action="store_true",
        help="Wait for Enter before next product (optional; default is no wait).",
    )
    args = parser.parse_args()
    if args.bg_only and args.delete_lifestyle_only:
        print("ERROR: use --bg-only OR --delete-lifestyle-only, not both.")
        return 1
    if args.all_white_urls and args.delete_lifestyle_only:
        print("ERROR: --all-white-urls is white_urls Pixelbin only (no lifestyle delete).")
        return 1
    if args.all_bg_urls and args.delete_lifestyle_only:
        print("ERROR: --all-bg-urls does BG remove on lifestyle (not delete).")
        return 1
    if args.all_white_urls and args.all_bg_urls:
        print("ERROR: use --all-white-urls OR --all-bg-urls, not both.")
        return 1

    bg_lifestyle = bool(args.all_bg_urls)
    white_urls_only = bool(args.all_white_urls or args.bg_only) and not bg_lifestyle
    dry_run = not args.apply

    report = json.loads(args.categories_report.read_text(encoding="utf-8"))
    if args.all_bg_urls:
        rows = _load_all_rows_for_bg(report)
        section_label = "all sections (white + lifestyle BG remove)"
    elif args.all_white_urls:
        rows = _load_all_rows_with_white_urls(report)
        section_label = "all sections (white_urls)"
    else:
        cats = ("white", "no_bg") if args.category == "both" else (args.category,)
        rows = _load_report_rows(
            report,
            categories=cats,
            mixed_lifestyle_only=args.delete_lifestyle_only,
        )
        section_label = ", ".join(cats)

    done_products = _load_checkpoint(args.checkpoint) if args.checkpoint else set()
    if done_products:
        rows = [r for r in rows if int(r["id"]) not in done_products]

    product_limit = args.product_limit or args.limit
    total_with_white = len(rows)
    if product_limit > 0:
        rows = rows[:product_limit]

    if args.product_id:
        rows = [r for r in rows if int(r["id"]) == args.product_id]
        if not rows:
            print(f"Product {args.product_id} not in selected report sections.")
            return 1

    white_url_count = sum(len(r.get("white_urls") or []) for r in rows)
    lifestyle_url_count = sum(len(r.get("lifestyle_urls") or []) for r in rows)
    bg_url_count = (
        sum(_bg_url_count(r) for r in rows) if bg_lifestyle else white_url_count
    )
    client = ShopifyClient(load_shopify_config())
    all_actions: list[ImageAction] = []
    missing_urls = 0

    print(f"Categories report: {args.categories_report}")
    print(f"Shop: {client.config.shop_host}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Source: {section_label}")
    if args.all_white_urls or args.all_bg_urls:
        counts = report.get("counts") or {}
        if counts:
            print(f"Report counts: {counts}")
    if args.checkpoint and done_products:
        print(f"Checkpoint skip: {len(done_products)} products already done")
    if args.checkpoint:
        print(f"Checkpoint file: {args.checkpoint}")
    if bg_lifestyle:
        print("Mode: white + lifestyle — Pixelbin BG remove on BOTH (no lifestyle delete)")
    elif white_urls_only:
        print("Mode: white_urls ONLY — Pixelbin BG remove, no lifestyle delete, no_bg skipped")
    if args.delete_lifestyle_only:
        print("Delete lifestyle only: skip BG remove")
    if args.time_limit_minutes > 0:
        print(f"Time limit this session: {args.time_limit_minutes} minutes")
    print(f"Products remaining (this run): {len(rows)}")
    if done_products:
        print(f"Total done (checkpoint): {len(done_products)} / {total_with_white + len(done_products)}")
    print(f"white_urls in this run: {white_url_count}")
    if bg_lifestyle:
        print(f"lifestyle_urls in this run: {lifestyle_url_count}")
        print(f"unique BG targets (white+lifestyle): {bg_url_count}")
    if args.apply and args.run_log:
        print(f"Live log: {args.run_log}")
    if args.open_admin:
        print("Review: opens Shopify admin tab after each OK product (no pause)")
    if args.review_pause:
        print("Review: pauses until you press Enter before next product")
    print()

    if dry_run:
        all_actions: list[ImageAction] = []
        missing_urls = 0
        live_scan_fallback = 0
        http_timeout = load_settings().http_timeout
        for i, row in enumerate(rows, 1):
            if i % 100 == 0 or i == len(rows):
                print(f"  scanning Shopify {i}/{len(rows)}...", flush=True)
            product = client.get_product(int(row["id"]))
            img_map = _map_shopify_images(product)
            actions = _actions_for_row(
                row,
                shopify_images=img_map,
                white_urls_only=white_urls_only,
                bg_lifestyle=bg_lifestyle,
            )
            if white_urls_only or bg_lifestyle:
                actions = [a for a in actions if a.action == "bg_remove"]
            if not actions and (white_urls_only or bg_lifestyle):
                actions = _actions_from_live_shopify(
                    product,
                    row,
                    timeout=http_timeout,
                    include_lifestyle=bg_lifestyle,
                )
                if actions:
                    live_scan_fallback += 1
            check_urls = list(row.get("white_urls") or [])
            if bg_lifestyle or not white_urls_only:
                check_urls += list(row.get("lifestyle_urls") or [])
            for url in check_urls:
                if _url_key(url) not in img_map:
                    missing_urls += 1
            all_actions.extend(actions)

        if white_urls_only or bg_lifestyle:
            all_actions = [a for a in all_actions if a.action == "bg_remove"]
        elif args.bg_only:
            all_actions = [a for a in all_actions if a.action == "bg_remove"]
        elif args.delete_lifestyle_only:
            all_actions = [a for a in all_actions if a.action == "delete_lifestyle"]

        bg = [a for a in all_actions if a.action == "bg_remove"]
        dels = [a for a in all_actions if a.action == "delete_lifestyle"]
        label = "white+lifestyle" if bg_lifestyle else "white_urls"
        print(f"Actions: {len(bg)} bg_remove ({label}), {len(dels)} delete_lifestyle")
        if missing_urls:
            print(f"WARN: {missing_urls} report URLs not found on live Shopify product")
        if live_scan_fallback:
            print(
                f"Live scan fallback: {live_scan_fallback} products "
                "(report URLs stale, images found on Shopify)"
            )
        print()
        for a in all_actions[:40]:
            print(
                f"  [{a.action}] {a.product_title} "
                f"(cat={a.product_category}, image_id={a.shopify_image_id})"
            )
        if len(all_actions) > 40:
            print(f"  ... and {len(all_actions) - 40} more")
        print()
        print("Dry run. Add --apply to run on Shopify.")
        return 0

    if not rows:
        print("Nothing to do — all products already in checkpoint.")
        return 0

    load_pixelbin_settings()
    http_timeout = load_settings().http_timeout
    args.output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    deadline = (
        t0 + args.time_limit_minutes * 60 if args.time_limit_minutes > 0 else None
    )
    done_ids = set(done_products)
    all_actions: list[ImageAction] = []
    session_products_ok = 0
    session_products_fail = 0
    stopped_time_limit = False

    for i, row in enumerate(rows, 1):
        if deadline and time.perf_counter() >= deadline:
            stopped_time_limit = True
            print(
                f"\nTime limit ({args.time_limit_minutes} min) reached. "
                "Stopping — resume later with the same command.",
                flush=True,
            )
            break

        pid = int(row["id"])
        title = str(row.get("title") or "").strip()
        n_white = len(row.get("white_urls") or [])
        n_life = len(row.get("lifestyle_urls") or [])
        if bg_lifestyle:
            print(
                f"[{i}/{len(rows)}] {title} (id={pid}, "
                f"{n_white} white + {n_life} lifestyle)...",
                flush=True,
            )
        else:
            print(
                f"[{i}/{len(rows)}] {title} (id={pid}, {n_white} white_urls)...",
                flush=True,
            )

        if args.delete_lifestyle_only and not white_urls_only and not bg_lifestyle:
            bg_source = "report"
            product = client.get_product(pid)
            img_map = _map_shopify_images(product)
            jobs = _actions_for_row(
                row, shopify_images=img_map, white_urls_only=False
            )
            jobs = [j for j in jobs if j.action == "delete_lifestyle"]
            for job in jobs:
                try:
                    client.delete_product_image(job.product_id, job.shopify_image_id)
                    job.done = True
                except Exception as exc:  # noqa: BLE001
                    job.error = str(exc)
                    print(f"      ERROR: {exc}", flush=True)
        else:
            jobs, bg_source = _process_one_product_bg(
                client,
                row,
                output_dir=args.output_dir,
                white_urls_only=white_urls_only,
                http_timeout=http_timeout,
                bg_lifestyle=bg_lifestyle,
            )
            if white_urls_only or bg_lifestyle:
                jobs = [j for j in jobs if j.action == "bg_remove"]

        all_actions.extend(jobs)
        admin_url = client.config.admin_product_url(pid)
        if not jobs:
            if white_urls_only or bg_lifestyle:
                skip_msg = (
                    "  done: live scan found no white+lifestyle images"
                    if bg_lifestyle
                    else "  done: live scan found no white images "
                    "(already no_bg or lifestyle only)"
                )
                print(skip_msg, flush=True)
                session_products_ok += 1
                done_ids.add(pid)
                if args.checkpoint:
                    _save_checkpoint(args.checkpoint, done_ids)
                if args.run_log:
                    _append_run_log(
                        args.run_log,
                        {
                            "product_id": pid,
                            "title": title,
                            "category": row.get("category"),
                            "images_ok": 0,
                            "credits": 0,
                            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "actions": [],
                            "url": admin_url,
                            "bg_source": "none",
                            "note": "no_targets_remaining",
                        },
                    )
            else:
                print("  skip: no matching images on live Shopify", flush=True)
            continue

        product_ok = all(j.done for j in jobs)
        if product_ok:
            session_products_ok += 1
            done_ids.add(pid)
            if args.checkpoint:
                _save_checkpoint(args.checkpoint, done_ids)
            if args.run_log:
                _append_run_log(
                    args.run_log,
                    {
                        "product_id": pid,
                        "title": title,
                        "category": row.get("category"),
                        "images_ok": len(jobs),
                        "credits": sum(j.credits for j in jobs),
                        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "actions": [_action_to_dict(j) for j in jobs],
                        "url": admin_url,
                        "bg_source": bg_source,
                    },
                )
            print(
                f"  OK {len(jobs)} image(s) — checkpoint {len(done_ids)} total",
                flush=True,
            )
            if args.open_admin:
                webbrowser.open(admin_url, new=2)
                print(f"  Opened: {admin_url}", flush=True)
            if args.review_pause:
                try:
                    input("  Press Enter when done reviewing (next product)... ")
                except EOFError:
                    pass
        else:
            session_products_fail += 1
            print("  PARTIAL FAIL — not checkpointed (will retry next run)", flush=True)

    ok = sum(1 for a in all_actions if a.done)
    fail = sum(1 for a in all_actions if a.error)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "session_products_ok": session_products_ok,
                "session_products_fail": session_products_fail,
                "checkpoint_total_done": len(done_ids),
                "stopped_time_limit": stopped_time_limit,
                "bg_remove": sum(1 for a in all_actions if a.action == "bg_remove"),
                "ok": ok,
                "failed": fail,
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
                "actions": [
                    {
                        "product_id": a.product_id,
                        "title": a.product_title,
                        "category": a.product_category,
                        "action": a.action,
                        "shopify_image_id": a.shopify_image_id,
                        "source_url": a.source_url,
                        "done": a.done,
                        "credits": a.credits,
                        "error": a.error,
                    }
                    for a in all_actions
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print()
    print(f"Session: {session_products_ok} products OK, {session_products_fail} failed")
    print(f"Images: {ok} ok, {fail} failed")
    print(f"Checkpoint total done: {len(done_ids)} products")
    print(f"Session report: {args.report}")
    if args.run_log:
        print(f"Live log: {args.run_log}")
    if args.checkpoint:
        print(f"Checkpoint: {args.checkpoint}")
    if stopped_time_limit or (done_ids and len(done_ids) < total_with_white + len(done_products)):
        print()
        print("Resume tomorrow — same command, already-done products are skipped.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
