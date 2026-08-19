"""
Baserow Database 3 to Database 2 Safe Migration Tool.

Migrates data in the proper relational order:
1. Brands (Table 3 -> Table 2) -> maps old Brand IDs to new Brand IDs
2. Categories & Subcategories (Table 3 -> Table 2) -> maps old Category/Subcategory IDs
3. Products (productsDetails Table 3 -> Table 2) -> remaps all fields & relations,
   copies all file fields, detects duplicates, and logs a comprehensive report.

Usage:
  # Test run (Dry Run - no changes made to DB2):
  python migrate_database.py --source-brands <ID> --source-categories <ID> --source-subcategories <ID> --source-products <ID> --dry-run

  # Full migration:
  python migrate_database.py --source-brands <ID> --source-categories <ID> --source-subcategories <ID> --source-products <ID>

  # Test with limit (e.g. 5 products):
  python migrate_database.py --source-brands <ID> --source-categories <ID> --source-subcategories <ID> --source-products <ID> --limit 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate_db")

# Read-only Baserow field types that cannot be written via API
READ_ONLY_FIELD_TYPES = {
    "formula",
    "lookup",
    "rollup",
    "created_on",
    "last_modified",
    "uuid",
    "autonumber",
    "count",
}


def norm_str(s: Any) -> str:
    """Normalize string for fuzzy/case-insensitive matching."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def norm_url(url: str) -> str:
    """Normalize URL for duplicate detection (strip query params, trailing slashes, www)."""
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u)
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = p.path.rstrip("/").lower()
        if not host:
            return path
        return f"{p.scheme}://{host}{path}"
    except Exception:
        return u.strip().lower().rstrip("/")


class BaserowAPI:
    """Lightweight API client for interacting with Baserow tables."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api"
        self.token = token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
            }
        )

    def check_token(self) -> bool:
        try:
            r = self.session.get(f"{self.api_url}/database/tokens/check/", timeout=30)
            return r.status_code == 200
        except Exception:
            return False

    def get_fields(self, table_id: int) -> list[dict[str, Any]]:
        r = self.session.get(f"{self.api_url}/database/fields/table/{table_id}/", timeout=60)
        r.raise_for_status()
        return r.json()

    def iter_all_rows(
        self, table_id: int, page_size: int = 100, user_field_names: bool = False
    ) -> Iterator[dict[str, Any]]:
        """Yield rows page by page with streaming."""
        page = 1
        params: dict[str, Any] = {"size": page_size}
        if user_field_names:
            params["user_field_names"] = "true"

        while True:
            params["page"] = page
            r = self.session.get(
                f"{self.api_url}/database/rows/table/{table_id}/",
                params=params,
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("results", [])
            for row in results:
                yield row
            if not data.get("next") or not results:
                break
            page += 1

    def list_all_rows(
        self, table_id: int, page_size: int = 100, user_field_names: bool = False
    ) -> list[dict[str, Any]]:
        """Fetch all rows handling pagination."""
        return list(self.iter_all_rows(table_id, page_size=page_size, user_field_names=user_field_names))

    def create_row(
        self, table_id: int, data: dict[str, Any], user_field_names: bool = False
    ) -> dict[str, Any]:
        params = {"user_field_names": "true"} if user_field_names else {}
        r = self.session.post(
            f"{self.api_url}/database/rows/table/{table_id}/",
            params=params,
            json=data,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def update_row(
        self, table_id: int, row_id: int, data: dict[str, Any], user_field_names: bool = False
    ) -> dict[str, Any]:
        params = {"user_field_names": "true"} if user_field_names else {}
        r = self.session.patch(
            f"{self.api_url}/database/rows/table/{table_id}/{row_id}/",
            params=params,
            json=data,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def upload_file_via_url(self, file_url: str) -> str:
        r = self.session.post(
            f"{self.api_url}/user-files/upload-via-url/",
            json={"url": file_url},
            timeout=120,
        )
        r.raise_for_status()
        name = r.json().get("name")
        if not name:
            raise ValueError(f"Failed to get file name token from upload response: {r.json()}")
        return str(name)


class DatabaseMigrator:
    """Manages the full migration pipeline from Source DB to Target DB."""

    def __init__(
        self,
        source_api: BaserowAPI,
        target_api: BaserowAPI,
        source_tables: dict[str, int],
        target_tables: dict[str, int],
        dry_run: bool = False,
        limit: int | None = None,
        brand_filter: str | None = None,
    ):
        self.src = source_api
        self.tgt = target_api
        self.src_tables = source_tables
        self.tgt_tables = target_tables
        self.dry_run = dry_run
        self.limit = limit
        self.brand_filter = brand_filter

        # ID Mapping Dictionaries
        self.brand_map: dict[int, int] = {}  # src_brand_id -> tgt_brand_id
        self.category_map: dict[int, int] = {}  # src_cat_id -> tgt_cat_id
        self.subcategory_map: dict[int, int] = {}  # src_subcat_id -> tgt_subcat_id
        self.auxiliary_link_maps: dict[str, dict[int, int]] = {}  # field_name -> {src_row_id: tgt_row_id}

        # Target Field Schema Caches
        self.tgt_product_fields_by_name: dict[str, dict[str, Any]] = {}
        self.tgt_product_fields_by_id: dict[str, dict[str, Any]] = {}
        self.src_product_fields_by_name: dict[str, dict[str, Any]] = {}

        # Existing items in Target DB for duplicate detection
        self.existing_product_urls: set[str] = set()
        self.existing_product_names: set[tuple[str, int | None]] = set()  # (norm_name, brand_id)
        self.existing_target_products_map: dict[str, int] = {}  # norm_url -> tgt_row_id

        # Reporting statistics
        self.report: dict[str, Any] = {
            "started_at": datetime.now().isoformat(),
            "dry_run": self.dry_run,
            "brands": {"total_source": 0, "created": 0, "matched_existing": 0},
            "categories": {"total_source": 0, "created": 0, "matched_existing": 0},
            "subcategories": {"total_source": 0, "created": 0, "matched_existing": 0},
            "products": {
                "total_source": 0,
                "migrated": 0,
                "duplicates_skipped": [],
                "errors": [],
            },
        }

    def prepare_schemas(self) -> None:
        """Fetch and cache schemas of target tables and auto-map auxiliary link_rows."""
        logger.info("Inspecting Target DB product table fields...")
        tgt_fields = self.tgt.get_fields(self.tgt_tables["products"])
        for f in tgt_fields:
            fname = f["name"]
            fid = f"field_{f['id']}"
            self.tgt_product_fields_by_name[fname] = f
            self.tgt_product_fields_by_id[fid] = f
        logger.info(f"Target DB products table has {len(tgt_fields)} fields.")

        logger.info("Inspecting Source DB product table fields...")
        src_fields = self.src.get_fields(self.src_tables["products"])
        for f in src_fields:
            fname = f["name"]
            self.src_product_fields_by_name[fname] = f

        # Auto-map auxiliary link_row fields (styleType, stores, productTrend, etc.)
        for f in src_fields:
            if f["type"] == "link_row":
                fname = f["name"]
                src_link_table = f.get("link_row_table_id")
                tgt_field = self.tgt_product_fields_by_name.get(fname)
                if not tgt_field or tgt_field["type"] != "link_row":
                    continue
                tgt_link_table = tgt_field.get("link_row_table_id")
                if not src_link_table or not tgt_link_table:
                    continue

                if fname.lower() in ("brand_table", "brands", "product_category", "sub_category"):
                    continue

                try:
                    s_rows = self.src.list_all_rows(src_link_table, user_field_names=True)
                    t_rows = self.tgt.list_all_rows(tgt_link_table, user_field_names=True)
                    t_row_by_name = {}
                    for tr in t_rows:
                        val = tr.get("name") or tr.get("Name")
                        if not val:
                            for k, v in tr.items():
                                if k not in ("id", "order") and isinstance(v, str) and v.strip():
                                    val = v
                                    break
                        if val:
                            t_row_by_name[norm_str(val)] = tr["id"]

                    field_map = {}
                    for sr in s_rows:
                        s_id = sr["id"]
                        val = sr.get("name") or sr.get("Name")
                        if not val:
                            for k, v in sr.items():
                                if k not in ("id", "order") and isinstance(v, str) and v.strip():
                                    val = v
                                    break
                        if val and norm_str(val) in t_row_by_name:
                            field_map[s_id] = t_row_by_name[norm_str(val)]
                        elif s_id in [tr["id"] for tr in t_rows]:
                            field_map[s_id] = s_id

                    self.auxiliary_link_maps[fname] = field_map
                    logger.info(f"Auto-mapped {len(field_map)} rows for link_row '{fname}'.")
                except Exception as exc:
                    logger.warning(f"Could not auto-map link_row '{fname}': {exc}")

    # -------------------------------------------------------------
    # STAGE 1: BRANDS MIGRATION
    # -------------------------------------------------------------
    def migrate_brands(self) -> None:
        logger.info("=== STAGE 1: Migrating Brands ===")
        src_table = self.src_tables["brands"]
        tgt_table = self.tgt_tables["brands"]

        logger.info(f"Fetching existing brands from Target DB (table {tgt_table})...")
        tgt_brands = self.tgt.list_all_rows(tgt_table, user_field_names=True)
        tgt_brand_by_name: dict[str, dict[str, Any]] = {}
        for row in tgt_brands:
            bname = row.get("Brand name") or row.get("Name") or row.get("name") or row.get("Brand")
            if not bname:
                for k, v in row.items():
                    if "brand" in k.lower() and isinstance(v, str) and v.strip():
                        bname = v
                        break
            if bname:
                tgt_brand_by_name[norm_str(bname)] = row

        logger.info(f"Fetching source brands from Source DB (table {src_table})...")
        src_brands = self.src.list_all_rows(src_table, user_field_names=True)
        self.report["brands"]["total_source"] = len(src_brands)

        for s_row in src_brands:
            src_id = s_row["id"]
            bname = s_row.get("Brand name") or s_row.get("Name") or s_row.get("name") or s_row.get("Brand")
            if not bname:
                for k, v in s_row.items():
                    if "brand" in k.lower() and isinstance(v, str) and v.strip():
                        bname = v
                        break

            if not bname:
                logger.warning(f"Source brand row {src_id} has no name. Skipping.")
                continue

            bname_clean = str(bname).strip()
            norm_bname = norm_str(bname_clean)

            if norm_bname in tgt_brand_by_name:
                tgt_id = tgt_brand_by_name[norm_bname]["id"]
                self.brand_map[src_id] = tgt_id
                self.report["brands"]["matched_existing"] += 1
                logger.info(f"Brand '{bname_clean}' already exists in Target DB (ID: {tgt_id}). Mapped.")
            else:
                if self.dry_run:
                    mock_id = 900000 + src_id
                    self.brand_map[src_id] = mock_id
                    self.report["brands"]["created"] += 1
                    logger.info(f"[DRY-RUN] Would create Brand '{bname_clean}' in Target DB.")
                else:
                    new_brand_payload: dict[str, Any] = {}
                    for k, v in s_row.items():
                        if k in ("id", "order") or k.startswith("field_"):
                            continue
                        if v is not None and not isinstance(v, (list, dict)):
                            new_brand_payload[k] = v

                    if "Brand name" not in new_brand_payload and "name" not in new_brand_payload:
                        new_brand_payload["Brand name"] = bname_clean

                    try:
                        created = self.tgt.create_row(tgt_table, new_brand_payload, user_field_names=True)
                        tgt_id = created["id"]
                        self.brand_map[src_id] = tgt_id
                        tgt_brand_by_name[norm_bname] = created
                        self.report["brands"]["created"] += 1
                        logger.info(f"Created Brand '{bname_clean}' in Target DB (ID: {tgt_id}). Mapped.")
                    except Exception as exc:
                        logger.error(f"Failed to create Brand '{bname_clean}': {exc}")

        logger.info(
            f"Brands Migration Done: {self.report['brands']['created']} created, "
            f"{self.report['brands']['matched_existing']} matched existing."
        )

    # -------------------------------------------------------------
    # STAGE 2: CATEGORIES & SUBCATEGORIES MIGRATION
    # -------------------------------------------------------------
    def migrate_categories_and_subcategories(self) -> None:
        logger.info("=== STAGE 2: Migrating Categories & Subcategories ===")
        src_cat_table = self.src_tables["categories"]
        tgt_cat_table = self.tgt_tables["categories"]
        src_sub_table = self.src_tables["subcategories"]
        tgt_sub_table = self.tgt_tables["subcategories"]

        # 1. Categories
        logger.info("Syncing Top-Level Categories...")
        tgt_cats = self.tgt.list_all_rows(tgt_cat_table, user_field_names=True)
        tgt_cats_by_name: dict[str, dict[str, Any]] = {}
        for row in tgt_cats:
            cname = row.get("Category_name") or row.get("name") or row.get("Name")
            if not cname:
                for k, v in row.items():
                    if "category" in k.lower() and isinstance(v, str) and v.strip():
                        cname = v
                        break
            if cname:
                tgt_cats_by_name[norm_str(cname)] = row

        src_cats = self.src.list_all_rows(src_cat_table, user_field_names=True)
        self.report["categories"]["total_source"] = len(src_cats)

        for s_row in src_cats:
            src_id = s_row["id"]
            cname = s_row.get("Category_name") or s_row.get("name") or s_row.get("Name")
            if not cname:
                for k, v in s_row.items():
                    if "category" in k.lower() and isinstance(v, str) and v.strip():
                        cname = v
                        break
            if not cname:
                continue

            cname_clean = str(cname).strip()
            norm_cname = norm_str(cname_clean)

            if norm_cname in tgt_cats_by_name:
                tgt_id = tgt_cats_by_name[norm_cname]["id"]
                self.category_map[src_id] = tgt_id
                self.report["categories"]["matched_existing"] += 1
            else:
                if self.dry_run:
                    mock_id = 800000 + src_id
                    self.category_map[src_id] = mock_id
                    self.report["categories"]["created"] += 1
                else:
                    payload = {"Category_name": cname_clean}
                    try:
                        created = self.tgt.create_row(tgt_cat_table, payload, user_field_names=True)
                        tgt_id = created["id"]
                        self.category_map[src_id] = tgt_id
                        tgt_cats_by_name[norm_cname] = created
                        self.report["categories"]["created"] += 1
                    except Exception as exc:
                        logger.error(f"Failed to create category '{cname_clean}': {exc}")

        # 2. Subcategories
        logger.info("Syncing Subcategories...")
        tgt_subs = self.tgt.list_all_rows(tgt_sub_table, user_field_names=True)
        tgt_subs_by_name: dict[str, dict[str, Any]] = {}
        for row in tgt_subs:
            sname = row.get("Sub_Category_name") or row.get("name") or row.get("Name")
            if not sname:
                for k, v in row.items():
                    if "sub" in k.lower() and isinstance(v, str) and v.strip():
                        sname = v
                        break
            if sname:
                tgt_subs_by_name[norm_str(sname)] = row

        src_subs = self.src.list_all_rows(src_sub_table, user_field_names=True)
        self.report["subcategories"]["total_source"] = len(src_subs)

        for s_row in src_subs:
            src_id = s_row["id"]
            sname = s_row.get("Sub_Category_name") or s_row.get("name") or s_row.get("Name")
            if not sname:
                for k, v in s_row.items():
                    if "sub" in k.lower() and isinstance(v, str) and v.strip():
                        sname = v
                        break
            if not sname:
                continue

            sname_clean = str(sname).strip()
            norm_sname = norm_str(sname_clean)

            parent_link = None
            for k, v in s_row.items():
                if ("parent" in k.lower() or "category" in k.lower()) and isinstance(v, list) and v:
                    old_parent_id = v[0].get("id") if isinstance(v[0], dict) else v[0]
                    if old_parent_id in self.category_map:
                        parent_link = [self.category_map[old_parent_id]]
                        break

            if norm_sname in tgt_subs_by_name:
                tgt_id = tgt_subs_by_name[norm_sname]["id"]
                self.subcategory_map[src_id] = tgt_id
                self.report["subcategories"]["matched_existing"] += 1
            else:
                if self.dry_run:
                    mock_id = 700000 + src_id
                    self.subcategory_map[src_id] = mock_id
                    self.report["subcategories"]["created"] += 1
                else:
                    payload = {"Sub_Category_name": sname_clean}
                    if parent_link:
                        payload["Parent_category"] = parent_link

                    try:
                        created = self.tgt.create_row(tgt_sub_table, payload, user_field_names=True)
                        tgt_id = created["id"]
                        self.subcategory_map[src_id] = tgt_id
                        tgt_subs_by_name[norm_sname] = created
                        self.report["subcategories"]["created"] += 1
                    except Exception as exc:
                        logger.error(f"Failed to create subcategory '{sname_clean}': {exc}")

        logger.info("Categories & Subcategories Migration Done.")

    # -------------------------------------------------------------
    # STAGE 3: PRODUCTS MIGRATION & DUPLICATE DETECTION
    # -------------------------------------------------------------
    def _build_existing_target_products_index(self) -> None:
        tgt_table = self.tgt_tables["products"]
        logger.info(f"Indexing existing products in Target DB (table {tgt_table})...")
        count = 0
        for row in self.tgt.iter_all_rows(tgt_table, page_size=100, user_field_names=True):
            count += 1
            purl = row.get("product_url") or row.get("Product URL") or ""
            pname = row.get("product_name") or row.get("Product Name") or ""
            brand_links = row.get("Brand_table") or row.get("brands") or []
            brand_id = brand_links[0].get("id") if (brand_links and isinstance(brand_links[0], dict)) else None

            if purl:
                nurl = norm_url(purl)
                self.existing_product_urls.add(nurl)
                self.existing_target_products_map[nurl] = row["id"]

            if pname:
                self.existing_product_names.add((norm_str(pname), brand_id))

            if count % 200 == 0:
                logger.info(f"Indexed {count} existing products from Target DB...")

        logger.info(f"Total {count} existing products in Target DB indexed for duplicate detection.")

    def migrate_products(self) -> None:
        logger.info("=== STAGE 3: Migrating Products & Images ===")
        self._build_existing_target_products_index()

        src_table = self.src_tables["products"]
        tgt_table = self.tgt_tables["products"]

        logger.info(f"Streaming products from Source DB (table {src_table})...")
        count = 0
        for s_product in self.src.iter_all_rows(src_table, page_size=100, user_field_names=True):
            self.report["products"]["total_source"] += 1

            if self.limit and count >= self.limit:
                logger.info(f"Limit of {self.limit} reached. Stopping product migration.")
                break

            src_row_id = s_product.get("id")
            pname = s_product.get("product_name") or s_product.get("Product Name") or f"Product #{src_row_id}"
            purl = s_product.get("product_url") or s_product.get("Product URL") or ""

            # Check Brand filtering
            src_brand_links = s_product.get("Brand_table") or s_product.get("brands") or []
            src_brand_id = (
                src_brand_links[0].get("id")
                if (src_brand_links and isinstance(src_brand_links[0], dict))
                else None
            )

            tgt_brand_id = self.brand_map.get(src_brand_id) if src_brand_id else None

            # -------------------------------------------------------------
            # DUPLICATE DETECTION & REPORTING
            # -------------------------------------------------------------
            is_duplicate = False
            dup_reason = ""

            if purl and norm_url(purl) in self.existing_product_urls:
                is_duplicate = True
                dup_reason = f"Duplicate Product URL ({purl}) already exists in Target DB"
            elif pname and (norm_str(pname), tgt_brand_id) in self.existing_product_names:
                is_duplicate = True
                dup_reason = f"Duplicate Product Name ('{pname}') under same Brand already exists in Target DB"

            if is_duplicate:
                dup_entry = {
                    "source_row_id": src_row_id,
                    "product_name": pname,
                    "product_url": purl,
                    "reason": dup_reason,
                }
                self.report["products"]["duplicates_skipped"].append(dup_entry)
                logger.warning(f"[DUPLICATE SKIPPED] Row #{src_row_id} '{pname}': {dup_reason}")
                continue

            # -------------------------------------------------------------
            # FIELD-BY-FIELD MAPPING & RELATION REMAPPING
            # -------------------------------------------------------------
            new_payload: dict[str, Any] = {}

            for field_name, val in s_product.items():
                if field_name in ("id", "order") or val is None:
                    continue

                target_field_info = self.tgt_product_fields_by_name.get(field_name)
                if not target_field_info:
                    for t_name, t_info in self.tgt_product_fields_by_name.items():
                        if norm_str(t_name) == norm_str(field_name):
                            target_field_info = t_info
                            field_name = t_name
                            break

                if not target_field_info:
                    continue

                ftype = target_field_info.get("type")

                if ftype in READ_ONLY_FIELD_TYPES:
                    continue

                # 1. Link Row Fields (Foreign Keys)
                if ftype == "link_row":
                    if not isinstance(val, list) or not val:
                        continue

                    raw_ids = [item.get("id") if isinstance(item, dict) else item for item in val]

                    if "brand" in field_name.lower():
                        mapped_ids = [self.brand_map[old_id] for old_id in raw_ids if old_id in self.brand_map]
                        if mapped_ids:
                            new_payload[field_name] = mapped_ids
                    elif "sub" in field_name.lower() or "subcategory" in field_name.lower():
                        mapped_ids = [
                            self.subcategory_map[old_id]
                            for old_id in raw_ids
                            if old_id in self.subcategory_map
                        ]
                        if mapped_ids:
                            new_payload[field_name] = mapped_ids
                    elif "category" in field_name.lower():
                        mapped_ids = [
                            self.category_map[old_id]
                            for old_id in raw_ids
                            if old_id in self.category_map
                        ]
                        if mapped_ids:
                            new_payload[field_name] = mapped_ids
                    elif field_name in self.auxiliary_link_maps:
                        fmap = self.auxiliary_link_maps[field_name]
                        mapped_ids = [fmap[old_id] for old_id in raw_ids if old_id in fmap]
                        if mapped_ids:
                            new_payload[field_name] = mapped_ids
                    else:
                        pass

                # 2. File / Image Fields
                elif ftype == "file":
                    if not isinstance(val, list) or not val:
                        continue
                    file_tokens = []
                    for f in val:
                        if isinstance(f, dict) and f.get("name"):
                            file_tokens.append({"name": f["name"]})
                    if file_tokens:
                        new_payload[field_name] = file_tokens

                # 3. Single Select
                elif ftype == "single_select":
                    if isinstance(val, dict):
                        new_payload[field_name] = val.get("value")
                    elif isinstance(val, (str, int)):
                        new_payload[field_name] = val

                # 4. Multiple Select
                elif ftype == "multiple_select":
                    if isinstance(val, list):
                        new_payload[field_name] = [
                            item.get("value") if isinstance(item, dict) else item for item in val
                        ]

                # 5. Standard Primitive Fields (text, long_text, number, boolean, date, url, etc.)
                else:
                    new_payload[field_name] = val

            if tgt_brand_id:
                new_payload["Brand_table"] = [tgt_brand_id]

            # -------------------------------------------------------------
            # WRITE TO TARGET DATABASE
            # -------------------------------------------------------------
            if self.dry_run:
                logger.info(
                    f"[DRY-RUN] Would migrate product '{pname}' (Source Row #{src_row_id}) "
                    f"with {len(new_payload)} mapped fields."
                )
                self.report["products"]["migrated"] += 1
                if purl:
                    self.existing_product_urls.add(norm_url(purl))
                if pname:
                    self.existing_product_names.add((norm_str(pname), tgt_brand_id))
            else:
                try:
                    created_row = self.tgt.create_row(
                        tgt_table, new_payload, user_field_names=True
                    )
                    new_id = created_row["id"]
                    self.report["products"]["migrated"] += 1
                    logger.info(
                        f"✓ Migrated Product '{pname}' (Src #{src_row_id} -> Tgt #{new_id})"
                    )

                    if purl:
                        self.existing_product_urls.add(norm_url(purl))
                    if pname:
                        self.existing_product_names.add((norm_str(pname), tgt_brand_id))

                except Exception as exc:
                    error_msg = f"Failed to migrate product '{pname}' (Src #{src_row_id}): {exc}"
                    logger.error(error_msg)
                    self.report["products"]["errors"].append(
                        {"source_row_id": src_row_id, "product_name": pname, "error": str(exc)}
                    )

            count += 1
            if count % 10 == 0:
                logger.info(
                    f"Progress: {count} processed | "
                    f"{self.report['products']['migrated']} migrated | "
                    f"{len(self.report['products']['duplicates_skipped'])} duplicates skipped"
                )

        logger.info(
            f"Products Migration Finished! Total Migrated: {self.report['products']['migrated']}, "
            f"Duplicates Skipped: {len(self.report['products']['duplicates_skipped'])}, "
            f"Errors: {len(self.report['products']['errors'])}"
        )

    def run(self, report_path: str = "output/migration_report.json") -> dict[str, Any]:
        """Execute the entire 3-stage migration pipeline."""
        start_time = time.time()
        logger.info(f"Starting Baserow Migration Pipeline (Dry-Run: {self.dry_run})...")

        self.prepare_schemas()
        self.migrate_brands()
        self.migrate_categories_and_subcategories()
        self.migrate_products()

        duration = round(time.time() - start_time, 2)
        self.report["duration_seconds"] = duration
        self.report["finished_at"] = datetime.now().isoformat()

        out_file = Path(report_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(self.report, f, indent=2, ensure_ascii=False)

        logger.info(f"Full migration report saved to: {out_file.resolve()}")
        return self.report


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate all data and products from Baserow Database 3 to Database 2."
    )
    parser.add_argument(
        "--source-url",
        default=os.getenv("SOURCE_BASEROW_URL") or os.getenv("BASEROW_URL"),
        help="Source Baserow URL (default: BASEROW_URL from .env)",
    )
    parser.add_argument(
        "--source-token",
        default=os.getenv("SOURCE_BASEROW_TOKEN") or os.getenv("BASEROW_TOKEN"),
        help="Source Baserow Token (default: BASEROW_TOKEN from .env)",
    )
    parser.add_argument(
        "--source-brands",
        type=int,
        default=None,
        help="Source Brands Table ID in Database 3",
    )
    parser.add_argument(
        "--source-categories",
        type=int,
        default=None,
        help="Source Categories Table ID in Database 3",
    )
    parser.add_argument(
        "--source-subcategories",
        type=int,
        default=None,
        help="Source Subcategories Table ID in Database 3",
    )
    parser.add_argument(
        "--source-products",
        type=int,
        default=None,
        help="Source Products Table ID in Database 3",
    )

    parser.add_argument(
        "--target-url",
        default=os.getenv("BASEROW_URL"),
        help="Target Baserow URL (default: BASEROW_URL from .env)",
    )
    parser.add_argument(
        "--target-token",
        default=os.getenv("BASEROW_TOKEN"),
        help="Target Baserow Token (default: BASEROW_TOKEN from .env)",
    )
    parser.add_argument(
        "--target-brands",
        type=int,
        default=int(os.getenv("BRANDS_TABLE_ID", "805")),
        help="Target Brands Table ID (default: 805)",
    )
    parser.add_argument(
        "--target-categories",
        type=int,
        default=int(os.getenv("CATEGORY_TABLE_ID", "806")),
        help="Target Categories Table ID (default: 806)",
    )
    parser.add_argument(
        "--target-subcategories",
        type=int,
        default=int(os.getenv("SUBCATEGORY_TABLE_ID", "807")),
        help="Target Subcategories Table ID (default: 807)",
    )
    parser.add_argument(
        "--target-products",
        type=int,
        default=int(os.getenv("PRODUCTS_TABLE_ID", "802")),
        help="Target Products Table ID (default: 802)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without modifying Target DB",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of products to migrate (useful for testing)",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive table selection prompt",
    )
    parser.add_argument(
        "--report-file",
        default="output/migration_report.json",
        help="Path to save detailed JSON summary report",
    )

    return parser.parse_args()


def prompt_int(label: str, default: int | None = None) -> int:
    default_str = f" [{default}]" if default is not None else ""
    while True:
        try:
            val = input(f"{label}{default_str}: ").strip()
            if not val and default is not None:
                return default
            if val:
                return int(val)
        except ValueError:
            print("Please enter a valid numeric Table ID.")


def main() -> int:
    args = parse_arguments()

    if not args.source_url or not args.source_token:
        logger.error("Source Baserow URL and Token must be provided.")
        return 1
    if not args.target_url or not args.target_token:
        logger.error("Target Baserow URL and Token must be provided.")
        return 1

    # Interactive prompt if requested or if source tables are not specified
    if args.interactive or args.source_products is None:
        print("\n" + "=" * 65)
        print("     BASEROW DATABASE MIGRATION TABLE SELECTOR")
        print("=" * 65)
        print("Select Source Table (DB3) and Target Table (DB2):\n")

        print("--- 1. Brands Table ---")
        args.source_brands = prompt_int("  DB3 Source Brands Table ID", args.source_brands or 805)
        args.target_brands = prompt_int("  DB2 Target Brands Table ID", args.target_brands or 805)

        print("\n--- 2. Categories Table ---")
        args.source_categories = prompt_int("  DB3 Source Categories Table ID", args.source_categories or 806)
        args.target_categories = prompt_int("  DB2 Target Categories Table ID", args.target_categories or 806)

        print("\n--- 3. Subcategories Table ---")
        args.source_subcategories = prompt_int("  DB3 Source Subcategories Table ID", args.source_subcategories or 807)
        args.target_subcategories = prompt_int("  DB2 Target Subcategories Table ID", args.target_subcategories or 807)

        print("\n--- 4. Products Table (productsDetails) ---")
        args.source_products = prompt_int("  DB3 Source Products Table ID (productsDetails)", args.source_products or 817)
        args.target_products = prompt_int("  DB2 Target Products Table ID (productsDetails)", args.target_products or 802)

        dry_choice = input("\nRun as Dry-Run (Simulation only, no data written)? (y/N): ").strip().lower()
        args.dry_run = dry_choice in ("y", "yes")

        print("=" * 65 + "\n")

    source_api = BaserowAPI(args.source_url, args.source_token)
    target_api = BaserowAPI(args.target_url, args.target_token)

    logger.info("Checking API connections...")
    if not source_api.check_token():
        logger.warning(
            "Source API token verification returned non-200. Proceeding if token has table permissions."
        )
    if not target_api.check_token():
        logger.warning(
            "Target API token verification returned non-200. Proceeding if token has table permissions."
        )

    source_tables = {
        "brands": args.source_brands,
        "categories": args.source_categories,
        "subcategories": args.source_subcategories,
        "products": args.source_products,
    }
    target_tables = {
        "brands": args.target_brands,
        "categories": args.target_categories,
        "subcategories": args.target_subcategories,
        "products": args.target_products,
    }

    migrator = DatabaseMigrator(
        source_api=source_api,
        target_api=target_api,
        source_tables=source_tables,
        target_tables=target_tables,
        dry_run=args.dry_run,
        limit=args.limit,
    )

    migrator.run(report_path=args.report_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
