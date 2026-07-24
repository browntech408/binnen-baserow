"""Draft Shopify products in the lifestyle category (room/scene images).

Usage:
  python shopify_draft_lifestyle.py              # preview (no changes)
  python shopify_draft_lifestyle.py --apply      # draft lifestyle products
  python shopify_draft_lifestyle.py --apply --workers 24
"""
from __future__ import annotations

import sys

from shopify_draft_bg_products import main

if __name__ == "__main__":
    sys.exit(main())
