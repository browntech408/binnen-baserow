"""Continuous Sync Daemon for Binnen OS.

Runs Baserow -> Shopify product & score sync automatically every 10 seconds in the background with live streaming logs.
"""
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime

INTERVAL_SECONDS = 10
BASE_DIR = Path(__file__).parent.resolve()

def run_sync_once():
    """Run one pass of baserow_shopify_sync.py and update_score_baserow_to_shopify.py with unbuffered live logs."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    python_bin = sys.executable

    # 1. Main Product & Metafield Sync (Live Streaming Logs)
    print(f"\n[{timestamp}] === Starting Baserow -> Shopify Product Sync ===", flush=True)
    cmd_products = [python_bin, "-u", str(BASE_DIR / "baserow_shopify_sync.py"), "--apply"]
    try:
        res1 = subprocess.run(cmd_products, cwd=str(BASE_DIR), timeout=300)
        if res1.returncode == 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Product sync finished.", flush=True)
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Product sync exited with code {res1.returncode}.", flush=True)
    except Exception as exc:
        print(f"[{timestamp}] Product sync error: {exc}", flush=True)

    # 2. Standalone Score Metafield Sync (Live Streaming Logs)
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] === Starting Baserow -> Shopify Score Sync ===", flush=True)
    cmd_scores = [python_bin, "-u", str(BASE_DIR / "update_score_baserow_to_shopify.py"), "--all"]
    try:
        res2 = subprocess.run(cmd_scores, cwd=str(BASE_DIR), timeout=300)
        if res2.returncode == 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Score sync finished.", flush=True)
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Score sync exited with code {res2.returncode}.", flush=True)
    except Exception as exc:
        print(f"[{timestamp}] Score sync error: {exc}", flush=True)

def main():
    print(f"=== Binnen Continuous Sync Daemon Started (Interval: {INTERVAL_SECONDS}s) ===", flush=True)
    while True:
        try:
            run_sync_once()
        except Exception as exc:
            print(f"Daemon iteration error: {exc}", flush=True)
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Waiting {INTERVAL_SECONDS} seconds before next sync...\n", flush=True)
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSync Daemon stopped by user.", flush=True)
        sys.exit(0)
