from __future__ import annotations

from typing import Any, Iterator

import requests

from config import Settings


class BaserowClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {settings.baserow_token}",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.settings.api_base}{path}"

    def list_table_rows(
        self,
        table_id: int,
        *,
        size: int = 200,
        filters: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield all rows from a table (handles pagination)."""
        page = 1
        while True:
            params: dict[str, Any] = {"page": page, "size": size}
            if filters:
                params.update(filters)

            response = self.session.get(
                self._url(f"/database/rows/table/{table_id}/"),
                params=params,
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()

            for row in payload.get("results", []):
                yield row

            if not payload.get("next"):
                break
            page += 1

    def get_row(self, table_id: int, row_id: int) -> dict[str, Any]:
        response = self.session.get(
            self._url(f"/database/rows/table/{table_id}/{row_id}/"),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def create_row(
        self, table_id: int, fields: dict[str, Any]
    ) -> dict[str, Any]:
        response = self.session.post(
            self._url(f"/database/rows/table/{table_id}/"),
            json=fields,
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def update_row(
        self, table_id: int, row_id: int, fields: dict[str, Any]
    ) -> dict[str, Any]:
        response = self.session.patch(
            self._url(f"/database/rows/table/{table_id}/{row_id}/"),
            json=fields,
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def upload_file_via_url(self, file_url: str, *, timeout: float = 120) -> str:
        """
        Upload a remote file into Baserow user storage.
        Returns the `name` token used in file field values: [{"name": "..."}].
        """
        response = self.session.post(
            self._url("/user-files/upload-via-url/"),
            json={"url": file_url},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        name = payload.get("name")
        if not name:
            raise ValueError(f"Upload response missing name: {payload}")
        return str(name)

    def list_tables(self, database_id: int) -> list[dict[str, Any]]:
        """Requires JWT on cloud; may work with database token on some setups."""
        response = self.session.get(
            self._url(f"/database/tables/database/{database_id}/"),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def get_table_fields(self, table_id: int) -> list[dict[str, Any]]:
        response = self.session.get(
            self._url(f"/database/fields/table/{table_id}/"),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()
