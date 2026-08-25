"""3-Tier Recommendation Algorithm & Frequently Bought Together (FBT) Bundle Engine.

Tier 1: Sub-category alignment & complementary cross-category mapping.
Tier 2: Visual style vector cosine similarity (13-dimensional style attributes).
Tier 3: Quality Score ranking & tie-breaker (explicit score or dynamic fallback quality score).
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple


STYLE_FIELDS = [
    ("field_7378", "Scandinavian"),
    ("field_7379", "Japandi"),
    ("field_7380", "Minimalist"),
    ("field_7381", "Organic Shapes"),
    ("field_7382", "Maximalist"),
    ("field_7383", "Art Deco Revival"),
    ("field_7384", "Bohemian Luxe"),
    ("field_7385", "Retro / Mid-Century"),
    ("field_7386", "Industrial/urban shic"),
    ("field_7387", "Duurzaamheid"),
    ("field_7388", "Modulariteit"),
    ("field_7389", "Revival of Classics"),
    ("field_7390", "Landelijk"),
]

# Complementary subcategory matrix for FBT & Cross-Selling
COMPLEMENTARY_SUBCATEGORIES: Dict[str, List[str]] = {
    # Sofas -> Coffee tables, Armchairs, Rugs, Lighting, Side tables
    "banken": ["salontafel", "fauteuils", "vloerkleden", "verlichting", "bijzettafel"],
    "2-zitsbank": ["salontafel", "fauteuils", "vloerkleden", "bijzettafel", "verlichting"],
    "3-zitsbank": ["salontafel", "fauteuils", "vloerkleden", "bijzettafel", "verlichting"],
    "4-zitsbank": ["salontafel", "fauteuils", "vloerkleden", "bijzettafel", "verlichting"],
    "hoekbanken": ["salontafel", "fauteuils", "vloerkleden", "bijzettafel", "verlichting"],
    "fauteuils": ["salontafel", "bijzettafel", "vloerkleden", "banken", "verlichting"],
    
    # Dining Tables -> Dining Chairs, Benches, Pendant Lights, Sideboards
    "eettafel": ["eetkamerstoelen", "eetkamerbanken", "verlichting", "opbergkasten", "woonaccessoires"],
    "tafels": ["eetkamerstoelen", "salontafel", "bijzettafel", "verlichting"],
    "eetkamerstoelen": ["eettafel", "verlichting", "opbergkasten"],
    "eetkamerbanken": ["eettafel", "verlichting"],
    
    # Tables & Cabinets
    "salontafel": ["banken", "hoekbanken", "fauteuils", "vloerkleden", "bijzettafel"],
    "bijzettafel": ["banken", "fauteuils", "salontafel", "verlichting"],
    "tv meubel": ["banken", "salontafel", "vloerkleden", "woonaccessoires"],
    "opbergkasten": ["eettafel", "eetkamerstoelen", "verlichting"],
    
    # Rugs & Decor
    "vloerkleden": ["banken", "salontafel", "fauteuils", "eettafel"],
    "verlichting": ["banken", "eettafel", "salontafel", "fauteuils"],
}


def compute_quality_score(product: Dict[str, Any]) -> float:
    """Calculate explicit Quality Score or dynamic fallback score (0-100)."""
    explicit_score = product.get("field_7394") or product.get("Score")
    if explicit_score is not None:
        try:
            val = float(explicit_score)
            if val > 0:
                return min(100.0, max(0.0, val))
        except (ValueError, TypeError):
            pass

    score = 0.0

    # 1. Asset Completeness (max 40 pts)
    heroes = product.get("field_7358") or product.get("hero_images")
    lifestyles = product.get("field_7359") or product.get("lifestyle_images")
    details = product.get("field_7360") or product.get("detail_image")
    raws = product.get("field_7349") or product.get("product_images")

    if heroes and len(heroes) > 0:
        score += 15.0
    if lifestyles and len(lifestyles) > 0:
        score += 15.0
    if details and len(details) > 0:
        score += 10.0
    elif raws and len(raws) > 0:
        score += 5.0

    # 2. Description Richness (max 30 pts)
    ai_desc = product.get("field_7362") or product.get("ai_description_translated_NL") or ""
    std_desc = product.get("field_7348") or product.get("product_description") or ""

    if len(str(ai_desc).strip()) > 50:
        score += 20.0
    elif len(str(std_desc).strip()) > 50:
        score += 15.0

    if len(str(std_desc).strip()) > 200:
        score += 10.0

    # 3. Taxonomy & Metadata Completeness (max 30 pts)
    brand = product.get("field_7376") or product.get("Brand_table")
    cat = product.get("field_7363") or product.get("product_category")
    subcat = product.get("field_7364") or product.get("sub_category")

    if brand:
        score += 10.0
    if cat:
        score += 10.0
    if subcat:
        score += 10.0

    return min(100.0, max(0.0, score))


def extract_style_vector(product: Dict[str, Any]) -> List[float]:
    """Extract 13-dimensional style vector from numeric fields or text fallback."""
    vec = []
    has_numeric = False
    for fkey, _ in STYLE_FIELDS:
        val = product.get(fkey)
        if val is not None and val != "":
            try:
                num = float(val)
                vec.append(num)
                if num > 0:
                    has_numeric = True
            except (ValueError, TypeError):
                vec.append(0.0)
        else:
            vec.append(0.0)

    if has_numeric:
        return vec

    # Text fallback from trend_classification, name, or description
    trend_text = str(product.get("field_7391") or product.get("trend_classification") or "").lower()
    name_text = str(product.get("field_7347") or product.get("product_name") or "").lower()
    desc_text = str(product.get("field_7348") or product.get("product_description") or "").lower()
    full_text = f"{trend_text} {name_text} {desc_text}"

    fallback_vec = []
    for _, style_name in STYLE_FIELDS:
        style_key = style_name.lower().split("/")[0].strip()
        if style_key in full_text or (len(style_key) > 4 and style_key[:4] in full_text):
            fallback_vec.append(80.0)
        else:
            fallback_vec.append(10.0)
    return fallback_vec


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two style vectors."""
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def extract_linked_name(val: Any) -> str:
    """Extract text name from Baserow link_row value or string."""
    if isinstance(val, list) and val:
        item = val[0]
        if isinstance(item, dict):
            return str(item.get("value") or "").strip().lower()
        return str(item).strip().lower()
    if isinstance(val, str):
        return val.strip().lower()
    return ""


def calculate_subcat_alignment(p1: Dict[str, Any], p2: Dict[str, Any]) -> float:
    """Tier 1: Calculate sub-category alignment score (0.0 to 1.0)."""
    sub1 = extract_linked_name(p1.get("field_7364") or p1.get("sub_category"))
    sub2 = extract_linked_name(p2.get("field_7364") or p2.get("sub_category"))
    cat1 = extract_linked_name(p1.get("field_7363") or p1.get("product_category"))
    cat2 = extract_linked_name(p2.get("field_7363") or p2.get("product_category"))

    if not sub1:
        sub1 = str(p1.get("field_7369") or p1.get("source_product_subcategory") or "").lower()
    if not sub2:
        sub2 = str(p2.get("field_7369") or p2.get("source_product_subcategory") or "").lower()
    if not cat1:
        cat1 = str(p1.get("field_7368") or p1.get("source_product_category") or "").lower()
    if not cat2:
        cat2 = str(p2.get("field_7368") or p2.get("source_product_category") or "").lower()

    # Check Complementary subcategories (Cross-selling / FBT)
    for key, complements in COMPLEMENTARY_SUBCATEGORIES.items():
        if key in sub1:
            for comp in complements:
                if comp in sub2:
                    return 1.0  # Perfect complementary match!

    # Same subcategory
    if sub1 and sub2 and (sub1 == sub2 or sub1 in sub2 or sub2 in sub1):
        return 0.85

    # Same top category
    if cat1 and cat2 and (cat1 == cat2 or cat1 in cat2 or cat2 in cat1):
        return 0.60

    return 0.20


class RecommendationEngine:
    """3-Tier Recommendation Engine for Catalog Products."""

    def __init__(self, products: List[Dict[str, Any]]) -> None:
        self.products = products
        self.vectors: Dict[int, List[float]] = {}
        self.scores: Dict[int, float] = {}

        # Precompute vectors and quality scores for fast matching
        for p in self.products:
            pid = p["id"]
            self.vectors[pid] = extract_style_vector(p)
            self.scores[pid] = compute_quality_score(p)

    def get_recommendations(
        self,
        target_product: Dict[str, Any],
        top_k: int = 4,
        store_target: str = "woonbloq"
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        Calculate top_k recommended products for target_product using 3-tier algorithm.
        Returns list of (candidate_product, match_score).
        """
        target_id = target_product["id"]
        target_vec = self.vectors.get(target_id, extract_style_vector(target_product))
        target_brand = extract_linked_name(target_product.get("field_7376") or target_product.get("Brand_table"))

        # Determine target store product ID field
        gid_field = "field_7425"  # WoonbloqProductID default
        if store_target == "binnen":
            gid_field = "field_7407"
        elif store_target == "sleepworld":
            gid_field = "field_7426"

        candidates: List[Tuple[Dict[str, Any], float]] = []

        for p in self.products:
            pid = p["id"]
            if pid == target_id:
                continue

            # Ensure candidate has a valid Shopify Product GID
            shopify_gid = p.get(gid_field) or p.get("field_7425") or p.get("field_7407")
            if not shopify_gid or not str(shopify_gid).startswith("gid://shopify/Product/"):
                continue

            # Tier 1: Sub-category alignment (0.0 to 1.0)
            subcat_score = calculate_subcat_alignment(target_product, p)

            # Tier 2: Style similarity (0.0 to 1.0)
            style_score = cosine_similarity(target_vec, self.vectors.get(pid, []))

            # Tier 3: Quality Score (0.0 to 1.0 normalized)
            qual_score = self.scores.get(pid, 50.0) / 100.0

            # Brand Alignment bonus (+0.05)
            cand_brand = extract_linked_name(p.get("field_7376") or p.get("Brand_table"))
            brand_bonus = 0.05 if (target_brand and cand_brand and target_brand == cand_brand) else 0.0

            # Combined 3-tier score formula
            final_score = (0.45 * subcat_score) + (0.35 * style_score) + (0.20 * qual_score) + brand_bonus
            candidates.append((p, final_score))

        # Sort by final score descending
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_k]

    def get_fbt_bundles(self, target_product: Dict[str, Any], top_k: int = 2) -> List[Dict[str, Any]]:
        """Get 'Frequently Bought Together' bundle complementary products."""
        recs = self.get_recommendations(target_product, top_k=10)
        # Filter for candidates with high subcategory alignment (>0.8, i.e., complementary)
        fbt_list = [p for p, score in recs if calculate_subcat_alignment(target_product, p) >= 0.8]
        return fbt_list[:top_k]
