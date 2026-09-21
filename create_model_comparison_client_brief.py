"""
Create a short client-facing DOCX explaining the AI model bake-offs:
  - output/bg compare          (background removal)
  - output/model_comparison    (detail image generation)

Both folders are intended to be zipped and sent with this document.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
BG_DIR = OUTPUT_DIR / "bg compare"
MODEL_DIR = OUTPUT_DIR / "model_comparison"
DOCX_PATH = OUTPUT_DIR / "AI_Model_Comparison_Client_Brief.docx"


def _set_run_font(run, *, size=11, bold=False, color=(30, 41, 59)):
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Calibri"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    run.font.color.rgb = RGBColor(*color)


def _add_heading(doc: Document, text: str, level: int = 1):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = RGBColor(24, 43, 73)


def _add_para(doc: Document, text: str, *, bold=False, size=11):
    p = doc.add_paragraph()
    run = p.add_run(text)
    _set_run_font(run, size=size, bold=bold)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.space_before = Pt(0)
    return p


def _add_bullet(doc: Document, text: str, *, bold_prefix: str | None = None):
    p = doc.add_paragraph(style="List Bullet")
    if bold_prefix:
        r1 = p.add_run(bold_prefix)
        _set_run_font(r1, bold=True, size=11)
        r2 = p.add_run(text)
        _set_run_font(r2, size=11)
    else:
        r = p.add_run(text)
        _set_run_font(r, size=11)
    p.paragraph_format.space_after = Pt(4)
    return p


def _add_table(doc: Document, headers: list[str], rows: list[list[str]]):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, title in enumerate(headers):
        hdr[i].text = ""
        run = hdr[i].paragraphs[0].add_run(title)
        _set_run_font(run, bold=True, size=10, color=(15, 23, 42))
    for r_idx, row in enumerate(rows):
        cells = table.rows[r_idx + 1].cells
        for c_idx, val in enumerate(row):
            cells[c_idx].text = ""
            run = cells[c_idx].paragraphs[0].add_run(val)
            _set_run_font(run, size=10, bold=(c_idx == 0 and "SELECTED" in val))
    doc.add_paragraph()
    return table


def build_docx() -> Path:
    doc = Document()

    section = doc.sections[0]
    section.top_margin = Inches(0.85)
    section.bottom_margin = Inches(0.85)
    section.left_margin = Inches(0.95)
    section.right_margin = Inches(0.95)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    tr = title.add_run("Binnen — AI Model Comparison Brief")
    _set_run_font(tr, size=18, bold=True, color=(24, 43, 73))

    sub = doc.add_paragraph()
    sr = sub.add_run(
        "Background removal & detail image generation — which models we tested, "
        "which performed best, and which we use in production."
    )
    _set_run_font(sr, size=11, color=(71, 85, 105))

    meta = doc.add_paragraph()
    mr = meta.add_run(
        f"Prepared for client review  |  {datetime.now(timezone.utc).strftime('%Y-%m-%d')}  |  "
        "Supporting folders: bg compare + model_comparison (attached as ZIP)"
    )
    _set_run_font(mr, size=9, color=(100, 116, 139))

    # Intro
    _add_heading(doc, "Purpose", level=1)
    _add_para(
        doc,
        "Before locking production models for the Binnen catalog pipeline, we ran "
        "side-by-side bake-offs on real furniture product photos. This short brief "
        "explains what is inside the two attached folders, which model won each test, "
        "why we selected it, and the approximate API cost per image.",
    )

    # ----------------------------------------------------------------
    # 1) Background removal
    # ----------------------------------------------------------------
    _add_heading(doc, "1. Background Removal Comparison  (folder: bg compare)", level=1)
    _add_para(
        doc,
        "This folder contains one original product photo plus the same image processed "
        "by four background-removal models. Open the folders side by side to compare "
        "edge quality (legs, fabric fringes, glass, thin arms) and silhouette accuracy.",
    )

    original = BG_DIR / "original image.jpg"
    if original.exists():
        _add_para(doc, "Original source image used for the test:", bold=True, size=10)
        try:
            doc.add_picture(str(original), width=Inches(3.2))
            last = doc.paragraphs[-1]
            last.alignment = WD_ALIGN_PARAGRAPH.LEFT
        except Exception:
            pass

    _add_para(doc, "Models tested in this folder:", bold=True)
    _add_table(
        doc,
        ["Model / folder", "What it is", "Approx. cost / image", "Result"],
        [
            [
                "fal-aibirefnetv2\n(BiRefNet v2)",
                "High-accuracy segmentation — best on complex furniture edges",
                "~$0.002",
                "SELECTED — used in production",
            ],
            [
                "Bria RMBG 2.0",
                "Studio-grade Bria background remove",
                "~$0.0015",
                "Good, slightly softer edges on fine details",
            ],
            [
                "Remove Background\n(RMBG / rembg)",
                "Fast classic rembg cutout",
                "~$0.001",
                "Cheapest / fastest; weaker on thin legs & complex silhouettes",
            ],
            [
                "ben-v2-image\n(BEN v2)",
                "Alternative rembg model family",
                "~$0.001–$0.002",
                "Acceptable; not as consistent as BiRefNet on our samples",
            ],
        ],
    )

    _add_para(doc, "Winner & production choice — BiRefNet v2", bold=True)
    _add_para(
        doc,
        "BiRefNet v2 (folder fal-aibirefnetv2, fal.ai endpoint fal-ai/birefnet/v2) "
        "gave the cleanest cutouts on furniture: sharper legs, better fabric/wood "
        "boundaries, and fewer holes in the alpha mask. We therefore use BiRefNet v2 "
        "for hero background removal / resize in the live pipeline. "
        "Typical cost is about $0.002 per image (fal.ai compute pricing; roughly "
        "$0.0008 per compute second × ~2–3 seconds).",
    )
    _add_para(
        doc,
        "How to review the ZIP: open bg compare/original image.jpg, then compare the "
        "four model subfolders. The BiRefNet result is the production standard.",
    )

    # ----------------------------------------------------------------
    # 2) Detail image generation
    # ----------------------------------------------------------------
    _add_heading(doc, "2. Detail Image Generation Comparison  (folder: model_comparison)", level=1)
    _add_para(
        doc,
        "This folder shows detail / macro close-ups generated for real catalog products "
        "(material texture, joinery, etc.). For each feature you will find a comparison_grid.jpg "
        "plus per-model subfolders. An interactive overview is also included: "
        "comparison_overview.html.",
    )

    _add_para(doc, "Models tested:", bold=True)
    _add_table(
        doc,
        ["Model", "Speciality", "Approx. cost / image", "Result"],
        [
            [
                "FLUX.2 Pro Edit\n(fal-ai/flux-2-pro/edit)",
                "Accepts a real product reference photo (image-to-image). "
                "Best identity & texture lock (wood grain, fabric weave, leather).",
                "~$0.05",
                "SELECTED — used in production",
            ],
            [
                "Recraft V3",
                "Very sharp catalog / commercial look; strong on metal & crisp edges",
                "~$0.04",
                "Strong runner-up; less faithful to the exact product photo",
            ],
            [
                "Ideogram v2",
                "High contrast / strong geometry; good structural parts",
                "~$0.08",
                "Good graphics; higher cost; weaker product identity lock",
            ],
            [
                "Luma Photon",
                "Cinematic lighting & soft bokeh / luxury aesthetic",
                "~$0.03",
                "Nice mood; less reliable for exact catalog material match",
            ],
        ],
    )

    _add_para(doc, "Winner & production choice — FLUX.2 Pro Edit", bold=True)
    _add_para(
        doc,
        "FLUX.2 Pro Edit performed best for Binnen’s use case: generating true product "
        "detail close-ups that still look like the same physical item. Its key speciality "
        "is that it accepts a reference image (image_urls) — so wood grain, stitching, "
        "legs, and fabric stay consistent with the real product photo instead of inventing "
        "a generic look. That is why we use fal-ai/flux-2-pro/edit as the default engine "
        "for Detailed_image_gen in Baserow. Approximate API cost: ~$0.05 per generated image "
        "(plus a small OpenRouter GPT-4o vision cost when planning which detail features to generate).",
    )
    _add_para(
        doc,
        "How to review the ZIP: open model_comparison/comparison_overview.html in a browser, "
        "or browse any product_*/feature_*/comparison_grid.jpg and compare flux2_pro vs the others.",
    )

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    _add_heading(doc, "3. Summary — what we use in production", level=1)
    _add_bullet(
        doc,
        " BiRefNet v2 (fal-ai/birefnet/v2) — ~$0.002 / image — cleanest furniture cutouts.",
        bold_prefix="Background removal / resize:",
    )
    _add_bullet(
        doc,
        " FLUX.2 Pro Edit (fal-ai/flux-2-pro/edit) — ~$0.05 / image — best reference-image "
        "detail close-ups for catalog work.",
        bold_prefix="Detail image generation:",
    )
    _add_para(
        doc,
        "Both bake-off folders (bg compare and model_comparison) are attached so you can "
        "visually verify the winner yourself. Costs are fal.ai API rates in USD and may change.",
    )

    footer = doc.add_paragraph()
    fr = footer.add_run("Binnen / Woonbloq data pipeline  ·  Confidential client review material")
    _set_run_font(fr, size=8, color=(148, 163, 184))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(str(DOCX_PATH))
    return DOCX_PATH


if __name__ == "__main__":
    path = build_docx()
    print(f"Wrote {path}")
