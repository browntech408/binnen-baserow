"""Minimal Shopify Admin REST client with client-credentials or static token auth."""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_API_VERSION = "2025-10"
MAX_PAGE_SIZE = 250


@dataclass
class ShopifyConfig:
    shop: str
    client_id: str
    client_secret: str
    access_token: str
    api_version: str

    @property
    def shop_host(self) -> str:
        shop = self.shop.strip().rstrip("/")
        if shop.startswith("https://"):
            shop = shop[len("https://") :]
        if shop.startswith("http://"):
            shop = shop[len("http://") :]
        if not shop.endswith(".myshopify.com"):
            shop = f"{shop}.myshopify.com"
        return shop

    @property
    def admin_base(self) -> str:
        return f"https://{self.shop_host}/admin/api/{self.api_version}"

    @property
    def store_slug(self) -> str:
        return self.shop_host.removesuffix(".myshopify.com")

    def admin_product_url(self, product_id: int) -> str:
        return f"https://admin.shopify.com/store/{self.store_slug}/products/{product_id}"


def load_shopify_config() -> ShopifyConfig:
    shop = os.getenv("SHOPIFY_SHOP", "").strip()
    client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
    access_token = os.getenv("SHOPIFY_ACCESS_TOKEN", "").strip().strip('"')
    api_version = os.getenv("SHOPIFY_API_VERSION", DEFAULT_API_VERSION).strip()

    if not shop:
        raise ValueError(
            "SHOPIFY_SHOP is required (e.g. ddvwt8-0k or ddvwt8-0k.myshopify.com)."
        )
    if not access_token and not (client_id and client_secret):
        raise ValueError(
            "Set SHOPIFY_ACCESS_TOKEN or both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET."
        )
    return ShopifyConfig(
        shop=shop,
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        api_version=api_version,
    )


class ShopifyClient:
    def __init__(self, config: ShopifyConfig | None = None) -> None:
        self.config = config or load_shopify_config()
        self._session = requests.Session()
        self._token = self.config.access_token
        self._token_expires_at = 0.0

    def _ensure_token(self) -> str:
        if self._token and (
            self.config.access_token or time.time() < self._token_expires_at - 60
        ):
            return self._token

        if not self.config.client_id or not self.config.client_secret:
            raise RuntimeError("Shopify access token expired and no client credentials set.")

        url = f"https://{self.config.shop_host}/admin/oauth/access_token"
        resp = self._session.post(
            url,
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Shopify token request failed ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()
        self._token = str(data["access_token"])
        self._token_expires_at = time.time() + float(data.get("expires_in", 86399))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "X-Shopify-Access-Token": self._ensure_token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        max_retries: int = 8,
    ) -> requests.Response:
        url = f"{self.config.admin_base}{path}"
        for attempt in range(max_retries):
            try:
                resp = self._session.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                    timeout=60,
                )
            except requests.RequestException:
                if attempt + 1 >= max_retries:
                    raise
                time.sleep(1 + attempt)
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 + attempt))
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt + 1 < max_retries:
                time.sleep(1 + attempt)
                continue
            return resp
        return resp

    def _get_with_retry(self, url: str, *, max_retries: int = 8) -> requests.Response:
        for attempt in range(max_retries):
            try:
                resp = self._session.get(url, headers=self._headers(), timeout=60)
            except requests.RequestException:
                if attempt + 1 >= max_retries:
                    raise
                time.sleep(1 + attempt)
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 + attempt))
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt + 1 < max_retries:
                time.sleep(1 + attempt)
                continue
            return resp
        return resp

    def iter_products(
        self,
        *,
        status: str = "active",
        fields: str = "id,title,status,images",
    ) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "limit": MAX_PAGE_SIZE,
            "fields": fields,
        }
        if status:
            params["status"] = status

        next_url: str | None = None
        while True:
            if next_url:
                resp = self._get_with_retry(next_url)
            else:
                resp = self._request("GET", "/products.json", params=params)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"List products failed ({resp.status_code}): {resp.text[:500]}"
                )
            products.extend(resp.json().get("products") or [])
            next_url = _parse_next_link(resp.headers.get("Link", ""))
            if not next_url:
                break

        return products

    def set_product_status(self, product_id: int, status: str) -> dict[str, Any]:
        resp = self._request(
            "PUT",
            f"/products/{product_id}.json",
            json_body={"product": {"id": product_id, "status": status}},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Update product {product_id} failed ({resp.status_code}): "
                f"{resp.text[:500]}"
            )
        return resp.json().get("product") or {}

    def get_product(self, product_id: int) -> dict[str, Any]:
        resp = self._request("GET", f"/products/{product_id}.json")
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Get product {product_id} failed ({resp.status_code}): "
                f"{resp.text[:500]}"
            )
        product = resp.json().get("product")
        if not product:
            raise RuntimeError(f"Product {product_id} not found.")
        return product

    def create_product(self, product: dict[str, Any]) -> dict[str, Any]:
        resp = self._request(
            "POST",
            "/products.json",
            json_body={"product": product},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Create product failed ({resp.status_code}): {resp.text[:800]}"
            )
        return resp.json().get("product") or {}

    def update_product(
        self, product_id: int, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Update product fields (title, body_html, vendor, etc.) via REST PUT."""
        resp = self._request(
            "PUT",
            f"/products/{product_id}.json",
            json_body={"product": {"id": product_id, **fields}},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Update product {product_id} failed ({resp.status_code}): "
                f"{resp.text[:500]}"
            )
        return resp.json().get("product") or {}

    def set_metafields_graphql(
        self, owner_id: str, metafields: list[dict[str, Any]]
    ) -> tuple[int, int, list[str]]:
        """Upsert metafields via GraphQL metafieldsSet (handles create + update).

        Tries batch first; on failure, retries each metafield individually
        so one conflict doesn't block the rest.

        Args:
            owner_id: Full GID, e.g. "gid://shopify/Product/12345"
            metafields: List of dicts with keys: namespace, key, value, type

        Returns:
            (ok_count, failed_count, error_messages)
        """
        if not metafields:
            return 0, 0, []
        mutation = (
            "mutation MetafieldsSet($metafields: [MetafieldsSetInput!]!) {"
            "  metafieldsSet(metafields: $metafields) {"
            "    metafields { id namespace key value }"
            "    userErrors { field message }"
            "  }"
            "}"
        )
        inputs = [
            {
                "ownerId": owner_id,
                "namespace": mf["namespace"],
                "key": mf["key"],
                "value": str(mf["value"]),
                "type": mf["type"],
            }
            for mf in metafields
            if mf.get("value") not in (None, "")
        ]
        if not inputs:
            return 0, 0, []

        # Try batch first
        try:
            data = self.graphql(mutation, {"metafields": inputs})
            result = data.get("metafieldsSet") or {}
            user_errors = result.get("userErrors") or []
            if not user_errors:
                ok_count = len(result.get("metafields") or [])
                return ok_count, 0, []
        except RuntimeError:
            pass  # Fall through to individual retry

        # Batch failed — retry each metafield individually
        ok = 0
        failed = 0
        errors: list[str] = []
        for inp in inputs:
            try:
                data = self.graphql(mutation, {"metafields": [inp]})
                result = data.get("metafieldsSet") or {}
                ue = result.get("userErrors") or []
                if ue:
                    key = inp.get("key", "?")
                    msg = "; ".join(str(e.get("message") or e) for e in ue)
                    errors.append(f"{key}: {msg}")
                    failed += 1
                else:
                    ok += 1
            except RuntimeError as exc:
                key = inp.get("key", "?")
                errors.append(f"{key}: {exc}")
                failed += 1
        return ok, failed, errors

    def create_product_metafield(
        self, product_id: int, metafield: dict[str, Any]
    ) -> dict[str, Any]:
        resp = self._request(
            "POST",
            f"/products/{product_id}/metafields.json",
            json_body={"metafield": metafield},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Create metafield on product {product_id} failed "
                f"({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json().get("metafield") or {}

    def graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        resp = self._request(
            "POST",
            "/graphql.json",
            json_body={"query": query, "variables": variables or {}},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"GraphQL request failed ({resp.status_code}): {resp.text[:500]}"
            )
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL errors: {payload['errors']}")
        return payload.get("data") or {}

    def create_image_files_from_urls(self, urls: list[str]) -> list[str]:
        """Upload images to Shopify Files; return MediaImage GIDs for metafields."""
        if not urls:
            return []
        gids: list[str] = []
        mutation = (
            "mutation fileCreate($files: [FileCreateInput!]!) {"
            "  fileCreate(files: $files) {"
            "    files { id }"
            "    userErrors { field message }"
            "  }"
            "}"
        )
        for i in range(0, len(urls), 10):
            chunk = urls[i : i + 10]
            data = self.graphql(
                mutation,
                {
                    "files": [
                        {"originalSource": url, "contentType": "IMAGE"}
                        for url in chunk
                    ],
                },
            )
            result = data.get("fileCreate") or {}
            user_errors = result.get("userErrors") or []
            if user_errors:
                messages = "; ".join(
                    str(e.get("message") or e) for e in user_errors
                )
                raise RuntimeError(f"fileCreate failed: {messages}")
            for file_row in result.get("files") or []:
                gid = str(file_row.get("id") or "").strip()
                if gid:
                    gids.append(gid)
        return gids

    def set_product_list_file_reference_metafield(
        self,
        product_id: int,
        namespace: str,
        key: str,
        file_gids: list[str],
    ) -> None:
        if not file_gids:
            return
        mutation = (
            "mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {"
            "  metafieldsSet(metafields: $metafields) {"
            "    metafields { id }"
            "    userErrors { field message }"
            "  }"
            "}"
        )
        data = self.graphql(
            mutation,
            {
                "metafields": [
                    {
                        "ownerId": f"gid://shopify/Product/{product_id}",
                        "namespace": namespace,
                        "key": key,
                        "type": "list.file_reference",
                        "value": json.dumps(file_gids, ensure_ascii=False),
                    }
                ],
            },
        )
        result = data.get("metafieldsSet") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            messages = "; ".join(str(e.get("message") or e) for e in user_errors)
            raise RuntimeError(f"metafieldsSet failed: {messages}")

    def create_product_metafields(
        self, product_id: int, metafields: list[dict[str, Any]]
    ) -> tuple[int, int, list[str]]:
        ok = 0
        failed = 0
        errors: list[str] = []
        for metafield in metafields:
            try:
                self.create_product_metafield(product_id, metafield)
                ok += 1
            except RuntimeError as exc:
                failed += 1
                key = str(metafield.get("key") or "?")
                errors.append(f"{key}: {exc}")
        return ok, failed, errors

    def replace_product_image(
        self,
        product_id: int,
        image_id: int,
        *,
        image_bytes: bytes,
        filename: str,
        position: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "image": {
                "id": image_id,
                "attachment": base64.b64encode(image_bytes).decode("ascii"),
                "filename": filename,
            }
        }
        if position is not None:
            body["image"]["position"] = position
        resp = self._request(
            "PUT",
            f"/products/{product_id}/images/{image_id}.json",
            json_body=body,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Replace image {image_id} on product {product_id} failed "
                f"({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json().get("image") or {}

    def delete_product_image(self, product_id: int, image_id: int) -> None:
        resp = self._request(
            "DELETE",
            f"/products/{product_id}/images/{image_id}.json",
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Delete image {image_id} on product {product_id} failed "
                f"({resp.status_code}): {resp.text[:500]}"
            )

    def add_product_images(
        self,
        product_id: int,
        images: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Add images to a product (attachment base64 or src URL per Shopify image dict)."""
        if not images:
            return []
        resp = self._request(
            "PUT",
            f"/products/{product_id}.json",
            json_body={"product": {"id": product_id, "images": images}},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Add images to product {product_id} failed "
                f"({resp.status_code}): {resp.text[:800]}"
            )
        return list((resp.json().get("product") or {}).get("images") or [])

    def create_product_image_from_src(
        self, product_id: int, src: str, *, position: int | None = None
    ) -> dict[str, Any]:
        """Append one image to a product from a public URL (does not replace existing)."""
        body: dict[str, Any] = {"image": {"src": src}}
        if position is not None:
            body["image"]["position"] = position
        resp = self._request(
            "POST",
            f"/products/{product_id}/images.json",
            json_body=body,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Create image on product {product_id} failed "
                f"({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json().get("image") or {}

    def get_product_list_file_reference_metafield(
        self,
        product_id: int,
        namespace: str,
        key: str,
        *,
        first: int = 50,
    ) -> dict[str, Any]:
        """Return metafield id + MediaImage URLs for a list.file_reference metafield."""
        query = (
            "query productMetafield($id: ID!, $namespace: String!, $key: String!, $first: Int!) {"
            "  product(id: $id) {"
            "    metafield(namespace: $namespace, key: $key) {"
            "      id"
            "      value"
            "      references(first: $first) {"
            "        nodes {"
            "          __typename"
            "          ... on MediaImage { id image { url } }"
            "        }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        data = self.graphql(
            query,
            {
                "id": f"gid://shopify/Product/{product_id}",
                "namespace": namespace,
                "key": key,
                "first": first,
            },
        )
        mf = (data.get("product") or {}).get("metafield")
        if not mf:
            return {"metafield_id": "", "file_gids": [], "urls": []}
        nodes = ((mf.get("references") or {}).get("nodes") or [])
        urls: list[str] = []
        gids: list[str] = []
        for node in nodes:
            gid = str(node.get("id") or "").strip()
            url = str((node.get("image") or {}).get("url") or "").strip()
            if gid:
                gids.append(gid)
            if url:
                urls.append(url)
        return {
            "metafield_id": str(mf.get("id") or "").strip(),
            "file_gids": gids,
            "urls": urls,
            "value": str(mf.get("value") or ""),
        }

    def delete_product_metafield(
        self,
        product_id: int,
        namespace: str,
        key: str,
    ) -> None:
        """Delete a product metafield via metafieldsDelete (API 2025+)."""
        mutation = (
            "mutation metafieldsDelete($metafields: [MetafieldIdentifierInput!]!) {"
            "  metafieldsDelete(metafields: $metafields) {"
            "    deletedMetafields { key namespace ownerId }"
            "    userErrors { field message }"
            "  }"
            "}"
        )
        data = self.graphql(
            mutation,
            {
                "metafields": [
                    {
                        "ownerId": f"gid://shopify/Product/{product_id}",
                        "namespace": namespace,
                        "key": key,
                    }
                ],
            },
        )
        result = data.get("metafieldsDelete") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            messages = "; ".join(str(e.get("message") or e) for e in user_errors)
            raise RuntimeError(f"metafieldsDelete failed: {messages}")

    def clear_product_list_file_reference_metafield(
        self,
        product_id: int,
        namespace: str,
        key: str,
        *,
        metafield_gid: str = "",
    ) -> None:
        """Remove list.file_reference metafield from a product."""
        del metafield_gid  # unused; API 2025+ deletes by owner/namespace/key
        self.delete_product_metafield(product_id, namespace, key)


def _parse_next_link(link_header: str) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' in section:
            start = section.find("<") + 1
            end = section.find(">")
            if start > 0 and end > start:
                return section[start:end]
    return None


def shopify_preview_url(url: str, width: int = 220) -> str:
    """Small CDN variant for faster background checks."""
    if not url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}width={width}"
