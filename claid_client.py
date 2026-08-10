"""Claid.ai Image Editing API — lifestyle background expansion (outpaint).

Docs: https://docs.claid.ai/
Auth: Authorization: Bearer {CLAID_API_KEY}
Endpoint: POST https://api.claid.ai/v1/image/edit

Env:
  CLAID_API_KEY=...
  CLAID_API_BASE=https://api.claid.ai   (optional)
  CLAID_HTTP_TIMEOUT=180                (optional)
  CLAID_OUTPAINT_BY=15%                 (optional; all sides)
  CLAID_FEATHERING=15%                  (optional)
  CLAID_JPEG_QUALITY=90                 (optional)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE = "https://api.claid.ai"
DEFAULT_OUTPAINT_BY = "15%"
DEFAULT_FEATHERING = "15%"
DEFAULT_JPEG_QUALITY = 90


@dataclass(frozen=True)
class ClaidSettings:
    api_key: str
    api_base: str
    timeout: float
    outpaint_by: str
    feathering: str
    jpeg_quality: int


@dataclass
class ClaidExpandResult:
    input_url: str
    output_url: str
    width: int | None
    height: int | None
    raw: dict[str, Any]


def load_claid_settings() -> ClaidSettings:
    key = os.getenv("CLAID_API_KEY", "").strip().strip('"')
    if not key:
        raise ValueError(
            "CLAID_API_KEY is not set. Add it to .env "
            "(Claid account → API keys)."
        )
    return ClaidSettings(
        api_key=key,
        api_base=os.getenv("CLAID_API_BASE", DEFAULT_BASE).strip().rstrip("/"),
        timeout=float(os.getenv("CLAID_HTTP_TIMEOUT", "180")),
        outpaint_by=os.getenv("CLAID_OUTPAINT_BY", DEFAULT_OUTPAINT_BY).strip()
        or DEFAULT_OUTPAINT_BY,
        feathering=os.getenv("CLAID_FEATHERING", DEFAULT_FEATHERING).strip()
        or DEFAULT_FEATHERING,
        jpeg_quality=int(os.getenv("CLAID_JPEG_QUALITY", str(DEFAULT_JPEG_QUALITY))),
    )


class ClaidClient:
    """Thin wrapper around Claid /v1/image/edit."""

    def __init__(self, settings: ClaidSettings | None = None) -> None:
        self.settings = settings or load_claid_settings()
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def edit_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.api_base}/v1/image/edit"
        resp = self._session.post(
            url,
            headers=self._headers(),
            json=payload,
            timeout=self.settings.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Claid edit failed ({resp.status_code}): {resp.text[:800]}"
            )
        return resp.json()

    def edit_image_upload(
        self, file_path: str | Path, operations_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST /v1/image/edit/upload with local file + operations JSON (no input URL)."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {path}")

        url = f"{self.settings.api_base}/v1/image/edit/upload"
        # Do not set Content-Type manually — requests sets multipart boundary.
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Accept": "application/json",
        }
        data_json = json.dumps(operations_payload, ensure_ascii=False)
        with path.open("rb") as handle:
            files = {
                "file": (path.name, handle, "application/octet-stream"),
                "data": (None, data_json, "application/json"),
            }
            resp = self._session.post(
                url,
                headers=headers,
                files=files,
                timeout=self.settings.timeout,
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Claid upload edit failed ({resp.status_code}): {resp.text[:800]}"
            )
        return resp.json()

    def expand_lifestyle_file(
        self,
        file_path: str | Path,
        *,
        outpaint_by: str | None = None,
        feathering: str | None = None,
        width: int | str | None = "auto",
        height: int | str | None = "auto",
        jpeg_quality: int | None = None,
    ) -> ClaidExpandResult:
        """Zoom-out / outpaint a local image file via Claid upload endpoint."""
        path = Path(file_path)
        by = (outpaint_by or self.settings.outpaint_by).strip()
        feather = (feathering or self.settings.feathering).strip()
        quality = jpeg_quality if jpeg_quality is not None else self.settings.jpeg_quality

        resizing: dict[str, Any] = {
            "fit": {
                "type": "outpaint",
                "outpaint_by": by,
                "feathering": feather,
            }
        }
        if width is not None:
            resizing["width"] = width
        if height is not None:
            resizing["height"] = height

        payload = {
            "operations": {
                "resizing": resizing,
                "restorations": {"upscale": "smart_enhance"},
            },
            "output": {
                "format": {"type": "jpeg", "quality": quality},
            },
        }
        raw = self.edit_image_upload(path, payload)
        output_url, w, h = _extract_output(raw)
        if not output_url:
            raise RuntimeError(f"Claid response missing output URL: {raw}")
        return ClaidExpandResult(
            input_url=str(path),
            output_url=output_url,
            width=w,
            height=h,
            raw=raw,
        )

    def expand_lifestyle_url(
        self,
        image_url: str,
        *,
        outpaint_by: str | None = None,
        feathering: str | None = None,
        width: int | str | None = "auto",
        height: int | str | None = "auto",
        jpeg_quality: int | None = None,
    ) -> ClaidExpandResult:
        """
        Zoom-out / outpaint lifestyle image on all sides.

        Default: keep aspect (width/height auto) and expand by CLAID_OUTPAINT_BY
        percentage on each side. Claid docs require an upscale method with outpaint_by.
        """
        image_url = (image_url or "").strip()
        if not image_url.startswith("http"):
            raise ValueError(f"Invalid image URL: {image_url!r}")

        by = (outpaint_by or self.settings.outpaint_by).strip()
        feather = (feathering or self.settings.feathering).strip()
        quality = jpeg_quality if jpeg_quality is not None else self.settings.jpeg_quality

        resizing: dict[str, Any] = {
            "fit": {
                "type": "outpaint",
                "outpaint_by": by,
                "feathering": feather,
            }
        }
        if width is not None:
            resizing["width"] = width
        if height is not None:
            resizing["height"] = height

        payload = {
            "input": image_url,
            "operations": {
                "resizing": resizing,
                # Required with outpaint_by per Claid docs
                "restorations": {
                    "upscale": "smart_enhance",
                },
            },
            "output": {
                "format": {
                    "type": "jpeg",
                    "quality": quality,
                }
            },
        }

        raw = self.edit_image(payload)
        output_url, w, h = _extract_output(raw)
        if not output_url:
            raise RuntimeError(f"Claid response missing output URL: {raw}")
        return ClaidExpandResult(
            input_url=image_url,
            output_url=output_url,
            width=w,
            height=h,
            raw=raw,
        )

    def expand_to_canvas(
        self,
        image_url: str,
        *,
        width: int,
        height: int,
        feathering: str | None = None,
        jpeg_quality: int | None = None,
    ) -> ClaidExpandResult:
        """Outpaint to a fixed canvas (e.g. 2000x2000 square PDP)."""
        image_url = (image_url or "").strip()
        if not image_url.startswith("http"):
            raise ValueError(f"Invalid image URL: {image_url!r}")

        feather = (feathering or self.settings.feathering).strip()
        quality = jpeg_quality if jpeg_quality is not None else self.settings.jpeg_quality

        payload = {
            "input": image_url,
            "operations": {
                "resizing": {
                    "width": width,
                    "height": height,
                    "fit": {
                        "type": "outpaint",
                        "feathering": feather,
                    },
                },
                "restorations": {
                    "upscale": "smart_enhance",
                },
            },
            "output": {
                "format": {
                    "type": "jpeg",
                    "quality": quality,
                }
            },
        }
        raw = self.edit_image(payload)
        output_url, w, h = _extract_output(raw)
        if not output_url:
            raise RuntimeError(f"Claid response missing output URL: {raw}")
        return ClaidExpandResult(
            input_url=image_url,
            output_url=output_url,
            width=w,
            height=h,
            raw=raw,
        )


def _extract_output(raw: dict[str, Any]) -> tuple[str, int | None, int | None]:
    """Parse Claid edit response for tmp/result URL and optional size."""
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    output = data.get("output") if isinstance(data, dict) else None

    if isinstance(output, list) and output:
        output = output[0]

    if not isinstance(output, dict):
        # Fallback: search common keys
        for key in ("tmp_url", "url", "output_url", "result_url"):
            val = data.get(key) if isinstance(data, dict) else None
            if isinstance(val, str) and val.startswith("http"):
                return val, None, None
        return "", None, None

    url = (
        output.get("tmp_url")
        or output.get("url")
        or output.get("src")
        or ""
    )
    if isinstance(url, dict):
        url = url.get("url") or url.get("tmp_url") or ""
    url = str(url or "").strip()

    w = output.get("width")
    h = output.get("height")
    try:
        width = int(w) if w is not None else None
    except (TypeError, ValueError):
        width = None
    try:
        height = int(h) if h is not None else None
    except (TypeError, ValueError):
        height = None
    return url, width, height
