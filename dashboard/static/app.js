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

/** Inline SVG icons — matches Lucide-style strokes used across the dashboard */
const COPILOT_ICONS = {
  chart: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="12" width="4" height="9"/><rect x="10" y="7" width="4" height="14"/><rect x="17" y="3" width="4" height="18"/></svg>',
  star: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
  package: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M16.5 9.4l-9-5.19M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
  dollar: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
  link: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
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
  
  // Playground — fal.ai model evaluation
  playground: {
    activeTask: "outpaint",
    viewMode: "grid",
    presets: [],
    falCatalog: {},
    falConfigured: false,
    modelSelection: {},
    modelSearch: "",
    catalogLoaded: false,
    catalogLoading: false,
    sourceMode: "catalog",
    catalogSearch: "",
    catalogProducts: [],
    catalogLoading: false,
    catalogFilter: "linked",
    selectedProduct: null,
    selectedAsset: null,
    assetTypeFilter: "all",
    imageUrl: "",
    localFileName: "",
    outpaintPercent: "15%",
    aspectRatio: "16:10",
    prompt: "",
    isRunning: false,
    lastResults: null,
    splitCompareModelId: null,
  },
  chatMessages: [
    {
      role: "assistant",
      content: `### Welcome to Binnen Catalog Intelligence

I am your **Autonomous AI Copilot** — connected live to **Baserow** and **Shopify Woonbloq**.

Pick a quick action below or ask anything about your catalog:

<div class="welcome-prompts-grid">
  <button type="button" class="welcome-prompt-btn" onclick="usePrompt('How many total products are in our Shopify store and what is the breakdown by active, draft, and archived?')">
    <span class="wpb-icon">${COPILOT_ICONS.chart}</span>
    <span class="wpb-text"><strong>Shopify Products &amp; Status</strong><em>Total counts &amp; publication state</em></span>
  </button>
  <button type="button" class="welcome-prompt-btn" onclick="usePrompt('Filter Shopify products by vendor Spectrum Design and check stock status')">
    <span class="wpb-icon">${COPILOT_ICONS.star}</span>
    <span class="wpb-text"><strong>Spectrum Design Audit</strong><em>Live items, pricing &amp; inventory</em></span>
  </button>
  <button type="button" class="welcome-prompt-btn" onclick="usePrompt('How many products on Shopify have 0 stock or no inventory value entered?')">
    <span class="wpb-icon">${COPILOT_ICONS.package}</span>
    <span class="wpb-text"><strong>Zero Stock Audit</strong><em>Inventory health check</em></span>
  </button>
  <button type="button" class="welcome-prompt-btn" onclick="usePrompt('How many products in Baserow have empty price field?')">
    <span class="wpb-icon">${COPILOT_ICONS.dollar}</span>
    <span class="wpb-text"><strong>Missing Prices</strong><em>Baserow pricing gaps</em></span>
  </button>
  <button type="button" class="welcome-prompt-btn" onclick="usePrompt('Show me the overall sync coverage between Baserow and Shopify')">
    <span class="wpb-icon">${COPILOT_ICONS.link}</span>
    <span class="wpb-text"><strong>Sync Coverage</strong><em>Baserow ↔ Shopify link health</em></span>
  </button>
</div>`,
    },
  ],
  isChatLoading: false,
};

// fal.ai model catalog loaded from /api/playground/catalog

// DOM References
const DOM = {
  // Sidebar & Navigation
  appSidebar: document.getElementById("appSidebar"),
  sidebarToggleBtn: document.getElementById("sidebarToggleBtn"),
  headerBreadcrumbTitle: document.getElementById("headerBreadcrumbTitle"),
  sidebarUrlBadge: document.getElementById("sidebarUrlBadge"),
  sidebarUrlText: document.getElementById("sidebarUrlText"),
  navCatalog: document.getElementById("navCatalog"),
  navPlayground: document.getElementById("navPlayground"),
  sideCatalogCount: document.getElementById("sideCatalogCount"),
  viewCatalog: document.getElementById("viewCatalog"),
  viewPlayground: document.getElementById("viewPlayground"),

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

  // Modal & Toast
  pgPresetSelect: document.getElementById("pgPresetSelect"),
  pgImageInputSection: document.getElementById("pgImageInputSection"),
  pgTextInputSection: document.getElementById("pgTextInputSection"),
  pgImageDropZone: document.getElementById("pgImageDropZone"),
  pgFileInput: document.getElementById("pgFileInput"),
  pgInputImageThumb: document.getElementById("pgInputImageThumb"),
  pgImageDimTag: document.getElementById("pgImageDimTag"),
  pgImageUrlInput: document.getElementById("pgImageUrlInput"),
  pgOutpaintControls: document.getElementById("pgOutpaintControls"),
  pgOutpaintPercent: document.getElementById("pgOutpaintPercent"),
  pgAspectRatio: document.getElementById("pgAspectRatio"),
  pgRembgControls: document.getElementById("pgRembgControls"),
  pgRembgMode: document.getElementById("pgRembgMode"),
  pgRembgFeather: document.getElementById("pgRembgFeather"),
  pgPromptControl: document.getElementById("pgPromptControl"),
  pgPromptInput: document.getElementById("pgPromptInput"),
  pgTextTitleInput: document.getElementById("pgTextTitleInput"),
  pgTextBrandInput: document.getElementById("pgTextBrandInput"),
  pgTextRawDesc: document.getElementById("pgTextRawDesc"),
  pgTempSlider: document.getElementById("pgTempSlider"),
  pgSelectedCountBadge: document.getElementById("pgSelectedCountBadge"),
  pgModelsChecklist: document.getElementById("pgModelsChecklist"),
  btnRunEval: document.getElementById("btnRunEval"),
  evalBtnLabel: document.getElementById("evalBtnLabel"),
  pgEmptyState: document.getElementById("pgEmptyState"),
  pgComparisonGrid: document.getElementById("pgComparisonGrid"),
  pgLeaderboardCard: document.getElementById("pgLeaderboardCard"),
  pgLeaderboardBody: document.getElementById("pgLeaderboardBody"),
  pgEvalStatusText: document.getElementById("pgEvalStatusText"),
  
  // Comparison & Split Slider
  btnModeGrid: document.getElementById("btnModeGrid"),
  btnModeSplit: document.getElementById("btnModeSplit"),
  pgSplitContainer: document.getElementById("pgSplitContainer"),
  pgSplitModelSelect: document.getElementById("pgSplitModelSelect"),
  beforeAfterWrap: document.getElementById("beforeAfterWrap"),
  splitImgBefore: document.getElementById("splitImgBefore"),
  splitImgAfter: document.getElementById("splitImgAfter"),
  baOverlay: document.getElementById("baOverlay"),
  baDivider: document.getElementById("baDivider"),

  // Lightbox Modal
  imageLightboxModal: document.getElementById("imageLightboxModal"),
  lightboxImg: document.getElementById("lightboxImg"),
  lightboxTitle: document.getElementById("lightboxTitle"),
  lightboxMeta: document.getElementById("lightboxMeta"),
  lightboxDownloadBtn: document.getElementById("lightboxDownloadBtn"),

  // Modal & Toast
  confirmModal: document.getElementById("confirmModal"),
  confirmModalMsg: document.getElementById("confirmModalMsg"),
  modalPayloadPreview: document.getElementById("modalPayloadPreview"),
  modalConfirmBtn: document.getElementById("modalConfirmBtn"),
  modalCancelBtn: document.getElementById("modalCancelBtn"),
  toastContainer: document.getElementById("toastContainer"),

  // AI Copilot floating widget
  aiFabBtn: document.getElementById("aiFabBtn"),
  aiChatWidget: document.getElementById("aiChatWidget"),
  closeChatWidgetBtn: document.getElementById("closeChatWidgetBtn"),
  clearChatBtn: document.getElementById("clearChatBtn"),
  chatMessages: document.getElementById("chatMessages"),
  chatInput: document.getElementById("chatInput"),
  sendBtn: document.getElementById("sendBtn"),
};

let pendingConfirmation = null;

// =============================================================================
// INITIALIZATION
// =============================================================================
document.addEventListener("DOMContentLoaded", async () => {
  if (typeof marked !== "undefined") {
    marked.setOptions({ breaks: true, gfm: true });
  }
  setupNavigationEvents();
  setupEventListeners();
  setupDragAndDrop();
  setupBeforeAfterSplitSlider();
  renderChat();

  // Initialize Dynamic URL Router
  initRouter();

  await Promise.all([fetchStats(), fetchBrands(), loadUserProfile()]);
  await fetchProducts();
  updatePlaygroundPreviewUI();
  // Load models when playground is opened (also prefetched here if already on that view)
  if (state.activeView === "playground") {
    await ensurePlaygroundCatalog();
  }
});

// Task help text removed — eval lab uses model descriptions from catalog

async function ensurePlaygroundCatalog(force = false) {
  if (state.playground.catalogLoading && !force) return;
  if (!force && state.playground.catalogLoaded && Object.keys(state.playground.falCatalog).length) {
    renderPlaygroundModelsChecklist();
    return;
  }
  await fetchPlaygroundCatalog(force);
}

async function fetchPlaygroundCatalog(force = false) {
  state.playground.catalogLoading = true;
  renderPlaygroundModelsChecklist();
  try {
    const url = force ? "/api/playground/catalog?refresh=1" : "/api/playground/catalog";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const tasks = data.tasks || data.catalog || null;
    if (data.ok && tasks && typeof tasks === "object") {
      state.playground.falCatalog = tasks;
      state.playground.falConfigured = !!data.fal_configured;
      state.playground.catalogLoaded = true;
      state.playground.catalogSource = data.source || "unknown";
      const statusEl = document.getElementById("falKeyStatus");
      if (statusEl) {
        const srcLabel = data.source === "fal_api" ? "live catalog" : "fallback";
        statusEl.textContent = data.fal_configured
          ? `fal.ai connected · ${srcLabel}`
          : "FAL_KEY missing in .env";
        statusEl.className = "fal-key-status " + (data.fal_configured ? "ok" : "warn");
      }
      for (const [task, models] of Object.entries(tasks)) {
        if (!Array.isArray(models)) continue;
        if (!state.playground.modelSelection[task]) {
          state.playground.modelSelection[task] = {};
        }
        models.forEach((m) => {
          if (state.playground.modelSelection[task][m.id] === undefined) {
            state.playground.modelSelection[task][m.id] = !!m.default_selected;
          }
        });
      }
      renderPlaygroundModelsChecklist();
    } else {
      console.warn("Playground catalog response missing tasks:", data);
      renderPlaygroundModelsChecklist(true);
    }
  } catch (e) {
    console.error("Failed to load fal catalog:", e);
    renderPlaygroundModelsChecklist(true);
  } finally {
    state.playground.catalogLoading = false;
  }
}

const TASK_LABELS = {
  outpaint: "Outpaint / Extend",
  rembg: "Remove Background",
  detail: "Detail / Macro",
};

function onModelSearchInput() {
  const el = document.getElementById("pgModelSearch");
  state.playground.modelSearch = el ? el.value.trim().toLowerCase() : "";
  renderPlaygroundModelsChecklist();
}

function getSelectionMode() {
  const count = getSelectedModelsForTask(state.playground.activeTask).length;
  return count <= 1 ? "single" : "compare";
}

function updateSelectionModeHint() {
  const el = document.getElementById("pgSelectionModeHint");
  if (!el) return;
  const count = getSelectedModelsForTask(state.playground.activeTask).length;
  if (count === 0) el.textContent = "Select 1 model to run, or 2+ to compare side-by-side";
  else if (count === 1) el.textContent = "Single model mode — add another to compare";
  else el.textContent = `Compare mode — ${count} models selected`;
}

function getFilteredModelsForTask(task) {
  const models = state.playground.falCatalog[task] || [];
  const q = (state.playground.modelSearch || "").toLowerCase();
  if (!q) return models;
  return models.filter((m) => {
    const aliases = Array.isArray(m.search_aliases) ? m.search_aliases.join(" ") : "";
    const hay = `${m.name} ${m.description || ""} ${m.badge || ""} ${m.endpoint || ""} ${m.id} ${aliases}`.toLowerCase();
    return hay.includes(q);
  });
}

function getSelectedModelsForTask(task) {
  const sel = state.playground.modelSelection[task] || {};
  return Object.entries(sel).filter(([, v]) => v).map(([id]) => id);
}

function selectAllModels(select) {
  const task = state.playground.activeTask;
  const models = getFilteredModelsForTask(task);
  if (!state.playground.modelSelection[task]) state.playground.modelSelection[task] = {};
  models.forEach((m) => {
    state.playground.modelSelection[task][m.id] = select;
  });
  renderPlaygroundModelsChecklist();
}

function selectDefaultModels() {
  const task = state.playground.activeTask;
  const models = state.playground.falCatalog[task] || [];
  if (!state.playground.modelSelection[task]) state.playground.modelSelection[task] = {};
  models.forEach((m) => {
    state.playground.modelSelection[task][m.id] = !!m.default_selected;
  });
  renderPlaygroundModelsChecklist();
}

async function loadUserProfile() {
  try {
    const res = await fetch("/api/auth/status");
    const data = await res.json();
    if (data.authenticated && data.user) {
      const emailEl = document.getElementById("sidebarUserEmail");
      const nameEl = document.getElementById("sidebarUserName");
      if (emailEl) emailEl.textContent = data.user;
      if (nameEl) nameEl.textContent = data.user.split("@")[0] || "Admin";
    }
  } catch (e) { /* ignore */ }
}

// =============================================================================
// BIDIRECTIONAL URL ROUTER & DEEP-LINKING
// =============================================================================
function parseCurrentUrl() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const path = window.location.pathname.replace(/^\//, "");

  let rawRoute = hash || path || "catalog";
  if (rawRoute.startsWith("products")) rawRoute = rawRoute.replace("products", "catalog");

  // Parse view & query string
  let view = "catalog";
  let params = {};

  if (rawRoute.includes("?")) {
    const [viewPart, queryPart] = rawRoute.split("?");
    view = viewPart.trim() || "catalog";
    try {
      params = Object.fromEntries(new URLSearchParams(queryPart));
    } catch (e) {
      params = {};
    }
  } else {
    view = rawRoute.trim() || "catalog";
  }

  // Normalize view name
  if (["catalog", "products", "playground"].includes(view)) {
    return { view: view === "products" ? "catalog" : view, params };
  }
  return { view: "catalog", params: {} };
}

function initRouter() {
  const parsed = parseCurrentUrl();
  switchView(parsed.view, parsed.params, false);

  window.addEventListener("popstate", () => {
    const p = parseCurrentUrl();
    switchView(p.view, p.params, false);
  });

  window.addEventListener("hashchange", () => {
    const p = parseCurrentUrl();
    switchView(p.view, p.params, false);
  });

  // Sidebar URL badge click copies URL
  if (DOM.sidebarUrlBadge) {
    DOM.sidebarUrlBadge.style.cursor = "pointer";
    DOM.sidebarUrlBadge.addEventListener("click", () => {
      navigator.clipboard.writeText(window.location.href).then(() => {
        showToast("Direct page URL copied to clipboard!");
      }).catch(() => {
        showToast(window.location.href);
      });
    });
  }
}

function updateUrlBar(viewName, params = {}) {
  const queryStr = Object.keys(params).length ? "?" + new URLSearchParams(params).toString() : "";
  const targetHash = `#${viewName}${queryStr}`;

  if (window.location.hash !== targetHash) {
    history.pushState({ view: viewName, params }, "", targetHash);
  }

  // Update Sidebar Badge
  if (DOM.sidebarUrlText) {
    const host = window.location.host || "localhost:8007";
    DOM.sidebarUrlText.textContent = `${host}/#${viewName}${queryStr ? queryStr : ''}`;
  }
}

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
      if (view) switchView(view);
    });
  });
}

function switchView(viewName, params = {}, updateHistory = true) {
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
  const views = [DOM.viewCatalog, DOM.viewPlayground];
  views.forEach((v) => {
    if (v) {
      v.style.display = "none";
      v.classList.remove("active");
    }
  });

  // Breadcrumb title map
  const titleMap = {
    catalog: "Catalog Explorer",
    playground: "fal.ai Model Eval",
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
    ensurePlaygroundCatalog();
    if (params.task) switchPlaygroundTask(params.task, false);
    else renderPlaygroundModelsChecklist();
    if (params.product) {
      openPlaygroundWithProduct(
        parseInt(params.product, 10),
        params.asset || null,
        params.task || null,
        false,
        params.image || null
      );
    } else if (!state.playground.catalogProducts.length) {
      searchPlaygroundProducts();
    }
  }

  // Update Browser URL Bar
  if (updateHistory) {
    let urlParams = { ...params };
    if (viewName === "playground" && state.playground.activeTask) {
      urlParams.task = state.playground.activeTask;
    }
    updateUrlBar(viewName, urlParams);
  } else {
    // Just sync badge text
    if (DOM.sidebarUrlText) {
      const host = window.location.host || "localhost:8007";
      const queryStr = Object.keys(params).length ? "?" + new URLSearchParams(params).toString() : "";
      DOM.sidebarUrlText.textContent = `${host}/#${viewName}${queryStr}`;
    }
  }
}

// =============================================================================
// PLAYGROUND — SHOPIFY CATALOG ASSET BROWSER
// =============================================================================
let catalogSearchTimer = null;

const ASSET_TYPE_LABELS = {
  all: "All",
  hero: "Hero",
  product: "Product",
  lifestyle: "Lifestyle",
  detail: "Detail",
  shopify: "Shopify",
};

function switchAssetSource(mode) {
  state.playground.sourceMode = mode;
  document.querySelectorAll(".fal-source-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-source") === mode);
  });
  const catalogEl = document.getElementById("pgCatalogSource");
  const uploadEl = document.getElementById("pgUploadSource");
  if (catalogEl) catalogEl.style.display = mode === "catalog" ? "block" : "none";
  if (uploadEl) uploadEl.style.display = mode === "upload" ? "block" : "none";
  if (mode === "catalog" && !state.playground.catalogProducts.length) {
    searchPlaygroundProducts();
  }
}

function setCatalogFilter(filterType) {
  state.playground.catalogFilter = filterType;
  document.querySelectorAll(".fal-catalog-filter-pill").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-filter") === filterType);
  });
  clearPlaygroundProduct();
  searchPlaygroundProducts();
}

function debounceCatalogSearch() {
  clearTimeout(catalogSearchTimer);
  catalogSearchTimer = setTimeout(() => {
    clearPlaygroundProduct();
    searchPlaygroundProducts();
  }, 320);
}

async function searchPlaygroundProducts() {
  const searchEl = document.getElementById("pgCatalogSearch");
  const resultsEl = document.getElementById("pgCatalogResults");
  const search = searchEl ? searchEl.value.trim() : "";
  const filterType = state.playground.catalogFilter || "linked";

  state.playground.catalogSearch = search;
  state.playground.catalogLoading = true;

  if (resultsEl) {
    resultsEl.innerHTML = `<div class="fal-catalog-loading"><span class="pulse-indicator-static"></span> Loading catalog...</div>`;
  }

  try {
    const params = new URLSearchParams({ search, filter_type: filterType, page: "1", size: "24" });
    const res = await fetch(`/api/playground/products?${params.toString()}`);
    const data = await res.json();
    state.playground.catalogProducts = data.results || [];
    renderCatalogResults(state.playground.catalogProducts);
  } catch (e) {
    if (resultsEl) {
      resultsEl.innerHTML = `<div class="fal-catalog-empty fal-catalog-error">Failed to load products: ${escapeHTML(e.message)}</div>`;
    }
  } finally {
    state.playground.catalogLoading = false;
  }
}

function renderCatalogResults(products) {
  const resultsEl = document.getElementById("pgCatalogResults");
  if (!resultsEl) return;

  if (!products.length) {
    resultsEl.innerHTML = `<div class="fal-catalog-empty">No products found. Try a different search or filter.</div>`;
    return;
  }

  resultsEl.innerHTML = products.map((p) => {
    const thumb = p.thumb_url || "";
    const shopBadge = p.shopify_id
      ? `<span class="fal-catalog-shop-badge">#${escapeHTML(String(p.shopify_id))}</span>`
      : `<span class="fal-catalog-shop-badge muted">Unlinked</span>`;
    return `
      <button type="button" class="fal-catalog-row" onclick="selectPlaygroundProduct(${p.row_id})">
        <div class="fal-catalog-thumb-wrap">
          ${thumb
            ? `<img src="${thumb}" alt="" loading="lazy" />`
            : `<div class="fal-catalog-thumb-empty">No img</div>`}
        </div>
        <div class="fal-catalog-row-meta">
          <span class="fal-catalog-row-title">${escapeHTML(p.title)}</span>
          <span class="fal-catalog-row-sub">${escapeHTML(p.brand)} · ${p.image_count || 0} images</span>
        </div>
        ${shopBadge}
      </button>
    `;
  }).join("");
}

async function selectPlaygroundProduct(rowId) {
  const pickerEl = document.getElementById("pgAssetPicker");
  const resultsEl = document.getElementById("pgCatalogResults");
  if (pickerEl) {
    pickerEl.style.display = "block";
    document.getElementById("pgAssetGrid").innerHTML = `<div class="fal-catalog-loading">Loading assets...</div>`;
  }
  if (resultsEl) resultsEl.style.display = "none";

  try {
    const res = await fetch(`/api/playground/products/${rowId}/assets?include_shopify=true`);
    const data = await res.json();
    if (!data.ok) throw new Error("Product not found");

    state.playground.selectedProduct = data;
    state.playground.assetTypeFilter = "all";
    renderAssetPicker(data);

    const first = (data.assets || [])[0];
    if (first) selectPlaygroundAsset(first.id, false);
    else showToast("This product has no images yet", "warning");
  } catch (e) {
    showToast(`Failed to load product assets: ${e.message}`, "error");
    clearPlaygroundProduct();
  }
}

function renderAssetPicker(product) {
  const filtersEl = document.getElementById("pgAssetTypeFilters");
  const gridEl = document.getElementById("pgAssetGrid");
  if (!filtersEl || !gridEl) return;

  const assets = product.assets || [];
  const types = ["all", ...new Set(assets.map((a) => a.type))];
  const activeType = state.playground.assetTypeFilter || "all";

  filtersEl.innerHTML = types.map((t) => `
    <button type="button" class="fal-asset-type-pill ${t === activeType ? "active" : ""}" onclick="filterPlaygroundAssets('${t}')">
      ${ASSET_TYPE_LABELS[t] || t}
    </button>
  `).join("");

  const filtered = activeType === "all" ? assets : assets.filter((a) => a.type === activeType);
  if (!assets.length) {
    gridEl.innerHTML = `<div class="fal-catalog-empty">No images found for this product. Try another product or upload an image.</div>`;
    return;
  }
  if (!filtered.length) {
    gridEl.innerHTML = `<div class="fal-catalog-empty">No images in this category. Try "All" filter above.</div>`;
    return;
  }

  const selectedId = state.playground.selectedAsset?.id;
  gridEl.innerHTML = filtered.map((asset) => `
    <button type="button" class="fal-asset-tile ${asset.id === selectedId ? "active" : ""}" onclick="selectPlaygroundAsset('${asset.id}')">
      <img src="${asset.url}" alt="${escapeHTML(asset.label)}" loading="lazy" />
      <div class="fal-asset-tile-meta">
        <span class="fal-asset-type-tag ${asset.source}">${escapeHTML(asset.type)}</span>
        <span class="fal-asset-label">${escapeHTML(asset.label)}</span>
      </div>
    </button>
  `).join("");

  updatePlaygroundProductContext();
}

function filterPlaygroundAssets(type) {
  state.playground.assetTypeFilter = type;
  if (state.playground.selectedProduct) renderAssetPicker(state.playground.selectedProduct);
}

function selectPlaygroundAsset(assetId, updateUrl = true) {
  const product = state.playground.selectedProduct;
  if (!product) return;
  const asset = (product.assets || []).find((a) => a.id === assetId);
  if (!asset) return;

  state.playground.selectedAsset = asset;
  setPlaygroundImage(asset.url, {
    source: "catalog",
    product,
    asset,
    updateUrl,
  });
  renderAssetPicker(product);
}

function clearPlaygroundProduct() {
  state.playground.selectedProduct = null;
  state.playground.selectedAsset = null;
  const pickerEl = document.getElementById("pgAssetPicker");
  const resultsEl = document.getElementById("pgCatalogResults");
  if (pickerEl) pickerEl.style.display = "none";
  if (resultsEl) resultsEl.style.display = "block";
  updatePlaygroundProductContext();
}

function updatePlaygroundProductContext() {
  const ctx = document.getElementById("pgProductContext");
  const badge = document.getElementById("pgSourceBadge");
  const product = state.playground.selectedProduct;
  const asset = state.playground.selectedAsset;

  if (product && ctx) {
    ctx.style.display = "block";
    const titleEl = document.getElementById("pgContextTitle");
    const metaEl = document.getElementById("pgContextMeta");
    const assetLabel = document.getElementById("pgActiveAssetLabel");
    const shopLink = document.getElementById("pgContextShopifyLink");

    if (titleEl) titleEl.textContent = product.title || "Product";
    if (metaEl) {
      metaEl.textContent = `${product.brand || "—"} · Row #${product.row_id}${product.shopify_id ? ` · Shopify #${product.shopify_id}` : ""}`;
    }
    if (assetLabel) {
      assetLabel.textContent = asset
        ? `${asset.type} · ${asset.label} (${asset.source})`
        : "Select an image below";
    }
    if (shopLink) {
      if (product.shopify_admin_url) {
        shopLink.href = product.shopify_admin_url;
        shopLink.style.display = "inline-flex";
      } else {
        shopLink.style.display = "none";
      }
    }
  } else if (ctx) {
    ctx.style.display = "none";
  }

  if (badge) {
    if (product && asset) badge.textContent = `${product.title} — ${asset.type}`;
    else if (state.playground.localFileName) badge.textContent = `Upload: ${state.playground.localFileName}`;
    else if (state.playground.imageUrl) badge.textContent = "Custom upload";
    else badge.textContent = "Select a product image";
  }
}

function updatePlaygroundPreviewUI() {
  const thumb = DOM.pgInputImageThumb;
  const empty = document.getElementById("pgPreviewEmpty");
  const hasImage = !!(state.playground.imageUrl && thumb);

  if (thumb) {
    if (hasImage) {
      thumb.src = state.playground.imageUrl;
      thumb.style.display = "block";
      thumb.onload = () => updateImageDimensionsTag(thumb);
    } else {
      thumb.removeAttribute("src");
      thumb.style.display = "none";
    }
  }
  if (empty) empty.style.display = hasImage ? "none" : "flex";
  updatePlaygroundProductContext();
}

function openPlaygroundWithProduct(rowId, assetId = null, task = null, navigate = true, imageUrl = null) {
  if (navigate) {
    const params = { product: rowId, task: task || state.playground.activeTask };
    if (assetId) params.asset = assetId;
    if (imageUrl) params.image = imageUrl;
    switchView("playground", params);
    return;
  }
  switchAssetSource("catalog");
  if (task) switchPlaygroundTask(task, false);
  selectPlaygroundProduct(rowId).then(() => {
    if (assetId) {
      selectPlaygroundAsset(assetId, true);
    } else if (imageUrl) {
      const asset = (state.playground.selectedProduct?.assets || []).find((a) => a.url === imageUrl);
      if (asset) selectPlaygroundAsset(asset.id, true);
    }
  });
}

function openPlaygroundWithImage(rowId, imageUrl, task = null) {
  openPlaygroundWithProduct(rowId, null, task, true, imageUrl);
}

function openDrawerProductInEval() {
  const p = state.selectedProduct;
  if (!p) return;
  const images = getDrawerProductImages(p);
  if (!images.length) {
    showToast("This product has no images to edit", "warning");
    return;
  }
  DOM.productDrawer?.classList.remove("open");
  DOM.drawerBackdrop?.classList.remove("open");
  openPlaygroundWithProduct(p.id, null, state.playground.activeTask, true);
}

function getDrawerProductImages(p) {
  const all = [
    ..._normalizeImageList(p.hero_images, "hero"),
    ..._normalizeImageList(p.product_images, "product"),
    ..._normalizeImageList(p.lifestyle_images, "lifestyle"),
    ..._normalizeImageList(p.detail_image, "detail"),
  ];
  return all;
}

function _normalizeImageList(value, type) {
  if (!value) return [];
  const list = Array.isArray(value) ? value : [value];
  return list.filter((img) => img && img.url).map((img, idx) => ({
    url: img.url,
    label: img.name || `${type} ${idx + 1}`,
    type,
  }));
}

// =============================================================================
// AI MODEL EVALUATION STUDIO CONTROLLER
// =============================================================================

function getPlaygroundImageUrl() {
  return (state.playground.imageUrl || "").trim();
}

function setPlaygroundImage(url, meta = {}) {
  state.playground.imageUrl = url;
  if (meta.fileName) state.playground.localFileName = meta.fileName;
  if (meta.source === "catalog" && meta.product) {
    state.playground.selectedProduct = meta.product;
    state.playground.selectedAsset = meta.asset || null;
    state.playground.localFileName = "";
    if (meta.product.prompt_detail && DOM.pgPromptInput && state.playground.activeTask === "detail") {
      DOM.pgPromptInput.value = meta.product.prompt_detail;
    }
  } else if (meta.source === "upload") {
    state.playground.selectedProduct = null;
    state.playground.selectedAsset = null;
  }

  if (DOM.pgImageUrlInput) {
    if (url.startsWith("http://") || url.startsWith("https://")) {
      DOM.pgImageUrlInput.value = url;
      DOM.pgImageUrlInput.placeholder = "Paste image URL...";
    } else if (url.startsWith("data:")) {
      DOM.pgImageUrlInput.value = "";
      DOM.pgImageUrlInput.placeholder = meta.fileName ? `${meta.fileName} (local)` : "Local file loaded";
    } else {
      DOM.pgImageUrlInput.value = "";
    }
  }
  updatePlaygroundPreviewUI();

  if (meta.updateUrl !== false && state.activeView === "playground" && state.playground.selectedProduct) {
    const params = { task: state.playground.activeTask, product: state.playground.selectedProduct.row_id };
    if (state.playground.selectedAsset?.id) params.asset = state.playground.selectedAsset.id;
    updateUrlBar("playground", params);
  }

  if (meta.toast !== false) showToast("Image loaded into eval studio");
}

function applyImageUrl() {
  const url = DOM.pgImageUrlInput ? DOM.pgImageUrlInput.value.trim() : "";
  if (!url || url.startsWith("[")) {
    showToast("Please enter a valid image URL", "warning");
    return;
  }
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    showToast("Please enter a valid http(s) image URL", "warning");
    return;
  }
  switchAssetSource("upload");
  setPlaygroundImage(url, { source: "upload" });
}

async function uploadLocalPlaygroundImage(dataUrl, fileName) {
  try {
    const res = await fetch("/api/playground/upload-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_data: dataUrl }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.detail || data.message || "Upload failed");
    setPlaygroundImage(data.url, { source: "upload", fileName, toast: false });
    if (DOM.pgImageUrlInput) DOM.pgImageUrlInput.placeholder = fileName;
    showToast(`Uploaded ${fileName} to fal.ai`);
  } catch (err) {
    console.warn("fal CDN upload failed, will upload on eval:", err);
    setPlaygroundImage(dataUrl, { source: "upload", fileName, toast: false });
    showToast(`Loaded ${fileName} — ready to evaluate`);
  }
}

function handlePlaygroundFileUpload(event) {
  const file = event.target?.files?.[0];
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) {
    showToast("Image too large — max 15MB", "warning");
    return;
  }

  const reader = new FileReader();
  reader.onload = async (e) => {
    switchAssetSource("upload");
    const dataUrl = e.target.result;
    setPlaygroundImage(dataUrl, { source: "upload", fileName: file.name, toast: false });
    await uploadLocalPlaygroundImage(dataUrl, file.name);
  };
  reader.readAsDataURL(file);
  event.target.value = "";
}

function setupDragAndDrop() {
  const zone = DOM.pgImageDropZone;
  if (!zone) return;

  ["dragenter", "dragover"].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove("dragover");
    });
  });

  zone.addEventListener("drop", (e) => {
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (file.type.startsWith("image/")) {
        const reader = new FileReader();
        reader.onload = async (re) => {
          switchAssetSource("upload");
          const dataUrl = re.target.result;
          setPlaygroundImage(dataUrl, { source: "upload", fileName: file.name, toast: false });
          await uploadLocalPlaygroundImage(dataUrl, file.name);
        };
        reader.readAsDataURL(file);
      } else {
        showToast("Please drop an image file (PNG/JPG/WebP)", "warning");
      }
    }
  });
}

function updateImageDimensionsTag(imgEl) {
  if (!DOM.pgImageDimTag || !imgEl) return;
  const w = imgEl.naturalWidth || imgEl.width || 1200;
  const h = imgEl.naturalHeight || imgEl.height || 900;
  const gcd = (a, b) => (b === 0 ? a : gcd(b, a % b));
  const r = gcd(w, h);
  DOM.pgImageDimTag.textContent = `${w} × ${h}px (${w/r}:${h/r})`;
}

function switchPlaygroundTask(taskType, updateUrl = true) {
  state.playground.activeTask = taskType;

  document.querySelectorAll(".fal-task-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-task") === taskType);
  });

  const isPromptTask = taskType === "detail";
  if (DOM.pgOutpaintControls) DOM.pgOutpaintControls.style.display = taskType === "outpaint" ? "block" : "none";
  if (DOM.pgRembgControls) DOM.pgRembgControls.style.display = taskType === "rembg" ? "block" : "none";
  if (DOM.pgPromptControl) DOM.pgPromptControl.style.display = isPromptTask ? "block" : "none";

  const modelSearchEl = document.getElementById("pgModelSearch");
  if (modelSearchEl) modelSearchEl.value = "";
  state.playground.modelSearch = "";

  const taskLabel = document.getElementById("pgModelsTaskLabel");
  if (taskLabel) updatePlaygroundTaskLabel();

  if (DOM.pgPromptInput && isPromptTask) {
    const product = state.playground.selectedProduct;
    if (product?.prompt_detail) {
      DOM.pgPromptInput.value = product.prompt_detail;
    }
  }

  renderPlaygroundModelsChecklist();
  if (updateUrl && state.activeView === "playground") updateUrlBar("playground", { task: taskType });
}

function renderPlaygroundModelsChecklist(catalogError = false) {
  const task = state.playground.activeTask;
  const allModels = state.playground.falCatalog[task] || [];
  const models = getFilteredModelsForTask(task);
  const selection = state.playground.modelSelection[task] || {};
  const listEl = document.getElementById("pgModelsChecklist") || DOM.pgModelsChecklist;

  if (!listEl) return;

  if (state.playground.catalogLoading) {
    listEl.innerHTML = `<div class="fal-catalog-empty"><span class="pulse-indicator-static"></span> Loading models...</div>`;
    updatePlaygroundButtonLabel();
    return;
  }

  if (catalogError) {
    listEl.innerHTML = `<div class="fal-catalog-empty fal-catalog-error">Could not load models. <button type="button" class="pg-link-btn" onclick="ensurePlaygroundCatalog(true)">Retry</button></div>`;
    updatePlaygroundButtonLabel();
    return;
  }

  if (!state.playground.catalogLoaded) {
    listEl.innerHTML = `<div class="fal-catalog-empty">Loading model catalog...</div>`;
    updatePlaygroundButtonLabel();
    return;
  }

  if (!allModels.length) {
    listEl.innerHTML = `<div class="fal-catalog-empty">No models for this category.</div>`;
    updatePlaygroundButtonLabel();
    return;
  }

  if (!models.length) {
    listEl.innerHTML = `<div class="fal-catalog-empty">No models match your search.</div>`;
    updatePlaygroundButtonLabel();
    return;
  }

  listEl.innerHTML = models.map((m) => {
    const checked = !!selection[m.id];
    const costStr = m.cost_usd === 0 ? "Free" : `$${Number(m.cost_usd).toFixed(4)}`;
    const thumb = m.thumbnail_url
      ? `<img class="fal-model-thumb" src="${escapeHTML(m.thumbnail_url)}" alt="" loading="lazy" />`
      : "";
    return `
      <label class="fal-model-row ${checked ? "selected" : ""} ${m.production ? "production-model" : ""}" data-model-id="${escapeHTML(m.id)}">
        <input type="checkbox" ${checked ? "checked" : ""} data-model-id="${escapeHTML(m.id)}" />
        ${thumb}
        <div class="fal-model-info">
          <div class="fal-model-name">${escapeHTML(m.name)}</div>
          <div class="fal-model-desc">${escapeHTML(m.description || "")}</div>
          <div class="fal-model-meta">
            <span class="fal-model-badge ${m.production ? "production" : ""}">${escapeHTML(m.badge || "")}</span>
            <span class="fal-model-endpoint">${escapeHTML(m.endpoint || m.id || "")}</span>
          </div>
        </div>
        <div class="fal-model-cost">${costStr}</div>
      </label>
    `;
  }).join("");

  updatePlaygroundButtonLabel();
  updateSelectionModeHint();
  updatePlaygroundTaskLabel();
}

function updatePlaygroundTaskLabel() {
  const taskLabel = document.getElementById("pgModelsTaskLabel");
  if (!taskLabel) return;
  const task = state.playground.activeTask;
  const count = (state.playground.falCatalog[task] || []).length;
  taskLabel.textContent = `${TASK_LABELS[task] || task} · ${count} model${count === 1 ? "" : "s"}`;
}

function togglePlaygroundModelSelection(modelId, checked) {
  const task = state.playground.activeTask;
  if (!state.playground.modelSelection[task]) state.playground.modelSelection[task] = {};

  if (checked === undefined) {
    checked = !state.playground.modelSelection[task][modelId];
  }
  state.playground.modelSelection[task][modelId] = !!checked;

  renderPlaygroundModelsChecklist();
}

function updatePlaygroundButtonLabel() {
  const task = state.playground.activeTask;
  const count = getSelectedModelsForTask(task).length;
  if (DOM.pgSelectedCountBadge) DOM.pgSelectedCountBadge.textContent = `${count} selected`;
  if (DOM.evalBtnLabel) {
    if (count === 0) DOM.evalBtnLabel.textContent = "Run Evaluation";
    else if (count === 1) DOM.evalBtnLabel.textContent = "Run Single Model";
    else DOM.evalBtnLabel.textContent = `Compare ${count} Models`;
  }
  const runBtn = document.getElementById("btnRunEval");
  if (runBtn) runBtn.classList.toggle("compare-mode", count > 1);
}

function suggestDetailPrompt() {
  const title = DOM.pgTextTitleInput ? DOM.pgTextTitleInput.value : "Dutch designer furniture";
  const brand = DOM.pgTextBrandInput ? DOM.pgTextBrandInput.value : "Spectrum";
  const suggestions = [
    `Extreme macro close-up of solid oak wood joinery and matte lacquer finish, ${title} by ${brand}, studio lighting, 8k`,
    `Close-up detail of leather upholstery stitching and ergonomic foam contours, professional product photography`,
    `Macro shot of brushed metal hardware and material texture, craftsmanship detail, architectural digest style`
  ];
  const chosen = suggestions[Math.floor(Math.random() * suggestions.length)];
  if (DOM.pgPromptInput) {
    DOM.pgPromptInput.value = chosen;
  }
  showToast("Generated detail shot prompt!");
}

// =============================================================================
// PLAYGROUND BENCHMARK EXECUTION & RESULTS RENDERING
// =============================================================================
async function executePlaygroundEval() {
  const task = state.playground.activeTask;
  const selectedModels = getSelectedModelsForTask(task);

  if (selectedModels.length === 0) {
    showToast("Select at least one model to evaluate", "warning");
    return;
  }
  if (!getPlaygroundImageUrl()) {
    showToast("Select a product image or upload one first", "warning");
    return;
  }

  state.playground.isRunning = true;
  if (DOM.btnRunEval) DOM.btnRunEval.disabled = true;
  if (DOM.evalBtnLabel) DOM.evalBtnLabel.textContent = `Evaluating ${selectedModels.length} models...`;
  if (DOM.pgEvalStatusText) DOM.pgEvalStatusText.textContent = `Running ${selectedModels.length} fal.ai models in parallel...`;

  const progressWrap = document.getElementById("pgProgressWrap");
  const progressFill = document.getElementById("pgProgressFill");
  const progressLabel = document.getElementById("pgProgressLabel");
  if (progressWrap) progressWrap.style.display = "block";
  if (progressFill) progressFill.style.width = "8%";
  if (progressLabel) progressLabel.textContent = `Evaluating ${selectedModels.length} models...`;

  let progress = 8;
  const progressTimer = setInterval(() => {
    progress = Math.min(progress + 6, 88);
    if (progressFill) progressFill.style.width = `${progress}%`;
  }, 800);

  try {
    const payload = {
      task_type: task,
      image_url: getPlaygroundImageUrl(),
      models: selectedModels,
      prompt: DOM.pgPromptInput ? DOM.pgPromptInput.value.trim() : "",
      outpaint_percent: DOM.pgOutpaintPercent ? DOM.pgOutpaintPercent.value : "15%",
      aspect_ratio: DOM.pgAspectRatio ? DOM.pgAspectRatio.value : "16:10",
    };

    const res = await fetch("/api/playground/eval/image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    clearInterval(progressTimer);
    if (progressFill) progressFill.style.width = "100%";

    if (data.ok && data.results) {
      state.playground.lastResults = data.results;
      renderPlaygroundResults(data.results, task);
      const mode = getSelectionMode();
      showToast(
        mode === "single"
          ? "Single model evaluation complete"
          : `Compare complete — ${data.results.length} outputs`,
        "success"
      );
    } else {
      showToast(data.detail || "Evaluation failed", "error");
    }
  } catch (err) {
    clearInterval(progressTimer);
    showToast(`Error: ${err.message}`, "error");
  } finally {
    state.playground.isRunning = false;
    if (DOM.btnRunEval) DOM.btnRunEval.disabled = false;
    updatePlaygroundButtonLabel();
    if (DOM.pgEvalStatusText) DOM.pgEvalStatusText.textContent = "Evaluation complete";
    setTimeout(() => {
      if (progressWrap) progressWrap.style.display = "none";
      if (progressFill) progressFill.style.width = "0%";
    }, 1200);
  }
}

function renderPlaygroundResults(results, task) {
  if (DOM.pgEmptyState) DOM.pgEmptyState.style.display = "none";

  // Preserve run order (same as user selection order)
  const selectedOrder = getSelectedModelsForTask(task);
  const orderMap = new Map(selectedOrder.map((id, i) => [id, i]));
  const orderedResults = [...results].sort((a, b) => {
    const aid = a.model_id || a.method_id;
    const bid = b.model_id || b.method_id;
    return (orderMap.get(aid) ?? 999) - (orderMap.get(bid) ?? 999);
  });

  if (DOM.pgLeaderboardCard) DOM.pgLeaderboardCard.style.display = orderedResults.length > 1 ? "block" : "none";

  const viewToggle = document.getElementById("pgResultsViewToggle");
  if (viewToggle) viewToggle.style.display = orderedResults.length > 1 ? "flex" : "none";

  if (DOM.pgComparisonGrid) {
    DOM.pgComparisonGrid.classList.toggle("single-result", orderedResults.length === 1);
  }

  const sorted = [...orderedResults].sort((a, b) => (b.score || 0) - (a.score || 0) || (a.cost_usd || 0) - (b.cost_usd || 0));
  const winnerId = sorted[0]?.model_id || sorted[0]?.method_id;

  if (DOM.pgEvalStatusText) {
    DOM.pgEvalStatusText.textContent = `${orderedResults.length} model output${orderedResults.length === 1 ? "" : "s"} — each labeled below`;
  }

  // Populate Split Compare Dropdown
  if (DOM.pgSplitModelSelect) {
    DOM.pgSplitModelSelect.innerHTML = orderedResults.map((r) => {
      const mid = r.model_id || r.method_id;
      const mname = r.model_label || r.method_name || mid;
      return `<option value="${mid}">${mname}</option>`;
    }).join("");
    state.playground.splitCompareModelId = winnerId;
    updateSplitSliderModel(winnerId);
  }

  // Render Result Cards in Grid
  if (DOM.pgComparisonGrid) {
    DOM.pgComparisonGrid.style.display = "grid";
    DOM.pgComparisonGrid.innerHTML = orderedResults.map((r, idx) => {
      const modelId = r.model_id || r.method_id;
      const modelName = r.model_label || r.method_name || modelId;
      const isWinner = modelId === winnerId;
      const latencyStr = r.latency_sec ? `${r.latency_sec}s` : "—";
      const costStr = r.cost_usd !== undefined ? `$${Number(r.cost_usd).toFixed(4)}` : "—";
      const scoreStr = r.score ? `${r.score}%` : "—";
      const outputUrl = r.output_url || state.playground.imageUrl;
      const endpoint = r.endpoint || "";

      let outputHTML = `<img class="pg-res-image-preview" src="${outputUrl}" alt="${modelName}" onclick="openImageLightbox('${outputUrl}', '${modelName}', '${r.output_dimensions || ''}')" title="Click to zoom" />`;
      if (!r.ok) {
        outputHTML = `<div class="pg-res-error">${escapeHTML(r.error || r.status_note || "Failed")}</div>`;
      }

      return `
        <div class="pg-result-card ${isWinner ? 'highlight-winner' : ''}" data-model-id="${modelId}">
          <div class="pg-res-model-banner">
            <span class="pg-res-run-index">#${idx + 1}</span>
            <div class="pg-res-banner-text">
              <strong>${escapeHTML(modelName)}</strong>
              <span>${escapeHTML(endpoint)} · ${escapeHTML(r.provider || "fal.ai")}</span>
            </div>
            ${isWinner ? '<span class="winner-badge">Best score</span>' : ''}
          </div>
          <div class="pg-res-header">
            <div class="pg-res-title-box">
              <span class="pg-res-model-name">Output from: ${escapeHTML(modelName)}</span>
              <span class="pg-res-provider">${escapeHTML(r.tier_badge || r.recommendation || "")}</span>
            </div>
          </div>

          <div class="pg-res-metrics-bar">
            <div class="metric-cell">
              <span class="metric-label">Latency</span>
              <span class="metric-value">${latencyStr}</span>
            </div>
            <div class="metric-cell">
              <span class="metric-label">Cost / run</span>
              <span class="metric-value text-cyan">${costStr}</span>
            </div>
            <div class="metric-cell">
              <span class="metric-label">Quality Score</span>
              <span class="metric-value text-green">${scoreStr}</span>
            </div>
          </div>

          <div class="pg-res-output-box">
            ${outputHTML}
          </div>

          <div class="pg-res-footer">
            <span class="text-xs text-dim">${r.output_dimensions || (r.total_tokens ? `${r.total_tokens} tokens` : "OK")}</span>
            <div class="pg-res-actions">
              <button class="pg-card-btn" onclick="copyResultImageUrl('${outputUrl}')">Copy URL</button>
              <button class="pg-card-btn" onclick="openImageLightbox('${outputUrl}', '${modelName}', '${r.output_dimensions || ''}')">Zoom</button>
              <button class="pg-card-btn" onclick="setPlaygroundViewMode('split'); updateSplitSliderModel('${modelId}')">Compare</button>
            </div>
          </div>
        </div>
      `;
    }).join("");
  }

  // Render Leaderboard Table
  if (DOM.pgLeaderboardBody) {
    DOM.pgLeaderboardBody.innerHTML = sorted.map((r, idx) => {
      const modelName = r.model_label || r.method_name || r.model_id || r.method_id;
      const rankBadge = idx === 0 ? "#1" : (idx === 1 ? "#2" : `#${idx + 1}`);
      const costStr = r.cost_usd !== undefined ? `$${Number(r.cost_usd).toFixed(4)}` : "—";
      return `
        <tr class="${idx === 0 ? 'leader-winner' : ''}">
          <td><strong>#${idx + 1}</strong></td>
          <td>${modelName}${!r.ok ? ' <span class="text-rose">(failed)</span>' : ''}</td>
          <td class="text-dim">${r.provider || "—"}</td>
          <td class="font-mono">${r.latency_sec}s</td>
          <td class="font-mono text-cyan">${costStr}</td>
          <td><span class="status-micro green">${r.score}%</span></td>
          <td><strong>${r.recommendation || "—"}</strong></td>
        </tr>
      `;
    }).join("");
  }

  // Display current mode
  setPlaygroundViewMode(state.playground.viewMode);
}

function setPlaygroundViewMode(mode) {
  state.playground.viewMode = mode;

  if (mode === "split" && state.playground.lastResults) {
    if (DOM.btnModeSplit) DOM.btnModeSplit.classList.add("active");
    if (DOM.btnModeGrid) DOM.btnModeGrid.classList.remove("active");
    if (DOM.pgSplitContainer) DOM.pgSplitContainer.style.display = "flex";
    if (DOM.pgComparisonGrid) DOM.pgComparisonGrid.style.display = "none";
    updateSplitSliderModel(state.playground.splitCompareModelId);
  } else {
    if (DOM.btnModeGrid) DOM.btnModeGrid.classList.add("active");
    if (DOM.btnModeSplit) DOM.btnModeSplit.classList.remove("active");
    if (DOM.pgComparisonGrid) DOM.pgComparisonGrid.style.display = "grid";
    if (DOM.pgSplitContainer) DOM.pgSplitContainer.style.display = "none";
  }
}

// Interactive Before / After Split Slider Controller
function setupBeforeAfterSplitSlider() {
  const wrap = DOM.beforeAfterWrap;
  if (!wrap) return;

  let isDragging = false;

  const setSplitPosition = (clientX) => {
    const rect = wrap.getBoundingClientRect();
    let x = clientX - rect.left;
    let pct = Math.max(0, Math.min(100, (x / rect.width) * 100));
    if (DOM.baOverlay) DOM.baOverlay.style.width = `${pct}%`;
    if (DOM.baDivider) DOM.baDivider.style.left = `${pct}%`;
  };

  wrap.addEventListener("mousedown", (e) => {
    isDragging = true;
    setSplitPosition(e.clientX);
  });

  window.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    setSplitPosition(e.clientX);
  });

  window.addEventListener("mouseup", () => {
    isDragging = false;
  });

  // Touch Support
  wrap.addEventListener("touchstart", (e) => {
    isDragging = true;
    if (e.touches[0]) setSplitPosition(e.touches[0].clientX);
  });

  window.addEventListener("touchmove", (e) => {
    if (!isDragging) return;
    if (e.touches[0]) setSplitPosition(e.touches[0].clientX);
  });

  window.addEventListener("touchend", () => {
    isDragging = false;
  });
}

function updateSplitSliderModel(modelId) {
  state.playground.splitCompareModelId = modelId;
  const results = state.playground.lastResults || [];
  const target = results.find((r) => (r.model_id || r.method_id) === modelId) || results[0];

  if (DOM.splitImgBefore) DOM.splitImgBefore.src = state.playground.imageUrl;
  if (DOM.splitImgAfter && target) {
    DOM.splitImgAfter.src = target.output_url || state.playground.imageUrl;
  }
}

// Actions on Result Cards
function copyResultByIndex(idx) {
  const results = state.playground.lastResults || [];
  const text = results[idx]?.content || "";
  navigator.clipboard.writeText(text).then(() => {
    showToast("Description copied to clipboard!", "success");
  });
}

function copyResultContent(text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast("AI Dutch Description copied to clipboard!");
  });
}

function copyResultImageUrl(url) {
  navigator.clipboard.writeText(url).then(() => {
    showToast("Image URL copied to clipboard!");
  });
}

function applyPlaygroundResultToCatalog(methodId) {
  showToast(`Selected ${methodId} as preferred method for this task`);
}

// Lightbox Modal
function openImageLightbox(src, title = "Image Preview", meta = "1760 × 1100") {
  if (DOM.lightboxImg) DOM.lightboxImg.src = src;
  if (DOM.lightboxTitle) DOM.lightboxTitle.textContent = title;
  if (DOM.lightboxMeta) DOM.lightboxMeta.textContent = meta;
  if (DOM.lightboxDownloadBtn) DOM.lightboxDownloadBtn.href = src;
  DOM.imageLightboxModal?.classList.add("open");
}

function closeImageLightbox() {
  DOM.imageLightboxModal?.classList.remove("open");
}

// Benchmark Export (JSON & CSV)
function exportBenchmarkReport(format = "json") {
  if (!state.playground.lastResults || state.playground.lastResults.length === 0) {
    showToast("Please run a benchmark evaluation before exporting", "warning");
    return;
  }

  const task = state.playground.activeTask;
  const timestamp = new Date().toISOString();

  if (format === "csv") {
    const headers = ["Model", "Provider", "Latency (s)", "Cost Per 1K ($)", "Quality Score (%)", "Recommendation"];
    const rows = state.playground.lastResults.map((r) => [
      `"${r.model_label || r.method_name || r.model_id || r.method_id}"`,
      `"${r.tier_badge || r.provider || 'AI'}"`,
      r.latency_sec || 0,
      r.cost_per_1k !== undefined ? r.cost_per_1k : (r.cost_usd * 1000).toFixed(3),
      r.score || 0,
      `"${r.recommendation || 'Evaluated'}"`,
    ]);

    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
    const dlAnchor = document.createElement("a");
    dlAnchor.setAttribute("href", encodeURI(csvContent));
    dlAnchor.setAttribute("download", `binnen_benchmark_${task}_${Date.now()}.csv`);
    document.body.appendChild(dlAnchor);
    dlAnchor.click();
    dlAnchor.remove();
    showToast("Downloaded CSV Benchmark Report");
  } else {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify({
      timestamp,
      task_type: task,
      input_asset: state.playground.imageUrl,
      benchmark_results: state.playground.lastResults,
    }, null, 2));

    const dlAnchor = document.createElement("a");
    dlAnchor.setAttribute("href", dataStr);
    dlAnchor.setAttribute("download", `binnen_benchmark_${task}_${Date.now()}.json`);
    document.body.appendChild(dlAnchor);
    dlAnchor.click();
    dlAnchor.remove();
    showToast("Downloaded JSON Benchmark Report");
  }
}

// =============================================================================
// CATALOG & PRODUCTS CONTROLLER
// =============================================================================
function setupEventListeners() {
  // Playground model checklist — event delegation (endpoint ids contain slashes)
  const modelsList = document.getElementById("pgModelsChecklist");
  if (modelsList) {
    modelsList.addEventListener("change", (e) => {
      const input = e.target;
      if (!input.matches('input[type="checkbox"]')) return;
      const modelId = input.dataset.modelId || input.closest(".fal-model-row")?.dataset.modelId;
      if (modelId) togglePlaygroundModelSelection(modelId, input.checked);
    });
  }

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
    if (e.key === "/" && document.activeElement !== DOM.searchInput) {
      e.preventDefault();
      switchView("catalog");
      DOM.searchInput?.focus();
    }
    if (e.key === "Escape") {
      closeProductDrawer();
      closeConfirmModal();
      toggleChatWidget(false);
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
    DOM.productsTableBody.innerHTML = Array.from({ length: 6 }, () => `
      <tr class="skeleton-row">
        <td><div style="display:flex;gap:13px;align-items:center;"><div class="skeleton-thumb"></div><div style="flex:1;"><div class="skeleton-block" style="width:70%;margin-bottom:6px;"></div><div class="skeleton-block" style="width:40%;"></div></div></div></td>
        <td><div class="skeleton-block" style="width:60%;"></div></td>
        <td><div class="skeleton-block" style="width:50%;"></div></td>
        <td></td>
      </tr>`).join("");
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
            <button class="table-action-btn" onclick="event.stopPropagation(); openPlaygroundWithProduct(${p.id})">
              Eval
            </button>
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
    const allImages = getDrawerProductImages(p);
    const evalBtn = document.getElementById("drawerEvalBtn");
    if (evalBtn) evalBtn.style.display = allImages.length > 0 ? "inline-flex" : "none";

    if (DOM.drawerGallery) {
      if (allImages.length > 0) {
        DOM.drawerGallery.innerHTML = allImages.map((img) => `
          <div class="drawer-gallery-item">
            <img class="drawer-gallery-thumb" src="${img.url}" alt="${escapeHTML(img.label)}" loading="lazy" />
            <div class="drawer-gallery-actions">
              <span class="drawer-gallery-type">${escapeHTML(img.type)}</span>
              <button type="button" class="drawer-gallery-eval-btn" onclick="event.stopPropagation(); openPlaygroundWithImage(${p.id}, ${JSON.stringify(img.url)})">Eval</button>
            </div>
          </div>
        `).join("");
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

function renderMessageContent(content) {
  if (!content) return "";
  const gridMarker = '<div class="welcome-prompts-grid">';

  if (typeof marked !== "undefined") {
    try {
      if (content.includes(gridMarker)) {
        const idx = content.indexOf(gridMarker);
        const mdPart = content.slice(0, idx).trim();
        const htmlPart = content.slice(idx);
        return `<div class="md-content copilot-welcome-intro">${marked.parse(mdPart)}</div>${htmlPart}`;
      }
      return `<div class="md-content">${marked.parse(content)}</div>`;
    } catch (e) {
      return escapeHTML(content);
    }
  }
  return escapeHTML(content);
}

function renderChat() {
  if (!DOM.chatMessages) return;
  DOM.chatMessages.innerHTML = state.chatMessages.map((msg, idx) => {
    const isWelcome = idx === 0 && msg.role === "assistant";
    const isLoading = msg.content === "Thinking and querying catalog tools...";
    const bubbleClass = [
      "msg-bubble",
      isWelcome ? "copilot-welcome-bubble" : "",
      isLoading ? "copilot-thinking" : "",
    ].filter(Boolean).join(" ");

    return `
      <div class="msg-row ${msg.role}${isWelcome ? " welcome" : ""}">
        <div class="${bubbleClass}">
          ${renderMessageContent(msg.content)}
        </div>
      </div>
    `;
  }).join("");

  if (typeof hljs !== "undefined") {
    DOM.chatMessages.querySelectorAll("pre code").forEach((block) => {
      hljs.highlightElement(block);
    });
  }

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
    state.chatMessages.pop();

    if (data.requires_confirmation) {
      pendingConfirmation = data.confirmation_data;
      showConfirmModal(data.message, data.confirmation_data);
      state.chatMessages.push({ role: "assistant", content: data.message || "Please confirm this action." });
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

// =============================================================================
// CONFIRMATION MODAL
// =============================================================================
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
    state.chatMessages.push({
      role: "assistant",
      content: `**Action Executed**\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``,
    });
    renderChat();
    showToast("Action completed successfully", "success");
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
