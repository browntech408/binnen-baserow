"""Generate a high-quality multi-page English PDF report with Baserow product counts."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from baserow_client import BaserowClient
from brand_scraper import extract_brand_name, row_field
from config import Settings, load_settings


COMPLETED_BRANDS = [
    # (num, name, domain, source)
    (1, "Artifort", "www.artifort.com", "scrape"),
    (2, "Baenks", "www.baenks.nl", "scrape"),
    (3, "Beek Collection", "www.beekcollection.nl", "scrape"),
    (4, "Bert Plantagie", "www.bertplantagie.com", "scrape"),
    (5, "Brees New World", "www.breesnewworld.nl", "scrape"),
    (6, "Brinker", "www.brinkercarpets.nl", "scrape"),
    (7, "CS Rugs", "www.csrugs.com", "scrape"),
    (8, "Castelijn", "www.castelijn.nl", "scrape"),
    (9, "DesignOnStock", "www.designonstock.com", "scrape"),
    (10, "Estiluz", "www.estiluz.com", "scrape"),
    (11, "Eyye", "www.eyye.nl", "scrape"),
    (12, "Fontana Arte", "www.fontanaarte.com", "scrape"),
    (13, "Gazzda", "www.gazzda.com", "scrape"),
    (14, "Gealux", "www.gealux.nl", "scrape"),
    (15, "Harvink", "www.harvink.nl", "scrape"),
    (16, "House of Dutchz", "www.houseofdutchz.nl", "scrape"),
    (17, "Janssens Orient", "www.janssens-orient.nl (carpetrebel.com)", "scrape"),
    (18, "Jori", "www.jori.com", "scrape"),
    (19, "Label", "www.label.nl", "scrape"),
    (20, "Leolux", "www.leolux.nl", "scrape"),
    (21, "Metaform", "www.metaformmeubelen.nl", "scrape"),
    (22, "Montis", "www.montis.nl", "scrape"),
    (23, "Pastoe", "www.pastoe.com", "scrape"),
    (24, "Pode", "www.pode.eu", "scrape"),
    (25, "Sleepworld", "www.sleepworldhelmond.nl", "scrape"),
    (26, "Spectrum Design", "www.spectrumdesign.nl", "scrape"),
    (27, "Tonone", "www.tonone.com", "scrape"),
    (28, "Pronto Wonen", "www.prontowonen.nl", "xml feed"),
    (29, "Profijt Meubel", "www.profijtmeubel.nl", "xml feed"),
    (30, "IN.HOUSE", "www.in-house.nl", "xml feed"),
]

PENDING_BRANDS = [
    (1, "Artimeta", "www.artimeta.nl", "Unable to find patterns"),
    (2, "Evidence", "www.evidence-living.com", "Domain inactive / parked ('Gereserveerd' holding page; sub-brand of Leolux)"),
    (3, "Odesi", "www.odesi.nl", "Website offline (HTTP 500 Internal Server Error; site currently unreachable)"),
]

# XML feeds that were provided but not used (scraped instead)
UNUSED_XML_FEEDS = [
    (
        1,
        "House of Dutchz",
        "https://www.houseofdutchz.nl/var/feeds/woonbloq.xml",
        "XML feed product data was incomplete (e.g. product URLs were missing). Website scrape was used instead to get complete product data.",
    ),
    (
        2,
        "Baenks",
        "https://www.baenks.nl/feeds/baenks_wk_woonbloq.xml",
        "This brand was already scraped at the start of the brand-scrape phase, so the XML feed was not used afterwards.",
    ),
]

# AI cost estimate for client quote.
# Keep est_amount + scope as the client-facing quote (includes testing / growth buffer).
# Live API fetch only refreshes unit_cost rates — it must NOT overwrite est_amount/scope.
AI_COST_ROWS_FALLBACK = [
    {
        "work": "Product description (NL rewrite)",
        "provider": "OpenRouter",
        "model": "openai/gpt-4o-mini",
        "unit_cost": "$0.15 / 1M input + $0.60 / 1M output tokens",
        "est_amount": "~$15-$30",
        "scope": "Estimated API spend (gpt-4o-mini)",
        "notes": "Flat estimate — not multiplied by catalog size",
    },
    {
        "work": "Detail / lifestyle image generation",
        "provider": "fal.ai + OpenRouter",
        "model": "fal-ai/flux-2-pro/edit + openai/gpt-4o vision",
        "unit_cost": "Fal ~$0.03-$0.045 / image; GPT-4o vision planning extra",
        "est_amount": "~$280-$350",
        "scope": "~7,000 detail images (+ lifestyle if needed)",
        "notes": "Default engine flux-2-pro/edit",
    },
    {
        "work": "Image categorization",
        "provider": "OpenRouter",
        "model": "openai/gpt-4o",
        "unit_cost": "$2.50 / 1M input + $10.00 / 1M output tokens",
        "est_amount": "~$20-$30",
        "scope": "Remaining ~200-250 products (GPT-4o)",
        "notes": "Hero / lifestyle / detail labels; ~5k already done",
    },
    {
        "work": "Background removal / resize",
        "provider": "fal.ai",
        "model": "fal-ai/birefnet/v2",
        "unit_cost": "$0.0008 / compute second (~$0.002 / typical image)",
        "est_amount": "~$10-$12",
        "scope": "~5,000-6,000 hero images",
        "notes": "Skipped if already transparent",
    },
]

# Dummy / test brands — never show in the PDF
EXCLUDED_DUMMY_BRANDS = {
    "copy",
    "image categorization",
}

# Map report brand names -> Baserow brand name aliases for matching
NAME_ALIASES: dict[str, list[str]] = {
    "beek collection": ["beek", "beek collection"],
    "designonstock": ["design on stock", "designonstock"],
    "sleepworld": ["sleep world", "sleepworld"],
    "janssens orient": ["janssens orient", "janssens oriënt", "janssens orient"],
    "in.house": ["in.house", "inhouse", "in house"],
    "pronto wonen": ["pronto wonen"],
    "profijt meubel": ["profijt meubel"],
}


def _norm(name: str) -> str:
    text = name.lower().strip()
    text = text.replace("ë", "e").replace("ö", "o").replace("ü", "u")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ascii(text: str) -> str:
    """FPDF core fonts are Latin-1; normalize unsupported chars."""
    replacements = {
        "ë": "e",
        "ö": "o",
        "ü": "u",
        "ä": "a",
        "ï": "i",
        "–": "-",
        "—": "-",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "…": "...",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def fetch_baserow_brand_data(
    settings: Settings,
) -> tuple[dict[str, int], int, list[dict[str, str | int]]]:
    """Return counts by name, total products, and brand rows with product counts."""
    client = BaserowClient(settings)
    brand_rows: list[dict[str, str | int]] = []
    linked_by_id: dict[int, int] = {}
    for row in client.list_table_rows(settings.brands_table_id):
        bid = int(row["id"])
        name = extract_brand_name(row, settings.field_brand_name)
        domain = str(row_field(row, settings.field_domain) or "").strip()
        if not domain and settings.field_website_url:
            domain = str(row_field(row, settings.field_website_url) or "").strip()
        linked = row.get(settings.field_products) or []
        linked_by_id[bid] = len(linked) if isinstance(linked, list) else 0
        brand_rows.append({"id": bid, "name": name, "domain": domain})

    counts_by_id: Counter[int] = Counter()
    total = 0
    for row in client.list_table_rows(settings.products_table_id):
        total += 1
        links = row.get(settings.field_brand_link) or []
        for link in links:
            bid = link.get("id") if isinstance(link, dict) else link
            if bid is not None:
                counts_by_id[int(bid)] += 1

    by_name: dict[str, int] = {}
    enriched: list[dict[str, str | int]] = []
    for brand in brand_rows:
        bid = int(brand["id"])
        name = str(brand["name"])
        count = max(counts_by_id.get(bid, 0), linked_by_id.get(bid, 0))
        key = _norm(name)
        by_name[key] = by_name.get(key, 0) + count
        enriched.append(
            {
                "id": bid,
                "name": name,
                "domain": str(brand["domain"]),
                "products": count,
            }
        )

    return by_name, total, enriched


def lookup_count(counts: dict[str, int], brand_name: str) -> int:
    """Resolve count across exact name, aliases, and fuzzy matches (take max)."""
    key = _norm(brand_name)
    candidates = {key}
    for alias in NAME_ALIASES.get(key, []):
        candidates.add(_norm(alias))

    best = 0
    found = False
    for candidate in candidates:
        if candidate in counts:
            found = True
            best = max(best, counts[candidate])

    if found:
        return best

    # Fuzzy contains match (e.g. Beek vs Beek Collection)
    for stored_key, value in counts.items():
        if key in stored_key or stored_key in key:
            best = max(best, value)
            found = True
    return best if found else 0


def _covered_name_keys() -> set[str]:
    """Normalized names already represented in the static PDF sections."""
    keys: set[str] = set()
    for _, name, _, _ in COMPLETED_BRANDS:
        keys.add(_norm(name))
        for alias in NAME_ALIASES.get(_norm(name), []):
            keys.add(_norm(alias))
    for _, name, _, _ in PENDING_BRANDS:
        keys.add(_norm(name))
    return keys


def _is_covered(brand_name: str, covered: set[str]) -> bool:
    key = _norm(brand_name)
    if key in covered:
        return True
    for ek in covered:
        if key in ek or ek in key:
            return True
    return False


def find_extra_brands_with_products(
    brand_rows: list[dict[str, str | int]],
) -> list[tuple[str, str, int]]:
    """Brands that have products in Baserow but are not in the static PDF lists."""
    covered = _covered_name_keys()
    extras: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    for brand in sorted(
        brand_rows,
        key=lambda b: (-int(b["products"]), str(b["name"]).lower()),
    ):
        name = str(brand["name"])
        products = int(brand["products"])
        if products <= 0:
            continue
        key = _norm(name)
        if key in EXCLUDED_DUMMY_BRANDS:
            continue
        if _is_covered(name, covered):
            continue
        if key in seen:
            continue
        seen.add(key)
        domain = str(brand["domain"] or "").strip() or "-"
        extras.append((name, domain, products))
    return extras


def fetch_live_ai_cost_rows() -> tuple[list[dict[str, str]], str]:
    """Refresh unit_cost from OpenRouter + Fal only. Keep client est_amount/scope as-is."""
    import os

    import requests

    rows = [dict(r) for r in AI_COST_ROWS_FALLBACK]
    sources: list[str] = []

    # OpenRouter live model rates (unit cost only)
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=45)
        resp.raise_for_status()
        by_id = {m["id"]: m for m in resp.json().get("data", [])}

        def or_rate(model_id: str) -> tuple[float, float]:
            pricing = (by_id.get(model_id) or {}).get("pricing") or {}
            pin = float(pricing.get("prompt") or 0) * 1_000_000
            pout = float(pricing.get("completion") or 0) * 1_000_000
            return pin, pout

        mini_in, mini_out = or_rate("openai/gpt-4o-mini")
        gpt4_in, gpt4_out = or_rate("openai/gpt-4o")

        desc_per = (994 * mini_in + 150 * mini_out) / 1_000_000
        rows[0]["unit_cost"] = (
            f"${mini_in:.2f}/1M in + ${mini_out:.2f}/1M out (~${desc_per:.5f}/call)"
        )
        rows[2]["model"] = "openai/gpt-4o"
        rows[2]["unit_cost"] = f"${gpt4_in:.2f}/1M in + ${gpt4_out:.2f}/1M out"
        sources.append("OpenRouter /api/v1/models")
    except Exception as exc:
        sources.append(f"OpenRouter fallback ({type(exc).__name__})")

    # Fal live endpoint prices (unit cost only)
    try:
        fal_key = os.getenv("FAL_KEY", "").strip()
        if fal_key:
            endpoint_ids = [
                "fal-ai/flux-2-pro/edit",
                "fal-ai/birefnet/v2",
            ]
            params = [("endpoint_id", eid) for eid in endpoint_ids]
            resp = requests.get(
                "https://api.fal.ai/v1/models/pricing",
                params=params,
                headers={"Authorization": f"Key {fal_key}"},
                timeout=30,
            )
            resp.raise_for_status()
            price_map = {
                item["endpoint_id"]: item
                for item in (resp.json().get("prices") or [])
                if item.get("endpoint_id")
            }
            flux = price_map.get("fal-ai/flux-2-pro/edit") or {}
            rembg = price_map.get("fal-ai/birefnet/v2") or {}
            flux_unit = float(flux.get("unit_price") or 0.03)
            flux_unit_name = str(flux.get("unit") or "processed megapixels")
            rembg_unit = float(rembg.get("unit_price") or 0.0008)
            rembg_unit_name = str(rembg.get("unit") or "compute seconds")
            per_img_high = flux_unit + 0.015
            rembg_per_image = rembg_unit * 2.5

            rows[1]["unit_cost"] = (
                f"Fal ${flux_unit:.3f}-{per_img_high:.3f}/{flux_unit_name}; "
                f"GPT-4o vision planning extra"
            )
            rows[3]["unit_cost"] = (
                f"${rembg_unit:.4f}/{rembg_unit_name} (~${rembg_per_image:.4f}/image)"
            )
            sources.append("Fal /v1/models/pricing")
        else:
            sources.append("Fal fallback (no FAL_KEY)")
    except Exception as exc:
        sources.append(f"Fal fallback ({type(exc).__name__})")

    verified = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    source_note = f"Verified {verified} via: " + "; ".join(sources)
    return rows, source_note


class StatusReportPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(14, 14, 14)
        self.alias_nb_pages()

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(24, 43, 73)
        self.cell(0, 6, "Brands & XML Feeds Integration Status Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(203, 213, 225)
        self.line(14, self.get_y(), 196, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(226, 232, 240)
        self.line(14, self.get_y(), 196, self.get_y())
        self.ln(1.5)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(100, 116, 139)
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.cell(0, 5, f"Generated: {generated} | Binnen Baserow Data Pipeline", new_x=XPos.RIGHT, new_y=YPos.TOP, align="L")
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}}", new_x=XPos.RIGHT, new_y=YPos.TOP, align="R")


def create_report_pdf(output_path: Path, settings: Settings | None = None):
    settings = settings or load_settings()
    print("Fetching product counts from Baserow...")
    counts, total_products, brand_rows = fetch_baserow_brand_data(settings)
    print(f"Baserow total products: {total_products}")

    completed_rows: list[tuple[int, str, str, str, int]] = []
    for num, name, domain, source in COMPLETED_BRANDS:
        completed_rows.append((num, name, domain, source, lookup_count(counts, name)))

    pending_rows: list[tuple[int, str, str, str]] = []
    for num, name, domain, reason in PENDING_BRANDS:
        pending_rows.append((num, name, domain, reason))

    extra_brands = find_extra_brands_with_products(brand_rows)
    extra_rows: list[tuple[int, str, str, int]] = [
        (i, name, domain, products)
        for i, (name, domain, products) in enumerate(extra_brands, start=1)
    ]
    print(f"Extra brands with products (not in prior sections): {len(extra_rows)}")
    for num, name, domain, products in extra_rows:
        print(f"  + {num}. {name} ({domain}) = {products}")

    scraped_total = sum(r[4] for r in completed_rows)
    extra_total = sum(r[3] for r in extra_rows)

    pdf = StatusReportPDF()
    pdf.add_page()

    # Header banner
    pdf.set_fill_color(24, 43, 73)
    pdf.rect(14, 14, 182, 22, "F")
    pdf.set_xy(18, 16.5)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 7, "Brands & XML Feeds Integration Status Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(18)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(186, 210, 235)
    pdf.cell(
        0,
        5,
        _ascii(
            f"Binnen Baserow Project | Live product counts from Baserow "
            f"(table {settings.products_table_id}) | Total products in Baserow: {total_products:,}"
        ),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    pdf.set_y(42)

    # KPI strip
    _render_kpi_strip(
        pdf,
        [
            ("Completed brands", str(len(completed_rows))),
            ("Brand products", f"{scraped_total:,}"),
            ("Pending brands", str(len(pending_rows))),
            ("Baserow total products", f"{total_products:,}"),
        ],
    )
    pdf.ln(4)

    # Section 1
    _render_section_header(
        pdf,
        f"1. Completed Brand Scrapers ({len(completed_rows)} Brands)  |  Products in Baserow: {scraped_total:,}",
        20,
        120,
        65,
    )

    col_s1 = [10, 42, 72, 28, 30]
    _render_table_header(
        pdf,
        ["#", "Brand Name", "Website Domain / Route URL", "Source", "Total Products"],
        col_s1,
        header_bg=(236, 253, 245),
        text_color=(6, 95, 70),
    )

    row_h = 6.2
    for i, (num, name, domain, source, product_count) in enumerate(completed_rows):
        _ensure_space(pdf, row_h + 2)
        bg = (248, 250, 252) if i % 2 == 1 else (255, 255, 255)
        pdf.set_fill_color(*bg)
        pdf.set_draw_color(226, 232, 240)
        pdf.set_text_color(51, 65, 85)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(col_s1[0], row_h, str(num), border=1, align="C", fill=True)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_s1[1], row_h, f"  {_ascii(name)}", border=1, align="L", fill=True)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.cell(col_s1[2], row_h, f"  {_ascii(domain)}", border=1, align="L", fill=True)
        pdf.set_font("Helvetica", "B", 7.5)
        if source == "xml feed":
            pdf.set_text_color(146, 64, 14)
        else:
            pdf.set_text_color(6, 95, 70)
        pdf.cell(col_s1[3], row_h, _ascii(source), border=1, align="C", fill=True)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(6, 95, 70)
        pdf.cell(col_s1[4], row_h, f"{product_count:,}", border=1, align="C", fill=True)
        pdf.ln(row_h)

    # Totals row
    _ensure_space(pdf, row_h + 4)
    pdf.set_fill_color(220, 252, 231)
    pdf.set_draw_color(134, 239, 172)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(22, 101, 52)
    pdf.cell(sum(col_s1[:4]), row_h, "  TOTAL (completed brands)", border=1, align="L", fill=True)
    pdf.cell(col_s1[4], row_h, f"{scraped_total:,}", border=1, align="C", fill=True)
    pdf.ln(row_h + 5)

    # Section 2
    _render_section_header(
        pdf,
        f"2. Pending Brands ({len(pending_rows)} Brands)",
        220,
        38,
        38,
    )

    col_s2 = [10, 36, 50, 86]
    _render_table_header(
        pdf,
        ["#", "Brand Name", "Website Domain", "Reason / Technical Diagnosis"],
        col_s2,
        header_bg=(254, 226, 226),
        text_color=(153, 27, 27),
    )

    for i, (num, name, domain, reason) in enumerate(pending_rows):
        _ensure_space(pdf, row_h + 2)
        bg = (255, 245, 245) if i % 2 == 0 else (255, 255, 255)
        pdf.set_fill_color(*bg)
        pdf.set_draw_color(254, 202, 202)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(51, 65, 85)
        pdf.cell(col_s2[0], row_h, str(num), border=1, align="C", fill=True)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.cell(col_s2[1], row_h, f"  {_ascii(name)}", border=1, align="L", fill=True)
        pdf.set_font("Helvetica", "", 7.2)
        pdf.cell(col_s2[2], row_h, f"  {_ascii(domain)}", border=1, align="L", fill=True)
        pdf.set_text_color(185, 28, 28)
        pdf.cell(col_s2[3], row_h, f"  {_ascii(_truncate(reason, 78))}", border=1, align="L", fill=True)
        pdf.ln(row_h)

    pdf.ln(5)

    # Section 3 — only when real extra brands exist (dummy brands are excluded)
    next_section = 3
    if extra_rows:
        _render_section_header(
            pdf,
            f"{next_section}. Additional Brands in Baserow with Products ({len(extra_rows)} Brands)  |  Products: {extra_total:,}",
            37,
            99,
            235,
        )
        next_section += 1

        col_s3 = [10, 58, 78, 36]
        _render_table_header(
            pdf,
            ["#", "Brand Name", "Website / Domain (Baserow)", "Total Products"],
            col_s3,
            header_bg=(219, 234, 254),
            text_color=(30, 64, 175),
        )

        for i, (num, name, domain, product_count) in enumerate(extra_rows):
            _ensure_space(pdf, row_h + 2)
            bg = (239, 246, 255) if i % 2 == 0 else (255, 255, 255)
            pdf.set_fill_color(*bg)
            pdf.set_draw_color(191, 219, 254)
            pdf.set_text_color(51, 65, 85)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(col_s3[0], row_h, str(num), border=1, align="C", fill=True)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(col_s3[1], row_h, f"  {_ascii(name)}", border=1, align="L", fill=True)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.cell(col_s3[2], row_h, f"  {_ascii(_truncate(domain, 55))}", border=1, align="L", fill=True)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(30, 64, 175)
            pdf.cell(col_s3[3], row_h, f"{product_count:,}", border=1, align="C", fill=True)
            pdf.ln(row_h)

        _ensure_space(pdf, row_h + 4)
        pdf.set_fill_color(219, 234, 254)
        pdf.set_draw_color(147, 197, 253)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(30, 64, 175)
        pdf.cell(sum(col_s3[:3]), row_h, "  TOTAL (additional Baserow brands)", border=1, align="L", fill=True)
        pdf.cell(col_s3[3], row_h, f"{extra_total:,}", border=1, align="C", fill=True)
        pdf.ln(row_h)
        pdf.ln(5)

    # Final section: XML feeds provided but not used (scraped instead)
    _render_section_header(
        pdf,
        f"{next_section}. XML Feeds Not Used (2 of 5)  |  Brands scraped from website instead",
        124,
        58,
        237,
    )
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(71, 85, 105)
    pdf.multi_cell(
        182,
        4,
        _ascii(
            "Client provided 5 XML feeds in total. 3 feeds were imported "
            "(Pronto Wonen, Profijt Meubel, IN.HOUSE). The remaining 2 feeds "
            "were not used for the reasons below; those brands were scraped from the website."
        ),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(2)

    col_unused = [10, 36, 58, 78]
    _render_table_header(
        pdf,
        ["#", "Brand Name", "XML Feed URL", "Why Feed Was Not Used"],
        col_unused,
        header_bg=(237, 233, 254),
        text_color=(91, 33, 182),
    )

    for i, (num, name, feed_url, reason) in enumerate(UNUSED_XML_FEEDS):
        _render_wrapped_row(
            pdf,
            [
                (str(num), col_unused[0], "C", False),
                (name, col_unused[1], "L", True),
                (feed_url, col_unused[2], "L", False),
                (reason, col_unused[3], "L", False),
            ],
            fill_rgb=(245, 243, 255) if i % 2 == 0 else (255, 255, 255),
            border_rgb=(221, 214, 254),
            text_rgb=(51, 65, 85),
            emphasis_rgb=(91, 33, 182),
        )
    next_section += 1

    pdf.ln(5)

    # Final section: AI cost estimate for client
    ai_rows, ai_source_note = fetch_live_ai_cost_rows()
    print("AI cost verification:", ai_source_note)
    for row in ai_rows:
        print(
            f"  - {row['work']}: {row.get('est_amount', '?')} | {row.get('scope', '')} "
            f"({row['provider']} / {row['model']})"
        )

    _render_section_header(
        pdf,
        f"{next_section}. AI Workstreams & Cost Estimate (Client Quote Reference)",
        15,
        118,
        110,
    )
    pdf.set_font("Helvetica", "", 7.4)
    pdf.set_text_color(71, 85, 105)
    pdf.multi_cell(
        182,
        3.8,
        _ascii(
            "AI jobs used for Binnen. Unit prices verified live from OpenRouter and fal.ai. "
            "Estimated amounts below are client quote figures in USD (API cost band with buffer "
            "for testing, retries, and possible scope growth — not agency markup). "
            "Image categorization: ~5,000 products already done; ~200-250 still remaining."
        ),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(1.5)

    col_ai = [8, 36, 40, 52, 46]
    _render_table_header(
        pdf,
        ["#", "AI Work", "Provider / Model", "Scope", "Estimated Amount"],
        col_ai,
        header_bg=(204, 251, 241),
        text_color=(15, 118, 110),
    )

    for i, row in enumerate(ai_rows):
        _render_wrapped_row(
            pdf,
            [
                (str(i + 1), col_ai[0], "C", False),
                (row["work"], col_ai[1], "L", True),
                (f"{row['provider']}\n{row['model']}", col_ai[2], "L", False),
                (row.get("scope", ""), col_ai[3], "L", False),
                (f"{row.get('est_amount', '')}\n{row.get('unit_cost', '')}", col_ai[4], "L", False),
            ],
            fill_rgb=(240, 253, 250) if i % 2 == 0 else (255, 255, 255),
            border_rgb=(153, 246, 228),
            text_rgb=(51, 65, 85),
            emphasis_rgb=(15, 118, 110),
        )

    # Client quote summary — uses the same est_amount/scope as the table above
    pdf.ln(3)
    _ensure_space(pdf, 32)
    pdf.set_fill_color(236, 253, 245)
    pdf.set_draw_color(134, 239, 172)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(22, 101, 52)
    pdf.cell(
        182,
        5.5,
        "  Client planned scope estimate (USD API cost, with testing / growth buffer)",
        border="LTR",
        fill=True,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "", 7.3)
    pdf.set_text_color(51, 65, 85)
    scenario_lines = [
        "1) Product descriptions: ~= $15-$30 (flat estimate; not multiplied by total products)",
        "2) Detail / lifestyle image generation: ~7,000 detail images ~= $280-$350 (lifestyle if needed: same unit rate)",
        "3) Image categorization (GPT-4o): remaining ~200-250 products ~= $20-$30 (~5,000 already done)",
        "4) Background removal / resize: ~5,000-6,000 images ~= $10-$12",
        "Combined planned API spend for client: ~= $400-$600 USD (includes buffer for testing, retries, and scope growth)",
        f"Source check: {_truncate(ai_source_note, 140)}",
    ]
    for idx, line in enumerate(scenario_lines):
        border = "LR" if idx < len(scenario_lines) - 1 else "LRB"
        pdf.cell(182, 4.3, f"  {_ascii(line)}", border=border, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Client demo: Image Categorization + Detail Image Generation (50 products)
    next_section += 1
    demo_grid_url = (
        "https://binnen-baserow.alsoknownas.me/public/grid/"
        "6BoIdK_5UAg5FhcE2tUEJoqBgBddy0iex5Ar_poA4MM"
    )

    pdf.ln(5)
    _render_section_header(
        pdf,
        f"{next_section}. Client Demo — Image Categorization & Detail Image Generation",
        67,
        56,
        202,
    )
    pdf.set_font("Helvetica", "", 7.4)
    pdf.set_text_color(71, 85, 105)
    pdf.multi_cell(
        182,
        3.8,
        _ascii(
            "For client review we prepared a focused demo brand (Image Categorization) "
            "with 50 sample products. Both workstreams below point to the same Baserow grid."
        ),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(1.5)

    _ensure_space(pdf, 42)
    pdf.set_fill_color(245, 243, 255)
    pdf.set_draw_color(196, 181, 253)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(76, 29, 149)
    pdf.cell(
        182,
        5.5,
        "  1) Image Categorization (hero / lifestyle / detail)",
        border="LTR",
        fill=True,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "", 7.3)
    pdf.set_text_color(51, 65, 85)
    pdf.multi_cell(
        182,
        4.2,
        _ascii(
            "  We ran AI image categorization on 50 products for the client demo. "
            "Each product image is labeled as hero, lifestyle, or detail, then stored "
            "in the matching Baserow fields (hero_images / lifestyle_images / detail_image). "
            "Click the link below to open and review those 50 products:"
        ),
        border="LR",
        fill=True,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "B", 7.2)
    pdf.set_text_color(37, 99, 235)
    pdf.cell(
        182,
        4.5,
        f"  {_ascii(demo_grid_url)}",
        border="LR",
        fill=True,
        link=demo_grid_url,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(76, 29, 149)
    pdf.cell(
        182,
        5.5,
        "  2) Detail Image Generation (AI close-ups)",
        border="LR",
        fill=True,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "", 7.3)
    pdf.set_text_color(51, 65, 85)
    pdf.multi_cell(
        182,
        4.2,
        _ascii(
            "  On the same 50 demo products we generate AI detail close-ups where needed "
            "(fill up to 3 detail images per product). The AI-generated images are stored "
            "in the Baserow column: Detailed_image_gen. "
            "Open the same grid to review those generated images:"
        ),
        border="LR",
        fill=True,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "B", 7.2)
    pdf.set_text_color(37, 99, 235)
    pdf.cell(
        182,
        4.5,
        f"  {_ascii(demo_grid_url)}",
        border="LR",
        fill=True,
        link=demo_grid_url,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "", 7.3)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(
        182,
        4.5,
        "  Column to check: Detailed_image_gen  |  Brand filter: Image Categorization",
        border="LRB",
        fill=True,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Uncompressed output improves compatibility / avoids some viewers treating
    # heavily compressed single-page overflow PDFs as corrupt.
    pdf.set_compression(False)
    pdf.output(str(output_path))
    print(f"PDF successfully created at: {output_path}")
    print(f"Completed scrapers products: {scraped_total}")
    print(f"Pending brands: {len(pending_rows)}")
    print(f"Additional brands products: {extra_total}")


def _truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _render_wrapped_row(
    pdf: FPDF,
    cells: list[tuple[str, float, str, bool]],
    *,
    fill_rgb: tuple[int, int, int],
    border_rgb: tuple[int, int, int],
    text_rgb: tuple[int, int, int],
    emphasis_rgb: tuple[int, int, int],
    line_h: float = 4.2,
    pad_y: float = 1.5,
) -> None:
    """Draw a table row with wrapped text; all cells share the same row height."""
    line_counts: list[int] = []
    wrapped: list[list[str]] = []
    for text, width, _align, bold in cells:
        pdf.set_font("Helvetica", "B" if bold else "", 7.2)
        lines = pdf.multi_cell(width - 2, line_h, _ascii(f" {text}"), dry_run=True, output="LINES")
        wrapped.append(lines)
        line_counts.append(max(1, len(lines)))

    row_h = max(line_counts) * line_h + pad_y * 2
    _ensure_space(pdf, row_h + 2)

    x0 = pdf.get_x()
    y0 = pdf.get_y()
    pdf.set_fill_color(*fill_rgb)
    pdf.set_draw_color(*border_rgb)
    pdf.rect(x0, y0, sum(c[1] for c in cells), row_h, "FD")

    x = x0
    for idx, ((_text, width, align, bold), lines) in enumerate(zip(cells, wrapped)):
        pdf.set_xy(x + 1, y0 + pad_y)
        pdf.set_font("Helvetica", "B" if bold else "", 7.2)
        pdf.set_text_color(*(emphasis_rgb if bold else text_rgb))
        for line in lines:
            pdf.set_x(x + 1)
            pdf.cell(width - 2, line_h, line, align=align)
            pdf.ln(line_h)
        # vertical divider
        pdf.line(x, y0, x, y0 + row_h)
        x += width
    pdf.line(x, y0, x, y0 + row_h)
    pdf.set_xy(x0, y0 + row_h)


def _ensure_space(pdf: FPDF, needed: float) -> None:
    if pdf.get_y() + needed > pdf.page_break_trigger:
        pdf.add_page()


def _render_kpi_strip(pdf: FPDF, items: list[tuple[str, str]]) -> None:
    width = 182 / len(items)
    y = pdf.get_y()
    for i, (label, value) in enumerate(items):
        x = 14 + i * width
        pdf.set_xy(x, y)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(x, y, width - 2, 14, "FD")
        pdf.set_xy(x + 2, y + 1.5)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(width - 6, 4, _ascii(label), new_x=XPos.LEFT, new_y=YPos.NEXT)
        pdf.set_x(x + 2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(24, 43, 73)
        pdf.cell(width - 6, 6, _ascii(value))
    pdf.set_y(y + 16)


def _render_section_header(pdf: FPDF, title: str, r: int, g: int, b: int):
    _ensure_space(pdf, 12)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_text_color(r, g, b)
    pdf.cell(0, 5.5, _ascii(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1.2)


def _render_table_header(
    pdf: FPDF,
    titles: list[str],
    widths: list[float],
    header_bg=(241, 245, 249),
    text_color=(30, 41, 59),
):
    _ensure_space(pdf, 8)
    pdf.set_fill_color(*header_bg)
    pdf.set_draw_color(203, 213, 225)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*text_color)
    for title, width in zip(titles, widths):
        pdf.cell(width, 6.2, f"  {_ascii(title)}", border=1, align="L", fill=True)
    pdf.ln(6.2)


if __name__ == "__main__":
    output_pdf = Path(__file__).resolve().parent / "output" / "brands_and_xml_status_report.pdf"
    create_report_pdf(output_pdf)
