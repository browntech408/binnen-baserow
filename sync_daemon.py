"""Continuous Sync Daemon for Binnen OS.

Runs Baserow -> Shopify sync automatically every 10 seconds in the background.
"""
import sys
import time
import subprocess
from datetime import datetime

INTERVAL_SECONDS = 10

def run_sync_once():
    """Run one pass of baserow_shopify_sync.py --apply."""
    cmd = [sys.executable, "baserow_shopify_sync.py", "--apply"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Starting Baserow -> Shopify auto-sync...")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print(f"[{timestamp}] Sync completed successfully.")
        else:
            print(f"[{timestamp}] Sync finished with errors:\n{result.stderr}")
    except subprocess.TimeoutExpired:
        print(f"[{timestamp}] Sync process timed out after 120 seconds.")
    except Exception as exc:
        print(f"[{timestamp}] Unexpected error during sync execution: {exc}")

def main():
    print(f"=== Binnen Continuous Sync Daemon Started (Interval: {INTERVAL_SECONDS}s) ===")
    while True:
        run_sync_once()
        print(f"Waiting {INTERVAL_SECONDS} seconds before next sync iteration...\n")
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSync Daemon stopped by user.")
        sys.exit(0)
