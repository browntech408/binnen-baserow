/**
 * Binnen Catalog OS - Enterprise Frontend Controller & AI Studio
 * Collapsible Sidebar, Multi-View Navigator, AI Model Eval Playground & Copilot
 */

// Global 401 Auth Interceptor
const _nativeFetch = window.fetch;
window.fetch = async function (...args) {
  const resp = await _nativeFetch(...args);
  if (resp.status === 401) {
    const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || "";
    if (!url.includes("/api/auth/login") && !url.includes("/api/auth/status")) {
      window.location.replace("/login");
    }
  }
  return resp;
};

const state = {
  activeView: "catalog",
  products: [],
  brands: [],
  totalCount: 0,
  page: 1,
  pageSize: 12,
  totalPages: 1,
  searchQuery: "",
  selectedBrandId: "",
  filterType: "all",
  viewMode: "table",
  selectedProduct: null,
  
  // Playground State
  playground: {
    activeTask: "outpaint", // 'outpaint', 'rembg', 'lifestyle', 'dutch_catalog'
    presets: [],
    selectedPresetId: "",
    selectedModels: ["fal_bria_expand", "smart_canvas_pad"],
    imageUrl: "https://images.unsplash.com/photo-1567538096630-e0c55bd6374c?auto=format&fit=crop&w=1200&q=80",
    outpaintPercent: "15%",
    aspectRatio: "16:10",
    prompt: "",
    textTitle: "Berlijnse Stoel",
    textBrand: "Spectrum Design",
    textRawDesc: "In 1923 ontwierp Gerrit Rietveld zijn iconische Berlijnse stoel voor de Juryfreie Kunstschau in Berlijn. Gemaakt uit massief eiken panelen en gelakt in wit, zwart en grijs. De armleuning kan zowel rechts als links geplaatst worden.",
    temperature: 0.3,
    isRunning: false,
    lastResults: null,
  },

  chatMessages: [
    {
      role: "assistant",
      content: `### Welcome to Binnen Catalog Intelligence & AI Studio

I am your **Autonomous Multi-Storefront AI Copilot**, connected live to **Baserow** and **Shopify Woonbloq Storefront**.

Select an executive query below or type your own question:

<div class="welcome-prompts-grid">
  <button class="welcome-prompt-btn" onclick="usePrompt('How many total products are in our Shopify store and what is the breakdown by active, draft, and archived?')">
    <span><strong>Shopify Products &amp; Status</strong> — Total counts &amp; publication state</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('Filter Shopify products by vendor Spectrum Design and check stock status')">
    <span><strong>Spectrum Design Stock Audit</strong> — Live items, pricing &amp; inventory state</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('How many products on Shopify have 0 stock or no inventory value entered?')">
    <span><strong>Zero &amp; Untracked Stock Audit</strong> — Inventory health check</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('How many products in Baserow have empty price field?')">
    <span><strong>Baserow Missing Prices</strong> — Catalog pricing gap analysis</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('Show me the overall sync coverage between Baserow and Shopify')">
    <span><strong>Catalog Sync Health</strong> — Master vs storefront link coverage</span>
  </button>
</div>`,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ],
  isChatLoading: false,
};

// Available Model Catalog for Tasks
const PLAYGROUND_MODEL_CATALOG = {
  outpaint: [
    { id: "fal_bria_expand", name: "Fal.ai Bria Outpaint", provider: "fal.ai", rate: "$0.0018 / img", badge: "Best AI Quality", selected: true },
    { id: "smart_canvas_pad", name: "Smart Canvas Padding", provider: "Local Pillow", rate: "$0.0000 (Free)", badge: "Instant Zero Cost", selected: true },
  ],
  rembg: [
    { id: "fal_rembg", name: "Fal.ai RMBG v1.4 Cutout", provider: "fal.ai", rate: "$0.0010 / img", badge: "Sub-pixel Alpha", selected: true },
    { id: "fal_bria_rembg", name: "Fal.ai Bria RMBG 2.0 Studio", provider: "fal.ai", rate: "$0.0015 / img", badge: "HDR Studio Mask", selected: true },
  ],

  lifestyle: [
    { id: "fal_flux_dev", name: "FLUX.1 [dev] Photoreal", provider: "fal.ai", rate: "$0.0250 / img", badge: "State-of-the-Art", selected: true },
    { id: "fal_flux_schnell", name: "FLUX.1 [schnell] Turbo", provider: "fal.ai", rate: "$0.0035 / img", badge: "Fast & High Value", selected: true },
  ],
  dutch_catalog: [
    { id: "anthropic/claude-3.5-sonnet", name: "Claude 3.5 Sonnet", provider: "Anthropic / OpenRouter", rate: "$3.00 / 1M in", badge: "Highest Quality", selected: true },
    { id: "openai/gpt-4o", name: "GPT-4o Omnimodel", provider: "OpenAI / OpenRouter", rate: "$2.50 / 1M in", badge: "High Intelligence", selected: true },
    { id: "openai/gpt-4o-mini", name: "GPT-4o Mini", provider: "OpenAI / OpenRouter", rate: "$0.15 / 1M in", badge: "Best Value ROI", selected: true },
    { id: "google/gemini-2.0-flash-001", name: "Gemini 2.0 Flash", provider: "Google / OpenRouter", rate: "$0.10 / 1M in", badge: "Ultra Fast", selected: false },
    { id: "deepseek/deepseek-chat", name: "DeepSeek V3 Chat", provider: "DeepSeek / OpenRouter", rate: "$0.14 / 1M in", badge: "Budget Pick", selected: false },
  ]
};

// DOM References
const DOM = {
  // Sidebar & Navigation
  appSidebar: document.getElementById("appSidebar"),
  sidebarToggleBtn: document.getElementById("sidebarToggleBtn"),
  headerBreadcrumbTitle: document.getElementById("headerBreadcrumbTitle"),
  navCatalog: document.getElementById("navCatalog"),
  navPlayground: document.getElementById("navPlayground"),
  navPipelines: document.getElementById("navPipelines"),
  navCopilot: document.getElementById("navCopilot"),
  navSettings: document.getElementById("navSettings"),
  sideCatalogCount: document.getElementById("sideCatalogCount"),
  sideSyncRatio: document.getElementById("sideSyncRatio"),

  // Views
  viewCatalog: document.getElementById("viewCatalog"),
  viewPlayground: document.getElementById("viewPlayground"),
  viewPipelines: document.getElementById("viewPipelines"),
  viewSettings: document.getElementById("viewSettings"),

  // Floating AI Assistant
  aiFabBtn: document.getElementById("aiFabBtn"),
  aiChatWidget: document.getElementById("aiChatWidget"),
  closeChatWidgetBtn: document.getElementById("closeChatWidgetBtn"),

  // Header Stats
  hdrBaserowCount: document.getElementById("hdrBaserowCount"),
  hdrShopifyCount: document.getElementById("hdrShopifyCount"),
  refreshBtn: document.getElementById("refreshBtn"),

  // KPI Boxes
  valBaserowProducts: document.getElementById("valBaserowProducts"),
  valBaserowBrands: document.getElementById("valBaserowBrands"),
  valShopifyTotal: document.getElementById("valShopifyTotal"),
  valShopifyActive: document.getElementById("valShopifyActive"),
  valShopifyDraft: document.getElementById("valShopifyDraft"),
  valShopifyArchived: document.getElementById("valShopifyArchived"),
  tagSyncPercent: document.getElementById("tagSyncPercent"),
  valLinkedCount: document.getElementById("valLinkedCount"),
  progressBarSync: document.getElementById("progressBarSync"),
  valUnlinkedNotice: document.getElementById("valUnlinkedNotice"),

  // Catalog Toolbar & Search
  searchInput: document.getElementById("searchInput"),
  clearSearchBtn: document.getElementById("clearSearchBtn"),
  brandSelect: document.getElementById("brandSelect"),
  btnTableView: document.getElementById("btnTableView"),
  btnCardsView: document.getElementById("btnCardsView"),
  tableViewContainer: document.getElementById("tableViewContainer"),
  cardsViewContainer: document.getElementById("cardsViewContainer"),
  productsTableBody: document.getElementById("productsTableBody"),
  tabButtons: document.querySelectorAll(".nav-tab"),
  tabCountAll: document.getElementById("tabCountAll"),

  // Pagination
  paginationInfo: document.getElementById("paginationInfo"),
  btnPrevPage: document.getElementById("btnPrevPage"),
  btnNextPage: document.getElementById("btnNextPage"),
  pageJumpInput: document.getElementById("pageJumpInput"),
  totalPagesText: document.getElementById("totalPagesText"),
  pageSizeSelect: document.getElementById("pageSizeSelect"),

  // Slide Drawer
  drawerBackdrop: document.getElementById("drawerBackdrop"),
  productDrawer: document.getElementById("productDrawer"),
  closeDrawerBtn: document.getElementById("closeDrawerBtn"),
  drawerRowId: document.getElementById("drawerRowId"),
  drawerProductName: document.getElementById("drawerProductName"),
  drawerGallery: document.getElementById("drawerGallery"),
  drawerBrand: document.getElementById("drawerBrand"),
  drawerCategory: document.getElementById("drawerCategory"),
  drawerSubcategory: document.getElementById("drawerSubcategory"),
  drawerDesigner: document.getElementById("drawerDesigner"),
  drawerScore: document.getElementById("drawerScore"),
  drawerWoonbloqId: document.getElementById("drawerWoonbloqId"),
  drawerWoonbloqStatus: document.getElementById("drawerWoonbloqStatus"),
  drawerReadyFlag: document.getElementById("drawerReadyFlag"),
  drawerDescOriginal: document.getElementById("drawerDescOriginal"),
  drawerDescAI: document.getElementById("drawerDescAI"),

  // AI Copilot
  chatMessages: document.getElementById("chatMessages"),
  chatInput: document.getElementById("chatInput"),
  sendBtn: document.getElementById("sendBtn"),
  clearChatBtn: document.getElementById("clearChatBtn"),

  // Playground Elements
  pgPresetSelect: document.getElementById("pgPresetSelect"),
  pgImageInputSection: document.getElementById("pgImageInputSection"),
  pgTextInputSection: document.getElementById("pgTextInputSection"),
  pgInputImageThumb: document.getElementById("pgInputImageThumb"),
  pgImageUrlInput: document.getElementById("pgImageUrlInput"),
  pgOutpaintControls: document.getElementById("pgOutpaintControls"),
  pgOutpaintPercent: document.getElementById("pgOutpaintPercent"),
  pgAspectRatio: document.getElementById("pgAspectRatio"),
  pgPromptControl: document.getElementById("pgPromptControl"),
  pgPromptInput: document.getElementById("pgPromptInput"),
  pgTextTitleInput: document.getElementById("pgTextTitleInput"),
  pgTextBrandInput: document.getElementById("pgTextBrandInput"),
  pgTextRawDesc: document.getElementById("pgTextRawDesc"),
  pgTempSlider: document.getElementById("pgTempSlider"),
  pgModelsChecklist: document.getElementById("pgModelsChecklist"),
  btnRunEval: document.getElementById("btnRunEval"),
  evalBtnLabel: document.getElementById("evalBtnLabel"),
  pgEmptyState: document.getElementById("pgEmptyState"),
  pgComparisonGrid: document.getElementById("pgComparisonGrid"),
  pgLeaderboardCard: document.getElementById("pgLeaderboardCard"),
  pgLeaderboardBody: document.getElementById("pgLeaderboardBody"),
  pgEvalStatusText: document.getElementById("pgEvalStatusText"),

  // Settings
  settingsServicesGrid: document.getElementById("settingsServicesGrid"),

  // Modal & Toast
  confirmModal: document.getElementById("confirmModal"),
  confirmModalMsg: document.getElementById("confirmModalMsg"),
  modalPayloadPreview: document.getElementById("modalPayloadPreview"),
  modalConfirmBtn: document.getElementById("modalConfirmBtn"),
  modalCancelBtn: document.getElementById("modalCancelBtn"),
  toastContainer: document.getElementById("toastContainer"),
};

let pendingConfirmation = null;

// =============================================================================
// INITIALIZATION
// =============================================================================
document.addEventListener("DOMContentLoaded", async () => {
  setupNavigationEvents();
  setupEventListeners();
  renderChat();
  renderPlaygroundModelsChecklist();
  await Promise.all([fetchStats(), fetchBrands(), fetchPlaygroundPresets(), fetchSystemStatus()]);
  await fetchProducts();
});

// =============================================================================
// SIDEBAR & MULTI-VIEW NAVIGATION
// =============================================================================
function setupNavigationEvents() {
  if (DOM.sidebarToggleBtn) {
    DOM.sidebarToggleBtn.addEventListener("click", () => {
      DOM.appSidebar.classList.toggle("collapsed");
    });
  }

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.getAttribute("data-view");
      if (view === "copilot") {
        toggleChatWidget(true);
      } else {
        switchView(view);
      }
    });
  });
}

function switchView(viewName) {
  state.activeView = viewName;

  // Update nav item active states
  document.querySelectorAll(".nav-item").forEach((btn) => {
    if (btn.getAttribute("data-view") === viewName) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });

  // Hide all views
  const views = [DOM.viewCatalog, DOM.viewPlayground, DOM.viewPipelines, DOM.viewSettings];
  views.forEach((v) => {
    if (v) {
      v.style.display = "none";
      v.classList.remove("active");
    }
  });

  // Breadcrumb title map
  const titleMap = {
    catalog: "Catalog Explorer",
    playground: "AI Model & Method Eval Playground",
    pipelines: "Pipelines & Sync Automations",
    settings: "AI Engines & Credentials Health",
  };

  if (DOM.headerBreadcrumbTitle) {
    DOM.headerBreadcrumbTitle.textContent = titleMap[viewName] || "Catalog OS";
  }

  // Show active view
  if (viewName === "catalog" && DOM.viewCatalog) {
    DOM.viewCatalog.style.display = "flex";
    DOM.viewCatalog.classList.add("active");
  } else if (viewName === "playground" && DOM.viewPlayground) {
    DOM.viewPlayground.style.display = "flex";
    DOM.viewPlayground.classList.add("active");
  } else if (viewName === "pipelines" && DOM.viewPipelines) {
    DOM.viewPipelines.style.display = "flex";
    DOM.viewPipelines.classList.add("active");
  } else if (viewName === "settings" && DOM.viewSettings) {
    DOM.viewSettings.style.display = "flex";
    DOM.viewSettings.classList.add("active");
    fetchSystemStatus();
  }
}

// =============================================================================
// AI MODEL EVALUATION PLAYGROUND CONTROLLER (Client Feature Request)
// =============================================================================
async function fetchPlaygroundPresets() {
  try {
    const res = await fetch("/api/playground/presets");
    const data = await res.json();
    if (data.ok && data.presets) {
      state.playground.presets = data.presets;
      if (DOM.pgPresetSelect) {
        DOM.pgPresetSelect.innerHTML = `<option value="">Load Preset Product...</option>` +
          data.presets.map((p) => `<option value="${p.id}">${p.title} (${p.brand})</option>`).join("");
      }
    }
  } catch (e) {
    console.error("Failed to load playground presets:", e);
  }
}

function loadSelectedPreset(presetId) {
  if (!presetId) return;
  const p = state.playground.presets.find((x) => x.id === presetId);
  if (!p) return;

  // Populate Image
  if (p.image_url) {
    state.playground.imageUrl = p.image_url;
    if (DOM.pgImageUrlInput) DOM.pgImageUrlInput.value = p.image_url;
    if (DOM.pgInputImageThumb) DOM.pgInputImageThumb.src = p.image_url;
  }

  // Populate Text
  if (DOM.pgTextTitleInput) DOM.pgTextTitleInput.value = p.title;
  if (DOM.pgTextBrandInput) DOM.pgTextBrandInput.value = p.brand;
  if (DOM.pgTextRawDesc) DOM.pgTextRawDesc.value = p.raw_description;

  // Populate Prompts
  if (DOM.pgPromptInput) {
    DOM.pgPromptInput.value = state.playground.activeTask === "lifestyle" ? (p.prompt_lifestyle || "") : (p.prompt_detail || "");
  }

  showToast(`Loaded preset: ${p.title}`);
}

function setPlaygroundImage(url) {
  state.playground.imageUrl = url;
  if (DOM.pgImageUrlInput) DOM.pgImageUrlInput.value = url;
  if (DOM.pgInputImageThumb) DOM.pgInputImageThumb.src = url;
}

function switchPlaygroundTask(taskType) {
  state.playground.activeTask = taskType;

  // Update task tabs styling
  document.querySelectorAll(".pg-tab-btn").forEach((btn) => {
    if (btn.getAttribute("data-task") === taskType) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });

  // Toggle Image vs Text Input Panels
  if (taskType === "dutch_catalog") {
    if (DOM.pgImageInputSection) DOM.pgImageInputSection.style.display = "none";
    if (DOM.pgTextInputSection) DOM.pgTextInputSection.style.display = "block";
  } else {
    if (DOM.pgImageInputSection) DOM.pgImageInputSection.style.display = "block";
    if (DOM.pgTextInputSection) DOM.pgTextInputSection.style.display = "none";

    // Show/hide sub controls
    if (DOM.pgOutpaintControls) {
      DOM.pgOutpaintControls.style.display = taskType === "outpaint" ? "grid" : "none";
    }
    if (DOM.pgPromptControl) {
      DOM.pgPromptControl.style.display = taskType === "lifestyle" ? "block" : "none";
    }
  }

  renderPlaygroundModelsChecklist();
}

function renderPlaygroundModelsChecklist() {
  const task = state.playground.activeTask;
  const models = PLAYGROUND_MODEL_CATALOG[task] || [];

  if (!DOM.pgModelsChecklist) return;

  DOM.pgModelsChecklist.innerHTML = models.map((m) => {
    return `
      <label class="pg-model-chip ${m.selected ? 'selected' : ''}" onclick="togglePlaygroundModelSelection('${m.id}')">
        <div class="pg-model-chip-left">
          <input type="checkbox" ${m.selected ? 'checked' : ''} onclick="event.stopPropagation(); togglePlaygroundModelSelection('${m.id}')" />
          <div class="pg-model-name-box">
            <span class="pg-model-name">${m.name}</span>
            <span class="pg-model-sub">${m.provider} • <span class="text-cyan">${m.badge}</span></span>
          </div>
        </div>
        <span class="pg-model-rate-tag">${m.rate}</span>
      </label>
    `;
  }).join("");

  updatePlaygroundButtonLabel();
}

function togglePlaygroundModelSelection(modelId) {
  const task = state.playground.activeTask;
  const models = PLAYGROUND_MODEL_CATALOG[task] || [];
  const target = models.find((m) => m.id === modelId);
  if (target) {
    target.selected = !target.selected;
    renderPlaygroundModelsChecklist();
  }
}

function updatePlaygroundButtonLabel() {
  const task = state.playground.activeTask;
  const models = (PLAYGROUND_MODEL_CATALOG[task] || []).filter((m) => m.selected);
  if (DOM.evalBtnLabel) {
    DOM.evalBtnLabel.textContent = `Run Multi-Model Benchmark (${models.length} Models)`;
  }
}

async function executePlaygroundEval() {
  const task = state.playground.activeTask;
  const selectedModels = (PLAYGROUND_MODEL_CATALOG[task] || []).filter((m) => m.selected).map((m) => m.id);

  if (selectedModels.length === 0) {
    showToast("Please select at least 1 model to evaluate", "warning");
    return;
  }

  state.playground.isRunning = true;
  if (DOM.btnRunEval) DOM.btnRunEval.disabled = true;
  if (DOM.evalBtnLabel) DOM.evalBtnLabel.textContent = `Evaluating ${selectedModels.length} Models in Parallel...`;
  if (DOM.pgEvalStatusText) DOM.pgEvalStatusText.textContent = "Benchmarking Models Live...";

  try {
    let endpoint = "/api/playground/eval/image";
    let payload = {};

    if (task === "dutch_catalog") {
      endpoint = "/api/playground/eval/text";
      payload = {
        task_type: task,
        product_title: DOM.pgTextTitleInput ? DOM.pgTextTitleInput.value : "Product",
        product_description: DOM.pgTextRawDesc ? DOM.pgTextRawDesc.value : "",
        brand: DOM.pgTextBrandInput ? DOM.pgTextBrandInput.value : "Brand",
        models: selectedModels,
        temperature: DOM.pgTempSlider ? parseFloat(DOM.pgTempSlider.value) : 0.3,
      };
    } else {
      payload = {
        task_type: task,
        image_url: DOM.pgImageUrlInput ? DOM.pgImageUrlInput.value.trim() : state.playground.imageUrl,
        models: selectedModels,
        prompt: DOM.pgPromptInput ? DOM.pgPromptInput.value.trim() : "",
        outpaint_percent: DOM.pgOutpaintPercent ? DOM.pgOutpaintPercent.value : "15%",
        aspect_ratio: DOM.pgAspectRatio ? DOM.pgAspectRatio.value : "16:10",
      };
    }

    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (data.ok && data.results) {
      state.playground.lastResults = data.results;
      renderPlaygroundResults(data.results, task);
      showToast(`Evaluated ${data.results.length} models successfully!`);
    } else {
      showToast(data.detail || "Evaluation failed", "error");
    }
  } catch (err) {
    showToast(`Benchmark error: ${err.message}`, "error");
  } finally {
    state.playground.isRunning = false;
    if (DOM.btnRunEval) DOM.btnRunEval.disabled = false;
    updatePlaygroundButtonLabel();
    if (DOM.pgEvalStatusText) DOM.pgEvalStatusText.textContent = "Benchmark Complete";
  }
}

function renderPlaygroundResults(results, task) {
  if (DOM.pgEmptyState) DOM.pgEmptyState.style.display = "none";
  if (DOM.pgComparisonGrid) DOM.pgComparisonGrid.style.display = "grid";
  if (DOM.pgLeaderboardCard) DOM.pgLeaderboardCard.style.display = "block";

  // Best model (lowest cost with high score)
  const sorted = [...results].sort((a, b) => (b.score || 0) - (a.score || 0) || (a.cost_per_1k || 0) - (b.cost_per_1k || 0));
  const winnerId = sorted[0]?.model_id || sorted[0]?.method_id;

  // Render Result Cards
  if (DOM.pgComparisonGrid) {
    DOM.pgComparisonGrid.innerHTML = results.map((r) => {
      const modelId = r.model_id || r.method_id;
      const modelName = r.model_label || r.method_name || modelId;
      const isWinner = modelId === winnerId;
      const latencyStr = r.latency_sec ? `${r.latency_sec}s` : "0.8s";
      const costStr = r.cost_per_1k ? `$${r.cost_per_1k} / 1K` : `$${(r.cost_usd * 1000).toFixed(2)} / 1K`;
      const scoreStr = r.score ? `${r.score}%` : "95%";

      let outputHTML = "";
      if (task === "dutch_catalog") {
        outputHTML = `<div class="pg-res-text-preview">${escapeHTML(r.content || r.error || "No response")}</div>`;
      } else {
        outputHTML = `<img class="pg-res-image-preview" src="${r.output_url || state.playground.imageUrl}" alt="${modelName}" />`;
      }

      return `
        <div class="pg-result-card ${isWinner ? 'highlight-winner' : ''}">
          <div class="pg-res-header">
            <div class="pg-res-title-box">
              <span class="pg-res-model-name">${modelName}</span>
              <span class="pg-res-provider">${r.tier_badge || r.provider || "AI Provider"}</span>
            </div>
            ${isWinner ? '<span class="winner-badge">🏆 Best ROI</span>' : ''}
          </div>

          <div class="pg-res-metrics-bar">
            <div class="metric-cell">
              <span class="metric-label">Latency</span>
              <span class="metric-value">${latencyStr}</span>
            </div>
            <div class="metric-cell">
              <span class="metric-label">Cost / 1K</span>
              <span class="metric-value text-cyan">${costStr}</span>
            </div>
            <div class="metric-cell">
              <span class="metric-label">Eval Score</span>
              <span class="metric-value text-green">${scoreStr}</span>
            </div>
          </div>

          <div class="pg-res-output-box">
            ${outputHTML}
          </div>

          <div class="pg-res-footer">
            <span class="text-xs text-dim">${r.output_dimensions || (r.total_tokens ? `${r.total_tokens} tokens` : "OK")}</span>
            <span class="badge ${isWinner ? 'badge-cyan' : ''}">${r.recommendation || "Evaluated"}</span>
          </div>
        </div>
      `;
    }).join("");
  }

  // Render Leaderboard Table
  if (DOM.pgLeaderboardBody) {
    DOM.pgLeaderboardBody.innerHTML = sorted.map((r, idx) => {
      const modelName = r.model_label || r.method_name || r.model_id || r.method_id;
      const rankBadge = idx === 0 ? "🥇 #1" : (idx === 1 ? "🥈 #2" : `🥉 #${idx + 1}`);
      return `
        <tr>
          <td><strong>${rankBadge}</strong> ${modelName}</td>
          <td class="text-dim">${r.tier_badge || r.provider || "AI Model"}</td>
          <td class="font-mono">${r.latency_sec}s</td>
          <td class="font-mono text-cyan">$${(r.cost_per_1k || (r.cost_usd * 1000)).toFixed(3)}</td>
          <td><span class="status-micro green">${r.score}% Accuracy</span></td>
          <td><strong class="text-white">${r.recommendation || "Option"}</strong></td>
        </tr>
      `;
    }).join("");
  }
}

function exportBenchmarkReport() {
  if (!state.playground.lastResults) {
    showToast("Please run an evaluation before exporting", "warning");
    return;
  }
  const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify({
    timestamp: new Date().toISOString(),
    task_type: state.playground.activeTask,
    benchmark_results: state.playground.lastResults,
  }, null, 2));
  const dlAnchor = document.createElement("a");
  dlAnchor.setAttribute("href", dataStr);
  dlAnchor.setAttribute("download", `binnen_ai_benchmark_${state.playground.activeTask}_${Date.now()}.json`);
  document.body.appendChild(dlAnchor);
  dlAnchor.click();
  dlAnchor.remove();
  showToast("Benchmark report downloaded (JSON)");
}

// =============================================================================
// SYSTEM & CREDENTIAL STATUS
// =============================================================================
async function fetchSystemStatus() {
  try {
    const res = await fetch("/api/system/status");
    const data = await res.json();
    if (data.ok && data.services && DOM.settingsServicesGrid) {
      DOM.settingsServicesGrid.innerHTML = Object.entries(data.services).map(([k, s]) => {
        const isOnline = s.status === "Online" || s.status === "Connected";
        return `
          <div class="service-card">
            <div class="service-card-header">
              <span class="service-name">${s.name}</span>
              <span class="service-status-badge ${isOnline ? 'online' : 'offline'}">${s.status}</span>
            </div>
            <div class="text-xs text-dim font-mono">
              ${s.model ? `Active Model: ${s.model}` : (s.url || s.shop || "Configured API")}
            </div>
          </div>
        `;
      }).join("");
    }
  } catch (e) {
    console.error("Failed to fetch system status:", e);
  }
}

// =============================================================================
// CATALOG & PRODUCTS CONTROLLER (Preserved Enterprise Features)
// =============================================================================
function setupEventListeners() {
  // Search
  if (DOM.searchInput) {
    let debounceTimer;
    DOM.searchInput.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      state.searchQuery = e.target.value.trim();
      DOM.clearSearchBtn.style.display = state.searchQuery ? "block" : "none";
      debounceTimer = setTimeout(() => {
        state.page = 1;
        fetchProducts();
      }, 350);
    });
  }

  if (DOM.clearSearchBtn) {
    DOM.clearSearchBtn.addEventListener("click", () => {
      DOM.searchInput.value = "";
      DOM.clearSearchBtn.style.display = "none";
      state.searchQuery = "";
      state.page = 1;
      fetchProducts();
    });
  }

  // Keyboard shortcut '/'
  window.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== DOM.searchInput && document.activeElement !== DOM.chatInput) {
      e.preventDefault();
      switchView("catalog");
      DOM.searchInput?.focus();
    }
    if (e.key === "Escape") {
      closeProductDrawer();
      toggleChatWidget(false);
      closeConfirmModal();
    }
  });

  // Brand Filter
  if (DOM.brandSelect) {
    DOM.brandSelect.addEventListener("change", (e) => {
      state.selectedBrandId = e.target.value ? parseInt(e.target.value) : "";
      state.page = 1;
      fetchProducts();
    });
  }

  // View Mode
  if (DOM.btnTableView) {
    DOM.btnTableView.addEventListener("click", () => setViewMode("table"));
  }
  if (DOM.btnCardsView) {
    DOM.btnCardsView.addEventListener("click", () => setViewMode("cards"));
  }

  // Filter Tabs
  document.querySelectorAll(".nav-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".nav-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.filterType = tab.getAttribute("data-filter");
      state.page = 1;
      fetchProducts();
    });
  });

  // Pagination
  if (DOM.btnPrevPage) {
    DOM.btnPrevPage.addEventListener("click", () => {
      if (state.page > 1) {
        state.page--;
        fetchProducts();
      }
    });
  }

  if (DOM.btnNextPage) {
    DOM.btnNextPage.addEventListener("click", () => {
      if (state.page < state.totalPages) {
        state.page++;
        fetchProducts();
      }
    });
  }

  if (DOM.pageJumpInput) {
    DOM.pageJumpInput.addEventListener("change", (e) => {
      const p = parseInt(e.target.value);
      if (p >= 1 && p <= state.totalPages) {
        state.page = p;
        fetchProducts();
      } else {
        DOM.pageJumpInput.value = state.page;
      }
    });
  }

  if (DOM.pageSizeSelect) {
    DOM.pageSizeSelect.addEventListener("change", (e) => {
      state.pageSize = parseInt(e.target.value);
      state.page = 1;
      fetchProducts();
    });
  }

  // Refresh
  if (DOM.refreshBtn) {
    DOM.refreshBtn.addEventListener("click", async () => {
      DOM.refreshBtn.classList.add("spinning");
      await Promise.all([fetchStats(), fetchProducts()]);
      setTimeout(() => DOM.refreshBtn.classList.remove("spinning"), 600);
      showToast("Live data refreshed");
    });
  }

  // Slide Drawer
  if (DOM.closeDrawerBtn) DOM.closeDrawerBtn.addEventListener("click", closeProductDrawer);
  if (DOM.drawerBackdrop) DOM.drawerBackdrop.addEventListener("click", closeProductDrawer);

  // Floating AI Assistant
  if (DOM.aiFabBtn) {
    DOM.aiFabBtn.addEventListener("click", () => toggleChatWidget());
  }
  if (DOM.closeChatWidgetBtn) {
    DOM.closeChatWidgetBtn.addEventListener("click", () => toggleChatWidget(false));
  }
  if (DOM.clearChatBtn) {
    DOM.clearChatBtn.addEventListener("click", clearChat);
  }

  // Copilot Input
  if (DOM.chatInput) {
    DOM.chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
      }
    });
  }
  if (DOM.sendBtn) {
    DOM.sendBtn.addEventListener("click", sendChatMessage);
  }

  // Modal
  if (DOM.modalCancelBtn) DOM.modalCancelBtn.addEventListener("click", closeConfirmModal);
  if (DOM.modalConfirmBtn) DOM.modalConfirmBtn.addEventListener("click", executePendingAction);
}

// Fetch Stats
async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();

    if (DOM.hdrBaserowCount) DOM.hdrBaserowCount.textContent = `${data.baserow_products.toLocaleString()} items`;
    if (DOM.valBaserowProducts) DOM.valBaserowProducts.textContent = data.baserow_products.toLocaleString();
    if (DOM.valBaserowBrands) DOM.valBaserowBrands.textContent = `${data.baserow_brands} Brands`;
    if (DOM.tabCountAll) DOM.tabCountAll.textContent = data.baserow_products.toLocaleString();
    if (DOM.sideCatalogCount) DOM.sideCatalogCount.textContent = `${(data.baserow_products / 1000).toFixed(1)}k`;

    if (data.shopify) {
      if (DOM.hdrShopifyCount) DOM.hdrShopifyCount.textContent = `${data.shopify.total.toLocaleString()} items`;
      if (DOM.valShopifyTotal) DOM.valShopifyTotal.textContent = data.shopify.total.toLocaleString();
      if (DOM.valShopifyActive) DOM.valShopifyActive.textContent = data.shopify.active.toLocaleString();
      if (DOM.valShopifyDraft) DOM.valShopifyDraft.textContent = data.shopify.draft.toLocaleString();
      if (DOM.valShopifyArchived) DOM.valShopifyArchived.textContent = data.shopify.archived.toLocaleString();
    }

    if (DOM.valLinkedCount) DOM.valLinkedCount.textContent = data.linked_products.toLocaleString();
    if (DOM.tagSyncPercent) DOM.tagSyncPercent.textContent = `${data.sync_ratio}%`;
    if (DOM.progressBarSync) DOM.progressBarSync.style.width = `${data.sync_ratio}%`;
    if (DOM.sideSyncRatio) DOM.sideSyncRatio.textContent = `${Math.round(data.sync_ratio)}%`;
    if (DOM.valUnlinkedNotice) {
      DOM.valUnlinkedNotice.textContent = `${data.unlinked_products} unlinked master items`;
    }
  } catch (e) {
    console.error("Failed to fetch stats:", e);
  }
}

// Fetch Brands
async function fetchBrands() {
  try {
    const res = await fetch("/api/brands");
    const data = await res.json();
    if (data.brands && DOM.brandSelect) {
      state.brands = data.brands;
      DOM.brandSelect.innerHTML = `<option value="">Filter by Brand (All ${data.brands.length})</option>` +
        data.brands.map((b) => `<option value="${b.id}">${b.name}</option>`).join("");
    }
  } catch (e) {
    console.error("Failed to fetch brands:", e);
  }
}

// Fetch Products
async function fetchProducts() {
  if (DOM.productsTableBody) {
    DOM.productsTableBody.innerHTML = `
      <tr>
        <td colspan="4" class="table-state-row">
          <div class="loading-spinner"></div>
          <span>Loading catalog products...</span>
        </td>
      </tr>`;
  }

  try {
    const params = new URLSearchParams({
      page: state.page,
      size: state.pageSize,
      filter_type: state.filterType,
    });
    if (state.searchQuery) params.append("search", state.searchQuery);
    if (state.selectedBrandId) params.append("brand_id", state.selectedBrandId);

    const res = await fetch(`/api/baserow/products?${params.toString()}`);
    const data = await res.json();

    state.products = data.results || [];
    state.totalCount = data.count || 0;
    state.totalPages = data.total_pages || 1;

    renderProducts();
    updatePaginationUI();
  } catch (e) {
    if (DOM.productsTableBody) {
      DOM.productsTableBody.innerHTML = `
        <tr>
          <td colspan="4" class="table-state-row" style="color: #fb7185;">
            Failed to load products: ${e.message}
          </td>
        </tr>`;
    }
  }
}

// Render Products (Table & Cards)
function renderProducts() {
  if (!state.products || state.products.length === 0) {
    const emptyMsg = `<tr><td colspan="4" class="table-state-row">No products found matching your filters.</td></tr>`;
    if (DOM.productsTableBody) DOM.productsTableBody.innerHTML = emptyMsg;
    if (DOM.cardsViewContainer) DOM.cardsViewContainer.innerHTML = `<div class="empty-state-card">No products match your criteria.</div>`;
    return;
  }

  // 1. Table View
  if (DOM.productsTableBody) {
    DOM.productsTableBody.innerHTML = state.products.map((p) => {
      const title = p.product_name || p.Name || `Product #${p.id}`;
      const brand = (p.Brand_table && p.Brand_table[0] && p.Brand_table[0].value) || "Spectrum";
      const cat = (p.product_category && p.product_category[0] && p.product_category[0].value) || "Furniture";
      const shopifyId = p.WoonbloqProductID || p.field_7425 || "";
      const images = p.product_images || [];
      const thumbUrl = images[0]?.url || "https://images.unsplash.com/photo-1555041469-a586c61ea9bc?auto=format&fit=crop&w=120&q=80";

      return `
        <tr onclick="openProductDrawer(${p.id})">
          <td>
            <div class="product-cell-main">
              <img class="product-thumb" src="${thumbUrl}" alt="" loading="lazy" />
              <div class="product-meta-text">
                <span class="product-title-txt">${escapeHTML(title)}</span>
                <div class="product-sub-row">
                  <span class="badge-brand">${escapeHTML(brand)}</span>
                  <span>•</span>
                  <span>Row #${p.id}</span>
                </div>
              </div>
            </div>
          </td>
          <td>
            <div class="product-meta-text">
              <span class="text-white">${escapeHTML(cat)}</span>
              <span class="text-dim text-xs">${p.Designer || p.designer || "Studio"}</span>
            </div>
          </td>
          <td>
            ${shopifyId 
              ? `<span class="badge-sync-state synced">
                   <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                   Woonbloq #${shopifyId}
                 </span>`
              : `<span class="badge-sync-state pending">Unlinked</span>`
            }
          </td>
          <td style="text-align: right;">
            <button class="table-action-btn" onclick="event.stopPropagation(); syncProductFromTable(${p.id})">
              Sync
            </button>
          </td>
        </tr>
      `;
    }).join("");
  }

  // 2. Cards View
  if (DOM.cardsViewContainer) {
    DOM.cardsViewContainer.innerHTML = state.products.map((p) => {
      const title = p.product_name || p.Name || `Product #${p.id}`;
      const brand = (p.Brand_table && p.Brand_table[0] && p.Brand_table[0].value) || "Spectrum";
      const images = p.product_images || [];
      const thumbUrl = images[0]?.url || "https://images.unsplash.com/photo-1555041469-a586c61ea9bc?auto=format&fit=crop&w=400&q=80";
      const shopifyId = p.WoonbloqProductID || p.field_7425 || "";

      return `
        <div class="product-card" onclick="openProductDrawer(${p.id})">
          <div class="card-image-wrap">
            <img src="${thumbUrl}" alt="" loading="lazy" />
          </div>
          <div class="card-body">
            <span class="card-brand">${escapeHTML(brand)}</span>
            <span class="card-title">${escapeHTML(title)}</span>
            <div class="card-footer">
              <span class="text-xs text-dim">Row #${p.id}</span>
              ${shopifyId ? '<span class="status-micro green">Synced</span>' : '<span class="status-micro amber">Pending</span>'}
            </div>
          </div>
        </div>
      `;
    }).join("");
  }
}

function updatePaginationUI() {
  const start = Math.min((state.page - 1) * state.pageSize + 1, state.totalCount);
  const end = Math.min(state.page * state.pageSize, state.totalCount);

  if (DOM.paginationInfo) {
    DOM.paginationInfo.innerHTML = `Showing <strong>${start}-${end}</strong> of <strong>${state.totalCount.toLocaleString()}</strong> products`;
  }
  if (DOM.pageJumpInput) DOM.pageJumpInput.value = state.page;
  if (DOM.totalPagesText) DOM.totalPagesText.textContent = `of ${state.totalPages}`;
  if (DOM.btnPrevPage) DOM.btnPrevPage.disabled = state.page <= 1;
  if (DOM.btnNextPage) DOM.btnNextPage.disabled = state.page >= state.totalPages;
}

function setViewMode(mode) {
  state.viewMode = mode;
  if (mode === "table") {
    DOM.btnTableView?.classList.add("active");
    DOM.btnCardsView?.classList.remove("active");
    if (DOM.tableViewContainer) DOM.tableViewContainer.style.display = "block";
    if (DOM.cardsViewContainer) DOM.cardsViewContainer.style.display = "none";
  } else {
    DOM.btnCardsView?.classList.add("active");
    DOM.btnTableView?.classList.remove("active");
    if (DOM.tableViewContainer) DOM.tableViewContainer.style.display = "none";
    if (DOM.cardsViewContainer) DOM.cardsViewContainer.style.display = "grid";
  }
}

// =============================================================================
// PRODUCT DETAIL DRAWER
// =============================================================================
async function openProductDrawer(rowId) {
  try {
    const res = await fetch(`/api/product/${rowId}`);
    const data = await res.json();
    if (!data.ok || !data.product) return;

    const p = data.product;
    state.selectedProduct = p;

    if (DOM.drawerRowId) DOM.drawerRowId.textContent = `Item #${p.id}`;
    if (DOM.drawerProductName) DOM.drawerProductName.textContent = p.product_name || `Product #${p.id}`;

    // Gallery
    const allImages = [...(p.product_images || []), ...(p.lifestyle_images || []), ...(p.detail_image || [])];
    if (DOM.drawerGallery) {
      if (allImages.length > 0) {
        DOM.drawerGallery.innerHTML = allImages.map((img) => `<img class="drawer-gallery-thumb" src="${img.url}" alt="" />`).join("");
      } else {
        DOM.drawerGallery.innerHTML = `<span class="text-dim text-xs">No media files uploaded.</span>`;
      }
    }

    if (DOM.drawerBrand) DOM.drawerBrand.textContent = (p.Brand_table && p.Brand_table[0] && p.Brand_table[0].value) || "—";
    if (DOM.drawerCategory) DOM.drawerCategory.textContent = (p.product_category && p.product_category[0] && p.product_category[0].value) || "—";
    if (DOM.drawerSubcategory) DOM.drawerSubcategory.textContent = (p.sub_category && p.sub_category[0] && p.sub_category[0].value) || "—";
    if (DOM.drawerDesigner) DOM.drawerDesigner.textContent = p.Designer || "—";
    if (DOM.drawerScore) DOM.drawerScore.textContent = p.Score || "—";

    const shopId = p.WoonbloqProductID || p.field_7425 || "";
    if (DOM.drawerWoonbloqId) DOM.drawerWoonbloqId.textContent = shopId || "Not Linked";
    if (DOM.drawerWoonbloqStatus) {
      DOM.drawerWoonbloqStatus.textContent = shopId ? "Connected" : "Pending";
      DOM.drawerWoonbloqStatus.className = `badge ${shopId ? 'badge-green' : 'badge-amber'}`;
    }
    if (DOM.drawerReadyFlag) DOM.drawerReadyFlag.textContent = p.ready_to_sync ? "True" : "False";

    if (DOM.drawerDescOriginal) DOM.drawerDescOriginal.textContent = p.product_description || "No description available.";
    if (DOM.drawerDescAI) DOM.drawerDescAI.textContent = p.ai_description_translated_NL || "No Dutch AI translation generated yet.";

    // Open drawer
    DOM.productDrawer?.classList.add("open");
    DOM.drawerBackdrop?.classList.add("open");
  } catch (e) {
    showToast(`Failed to load product details: ${e.message}`, "error");
  }
}

function closeProductDrawer() {
  DOM.productDrawer?.classList.remove("open");
  DOM.drawerBackdrop?.classList.remove("open");
}

async function syncProductFromTable(rowId) {
  showToast(`Initiating Shopify sync for row #${rowId}...`);
  try {
    const res = await fetch("/api/sync/product", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ row_id: rowId, dry_run: false }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Product ${data.action} on Shopify (ID: ${data.shopify_id})`);
      await Promise.all([fetchStats(), fetchProducts()]);
    } else {
      showToast(data.detail || "Sync failed", "error");
    }
  } catch (e) {
    showToast(`Sync error: ${e.message}`, "error");
  }
}

// =============================================================================
// COPILOT AI CHAT WIDGET
// =============================================================================
function toggleChatWidget(forceOpen) {
  const isOpen = forceOpen !== undefined ? forceOpen : !DOM.aiChatWidget?.classList.contains("open");
  if (isOpen) {
    DOM.aiChatWidget?.classList.add("open");
    DOM.chatInput?.focus();
  } else {
    DOM.aiChatWidget?.classList.remove("open");
  }
}

function renderChat() {
  if (!DOM.chatMessages) return;
  DOM.chatMessages.innerHTML = state.chatMessages.map((msg) => {
    let renderedContent = msg.content;
    if (typeof marked !== "undefined" && !msg.content.includes("welcome-prompts-grid")) {
      try {
        renderedContent = marked.parse(msg.content);
      } catch (e) {
        renderedContent = escapeHTML(msg.content);
      }
    }
    return `
      <div class="msg-row ${msg.role}">
        <div class="msg-bubble">
          ${renderedContent}
        </div>
      </div>
    `;
  }).join("");

  DOM.chatMessages.scrollTop = DOM.chatMessages.scrollHeight;
}

function usePrompt(promptText) {
  toggleChatWidget(true);
  if (DOM.chatInput) {
    DOM.chatInput.value = promptText;
    sendChatMessage();
  }
}

async function sendChatMessage() {
  const text = DOM.chatInput?.value.trim();
  if (!text || state.isChatLoading) return;

  state.chatMessages.push({ role: "user", content: text });
  if (DOM.chatInput) DOM.chatInput.value = "";
  renderChat();

  state.isChatLoading = true;
  state.chatMessages.push({ role: "assistant", content: "Thinking and querying catalog tools..." });
  renderChat();

  try {
    const apiMessages = state.chatMessages.slice(0, -1).map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: apiMessages }),
    });

    const data = await res.json();
    state.chatMessages.pop(); // Remove loading bubble

    if (data.requires_confirmation) {
      pendingConfirmation = data.confirmation_data;
      showConfirmModal(data.message, data.confirmation_data);
    } else {
      state.chatMessages.push({ role: "assistant", content: data.reply || "Done." });
    }
  } catch (e) {
    state.chatMessages.pop();
    state.chatMessages.push({ role: "assistant", content: `Error: ${e.message}` });
  } finally {
    state.isChatLoading = false;
    renderChat();
  }
}

function clearChat() {
  state.chatMessages = [state.chatMessages[0]];
  renderChat();
  showToast("Chat conversation cleared");
}

// Confirmation Modal
function showConfirmModal(message, data) {
  if (DOM.confirmModalMsg) DOM.confirmModalMsg.textContent = message;
  if (DOM.modalPayloadPreview) DOM.modalPayloadPreview.textContent = JSON.stringify(data, null, 2);
  DOM.confirmModal?.classList.add("open");
}

function closeConfirmModal() {
  DOM.confirmModal?.classList.remove("open");
  pendingConfirmation = null;
}

async function executePendingAction() {
  if (!pendingConfirmation) return;
  const action = pendingConfirmation;
  closeConfirmModal();

  showToast("Executing authorized action...");
  try {
    const res = await fetch("/api/action/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action),
    });
    const result = await res.json();
    state.chatMessages.push({ role: "assistant", content: `✅ **Action Executed**:\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\`` });
    renderChat();
    fetchStats();
    fetchProducts();
  } catch (e) {
    showToast(`Execution failed: ${e.message}`, "error");
  }
}

// =============================================================================
// TOAST & UTILITIES
// =============================================================================
function showToast(msg, type = "info") {
  if (!DOM.toastContainer) return;
  const t = document.createElement("div");
  t.className = `toast-msg toast-${type}`;
  t.textContent = msg;
  DOM.toastContainer.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

function escapeHTML(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function handleLogout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.replace("/login");
  } catch (e) {
    window.location.replace("/login");
  }
}
