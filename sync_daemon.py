"""Continuous Score Auto-Sync Daemon for Binnen OS.

Only runs Score Auto-Sync from Baserow -> Shopify every 10 seconds.
"""
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime

INTERVAL_SECONDS = 10
BASE_DIR = Path(__file__).parent.resolve()

def run_score_sync_once():
    """Run one pass of update_score_baserow_to_shopify.py --all."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    python_bin = sys.executable

    print(f"[{timestamp}] === Starting Baserow -> Shopify Score Auto-Sync ===", flush=True)
    cmd_scores = [python_bin, "-u", str(BASE_DIR / "update_score_baserow_to_shopify.py"), "--all"]
    try:
        res = subprocess.run(cmd_scores, cwd=str(BASE_DIR), timeout=120)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Score sync pass completed (Exit Code: {res.returncode}).", flush=True)
    except Exception as exc:
        print(f"[{timestamp}] Score sync error: {exc}", flush=True)

def main():
    print(f"=== Binnen Score Auto-Sync Daemon Active (Interval: {INTERVAL_SECONDS}s) ===", flush=True)
    while True:
        try:
            run_score_sync_once()
        except Exception as exc:
            print(f"Daemon error: {exc}", flush=True)
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Waiting {INTERVAL_SECONDS} seconds before next score sync...\n", flush=True)
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScore Sync Daemon stopped.", flush=True)
        sys.exit(0)
