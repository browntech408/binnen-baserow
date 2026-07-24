"""Clean scraped product descriptions (footer noise, junk paragraphs)."""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

FOOTER_SNIPPETS = (
    "kvk:",
    "btw:",
    "nieuwsbrief",
    "newsletter",
    "subscribe to our",
    "subscribe to our newsletter",
    "ramgatseweg",
    "raamsdonksveer",
    "vul je hieronder",
    "mailadres in om",
    "[email protected]",
    "email protected",
    "showroom – fabriek",
    "showroom - fabriek",
    "showroom fabriek",
    "privacy en cookies",
    "disclaimer",
    "always up to date with the latest pode",
    "find your dealer",
    "label produkties b.v",
)

JUNK_PARAGRAPH_SNIPPETS = FOOTER_SNIPPETS + (
    "reviews can only be submitted",
    "please enter your login details",
    "check the faq page for more information",
)


def is_footer_text(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    return any(snippet in low for snippet in FOOTER_SNIPPETS)


def is_junk_paragraph(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    if len(low) < 25:
        return True
    return any(snippet in low for snippet in JUNK_PARAGRAPH_SNIPPETS)


def strip_footer_from_description(text: str) -> str:
    """Drop footer blocks and anything after the first footer marker."""
    if not text:
        return ""

    cleaned = text.strip()
    low = cleaned.lower()
    cut_at: int | None = None
    for snippet in FOOTER_SNIPPETS:
        idx = low.find(snippet)
        if idx >= 0 and (cut_at is None or idx < cut_at):
            cut_at = idx
    if cut_at is not None:
        cleaned = cleaned[:cut_at].strip()

    parts: list[str] = []
    for block in re.split(r"\n\n+", cleaned):
        block = block.strip()
        if not block or is_footer_text(block):
            break
        parts.append(block)
    return "\n\n".join(parts).strip()


def description_from_json_ld(soup: BeautifulSoup) -> str:
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") != "Product":
                continue
            desc = (node.get("description") or "").strip()
            if len(desc) > 80:
                return strip_footer_from_description(desc)
    return ""


def clean_product_description(text: str) -> str:
    return strip_footer_from_description(text)
