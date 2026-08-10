"""
Expand lifestyle product images via Claid.ai outpaint (Phase 2 Day 2).

Does NOT modify Shopify/Baserow by default — downloads results to output/.

Setup:
  1. Add to .env:
       CLAID_API_KEY=your_claid_key
  2. Test one URL:
       python claid_expand_lifestyle.py --url "https://cdn.shopify.com/...jpg"
  3. Expand lifestyle labels from AI suggest report:
       python claid_expand_lifestyle.py --from-report output/shopify_ai_image_suggest_latest10_gpt41.json --limit 5

Optional canvas mode (fixed square):
  python claid_expand_lifestyle.py --url "..." --width 2000 --height 2000
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from claid_client import ClaidClient, load_claid_settings


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (text or "").strip())[:60].strip("_") or "image"


def _download(url: str, dest: Path, timeout: float = 120) -> None:
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        for chunk in resp.iter_content(65536):
            if chunk:
                handle.write(chunk)


def _jobs_from_report(path: Path, *, limit: int) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    jobs: list[dict[str, Any]] = []
    for product in data.get("products") or []:
        title = str(product.get("title") or "")
        pid = product.get("product_id")
        for img in product.get("images") or []:
            if str(img.get("label") or "").lower() != "lifestyle":
                continue
            src = str(img.get("src") or "").strip()
            if not src:
                continue
            jobs.append(
                {
                    "product_id": pid,
                    "title": title,
                    "position": img.get("position"),
                    "image_id": img.get("image_id"),
                    "src": src,
                    "label": img.get("label"),
                    "confidence": img.get("confidence"),
                }
            )
            if limit > 0 and len(jobs) >= limit:
                return jobs
    return jobs


def _jobs_from_urls(urls: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, url in enumerate(urls, 1):
        u = url.strip()
        if not u:
            continue
        out.append(
            {
                "product_id": None,
                "title": f"url_{i}",
                "position": i,
                "image_id": None,
                "src": u,
                "label": "lifestyle",
                "confidence": None,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Claid.ai lifestyle image expansion (outpaint)"
    )
    parser.add_argument("--url", action="append", default=[], help="Image URL (repeatable)")
    parser.add_argument(
        "--from-report",
        type=Path,
        help="AI suggest JSON; only images labeled lifestyle are expanded",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max images (0 = all)")
    parser.add_argument(
        "--outpaint-by",
        default=None,
        help='Expand amount, e.g. "15%%" or "100px 50px" (default from env)',
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Fixed canvas width (with --height uses canvas outpaint mode)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Fixed canvas height (with --width uses canvas outpaint mode)",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("output") / "claid_expanded",
        help="Folder for downloaded expanded JPEGs",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output") / "claid_expand_report.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List jobs only; do not call Claid",
    )
    args = parser.parse_args()

    jobs: list[dict[str, Any]] = []
    if args.from_report:
        if not args.from_report.exists():
            print(f"Report not found: {args.from_report}", file=sys.stderr)
            return 1
        jobs.extend(_jobs_from_report(args.from_report, limit=args.limit))
    if args.url:
        jobs.extend(_jobs_from_urls(args.url))
        if args.limit > 0:
            jobs = jobs[: args.limit]

    if not jobs:
        print(
            "No images to expand. Use --url and/or --from-report "
            "(lifestyle labels only).",
            file=sys.stderr,
        )
        return 1

    canvas_mode = args.width is not None and args.height is not None
    if (args.width is None) ^ (args.height is None):
        print("Provide both --width and --height for canvas mode.", file=sys.stderr)
        return 1

    print(f"Jobs: {len(jobs)}")
    print(f"Mode: {'canvas outpaint' if canvas_mode else 'zoom-out outpaint_by'}")
    if args.dry_run:
        for j in jobs:
            print(f"  - {j.get('title')} pos={j.get('position')} {j['src'][:90]}")
        print("Dry-run only — no Claid calls.")
        return 0

    try:
        settings = load_claid_settings()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    client = ClaidClient(settings)
    args.outdir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "canvas" if canvas_mode else "outpaint_by",
        "outpaint_by": args.outpaint_by or settings.outpaint_by,
        "width": args.width,
        "height": args.height,
        "results": [],
        "ok": 0,
        "failed": 0,
    }

    for idx, job in enumerate(jobs, 1):
        src = job["src"]
        title = str(job.get("title") or "image")
        print(f"[{idx}/{len(jobs)}] {title} pos={job.get('position')}")
        print(f"  in: {src[:100]}")
        entry: dict[str, Any] = {
            **job,
            "output_url": "",
            "local_path": "",
            "output_width": None,
            "output_height": None,
            "error": "",
        }
        try:
            if canvas_mode:
                result = client.expand_to_canvas(
                    src, width=int(args.width), height=int(args.height)
                )
            else:
                result = client.expand_lifestyle_url(
                    src, outpaint_by=args.outpaint_by
                )
            entry["output_url"] = result.output_url
            entry["output_width"] = result.width
            entry["output_height"] = result.height

            stem = _safe_name(
                f"{job.get('product_id') or 'x'}_{title}_p{job.get('position') or idx}"
            )
            local = args.outdir / f"{stem}.jpg"
            _download(result.output_url, local, timeout=settings.timeout)
            entry["local_path"] = str(local)
            report["ok"] += 1
            print(f"  out: {result.output_url[:100]}")
            print(f"  saved: {local}")
        except Exception as exc:
            entry["error"] = str(exc)[:500]
            report["failed"] += 1
            print(f"  ERROR: {exc}", file=sys.stderr)

        report["results"].append(entry)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDone: ok={report['ok']} failed={report['failed']}")
    print(f"Report: {args.report.resolve()}")
    print(f"Files:  {args.outdir.resolve()}")
    return 0 if report["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
