"""Generate Step-by-Step Technical Implementation Guide PDF (v5.0 NodeTool-First).

Comprehensive step-by-step engineering and deployment playbook highlighting completed codebase foundations
and detailing the exact architectural DELTA required for each step.
"""
import os
import sys
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class ImplementationNumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(ImplementationNumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super(ImplementationNumberedCanvas, self).showPage()
        super(ImplementationNumberedCanvas, self).save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(54, 11 * 72 - 36, "Binnen & Baserow — Step-by-Step Technical Implementation Guide (v5.0 Delta Edition)")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, 11 * 72 - 42, 8.5 * 72 - 54, 11 * 72 - 42)
            
        # Footer
        footer_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(8.5 * 72 - 54, 36, footer_text)
        self.drawString(54, 36, "TECHNICAL PLAYBOOK — ENGINEERING & DEPLOYMENT SPECIFICATION")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 48, 8.5 * 72 - 54, 48)
        self.restoreState()


def build_implementation_pdf(filename: str):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )
    
    styles = getSampleStyleSheet()
    
    primary_color = colors.HexColor("#0F172A") # Slate 900
    accent_color = colors.HexColor("#2563EB")  # Blue 600
    secondary_color = colors.HexColor("#0284C7") # Sky 600
    bg_light = colors.HexColor("#F8FAFC")
    code_bg = colors.HexColor("#F1F5F9")
    border_color = colors.HexColor("#E2E8F0")
    badge_done = colors.HexColor("#166534") # Green 800
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=primary_color,
        spaceAfter=4
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=accent_color,
        spaceAfter=10
    )
    
    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=primary_color,
        spaceBefore=11,
        spaceAfter=6,
        keepWithNext=True
    )
    
    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=8,
        spaceAfter=3,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155"),
        spaceAfter=5
    )
    
    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=body_style,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=2.5
    )
    
    code_style = ParagraphStyle(
        'CodeStyle',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white
    )
    
    table_body_style = ParagraphStyle(
        'TableBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#1E293B")
    )
    
    table_body_bold = ParagraphStyle(
        'TableBodyBold',
        parent=table_body_style,
        fontName='Helvetica-Bold'
    )
    
    story = []
    
    # Title Block
    story.append(Paragraph("Media Enrichment Extension (NodeTool-First Architecture)", title_style))
    story.append(Paragraph("Step-by-Step Technical Implementation & Architectural Delta Playbook (v5.0)", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=accent_color, spaceBefore=0, spaceAfter=8))
    
    # Section 1: Foundation & Existing Capabilities
    story.append(Paragraph("1. Codebase Baseline & Governing System Rules", h1_style))
    story.append(Paragraph(
        "This playbook outlines the exact engineering steps to migrate the existing codebase into the client's v5.0 NodeTool-First architecture. "
        "<b>No development time is allocated for features already built.</b> Below is the baseline status and system rules:",
        body_style
    ))
    
    story.append(Paragraph("• <b>Scrapers (100% COMPLETE - 0 Days):</b> All 28 brand scrapers in <code>scrapers/router.py</code> are operational. No scraper rewrites needed.", bullet_style))
    story.append(Paragraph("• <b>Image Standardization Base (100% FUNCTIONAL):</b> Background removal (Pixelbin/fal) and canvas framing (1000x880 transparent hero, 1760x1100 white detail) already built. <i>Delta: Plug into n8n intake chain + save to S3.</i>", bullet_style))
    story.append(Paragraph("• <b>MCP Base (100% FUNCTIONAL):</b> Native Python MCP server (<code>mcp_server.py</code>) operational. <i>Delta: Add Jobs table tool + configure Tri-MCP with NodeTool and FableCut.</i>", bullet_style))
    story.append(Paragraph("• <b>Recommendation Engine (100% READY):</b> 3-tier vector matcher + FBT bundle engine in <code>recommendation_engine.py</code>. <i>Delta: Live sync to Shopify metafields.</i>", bullet_style))
    story.append(Paragraph("• <b>Strict Invariant:</b> Owned storage first (S3/MinIO <code>asset__v{n}</code>) ➔ Baserow update. Never save temporary provider URLs.", bullet_style))
    story.append(Paragraph("• <b>The 'Edited' Lock:</b> Any human/agent edit in NodeTool/FableCut sets <code>enrichment_status = 'edited'</code>, permanently protecting it from automation overwrites.", bullet_style))
    
    story.append(Spacer(1, 4))
    
    # Section 2: Phase 0 — NodeTool Playground Validation
    story.append(Paragraph("2. Phase 0: Standalone NodeTool Playground Sandbox Validation (2.5 Working Days)", h1_style))
    story.append(Paragraph(
        "<i>Objective: Validate NodeTool in a standalone Docker sandbox before touching production Baserow or n8n (§6).</i>",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 0.1: Standalone Docker Deployment</b>", h2_style))
    story.append(Paragraph(
        "Deploy the official NodeTool container on the server/local test machine behind local reverse proxy:",
        body_style
    ))
    
    docker_snippet = (
        "docker run -d --name nodetool-playground -p 3000:3000 \\<br/>"
        "  -e FAL_KEY=\"your_fal_api_key\" \\<br/>"
        "  -e STORAGE_LOCAL_PATH=\"/data/nodetool\" \\<br/>"
        "  -v nodetool_data:/data/nodetool \\<br/>"
        "  ghcr.io/nodetool-ai/nodetool:latest"
    )
    story.append(Paragraph(docker_snippet, code_style))
    
    story.append(Paragraph("<b>Step 0.2: Execution of 6 Mandatory Test Gates (§6)</b>", h2_style))
    story.append(Paragraph(
        "Load 10 real furniture products (Spectrum Design, Leolux, Artifort) and execute the 6 validation criteria:<br/>"
        "• <b>Gate 1 (Canvas Image Workflows):</b> Construct visual node graph for lifestyle scene composition using FLUX / fal.ai. Measure generation speed and visual realism.<br/>"
        "• <b>Gate 2 (Sketch Editor Masking - Highest Risk):</b> Open real furniture image in Layered Sketch Editor. Draw precision mask around furniture and execute inpainting background swap. Ensure furniture shape and upholstery remain 100% unaltered.<br/>"
        "• <b>Gate 3 (Video Timeline for Humans):</b> Import generated footage into multi-track timeline. Perform clip trims, cuts, multi-layer arrangement, and verify clean video export.<br/>"
        "• <b>Gate 4 (Timeline-Prompted Ad Generation):</b> Author timestamped shot list in NodeTool using Seedance 2.0 with <code>@product</code> reference image anchors. Verify product fidelity across shots.<br/>"
        "• <b>Gate 5 (Headless Execution via API/CLI):</b> Execute lifestyle composition workflow via curl / CLI with dynamic JSON inputs. Confirm output image URLs are retrievable programmatically.<br/>"
        "• <b>Gate 6 (NodeTool MCP Connectivity):</b> Connect NodeTool MCP to Claude Cowork. Verify Claude can inspect workflows and trigger headless generation conversationally.",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 0.3: Fallback Activation Protocols (§7)</b>", h2_style))
    story.append(Paragraph(
        "If any gate fails during Phase 0, immediately activate the pre-decided component fallback:<br/>"
        "• <i>Gate 2 Fails (Masking lag/inaccuracy)</i> ➔ Deploy <b>InvokeAI</b> on an on-demand Vast.ai GPU (~$0.25/hr booted by n8n on session start).<br/>"
        "• <i>Gate 3 Fails (Timeline unstable)</i> ➔ Use <b>FableCut</b> as the human video editor too (via active project JSON swap wrapper).<br/>"
        "• <i>Gate 5 Fails (Headless execution flaky)</i> ➔ Move creative logic into <b>n8n direct fal.ai API calls</b> with Baserow prompt templates.",
        body_style
    ))
    
    story.append(Spacer(1, 6))
    story.append(PageBreak())
    
    # Section 3: Phase 1 — Storage, Baserow Schema & n8n Glue
    story.append(Paragraph("3. Phase 1: Storage Setup, Baserow Schema Expansion & n8n System Glue (2 Working Days)", h1_style))
    story.append(Paragraph(
        "<i>Objective: Establish owned S3 storage, create Jobs & Template tables in Baserow, and configure n8n triggers.</i>",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 1.1: Provision S3 / MinIO Asset Storage</b>", h2_style))
    story.append(Paragraph(
        "1. Create dedicated bucket <code>binnen-catalog-assets</code> with public read / CDN policy.<br/>"
        "2. Configure standardized versioning path conventions:<br/>"
        "   <code>/products/{row_id}/hero/asset__v{n}.png</code><br/>"
        "   <code>/products/{row_id}/lifestyle/variant_{k}__v{n}.jpg</code><br/>"
        "   <code>/products/{row_id}/video/showcase__v{n}.mp4</code><br/>"
        "   <code>/products/{row_id}/3d/model__v{n}.glb</code> & <code>model__v{n}.usdz</code>",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 1.2: Baserow Schema Expansions</b>", h2_style))
    story.append(Paragraph(
        "1. <b>Modify Products Table (Table 742):</b> Add URL fields (<code>clean_hero_url</code>, <code>lifestyle_variants</code>, <code>lifestyle_chosen_url</code>, <code>video_showcase_url</code>, <code>model_3d_glb_url</code>, <code>model_3d_usdz_url</code>), status fields (<code>enrichment_status</code>: pending/processing/completed/edited/error, <code>edited_at</code>), trigger field (<code>operation_marker</code>: run_lifestyle/run_video/run_3d), and Seam formula fields (<code>seam_image_url</code>, <code>seam_video_url</code>, <code>seam_3d_url</code>).<br/>"
        "2. <b>Create 'Jobs' Table:</b> <code>id</code> (Auto), <code>operation</code> (Single select), <code>status</code> (queued/running/done/failed), <code>target_row_ids</code> (Long text/JSON), <code>template_id</code> (Link to template tables), <code>parameters</code> (Long text JSON), <code>progress_counter</code> (Number), <code>total_items</code> (Number), <code>created_at</code>, <code>completed_at</code>.<br/>"
        "3. <b>Create 'Prompt_Scene_Templates' Table:</b> <code>template_name</code>, <code>category_scope</code>, <code>prompt_template_text</code>, <code>negative_prompt</code>, <code>aspect_ratio</code>, <code>model_tier</code>.<br/>"
        "4. <b>Create 'Video_Templates' Table:</b> <code>template_name</code>, <code>type</code> (Functional/Lifestyle), <code>timeline_prompt_recipe</code>, <code>remotion_composition_id</code>, <code>overlay_schema</code>.",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 1.3: n8n Workflow Architecture</b>", h2_style))
    story.append(Paragraph(
        "• <b>Workflow 1 (Entry Point A - Product Intake):</b> Webhook on new product in Baserow ➔ Creates <code>Jobs</code> row for default <code>clean_hero</code> derivation.<br/>"
        "• <b>Workflow 2 (Entry Point B - Grid Multi-Select Marker):</b> Webhook listening to <code>operation_marker</code> changes ➔ Collects all marked product IDs ➔ Creates unified <code>Jobs</code> row ➔ Resets <code>operation_marker</code> to empty.<br/>"
        "• <b>Workflow 3 (Unified Job Queue Runner):</b> Polls/triggers on new <code>Jobs</code> row ➔ Iterates through product IDs with rate-limit awareness ➔ Executes NodeTool headless API / fal.ai ➔ Uploads result to S3 ➔ Writes owned URL to Baserow ➔ Increments <code>progress_counter</code>.",
        body_style
    ))
    
    story.append(Spacer(1, 6))
    
    # Section 4: Phase 2 — The Editor Seam & Rewired Image Pipelines
    story.append(Paragraph("4. Phase 2: The Editor Seam Service & Rewired Image Pipelines (2.5 Working Days)", h1_style))
    story.append(Paragraph(
        "<i>Objective: Deploy The Editor Seam and rewire existing image resizing logic into the automated intake & correction loop.</i>",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 2.1: Build The Editor Seam Microservice (FastAPI)</b>", h2_style))
    story.append(Paragraph(
        "The Editor Seam is the thin custom service connecting Baserow, human editing surfaces, and S3 storage:<br/>"
        "1. <b>Open Endpoint (<code>GET /open?type={type}&id={row_id}</code>):</b> Reads Baserow product row ➔ Loads current asset into NodeTool board / FableCut project / <code>&lt;model-viewer&gt;</code> page.<br/>"
        "2. <b>Watched-Folder / Push Endpoint (<code>POST /export-upload</code>):</b> Listens for exported files from editors ➔ Generates new version filename (<code>asset__v{n+1}</code>) ➔ Uploads to S3 ➔ Executes Baserow PATCH: <code>enrichment_status = 'edited'</code>, <code>edited_at = now()</code>.",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 2.2: Rewiring Existing Image Processing to Intake Chain</b>", h2_style))
    story.append(Paragraph(
        "• <b>What Exists in Code:</b> <code>fal_image_processor.py</code> already handles transparent hero framing (1000x880) and white canvas detail framing (1760x1100).<br/>"
        "• <b>Required Delta:</b> Wrap this existing logic into the automated n8n intake chain calling NodeTool Headless API. Save final image to S3 (<code>asset__v1.png</code>) and write S3 URL to Baserow <code>clean_hero_url</code>.<br/>"
        "• <b>On-Demand Generative Tier:</b> Reads prompt recipe from <code>Prompt_Scene_Templates</code> ➔ Generates 2–3 lifestyle variants via NodeTool/FLUX ➔ Stores in <code>lifestyle_variants</code> for admin grid selection.",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 2.3: 3-Tier Image Correction Loop Validation</b>", h2_style))
    story.append(Paragraph(
        "Validate all 3 correction paths on staging:<br/>"
        "1. <b>Approve:</b> Admin clicks approve in grid ➔ Status set to <code>completed</code> ➔ Pushed to Shopify.<br/>"
        "2. <b>Reject & Re-render:</b> Parameter escalation (higher model tier, different template) without code changes.<br/>"
        "3. <b>Edit:</b> Click Baserow formula link ➔ Opens NodeTool sketch editor ➔ Inpaint/composite ➔ Export ➔ Auto-saved to S3 and locked as <code>edited</code>.",
        body_style
    ))
    
    story.append(Spacer(1, 6))
    story.append(PageBreak())
    
    # Section 5: Phase 3 — Claude Cowork & Tri-MCP
    story.append(Paragraph("5. Phase 3: Conversational Claude Cowork & Tri-MCP Suite (2 Working Days)", h1_style))
    story.append(Paragraph(
        "<i>Objective: Enable seamless conversational catalog management, bulk jobs, and JSON video edits via Claude.</i>",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 3.1: Upgrading Existing Python MCP Server</b>", h2_style))
    story.append(Paragraph(
        "• <b>What Exists in Code:</b> <code>mcp_server.py</code> already provides Baserow and Shopify tools.<br/>"
        "• <b>Required Delta:</b> Add <code>create_enrichment_job</code> and <code>get_job_status</code> tools to <code>mcp_server.py</code>.<br/>"
        "• <b>Tri-MCP Configuration:</b> Configure Claude Cowork / Desktop config (<code>claude_desktop_config.json</code>) to load:<br/>"
        "  1. <b>Baserow MCP:</b> Catalog search & Job creation (Entry Point C).<br/>"
        "  2. <b>NodeTool MCP:</b> Inspect and trigger creative canvas workflows.<br/>"
        "  3. <b>FableCut MCP:</b> Inspect and patch JSON video timelines with conflict-safe revisions.",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 3.2: Agentic Conversational Editing Workflow</b>", h2_style))
    story.append(Paragraph(
        "Cowork User Prompt: <i>'Find all Leolux sofas missing lifestyle images and generate Japandi living room scenes.'</i> ➔ Claude executes Baserow MCP search ➔ Creates row in <code>Jobs</code> table ➔ n8n executes in bulk ➔ Live progress displays in Baserow grid.",
        body_style
    ))
    
    story.append(Spacer(1, 6))
    
    # Section 6: Phase 4 — Video Production Pipeline
    story.append(Paragraph("6. Phase 4: Automated Video Production Pipeline (3.5 Working Days)", h1_style))
    story.append(Paragraph(
        "<i>Objective: Build dual-class video engine (Functional vs Lifestyle Ads) with strict zero-text baking.</i>",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 4.1: Functional Videos (Programmatic Assembly)</b>", h2_style))
    story.append(Paragraph(
        "1. Author Remotion / FFmpeg composition templates: Approved stills + short i2v motion clips + dynamic brand overlays.<br/>"
        "2. <b>Overlay Pass:</b> Product title, price card, dimensions, and CTA rendered dynamically over footage. Bulk-safe and 100% translatable.",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 4.2: Lifestyle Ads (Timeline-Prompt Generation)</b>", h2_style))
    story.append(Paragraph(
        "1. Configure <code>Video_Templates</code> with structured shot recipes for Seedance 2.0 / FLUX 3:<br/>"
        "   <code>[0-3s: Wide shot {room_style}] [3-7s: Smooth dolly zoom on {product_name} fabric] [7-10s: Ambient light pan]</code><br/>"
        "2. Inject <code>@product</code> approved image URLs into reference slots to lock furniture appearance across shots.<br/>"
        "3. Connect FableCut JSON timeline for conversational agent edits and NodeTool timeline for manual trims.",
        body_style
    ))
    
    story.append(Spacer(1, 6))
    
    # Section 7: Phase 5 — 3D Asset Pipeline
    story.append(Paragraph("7. Phase 5: 3D Asset Pipeline Integration & <model-viewer> (2.5 Working Days)", h1_style))
    story.append(Paragraph(
        "<i>Objective: Automate 2D-to-3D generation, asset compression, and interactive review.</i>",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 5.1: 2D-to-3D Generation & Post-Processing</b>", h2_style))
    story.append(Paragraph(
        "1. Connect fal image-to-3D (Hunyuan-class Rapid ~$0.225; Pro ~$0.375 on escalation). Multi-angle photos passed if available.<br/>"
        "2. <b>n8n Post-Processing:</b> Execute <code>gltf-transform optimize input.glb output.glb</code> (mesh decimation and KTX2 texture compression) + <code>usdzconvert</code> for iOS AR QuickLook.<br/>"
        "3. Upload optimized GLB and USDZ files to owned S3 storage.",
        body_style
    ))
    
    story.append(Paragraph("<b>Step 5.2: Standalone <model-viewer> Review Page</b>", h2_style))
    story.append(Paragraph(
        "Deploy lightweight HTML5 <code>&lt;model-viewer&gt;</code> interface: Admin inspects 3D mesh ➔ Clicks <b>Approve</b> (syncs to Baserow & Shopify), <b>Reject & Escalate</b> (re-runs with Pro model), or <b>Download</b> (for Blender deep fixes).",
        body_style
    ))
    
    story.append(Spacer(1, 6))
    story.append(PageBreak())
    
    # Section 8: Phases 6 to 9 — Downstream Integrations
    story.append(Paragraph("8. Phases 6 to 9: Downstream Integrations & Production Launch", h1_style))
    
    story.append(Paragraph("<b>Phase 6: Recommendation Engine Live Sync (1 Working Day)</b>", h2_style))
    story.append(Paragraph(
        "• <b>What Exists:</b> <code>recommendation_engine.py</code> (3-tier vector matcher + FBT bundle logic) is 100% complete.<br/>"
        "• <b>Required Delta:</b> Run <code>sync_recommendations_to_shopify.py</code> to populate Shopify <code>metafields.custom.similar_products</code> and FBT metafields on live storefront.",
        body_style
    ))
    
    story.append(Paragraph("<b>Phase 7: Composite Multi-Product Bundle Engine (2.5 Working Days)</b>", h2_style))
    story.append(Paragraph(
        "• Build canvas layout engine to arrange FBT pairs (e.g. Sofa + Coffee Table + Rug) onto a unified room background.<br/>"
        "• Apply contact-shadow filters and edge-blending via NodeTool/fal inpainting for realistic lighting cohesion.<br/>"
        "• Connect bundle generator into n8n automated batch workflows for Shopify collection pages.",
        body_style
    ))
    
    story.append(Paragraph("<b>Phase 8: Metadata, Options & Dynamic QR Codes (2 Working Days)</b>", h2_style))
    story.append(Paragraph(
        "• Implement dynamic vector QR code generator linking directly to live Shopify PDPs with UTM campaign tracking.<br/>"
        "• Configure backend variation logic separating configurable variants from standalone products and sync QR assets to Shopify.",
        body_style
    ))
    
    story.append(Paragraph("<b>Phase 9: AI Shopping Assistant Chatbot & Final Go-Live (3.5 Working Days)</b>", h2_style))
    story.append(Paragraph(
        "• Vectorize complete product catalog (embeddings for titles, Dutch descriptions, 13 style vectors, prices, and stock).<br/>"
        "• Embed AI Shopping Assistant chatbot widget on storefront frontend.<br/>"
        "• Conduct full-scale end-to-end regression testing across Baserow, n8n, S3, NodeTool, and Shopify webshops. Execute live production cutover!",
        body_style
    ))
    
    story.append(Spacer(1, 8))
    
    # Section 9: Deployment Checklist & Invariants
    story.append(Paragraph("9. Production Deployment Checklist & Environment Matrix", h1_style))
    
    env_data = [
        [Paragraph("Service / Component", table_header_style), Paragraph("Deployment Method", table_header_style), Paragraph("Key Environment Variables / Auth", table_header_style), Paragraph("Primary Port / Path", table_header_style)],
        [
            Paragraph("<b>Baserow</b>", table_body_bold),
            Paragraph("Hetzner Dedicated Server", table_body_style),
            Paragraph("<code>BASEROW_URL</code>, <code>BASEROW_TOKEN</code>, Table IDs (742, Jobs)", table_body_style),
            Paragraph("Port 443 (HTTPS)", table_body_style)
        ],
        [
            Paragraph("<b>NodeTool</b>", table_body_bold),
            Paragraph("Docker (Self-Hosted)", table_body_style),
            Paragraph("<code>FAL_KEY</code> (BYOK), <code>SECRET_STORE</code>", table_body_style),
            Paragraph("Port 3000", table_body_style)
        ],
        [
            Paragraph("<b>The Editor Seam</b>", table_body_bold),
            Paragraph("FastAPI / Docker", table_body_style),
            Paragraph("<code>BASEROW_TOKEN</code>, <code>S3_ACCESS_KEY</code>, <code>S3_SECRET_KEY</code>", table_body_style),
            Paragraph("Port 8000 (Internal)", table_body_style)
        ],
        [
            Paragraph("<b>FableCut</b>", table_body_bold),
            Paragraph("Docker (Self-Hosted)", table_body_style),
            Paragraph("<code>MCP_PORT</code>, Project JSON storage volume", table_body_style),
            Paragraph("Port 4000", table_body_style)
        ],
        [
            Paragraph("<b>Owned Storage</b>", table_body_bold),
            Paragraph("AWS S3 / MinIO", table_body_style),
            Paragraph("Bucket: <code>binnen-catalog-assets</code> (Public read / CORS enabled)", table_body_style),
            Paragraph("S3 HTTPS endpoint", table_body_style)
        ],
        [
            Paragraph("<b>n8n Orchestrator</b>", table_body_bold),
            Paragraph("Docker / Hetzner", table_body_style),
            Paragraph("Baserow Credential, S3 Credential, NodeTool API Credential", table_body_style),
            Paragraph("Port 5678", table_body_style)
        ],
        [
            Paragraph("<b>Shopify Stores</b>", table_body_bold),
            Paragraph("Live REST / GraphQL", table_body_style),
            Paragraph("<code>SHOPIFY_ADMIN_TOKEN</code>, Store Targets: Woonbloq (1), Binnen (3)", table_body_style),
            Paragraph("Shopify Cloud", table_body_style)
        ]
    ]
    
    env_table = Table(env_data, colWidths=[1.1*inch, 1.4*inch, 3.4*inch, 1.3*inch])
    env_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg_light]),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
    ]))
    story.append(env_table)
    
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Next Engineering Action:</b> Proceed with <b>Phase 0 Task 0.1</b> (NodeTool Docker sandbox setup and 6-gate validation).", h2_style))
    
    # Build Document with ImplementationNumberedCanvas
    doc.build(story, canvasmaker=ImplementationNumberedCanvas)
    print(f"Step-by-Step Implementation Guide PDF successfully generated at: {filename}")

if __name__ == "__main__":
    out_dir = Path("c:/projects/binnen-baserow2/output")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "Step_by_Step_Technical_Implementation_Guide.pdf"
    build_implementation_pdf(str(pdf_path))
    
    # Also copy to root
    root_pdf_path = Path("c:/projects/binnen-baserow2/Step_by_Step_Technical_Implementation_Guide.pdf")
    build_implementation_pdf(str(root_pdf_path))
