"""
Organize categorization bake-off into a simple client folder:

  output/image categorization/
    README.txt
    00_Expected labels/          <- correct category for each image
      hero/  lifestyle/  detail/
    GPT-4o/                      <- SELECTED production model
      hero/  lifestyle/  detail/
    Gemini 2.5 Flash/
      hero/  lifestyle/  detail/
    ...

Each model folder contains the same images, placed into the category
that model predicted. Client just opens folders — no JSON needed.

Also refreshes AI_Model_Comparison_Client_Brief.docx with section 3
for image categorization, and rebuilds the client ZIP.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SRC_RESULTS = OUTPUT_DIR / "categorization_comparison" / "results.json"
SRC_IMAGES = OUTPUT_DIR / "categorization_comparison"
CAT_DIR = OUTPUT_DIR / "image categorization"
DOCX_PATH = OUTPUT_DIR / "AI_Model_Comparison_Client_Brief.docx"
BG_DIR = OUTPUT_DIR / "bg compare"
MODEL_DIR = OUTPUT_DIR / "model_comparison"
ZIP_PATH = OUTPUT_DIR / "AI_Model_Comparison_Client_Package.zip"

LABELS = ("hero", "lifestyle", "detail")


def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in (" ", "-", "_", ".")).strip()


def organize_folders(report: dict) -> Path:
    if CAT_DIR.exists():
        shutil.rmtree(CAT_DIR)
    CAT_DIR.mkdir(parents=True)

    # Expected / reference tree
    expected_root = CAT_DIR / "00_Expected labels (correct category)"
    for lab in LABELS:
        (expected_root / lab).mkdir(parents=True)

    # Per-model trees
    model_dirs: dict[str, Path] = {}
    for m in report.get("models") or []:
        folder_name = m["name"]
        if m.get("selected"):
            folder_name = f"{m['name']} - SELECTED"
        mdir = CAT_DIR / _safe_name(folder_name)
        for lab in LABELS:
            (mdir / lab).mkdir(parents=True)
        model_dirs[m["id"]] = mdir

    copied = 0
    for s in report.get("samples") or []:
        src = SRC_IMAGES / s["local_image"]
        if not src.exists():
            # try basename under images/
            alt = SRC_IMAGES / "images" / Path(s["local_image"]).name
            src = alt if alt.exists() else src
        if not src.exists():
            print("MISSING", s["local_image"])
            continue

        expected = s.get("expected_label") or "lifestyle"
        if expected not in LABELS:
            expected = "lifestyle"

        # Nice client filename: expected_product_slug.jpg
        product = _safe_name(str(s.get("product_name") or "product"))[:50]
        base = f"{s.get('index', 0):02d}_{expected}_{product}.jpg"

        # Copy into expected tree
        shutil.copy2(src, expected_root / expected / base)
        copied += 1

        # Copy into each model's predicted category folder
        for mr in s.get("models") or []:
            mid = mr.get("model_id")
            pred = (mr.get("label") or "").strip().lower()
            if mid not in model_dirs:
                continue
            if pred not in LABELS:
                # put errors in a catch-all under lifestyle with prefix
                pred = "lifestyle"
                out_name = f"UNLABELED_{base}"
            else:
                out_name = base
                if pred != expected:
                    # mark mismatches so client spots them quickly
                    out_name = f"DIFFERS_{base}"
            shutil.copy2(src, model_dirs[mid] / pred / out_name)

    readme = CAT_DIR / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "IMAGE CATEGORIZATION — how to review",
                "====================================",
                "",
                "Open each model folder. Inside you will see three folders:",
                "  hero / lifestyle / detail",
                "",
                "Images are placed into the folder matching what THAT model predicted.",
                "So you can visually compare how models sorted the same photos.",
                "",
                "00_Expected labels (correct category)",
                "  = the correct / production target category for each image.",
                "",
                "Files named DIFFERS_... mean the model put the image in a different",
                "category than the expected label — look at those first.",
                "",
                "Production choice: GPT-4o (folder marked SELECTED)",
                "  Clearest separation of hero / lifestyle / detail.",
                "",
                f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Organized {copied} images into: {CAT_DIR}")
    return CAT_DIR


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
    for i, title in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(title)
        _set_run_font(run, bold=True, size=10, color=(15, 23, 42))
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            cell = table.rows[r_idx + 1].cells[c_idx]
            cell.text = ""
            run = cell.paragraphs[0].add_run(val)
            _set_run_font(run, size=10, bold=(c_idx == 0 and "SELECTED" in val))
    doc.add_paragraph()
    return table


def build_docx(report: dict) -> Path:
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
        "Background removal, detail image generation, and image categorization — "
        "which models we tested, which performed best, and which we use in production."
    )
    _set_run_font(sr, size=11, color=(71, 85, 105))

    meta = doc.add_paragraph()
    mr = meta.add_run(
        f"Prepared for client review  |  {datetime.now(timezone.utc).strftime('%Y-%m-%d')}  |  "
        "Folders: bg compare + model_comparison + image categorization"
    )
    _set_run_font(mr, size=9, color=(100, 116, 139))

    _add_heading(doc, "Purpose", level=1)
    _add_para(
        doc,
        "Before locking production models for the Binnen catalog pipeline, we ran "
        "side-by-side bake-offs on real furniture product photos. This brief explains "
        "what is inside the attached folders, which model won each test, why we selected "
        "it, and the approximate API cost per image.",
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
        "Typical cost is about $0.002 per image.",
    )

    # ----------------------------------------------------------------
    # 2) Detail image generation
    # ----------------------------------------------------------------
    _add_heading(doc, "2. Detail Image Generation Comparison  (folder: model_comparison)", level=1)
    _add_para(
        doc,
        "This folder shows detail / macro close-ups generated for real catalog products. "
        "For each feature you will find a comparison_grid.jpg plus per-model subfolders. "
        "You can also open comparison_overview.html.",
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
        "is that it accepts a reference image — so wood grain, stitching, legs, and fabric "
        "stay consistent with the real product photo. Approximate API cost: ~$0.05 per image.",
    )

    # ----------------------------------------------------------------
    # 3) Image categorization (NEW)
    # ----------------------------------------------------------------
    _add_heading(doc, "3. Image Categorization Comparison  (folder: image categorization)", level=1)
    _add_para(
        doc,
        "We tested sorting product images into Hero / Lifestyle / Detail using multiple "
        "vision models (Gemini 2.5 Flash, Gemini 2.5 Pro, GPT-4o, GPT-4.1, Claude Sonnet 4).",
    )
    _add_para(
        doc,
        "How to review (no JSON needed): open the folder image categorization. "
        "Each model has its own folder, and inside every model you will find three "
        "subfolders — hero, lifestyle, and detail. Each photo is placed into the "
        "category that model chose. Files named DIFFERS_... mean that model disagreed "
        "with the expected (correct) category — those are the important ones to check. "
        "The folder 00_Expected labels shows the correct category for each image.",
    )

    board_rows = []
    for m in report.get("leaderboard") or []:
        name = m.get("name") or ""
        if m.get("selected"):
            name = f"{name}\nSELECTED"
        board_rows.append(
            [
                name,
                f"{m.get('accuracy_pct', 0):.1f}%  ({m.get('correct', 0)}/{m.get('total', 0)})",
                m.get("approx_cost") or "",
                m.get("note") or "",
            ]
        )
    _add_para(doc, "Models tested & scores:", bold=True)
    _add_table(
        doc,
        ["Model", "Accuracy", "Approx. cost / image", "Notes"],
        board_rows,
    )

    _add_para(doc, "Winner & production choice — GPT-4o", bold=True)
    _add_para(
        doc,
        "Best result so far: GPT-4o — clearest separation, especially for lifestyle vs hero "
        "vs detail. Cheaper models (e.g. Gemini 2.5 Flash, Claude Sonnet 4) sometimes label "
        "a true detail close-up as lifestyle. That is why we use GPT-4o for production image "
        "categorization (including the 50-product Image Categorization demo in Baserow).",
    )

    # Sample image from bake-off if available
    sample_img = None
    for s in report.get("samples") or []:
        p = SRC_IMAGES / s.get("local_image", "")
        if p.exists():
            sample_img = p
            break
    if sample_img:
        _add_para(doc, "Example image from the bake-off:", bold=True, size=10)
        try:
            doc.add_picture(str(sample_img), width=Inches(2.6))
        except Exception:
            pass

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    _add_heading(doc, "4. Summary — what we use in production", level=1)
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
    _add_bullet(
        doc,
        " GPT-4o (openai/gpt-4o) — clearest Hero / Lifestyle / Detail separation.",
        bold_prefix="Image categorization:",
    )
    _add_para(
        doc,
        "Attached folders for visual review: bg compare, model_comparison, and "
        "image categorization. Costs are provider API rates in USD and may change.",
    )

    footer = doc.add_paragraph()
    fr = footer.add_run("Binnen / Woonbloq data pipeline  ·  Confidential client review material")
    _set_run_font(fr, size=8, color=(148, 163, 184))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(str(DOCX_PATH))
    print(f"Wrote {DOCX_PATH}")
    return DOCX_PATH


def build_zip() -> Path:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    # zipfile is more reliable with spaces in folder names than Compress-Archive quirks
    import zipfile

    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # brief
        if DOCX_PATH.exists():
            zf.write(DOCX_PATH, DOCX_PATH.name)
        for folder_name, folder in (
            ("bg compare", BG_DIR),
            ("model_comparison", MODEL_DIR),
            ("image categorization", CAT_DIR),
        ):
            if not folder.exists():
                continue
            for path in folder.rglob("*"):
                if path.is_file():
                    arc = f"{folder_name}/{path.relative_to(folder).as_posix()}"
                    zf.write(path, arc)
    print(f"Wrote {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)")
    return ZIP_PATH


def main() -> int:
    if not SRC_RESULTS.exists():
        print(f"ERROR: missing {SRC_RESULTS}")
        return 1
    report = json.loads(SRC_RESULTS.read_text(encoding="utf-8"))
    organize_folders(report)
    build_docx(report)
    build_zip()
    # Show tree summary
    print("\nFolder tree (top level):")
    for p in sorted(CAT_DIR.iterdir()):
        if p.is_dir():
            counts = {lab: len(list((p / lab).glob("*.jpg"))) for lab in LABELS if (p / lab).exists()}
            print(f"  {p.name}: {counts}")
        else:
            print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
