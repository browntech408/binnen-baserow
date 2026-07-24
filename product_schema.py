"""
productsDetails table columns — what we scrape vs what is generated later in Baserow/workflows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Columns we try to fill from the website (scrape)
SCRAPE_FIELDS = [
    "product_name",
    "product_description",
    "product_images",
    "product_url",
    "Status",
    "designer",
    "designerDescription",
    "designerImage",
    "hero_images",
    "lifestyle_images",
    "detail_image",
    "product_category",
    "sub_category",
    "source_product_category",
    "source_product_subcategory",
    "price",
    "Brand_table",
]

# Filled by Baserow / n8n / AI later — not from website scrape
GENERATED_LATER = [
    "id",
    "Row ID",
    "UUID",
    "3d _models",
    "cinematic_video",
    "Rank",
    "designerImage",
    "last_update",
    "ai_description_translated_NL",
    "Created on",
    "spotlight_new",
    "spotlight_hot",
    "styleType",
    "create_enhanced_image",
    "create_3d_models",
    "create_cinematic_video",
    "stores",
    "Generate Category and Subcategory",
    "image_classification",
    "Scandinavian",
    "Japandi",
    "Minimalist",
    "trend_classification",
    "Configurable",
    "Send To Shop",
    "Score",
    "Compound Key",
    "last_hash",
    "material_name",
    "material_images",
    "upholstery_name",
    "upholstery_images",
    "bg_removed_hero",
    "final_hero_image",
    "SendToShopify",
    "BinnenStatus",
    "Accordion_Product_Description",
    "qr_code",
    "WoonbloqProductID",
    "SleepworldProductID",
]


@dataclass
class ScrapedProduct:
    """One product row mapped toward productsDetails columns."""

    product_name: str = ""
    product_description: str = ""
    product_images: list[str] = field(default_factory=list)
    local_image_files: list[str] = field(default_factory=list)
    product_url: str = ""
    Status: str = "active"
    designer: str = ""
    designerDescription: str = ""
    designerImage: str = ""
    local_designer_image_file: str = ""
    hero_images: list[str] = field(default_factory=list)
    lifestyle_images: list[str] = field(default_factory=list)
    detail_image: str = ""
    product_category: str = ""
    sub_category: str = ""
    source_product_category: str = ""
    source_product_subcategory: str = ""
    price: str = ""
    Brand_table: str = ""
    ai_description_translated_NL: str = ""
    Accordion_Product_Description: str = ""

    scrape_ok: bool = True
    scrape_error: str = ""

    def to_report_dict(self) -> dict:
        return {
            "product_name": self.product_name,
            "product_description": (self.product_description or "").strip(),
            "ai_description_translated_NL": (
                self.ai_description_translated_NL or ""
            ).strip(),
            "product_images": self.product_images,
            "product_url": self.product_url,
            "Status": self.Status,
            "designer": self.designer,
            "designerDescription": self._short(self.designerDescription, 200),
            "designerImage": self.designerImage,
            "hero_images": self.hero_images[:3],
            "lifestyle_images": self.lifestyle_images[:3],
            "detail_image": self.detail_image,
            "product_category": self.product_category,
            "sub_category": self.sub_category,
            "source_product_category": self.source_product_category,
            "source_product_subcategory": self.source_product_subcategory,
            "price": self.price,
            "Brand_table": self.Brand_table,
            "scrape_ok": self.scrape_ok,
            "scrape_error": self.scrape_error,
        }

    @staticmethod
    def _short(text: str, n: int) -> str:
        text = (text or "").strip()
        if len(text) <= n:
            return text
        return text[: n - 3] + "..."
