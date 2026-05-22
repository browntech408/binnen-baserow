import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _split_statuses(raw: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return frozenset(parts)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    baserow_url: str
    baserow_token: str
    brands_table_id: int
    field_brand_name: str
    field_domain: str
    field_website_url: str
    field_brand_quote: str
    field_products: str
    field_bg_remove: str
    field_scrape_status: str
    field_scrape_error: str
    field_last_scraped: str
    field_page_title: str
    field_meta_description: str
    scrape_only_statuses: frozenset[str]
    scrape_only_bg_remove: bool
    skip_if_brand_quote: bool
    skip_if_has_products: bool
    scrape_delay_seconds: float
    http_timeout: float

    @property
    def api_base(self) -> str:
        return self.baserow_url.rstrip("/") + "/api"


def load_settings() -> Settings:
    url = os.getenv("BASEROW_URL", "").strip()
    token = os.getenv("BASEROW_TOKEN", "").strip().strip('"')
    table_id = os.getenv("BRANDS_TABLE_ID", "").strip()

    missing = []
    if not url:
        missing.append("BASEROW_URL")
    if not token:
        missing.append("BASEROW_TOKEN")
    if not table_id:
        missing.append("BRANDS_TABLE_ID")

    if missing:
        raise ValueError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill them in."
        )

    return Settings(
        baserow_url=url,
        baserow_token=token,
        brands_table_id=int(table_id),
        field_brand_name=os.getenv("FIELD_BRAND_NAME", "field_8036").strip(),
        field_domain=os.getenv("FIELD_DOMAIN", "field_8037").strip(),
        field_website_url=os.getenv("FIELD_WEBSITE_URL", "").strip(),
        field_brand_quote=os.getenv("FIELD_BRAND_QUOTE", "field_8038").strip(),
        field_products=os.getenv("FIELD_PRODUCTS", "field_8039").strip(),
        field_bg_remove=os.getenv("FIELD_BG_REMOVE", "field_8041").strip(),
        field_scrape_status=os.getenv("FIELD_SCRAPE_STATUS", "").strip(),
        field_scrape_error=os.getenv("FIELD_SCRAPE_ERROR", "").strip(),
        field_last_scraped=os.getenv("FIELD_LAST_SCRAPED", "").strip(),
        field_page_title=os.getenv("FIELD_PAGE_TITLE", "").strip(),
        field_meta_description=os.getenv(
            "FIELD_META_DESCRIPTION", ""
        ).strip(),
        scrape_only_statuses=_split_statuses(
            os.getenv("SCRAPE_ONLY_STATUSES", "pending,")
        ),
        scrape_only_bg_remove=_env_bool("SCRAPE_ONLY_BG_REMOVE", False),
        skip_if_brand_quote=_env_bool("SKIP_IF_BRAND_QUOTE", True),
        skip_if_has_products=_env_bool("SKIP_IF_HAS_PRODUCTS", False),
        scrape_delay_seconds=float(os.getenv("SCRAPE_DELAY_SECONDS", "2")),
        http_timeout=float(os.getenv("HTTP_TIMEOUT", "30")),
    )
