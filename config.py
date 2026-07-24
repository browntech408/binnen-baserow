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
    products_table_id: int
    category_table_id: int
    subcategory_table_id: int
    field_brand_name: str
    field_domain: str
    field_website_url: str
    field_brand_quote: str
    field_products: str
    field_bg_remove: str
    field_product_name: str
    field_product_description: str
    field_product_url: str
    field_product_status: str
    field_designer: str
    field_designer_description: str
    field_designer_image: str
    field_source_category: str
    field_source_subcategory: str
    field_price: str
    field_product_category: str
    field_sub_category: str
    field_brand_link: str
    field_product_images: str
    field_hero_images: str
    field_lifestyle_images: str
    field_detail_image: str
    upload_product_images: bool
    max_product_images_upload: int
    max_lifestyle_images_upload: int
    field_category_name: str
    field_subcategory_name: str
    field_subcategory_parent: str
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
    openrouter_api_key: str
    openrouter_model: str
    ai_product_descriptions: bool
    field_ai_description_nl: str
    field_accordion_product_description: str
    shopify_metafield_namespace: str
    shopify_metafield_category: str
    shopify_metafield_sub_category: str
    shopify_metafield_lifestyle_images: str
    shopify_metafield_designer: str
    shopify_metafield_designer_image: str

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
        products_table_id=int(os.getenv("PRODUCTS_TABLE_ID", "802")),
        category_table_id=int(os.getenv("CATEGORY_TABLE_ID", "806")),
        subcategory_table_id=int(os.getenv("SUBCATEGORY_TABLE_ID", "807")),
        field_brand_name=os.getenv("FIELD_BRAND_NAME", "field_8323").strip(),
        field_domain=os.getenv("FIELD_DOMAIN", "field_8324").strip(),
        field_website_url=os.getenv("FIELD_WEBSITE_URL", "").strip(),
        field_brand_quote=os.getenv("FIELD_BRAND_QUOTE", "field_8325").strip(),
        field_products=os.getenv("FIELD_PRODUCTS", "field_8326").strip(),
        field_bg_remove=os.getenv("FIELD_BG_REMOVE", "field_8328").strip(),
        field_product_name=os.getenv("FIELD_PRODUCT_NAME", "field_8224").strip(),
        field_product_description=os.getenv(
            "FIELD_PRODUCT_DESCRIPTION", "field_8225"
        ).strip(),
        field_product_url=os.getenv("FIELD_PRODUCT_URL", "field_8229").strip(),
        field_product_status=os.getenv("FIELD_PRODUCT_STATUS", "field_8230").strip(),
        field_designer=os.getenv("FIELD_DESIGNER", "field_8233").strip(),
        field_designer_description=os.getenv(
            "FIELD_DESIGNER_DESCRIPTION", "field_8234"
        ).strip(),
        field_designer_image=os.getenv(
            "FIELD_DESIGNER_IMAGE", "field_8232"
        ).strip(),
        field_source_category=os.getenv(
            "FIELD_SOURCE_CATEGORY", "field_8245"
        ).strip(),
        field_source_subcategory=os.getenv(
            "FIELD_SOURCE_SUBCATEGORY", "field_8246"
        ).strip(),
        field_price=os.getenv("FIELD_PRICE", "field_8248").strip(),
        field_product_category=os.getenv(
            "FIELD_PRODUCT_CATEGORY", "field_8240"
        ).strip(),
        field_sub_category=os.getenv("FIELD_SUB_CATEGORY", "field_8241").strip(),
        field_brand_link=os.getenv("FIELD_BRAND_LINK", "field_8253").strip(),
        field_product_images=os.getenv("FIELD_PRODUCT_IMAGES", "field_8226").strip(),
        field_hero_images=os.getenv("FIELD_HERO_IMAGES", "field_8235").strip(),
        field_lifestyle_images=os.getenv(
            "FIELD_LIFESTYLE_IMAGES", "field_8236"
        ).strip(),
        field_detail_image=os.getenv("FIELD_DETAIL_IMAGE", "field_8237").strip(),
        upload_product_images=_env_bool("UPLOAD_PRODUCT_IMAGES", True),
        max_product_images_upload=int(os.getenv("MAX_PRODUCT_IMAGES_UPLOAD", "0")),
        max_lifestyle_images_upload=int(os.getenv("MAX_LIFESTYLE_IMAGES_UPLOAD", "0")),
        field_category_name=os.getenv("FIELD_CATEGORY_NAME", "field_8329").strip(),
        field_subcategory_name=os.getenv(
            "FIELD_SUBCATEGORY_NAME", "field_8333"
        ).strip(),
        field_subcategory_parent=os.getenv(
            "FIELD_SUBCATEGORY_PARENT", "field_8334"
        ).strip(),
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
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip().strip('"'),
        openrouter_model=os.getenv(
            "OPENROUTER_MODEL", "openai/gpt-4o-mini"
        ).strip(),
        ai_product_descriptions=_env_bool("AI_PRODUCT_DESCRIPTIONS", False),
        field_ai_description_nl=os.getenv(
            "FIELD_AI_DESCRIPTION_NL", "field_8239"
        ).strip(),
        field_accordion_product_description=os.getenv(
            "FIELD_ACCORDION_PRODUCT_DESCRIPTION", "field_8287"
        ).strip(),
        shopify_metafield_namespace=os.getenv(
            "SHOPIFY_METAFIELD_NAMESPACE", "custom"
        ).strip(),
        shopify_metafield_category=os.getenv(
            "SHOPIFY_METAFIELD_CATEGORY", "product_category"
        ).strip(),
        shopify_metafield_sub_category=os.getenv(
            "SHOPIFY_METAFIELD_SUB_CATEGORY", "sub_category"
        ).strip(),
        shopify_metafield_lifestyle_images=os.getenv(
            "SHOPIFY_METAFIELD_LIFESTYLE_IMAGES", "lifestyle_images"
        ).strip(),
        shopify_metafield_designer=os.getenv(
            "SHOPIFY_METAFIELD_DESIGNER", "designer"
        ).strip(),
        shopify_metafield_designer_image=os.getenv(
            "SHOPIFY_METAFIELD_DESIGNER_IMAGE", "designer_image"
        ).strip(),
    )
