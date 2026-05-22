"""Write scrape results to text and PDF files."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brand_scraper import ScrapeResult, row_field


@dataclass
class BrandReportContext:
    row_id: int
    brand_name: str
    source_url: str
    brand_quote: str
    product_count: int
    bg_remove: bool | None


def _count_products(row: dict[str, Any], field_products: str) -> int:
    val = row_field(row, field_products) if field_products else None
    if isinstance(val, list):
        return len(val)
    return 0


def build_context(
    row: dict[str, Any], settings: Any, source_url: str
) -> BrandReportContext:
    quote = row_field(row, settings.field_brand_quote) if settings.field_brand_quote else ""
    bg = row_field(row, settings.field_bg_remove) if settings.field_bg_remove else None
    return BrandReportContext(
        row_id=int(row["id"]),
        brand_name=str(row_field(row, settings.field_brand_name) or f"row_{row['id']}"),
        source_url=source_url,
        brand_quote=str(quote or "").strip(),
        product_count=_count_products(row, settings.field_products),
        bg_remove=bool(bg) if bg is not None else None,
    )


def format_report_text(ctx: BrandReportContext, result: ScrapeResult, scraped_at: str) -> str:
    lines = [
        "=" * 60,
        "BRAND SCRAPE REPORT (first brand test)",
        "=" * 60,
        "",
        f"Scraped at (UTC): {scraped_at}",
        "",
        "--- Baserow row ---",
        f"Row ID:           {ctx.row_id}",
        f"Brand name:       {ctx.brand_name}",
        f"URL (Baserow):    {ctx.source_url}",
        f"Brand quote:      {ctx.brand_quote or '(empty)'}",
        f"Linked products:  {ctx.product_count}",
        f"bg_remove:        {ctx.bg_remove}",
        "",
        "--- Scrape result ---",
        f"Success:          {result.ok}",
        f"HTTP status:      {result.status_code}",
        f"Requested URL:    {result.url}",
        f"Final URL:        {result.final_url or result.url}",
        f"HTML size (bytes): {result.html_size_bytes or 'n/a'}",
        "",
        f"Page title:",
        f"  {result.page_title or '(not found)'}",
        "",
        f"Meta description:",
        f"  {result.meta_description or '(not found)'}",
        "",
        f"OG image:",
        f"  {result.og_image or '(not found)'}",
        "",
    ]

    if result.error:
        lines.extend([f"Error:", f"  {result.error}", ""])

    lines.append("H1 headings:")
    if result.h1_headings:
        for h in result.h1_headings:
            lines.append(f"  - {h}")
    else:
        lines.append("  (none found)")

    lines.extend(["", "Sample internal links (up to 15):"])
    if result.sample_links:
        for link in result.sample_links:
            lines.append(f"  - {link}")
    else:
        lines.append("  (none found)")

    lines.extend(["", "=" * 60])
    return "\n".join(lines)


def _pdf_safe(text: str) -> str:
    """FPDF core fonts are Latin-1; replace unsupported chars."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


def write_pdf_report(
    path: Path, ctx: BrandReportContext, result: ScrapeResult, scraped_at: str
) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, _pdf_safe("Brand Scrape Report (test)"), ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(4)

    body = format_report_text(ctx, result, scraped_at)
    line_height = 5
    width = pdf.epw
    for line in body.splitlines():
        safe = _pdf_safe(line) or " "
        # Break very long tokens (URLs) so PDF renderer does not fail
        if len(safe) > 90:
            chunk_size = 85
            for i in range(0, len(safe), chunk_size):
                pdf.multi_cell(width, line_height, safe[i : i + chunk_size])
        else:
            pdf.multi_cell(width, line_height, safe)
    pdf.output(str(path))


def save_reports(
    output_dir: Path,
    ctx: BrandReportContext,
    result: ScrapeResult,
    *,
    basename: str | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    safe_name = "".join(c if c.isalnum() else "_" for c in ctx.brand_name).strip("_")
    base = basename or f"scrape_{ctx.row_id}_{safe_name}"

    txt_path = output_dir / f"{base}.txt"
    pdf_path = output_dir / f"{base}.pdf"

    text = format_report_text(ctx, result, scraped_at)
    txt_path.write_text(text, encoding="utf-8")
    write_pdf_report(pdf_path, ctx, result, scraped_at)

    return txt_path, pdf_path
