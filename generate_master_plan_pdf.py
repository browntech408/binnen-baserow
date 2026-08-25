"""Generate Master Progress, Architecture Evolution & Daily Sprint Implementation Plan PDF (v5.0 NodeTool-First).

Updates schedule to accurately reflect that scrapers, basic image resizing, MCP base, and recommendation algorithms
are ALREADY COMPLETE (0 days), detailing the exact architectural DELTA required for each component.
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

class MasterNumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(MasterNumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super(MasterNumberedCanvas, self).showPage()
        super(MasterNumberedCanvas, self).save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(54, 11 * 72 - 36, "Binnen & Baserow — Master Implementation Plan (v5.0 Delta Edition)")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, 11 * 72 - 42, 8.5 * 72 - 54, 11 * 72 - 42)
            
        # Footer
        footer_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(8.5 * 72 - 54, 36, footer_text)
        self.drawString(54, 36, "CONFIDENTIAL — FOR CLIENT & STAKEHOLDER REVIEW")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 48, 8.5 * 72 - 54, 48)
        self.restoreState()


def build_pdf(filename: str):
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
    border_color = colors.HexColor("#E2E8F0")
    badge_done = colors.HexColor("#166534") # Green 800
    badge_done_bg = colors.HexColor("#DCFCE7") # Green 100
    
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
        fontName='Helvetica',
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
    
    table_body_green = ParagraphStyle(
        'TableBodyGreen',
        parent=table_body_style,
        fontName='Helvetica-Bold',
        textColor=badge_done
    )
    
    story = []
    
    # Title & Metadata Block
    story.append(Paragraph("Comprehensive Project Status, Delta Analysis & Master Sprint Roadmap", title_style))
    story.append(Paragraph("v5.0 NodeTool-First Architecture · Audited Codebase & Net Effort Plan · August 2026", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=accent_color, spaceBefore=0, spaceAfter=8))
    
    # 1. Executive Summary & Delivery Protocol
    story.append(Paragraph("1. Executive Summary & Project Context", h1_style))
    story.append(Paragraph(
        "This master implementation plan establishes an audited, high-precision roadmap tailored to the client's v5.0 NodeTool-First "
        "specification. Crucially, <b>time is not allocated to tasks already completed in the codebase</b> (such as scrapers, standard image resizing, "
        "Dutch AI descriptions, basic MCP tools, and recommendation algorithms). Instead, this document details the exact <b>Architectural Delta</b> "
        "(what exists vs. what changes) and allocates timeline solely to the new NodeTool integration, Editor Seam contract, owned storage, video assembly, and 3D pipelines.",
        body_style
    ))
    
    story.append(Paragraph("<b>Strict Delivery & QA Protocol:</b>", h2_style))
    story.append(Paragraph("• <b>1. Spec Alignment:</b> Define explicit input/output criteria before building each sprint goal.", bullet_style))
    story.append(Paragraph("• <b>2. Client Approval:</b> Client sign-off on sprint specifications.", bullet_style))
    story.append(Paragraph("• <b>3. Internal QA:</b> End-to-end verification across Baserow, n8n, fal.ai, and Shopify staging.", bullet_style))
    story.append(Paragraph("• <b>4. Client Demo Walkthrough:</b> Interactive inspection grids, testing links, and video walkthroughs.", bullet_style))
    story.append(Paragraph("• <b>5. Go-Live Verification:</b> Production cutover with automatic safety locks.", bullet_style))
    
    story.append(Spacer(1, 4))
    
    # 2. Detailed Delta Analysis Table (What Exists vs What Changes)
    story.append(Paragraph("2. Detailed Delta Analysis: Existing Codebase vs. v5.0 Architectural Changes", h1_style))
    story.append(Paragraph(
        "The following matrix maps every project capability, its current verified completion in the codebase, and the precise architectural modification required by the v5.0 specification:",
        body_style
    ))
    
    delta_data = [
        [
            Paragraph("System Capability", table_header_style),
            Paragraph("Current Status in Codebase", table_header_style),
            Paragraph("Required Architectural Change (Delta) in v5.0 Spec", table_header_style),
            Paragraph("Net Effort Remaining", table_header_style)
        ],
        [
            Paragraph("<b>Scraper Infrastructure</b>", table_body_bold),
            Paragraph("<b>100% COMPLETE (28/28 Brands)</b><br/>All 9 priority & 19 standard brand scrapers built in <code>scrapers/router.py</code>.", table_body_style),
            Paragraph("<b>Zero Scraper Changes Needed:</b> Scraper router extracts full product specs, dimensions, and Dutch translations directly to Baserow.", table_body_style),
            Paragraph("<b>0 DAYS<br/>(COMPLETED)</b>", table_body_green)
        ],
        [
            Paragraph("<b>Hero BG Removal & Resizing</b>", table_body_bold),
            Paragraph("<b>100% FUNCTIONAL</b><br/>Built in <code>fal_image_processor.py</code> & <code>shopify_pixelbin_remove_bg.py</code> (1000x880 transparent, 1760x1100 white).", table_body_style),
            Paragraph("<b>Rewire into Intake Chain:</b> Move from standalone CLI execution into n8n automated intake chain calling NodeTool headless API. Save to owned S3 (<code>asset__v1</code>) instead of temporary URLs.", table_body_style),
            Paragraph("1 Day<br/>(Rewiring)", table_body_style)
        ],
        [
            Paragraph("<b>AI Dutch Descriptions</b>", table_body_bold),
            Paragraph("<b>100% COMPLETE</b><br/>Operational in <code>description_ai.py</code> (OpenRouter GPT-4o / Claude).", table_body_style),
            Paragraph("<b>Token Referencing:</b> Hook into Baserow <code>Prompt_Scene_Templates</code> and <code>Video_Templates</code> to inject descriptions as token parameters (<code>{title}</code>, <code>{description}</code>).", table_body_style),
            Paragraph("0.5 Day<br/>(Integration)", table_body_style)
        ],
        [
            Paragraph("<b>Shopify Storefront Sync</b>", table_body_bold),
            Paragraph("<b>100% FUNCTIONAL</b><br/>Operational in <code>baserow_shopify_sync.py</code> for Woonbloq (1) and Binnen (3).", table_body_style),
            Paragraph("<b>Field Mapping & Safety Lock:</b> Map new S3 URL fields (<code>clean_hero_url</code>, <code>lifestyle_chosen_url</code>, <code>video_urls</code>, <code>3D_urls</code>) and enforce <code>enrichment_status = 'edited'</code> safety lock.", table_body_style),
            Paragraph("0.5 Day<br/>(Schema update)", table_body_style)
        ],
        [
            Paragraph("<b>Agentic MCP Suite</b>", table_body_bold),
            Paragraph("<b>100% FUNCTIONAL BASE</b><br/>Native Python MCP server in <code>mcp_server.py</code> (Baserow & Shopify tools).", table_body_style),
            Paragraph("<b>Add Jobs Tool & Tri-MCP:</b> Add <code>create_enrichment_job</code> and <code>get_job_status</code> tools to <code>mcp_server.py</code>; configure Claude Cowork to connect Baserow MCP + NodeTool MCP + FableCut MCP.", table_body_style),
            Paragraph("1.5 Days<br/>(Tri-MCP config)", table_body_style)
        ],
        [
            Paragraph("<b>Recommendation Engine</b>", table_body_bold),
            Paragraph("<b>100% ALGORITHMS READY</b><br/>Pre-built in <code>recommendation_engine.py</code> (3-tier vector cosine similarity + Quality score + FBT bundle engine).", table_body_style),
            Paragraph("<b>Staging Sync & Metafields:</b> Execute <code>sync_recommendations_to_shopify.py</code> against live store metafields and connect FBT pairs into the bundle image layout engine.", table_body_style),
            Paragraph("1 Day<br/>(Staging Sync)", table_body_style)
        ],
        [
            Paragraph("<b>NodeTool Creative Core</b>", table_body_bold),
            Paragraph("<b>NEW IN v5.0 SPEC</b><br/>Not yet deployed.", table_body_style),
            Paragraph("<b>Deploy & Validate (§6):</b> Self-hosted NodeTool Docker deployment with BYOK fal.ai key; execute Phase 0 6-gate playground validation; setup headless execution API.", table_body_style),
            Paragraph("2.5 Days<br/>(Phase 0 setup)", table_body_style)
        ],
        [
            Paragraph("<b>The Editor Seam Service</b>", table_body_bold),
            Paragraph("<b>NEW IN v5.0 SPEC</b><br/>Not yet deployed.", table_body_style),
            Paragraph("<b>Build Thin Seam Service:</b> FastAPI router resolving Baserow formula links into NodeTool/FableCut/3D Viewer; return watched folder ingester ➔ S3 versioned upload ➔ Baserow <code>edited=true</code> lock.", table_body_style),
            Paragraph("2.5 Days<br/>(Seam Build)", table_body_style)
        ],
        [
            Paragraph("<b>Video Production Pipeline</b>", table_body_bold),
            Paragraph("<b>NEW IN v5.0 SPEC</b><br/>Basic video prototypes only.", table_body_style),
            Paragraph("<b>Dual-Class Video Architecture:</b> Remotion/FFmpeg programmatic assembly (zero baked text) + Seedance 2.0 timeline-prompt lifestyle ads with <code>@product</code> image anchors + FableCut agent bench.", table_body_style),
            Paragraph("3.5 Days<br/>(Video Build)", table_body_style)
        ],
        [
            Paragraph("<b>3D Pipeline & Viewer</b>", table_body_bold),
            Paragraph("<b>NEW IN v5.0 SPEC</b><br/>Not yet deployed.", table_body_style),
            Paragraph("<b>Hunyuan + glTF/USDZ:</b> fal Hunyuan 3D connector + n8n <code>glTF-Transform</code> compression + USDZ conversion + lightweight <code>&lt;model-viewer&gt;</code> review page.", table_body_style),
            Paragraph("2.5 Days<br/>(3D Build)", table_body_style)
        ],
    ]
    
    delta_table = Table(delta_data, colWidths=[1.3*inch, 1.8*inch, 3.1*inch, 1.0*inch])
    delta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg_light]),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
    ]))
    story.append(delta_table)
    
    story.append(Spacer(1, 8))
    story.append(PageBreak())
    
    # 3. Streamlined Master Timeline Overview Table
    story.append(Paragraph("3. Streamlined Master Timeline & Sprint Roadmap (Net Effort)", h1_style))
    story.append(Paragraph(
        "By leveraging existing codebase foundations, the project timeline is condensed to <b>real active engineering days</b>, eliminating redundant milestones:",
        body_style
    ))
    
    timeline_data = [
        [Paragraph("Sprint / Phase", table_header_style), Paragraph("Core Focus & Architectural Deliverables", table_header_style), Paragraph("Active Duration", table_header_style), Paragraph("Dependencies / Key Inputs", table_header_style)],
        [
            Paragraph("<b>Phase 0 (Mandatory)</b>", table_body_bold),
            Paragraph("<b>NodeTool Playground Validation (§6):</b> Standalone Docker testing of Canvas, Sketch Masking on furniture, Video Timeline, Seedance 2.0, Headless CLI & MCP.", table_body_style),
            Paragraph("<b>2.5 Working Days</b>", table_body_bold),
            Paragraph("Docker host, fal.ai key, 10 test furniture products.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 1</b>", table_body_bold),
            Paragraph("<b>Storage, Baserow Schemas & n8n System Glue:</b> S3/MinIO bucket setup, Baserow Jobs & Template tables, n8n intake (A) & grid marker (B) triggers.", table_body_style),
            Paragraph("<b>2 Working Days</b>", table_body_bold),
            Paragraph("Baserow admin token, S3 credentials, n8n instance.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 2</b>", table_body_bold),
            Paragraph("<b>The Editor Seam & Rewired Image Pipelines:</b> Deploy Seam microservice, rewire existing resizing into intake chain, on-demand lifestyle generation & correction loop.", table_body_style),
            Paragraph("<b>2.5 Working Days</b>", table_body_bold),
            Paragraph("NodeTool API endpoints, Baserow formula links.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 3</b>", table_body_bold),
            Paragraph("<b>Claude Cowork Tri-MCP & FableCut Setup:</b> Extend Python MCP server for Jobs queue, deploy FableCut Docker, test Cowork JSON timeline patching.", table_body_style),
            Paragraph("<b>2 Working Days</b>", table_body_bold),
            Paragraph("Claude Cowork / Desktop setup, Anthropic key.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 4</b>", table_body_bold),
            Paragraph("<b>Automated Video Production Pipeline:</b> Remotion/FFmpeg assembly (zero baked text), Seedance lifestyle ads with <code>@product</code> anchors, Shopify media push.", table_body_style),
            Paragraph("<b>3.5 Working Days</b>", table_body_bold),
            Paragraph("Seedance 2.0 / FLUX 3 via fal.ai, Remotion templates.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 5</b>", table_body_bold),
            Paragraph("<b>3D Pipeline Integration & &lt;model-viewer&gt;:</b> fal image-to-3D, glTF compression, USDZ conversion, review page & Shopify 3D metafield sync.", table_body_style),
            Paragraph("<b>2.5 Working Days</b>", table_body_bold),
            Paragraph("fal.ai 3D endpoint, glTF-Transform, USDZ tools.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 6</b>", table_body_bold),
            Paragraph("<b>Recommendation Engine & FBT Live Sync:</b> Push pre-built 3-tier matching and Frequently Bought Together bundles to live Shopify storefront.", table_body_style),
            Paragraph("<b>1 Working Day</b>", table_body_bold),
            Paragraph("Existing <code>recommendation_engine.py</code>, Shopify theme.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 7</b>", table_body_bold),
            Paragraph("<b>Composite Bundle Image Generation:</b> Multi-product layout canvas, edge-blending & contact shadows via NodeTool/fal inpainting.", table_body_style),
            Paragraph("<b>2.5 Working Days</b>", table_body_bold),
            Paragraph("FBT bundle pairs, NodeTool inpaint workflows.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 8</b>", table_body_bold),
            Paragraph("<b>Metadata, Options & Dynamic QR Codes:</b> Dynamic vector QR codes with UTM tracking, configurable product variation matrix.", table_body_style),
            Paragraph("<b>2 Working Days</b>", table_body_bold),
            Paragraph("Shopify product handles & QR asset storage.", table_body_style)
        ],
        [
            Paragraph("<b>Phase 9</b>", table_body_bold),
            Paragraph("<b>AI Shopping Assistant Chatbot & Go-Live:</b> Catalog vectorization, chatbot widget embedding on frontend, end-to-end QA & production cutover.", table_body_style),
            Paragraph("<b>3.5 Working Days</b>", table_body_bold),
            Paragraph("Frontend store access, embedding API key.", table_body_style)
        ],
    ]
    
    timeline_table = Table(timeline_data, colWidths=[1.2*inch, 3.3*inch, 1.2*inch, 1.5*inch])
    timeline_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg_light]),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
    ]))
    story.append(timeline_table)
    
    story.append(Spacer(1, 8))
    
    # 4. Detailed Daily Sprint Breakdown
    story.append(Paragraph("4. Streamlined Daily Sprint Execution Breakdown", h1_style))
    story.append(Paragraph(
        "Sprints focus purely on new delta deliverables and integration checkpoints:",
        body_style
    ))
    
    # Phase 0
    story.append(Paragraph("Phase 0: NodeTool Playground Sandbox Validation (Duration: 2.5 Working Days — Mandatory per §6)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Deploy self-hosted NodeTool Docker container with fal.ai key. Load 10 sample products manually.<br/>"
        "• <b>Day 2:</b> Validate Canvas lifestyle workflows & Layered Sketch Editor (masking/inpaint on furniture).<br/>"
        "• <b>Day 3 (Deliverable 0 Checkpoint - Half Day):</b> Validate Video Timeline, Seedance 2.0 timeline generation, Headless CLI execution, and NodeTool MCP. Present verification matrix to client. (If partial fail, activate pre-decided fallback).",
        body_style
    ))
    
    # Phase 1
    story.append(Paragraph("Phase 1: Storage, Baserow Schemas & n8n System Glue (Duration: 2 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Provision S3/MinIO bucket (<code>binnen-catalog-assets</code>) with versioning (<code>asset__v{n}</code>). Create Baserow <code>Jobs</code>, <code>Prompt_Scene_Templates</code>, and <code>Video_Templates</code> tables.<br/>"
        "• <b>Day 2 (Deliverable 1 Checkpoint):</b> Configure n8n webhook listeners for Intake Trigger (A) and Multi-Row Marker (B). Deploy n8n Job Queue runner with live Baserow progress write-back.",
        body_style
    ))
    
    # Phase 2
    story.append(Paragraph("Phase 2: The Editor Seam & Rewired Image Pipelines (Duration: 2.5 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Build The Editor Seam microservice (FastAPI router resolving Baserow formula links into NodeTool/FableCut + watched folder export listener).<br/>"
        "• <b>Day 2 (Deliverable 2 Checkpoint):</b> Rewire existing <code>fal_image_processor.py</code> resizing logic into n8n intake chain calling NodeTool headless API. Push versioned assets to S3 and link to Baserow.<br/>"
        "• <b>Day 3 (Deliverable 3 Checkpoint - Half Day):</b> Connect On-Demand Generative Tier (2-3 lifestyle variants per product from Baserow prompt templates) and test 3-tier Image Correction Loop.",
        body_style
    ))
    
    # Phase 3
    story.append(Paragraph("Phase 3: Conversational Claude Cowork & Tri-MCP Suite (Duration: 2 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Add <code>create_enrichment_job</code> and <code>get_job_status</code> tools to existing <code>mcp_server.py</code>. Deploy FableCut Docker container.<br/>"
        "• <b>Day 2 (Deliverable 4 & 5 Checkpoints):</b> Connect Baserow MCP + NodeTool MCP + FableCut MCP for Claude Cowork (Entry Point C). Test conflict-safe JSON timeline patching and deliver operational demo.",
        body_style
    ))
    
    # Phase 4
    story.append(Paragraph("Phase 4: Automated Video Production Pipeline (Duration: 3.5 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Build Remotion / FFmpeg programmatic rendering templates for Functional Videos (stills + specs + price overlays; zero baked text).<br/>"
        "• <b>Day 2:</b> Author timeline-prompt recipes in Video Templates table using Seedance 2.0 with <code>@product</code> anchors.<br/>"
        "• <b>Day 3:</b> Configure n8n video assembly pipeline with dynamic audio syncing. Wire FableCut & NodeTool into the Seam.<br/>"
        "• <b>Day 4 (Deliverable 6 & 7 Checkpoints - Half Day):</b> Push generated product videos directly to Shopify media galleries on staging.",
        body_style
    ))
    
    # Phase 5
    story.append(Paragraph("Phase 5: 3D Asset Pipeline Integration & <model-viewer> (Duration: 2.5 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Connect fal.ai Hunyuan image-to-3D connectors with parameter escalation ladders on reject.<br/>"
        "• <b>Day 2:</b> Build automated n8n post-processing: <code>glTF-Transform</code> compression + USDZ conversion for iOS AR.<br/>"
        "• <b>Day 3 (Deliverable 8 Checkpoint - Half Day):</b> Host lightweight <code>&lt;model-viewer&gt;</code> review page. Deploy 3D models to Baserow and Shopify.",
        body_style
    ))
    
    # Phase 6
    story.append(Paragraph("Phase 6: Recommendation Engine & FBT Staging Sync (Duration: 1 Working Day)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1 (Deliverable 9 Checkpoint):</b> Run pre-built <code>sync_recommendations_to_shopify.py</code> to sync 3-tier recommendations and Frequently Bought Together (FBT) bundles to Shopify metafields. Showcase live PDPs.",
        body_style
    ))
    
    # Phase 7
    story.append(Paragraph("Phase 7: Composite Multi-Product Bundle Engine (Duration: 2.5 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Build canvas layout engine to arrange FBT pairs (e.g. Sofa + Coffee Table + Rug) onto a unified room canvas.<br/>"
        "• <b>Day 2-3 (Deliverable 10 Checkpoint):</b> Program contact shadows and edge-blending filters via NodeTool/fal inpaint. Sync composite graphics to Shopify collections.",
        body_style
    ))
    
    # Phase 8
    story.append(Paragraph("Phase 8: Metadata, Configurable Products & QR Engine (Duration: 2 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Build dynamic QR code generator assigning uniquely scannable codes with UTM tracking to incoming products.<br/>"
        "• <b>Day 2 (Deliverable 11 Checkpoint):</b> Configure backend variation logic separating configurable variants from standalone products and sync QR assets to Shopify.",
        body_style
    ))
    
    # Phase 9
    story.append(Paragraph("Phase 9: AI Shopping Assistant Chatbot & Final Go-Live (Duration: 3.5 Working Days)", h2_style))
    story.append(Paragraph(
        "• <b>Day 1:</b> Vectorize complete product catalog (embeddings for titles, Dutch descriptions, 13 style vectors, prices, stock).<br/>"
        "• <b>Day 2:</b> Embed and style AI Shopping Assistant chatbot widget onto the storefront frontend.<br/>"
        "• <b>Day 3 (Deliverable 12 Checkpoint):</b> Test and refine dynamic catalog search filters through chatbot queries.<br/>"
        "• <b>Day 4 (Deliverable 13 Checkpoint / Go-Live - Half Day):</b> Full-scale end-to-end QA across all user paths, Baserow, n8n, S3, NodeTool, and Shopify webshops. Live cutover!",
        body_style
    ))
    
    story.append(Spacer(1, 8))
    
    # 5. Pre-Decided Fallback Matrix
    story.append(Paragraph("5. Pre-Decided Fallback Matrix (Zero-Delay Risk Mitigation)", h1_style))
    story.append(Paragraph(
        "To guarantee that technical blockers never stall delivery, pre-decided fallbacks slot in behind the exact same Editor Seam contract:",
        body_style
    ))
    
    fb_data = [
        [Paragraph("Component", table_header_style), Paragraph("Primary Solution", table_header_style), Paragraph("Fallback Trigger Criteria", table_header_style), Paragraph("Pre-Decided Fallback Solution", table_header_style)],
        [
            Paragraph("<b>Image Masking</b>", table_body_bold),
            Paragraph("NodeTool Sketch Editor", table_body_style),
            Paragraph("Masking precision inadequate or laggy in Phase 0.", table_body_style),
            Paragraph("<b>InvokeAI</b> on Vast.ai on-demand GPU (~$0.25/hr booted by n8n) or Gemini image API.", table_body_style)
        ],
        [
            Paragraph("<b>Human Video Timeline</b>", table_body_bold),
            Paragraph("NodeTool Video Timeline", table_body_style),
            Paragraph("Timeline trims/splits unstable in Phase 0.", table_body_style),
            Paragraph("<b>FableCut</b> project wrapper for human timeline too (swapping active project JSON).", table_body_style)
        ],
        [
            Paragraph("<b>Workflow Execution</b>", table_body_bold),
            Paragraph("NodeTool Headless API/CLI", table_body_style),
            Paragraph("Headless node execution flaky.", table_body_style),
            Paragraph("<b>n8n directly calling fal.ai APIs</b> with prompt templates stored in Baserow.", table_body_style)
        ],
        [
            Paragraph("<b>Agentic Tools</b>", table_body_bold),
            Paragraph("NodeTool MCP", table_body_style),
            Paragraph("NodeTool MCP immature.", table_body_style),
            Paragraph("<b>Baserow MCP + FableCut MCP</b> only; NodeTool accessed via the Seam.", table_body_style)
        ],
    ]
    
    fb_table = Table(fb_data, colWidths=[1.2*inch, 1.5*inch, 2.0*inch, 2.5*inch])
    fb_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, bg_light]),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
    ]))
    story.append(fb_table)
    
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Next Engineering Action:</b> Proceed with <b>Phase 0 Task 0.1</b> (NodeTool Docker sandbox setup and 6-gate validation).", h2_style))
    
    # Build Document with MasterNumberedCanvas
    doc.build(story, canvasmaker=MasterNumberedCanvas)
    print(f"Master Progress & Implementation Plan PDF successfully generated at: {filename}")

if __name__ == "__main__":
    out_dir = Path("c:/projects/binnen-baserow2/output")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "Master_Progress_Sprints_and_Implementation_Plan.pdf"
    build_pdf(str(pdf_path))
    
    # Also copy to root for easy user access
    root_pdf_path = Path("c:/projects/binnen-baserow2/Master_Progress_Sprints_and_Implementation_Plan.pdf")
    build_pdf(str(root_pdf_path))
