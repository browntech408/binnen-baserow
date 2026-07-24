"""Check how many deleted lifestyle CDN URLs are still accessible."""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

REPORT = Path("output/shopify_delete_lifestyle_report.json")
OUT = Path("output/shopify_deleted_urls_check.json")


def check(url: str) -> tuple[str, int, str]:
    try:
        r = requests.head(url, timeout=20, allow_redirects=True)
        if r.status_code == 405:
            r = requests.get(
                url, timeout=20, stream=True, headers={"Range": "bytes=0-1"}
            )
        return url, r.status_code, ""
    except Exception as exc:  # noqa: BLE001
        return url, 0, str(exc)


def main() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    jobs = [j for j in report["jobs"] if j.get("deleted") and j.get("source_url")]
    unique_urls = list({j["source_url"].split("?")[0] for j in jobs})

    print(f"Deleted jobs: {len(jobs)}")
    print(f"Unique URLs: {len(unique_urls)}")
    print("Checking (30 workers)...")

    ok: list[str] = []
    bad: list[dict] = []

    with ThreadPoolExecutor(max_workers=30) as pool:
        futs = {pool.submit(check, u): u for u in unique_urls}
        for i, fut in enumerate(as_completed(futs), 1):
            url, status, err = fut.result()
            if status == 200:
                ok.append(url)
            else:
                bad.append({"url": url, "status": status, "error": err})
            if i % 200 == 0:
                print(f"  {i}/{len(unique_urls)}")

    print()
    print("=== RESULT ===")
    print(f"Accessible (HTTP 200): {len(ok)}")
    print(f"Not accessible:        {len(bad)}")
    print(f"Restore possible:      {len(ok)}/{len(unique_urls)} ({100*len(ok)/len(unique_urls):.1f}%)")

    if bad:
        print("Status breakdown:", dict(Counter(b["status"] for b in bad)))
        print("Sample failures:")
        for b in bad[:8]:
            print(f"  [{b['status']}] {b['url'][:110]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "total_jobs": len(jobs),
                "unique_urls": len(unique_urls),
                "accessible_200": len(ok),
                "not_accessible": len(bad),
                "accessible_urls": ok,
                "failed": bad,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
