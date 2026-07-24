"""Pixelbin erase.bg background removal (Prediction API).

Get API token: Pixelbin console -> Settings -> Tokens -> Create Token
https://www.pixelbin.io/docs/tokens/create-token/
"""
from __future__ import annotations

import os
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DOMAIN = "https://api.pixelbin.io"
DEFAULT_INDUSTRY = "ecommerce"
DEFAULT_QUALITY = "original"


@dataclass(frozen=True)
class PixelbinSettings:
    api_secret: str
    domain: str
    industry_type: str
    quality_type: str
    refine: bool
    shadow: bool
    max_wait_attempts: int
    retry_interval: float


@dataclass
class RemoveBgResult:
    request_id: str
    status: str
    output_url: str
    consumed_credits: int
    raw: dict[str, Any]


def load_pixelbin_settings() -> PixelbinSettings:
    token = os.getenv("PIXELBIN_API_TOKEN", "").strip().strip('"')
    if not token:
        raise ValueError(
            "PIXELBIN_API_TOKEN is required. "
            "Create one in Pixelbin console: Settings -> Tokens."
        )
    return PixelbinSettings(
        api_secret=token,
        domain=os.getenv("PIXELBIN_API_DOMAIN", DEFAULT_DOMAIN).strip().rstrip("/"),
        industry_type=os.getenv("PIXELBIN_INDUSTRY_TYPE", DEFAULT_INDUSTRY).strip(),
        quality_type=os.getenv("PIXELBIN_QUALITY_TYPE", DEFAULT_QUALITY).strip(),
        refine=os.getenv("PIXELBIN_REFINE", "true").strip().lower() in ("1", "true", "yes"),
        shadow=os.getenv("PIXELBIN_SHADOW", "false").strip().lower() in ("1", "true", "yes"),
        max_wait_attempts=int(os.getenv("PIXELBIN_MAX_WAIT_ATTEMPTS", "60")),
        retry_interval=float(os.getenv("PIXELBIN_RETRY_INTERVAL", "2")),
    )


def _patch_pixelbin_http_timeout(seconds: int) -> None:
    """SDK defaults to 15s — too short for erase_bg jobs."""
    import pixelbin.common.aiohttp_helper as ah

    if getattr(ah.AiohttpHelper.request, "_pixelbin_patched", False):
        return

    _orig = ah.AiohttpHelper.request

    async def request(
        self,
        method: str,
        url: str,
        params: dict,
        data: dict,
        headers: dict,
        timeout_allowed: int = seconds,
        trust_env: bool = False,
    ) -> dict:
        return await _orig(
            self,
            method,
            url,
            params,
            data,
            headers,
            timeout_allowed=timeout_allowed,
            trust_env=trust_env,
        )

    request._pixelbin_patched = True  # type: ignore[attr-defined]
    ah.AiohttpHelper.request = request


def _client(settings: PixelbinSettings):
    from pixelbin import PixelbinClient, PixelbinConfig

    http_timeout = int(os.getenv("PIXELBIN_HTTP_TIMEOUT", "120"))
    _patch_pixelbin_http_timeout(http_timeout)

    return PixelbinClient(
        config=PixelbinConfig(
            {
                "domain": settings.domain,
                "apiSecret": settings.api_secret,
            }
        )
    )


def remove_background(
    image: str | bytes,
    settings: PixelbinSettings | None = None,
    *,
    max_retries: int = 3,
) -> RemoveBgResult:
    """Remove background via Pixelbin erase_bg. `image` = URL or raw bytes."""
    cfg = settings or load_pixelbin_settings()
    last_exc: BaseException | None = None

    for attempt in range(max_retries):
        try:
            client = _client(cfg)
            job_input: dict[str, Any] = {
                "image": image,
                "industry_type": cfg.industry_type,
                "quality_type": cfg.quality_type,
                "refine": cfg.refine,
                "shadow": cfg.shadow,
            }
            result = client.predictions.create_and_wait(
                name="erase_bg",
                input=job_input,
                options={
                    "maxAttempts": cfg.max_wait_attempts,
                    "retryInterval": cfg.retry_interval,
                    "retryFactor": 1,
                },
            )
            break
        except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt + 1 >= max_retries:
                raise RuntimeError(
                    f"Pixelbin API timeout after {max_retries} attempts"
                ) from exc
            time.sleep(2 + attempt * 2)
    else:
        raise RuntimeError("Pixelbin erase_bg failed") from last_exc

    status = str(result.get("status") or "")
    if status != "SUCCESS":
        raise RuntimeError(
            f"Pixelbin erase_bg failed ({status}): {result.get('error') or result}"
        )

    output = result.get("output") or []
    if not output:
        raise RuntimeError(f"Pixelbin erase_bg returned no output: {result}")

    return RemoveBgResult(
        request_id=str(result.get("_id") or ""),
        status=status,
        output_url=str(output[0]),
        consumed_credits=int(result.get("consumedCredits") or 0),
        raw=result,
    )


def download_output(url: str, timeout: float = 60) -> bytes:
    resp = requests.get(url, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"Download failed ({resp.status_code}): {url}")
    return resp.content


def remove_background_to_bytes(
    image: str | bytes,
    settings: PixelbinSettings | None = None,
) -> tuple[bytes, RemoveBgResult]:
    result = remove_background(image, settings=settings)
    time.sleep(0.3)
    return download_output(result.output_url), result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Test Pixelbin background removal.")
    parser.add_argument("--test-url", required=True, help="Image URL to process.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output") / "pixelbin" / "test.png",
        help="Save result PNG here.",
    )
    args = parser.parse_args()

    print("Calling Pixelbin erase_bg...")
    png, result = remove_background_to_bytes(args.test_url)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(png)
    print(f"OK  credits={result.consumed_credits}")
    print(f"OK  output_url={result.output_url}")
    print(f"OK  saved={args.out}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
