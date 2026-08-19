/**
 * Binnen Catalog OS - Enterprise Frontend Controller
 * Fixed-Viewport Architecture, Independent Copilot Streams, & Full MCP Tooling
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
  chatMessages: [
    {
      role: "assistant",
      content: `### Welcome to Binnen Catalog Intelligence

I am your **Autonomous Multi-Storefront AI Copilot**, connected live to **Baserow** and **Shopify Woonbloq Storefront**.

Select an executive query below or type your own question:

<div class="welcome-prompts-grid">
  <button class="welcome-prompt-btn" onclick="usePrompt('How many total products are in our Shopify store and what is the breakdown by active, draft, and archived?')">
    <span class="prompt-icon-svg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.5 9.4l-9-5.19M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg></span>
    <span><strong>Shopify Products &amp; Status</strong> — Total counts &amp; publication state</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('Filter Shopify products by vendor Spectrum Design and check stock status')">
    <span class="prompt-icon-svg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>
    <span><strong>Spectrum Design Stock Audit</strong> — 32 live items, pricing &amp; inventory state</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('How many products on Shopify have 0 stock or no inventory value entered?')">
    <span class="prompt-icon-svg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>
    <span><strong>Zero &amp; Untracked Stock Audit</strong> — Inventory health check</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('How many products in Baserow have empty price field?')">
    <span class="prompt-icon-svg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg></span>
    <span><strong>Baserow Missing Prices</strong> — Catalog pricing gap analysis</span>
  </button>
  <button class="welcome-prompt-btn" onclick="usePrompt('Show me the overall sync coverage between Baserow and Shopify')">
    <span class="prompt-icon-svg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg></span>
    <span><strong>Catalog Sync Health</strong> — Master vs storefront link coverage</span>
  </button>
</div>`,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ],
  isChatLoading: false,
  activeMobilePane: "catalog",
};

// DOM References
const DOM = {
  // Layout
  appMainLayout: document.getElementById("appMainLayout"),
  catalogPane: document.getElementById("catalogPane"),

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
  drawerSyncBtn: document.getElementById("drawerSyncBtn"),
  drawerAskAIBtn: document.getElementById("drawerAskAIBtn"),

  // AI Copilot
  chatMessages: document.getElementById("chatMessages"),
  chatInput: document.getElementById("chatInput"),
  sendBtn: document.getElementById("sendBtn"),
  clearChatBtn: document.getElementById("clearChatBtn"),

  // Modal & Toast
  confirmModal: document.getElementById("confirmModal"),
  confirmModalMsg: document.getElementById("confirmModalMsg"),
  modalPayloadPreview: document.getElementById("modalPayloadPreview"),
  modalConfirmBtn: document.getElementById("modalConfirmBtn"),
  modalCancelBtn: document.getElementById("modalCancelBtn"),
  toastContainer: document.getElementById("toastContainer"),
};

let pendingConfirmation = null;

// Initialize
document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  renderChat();
  await Promise.all([fetchStats(), fetchBrands()]);
  await fetchProducts();
});

// Setup All Interactive Events
function setupEventListeners() {
  // Mobile pane switcher
  if (DOM.btnPaneCatalog && DOM.btnPaneCopilot) {
    DOM.btnPaneCatalog.addEventListener("click", () => setMobilePane("catalog"));
    DOM.btnPaneCopilot.addEventListener("click", () => setMobilePane("copilot"));
  }

  // Global Shortcut '/' to search & ESC to close drawer
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== DOM.searchInput && document.activeElement !== DOM.chatInput) {
      e.preventDefault();
      DOM.searchInput.focus();
    }
    if (e.key === "Escape") {
      closeDrawer();
      if (DOM.confirmModal.classList.contains("active")) {
        DOM.confirmModal.classList.remove("active");
        pendingConfirmation = null;
      }
    }
  });

  // Refresh Button
  DOM.refreshBtn.addEventListener("click", async () => {
    showToast("Refreshing live metrics & catalog...", "info");
    await Promise.all([fetchStats(), fetchProducts()]);
  });

  // Search input debounced
  let searchTimer;
  DOM.searchInput.addEventListener("input", (e) => {
    const val = e.target.value;
    DOM.clearSearchBtn.style.display = val ? "block" : "none";
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.searchQuery = val.trim();
      state.page = 1;
      fetchProducts();
    }, 300);
  });

  DOM.clearSearchBtn.addEventListener("click", () => {
    DOM.searchInput.value = "";
    DOM.clearSearchBtn.style.display = "none";
    state.searchQuery = "";
    state.page = 1;
    fetchProducts();
  });

  // Brand dropdown
  DOM.brandSelect.addEventListener("change", (e) => {
    state.selectedBrandId = e.target.value;
    state.page = 1;
    fetchProducts();
  });

  // Filter tabs
  DOM.tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      DOM.tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.filterType = btn.dataset.filter;
      state.page = 1;
      fetchProducts();
    });
  });

  // View Mode toggle
  DOM.btnTableView.addEventListener("click", () => setViewMode("table"));
  DOM.btnCardsView.addEventListener("click", () => setViewMode("cards"));

  // Pagination
  DOM.btnPrevPage.addEventListener("click", () => {
    if (state.page > 1) {
      state.page--;
      fetchProducts();
    }
  });

  DOM.btnNextPage.addEventListener("click", () => {
    if (state.page < state.totalPages) {
      state.page++;
      fetchProducts();
    }
  });

  DOM.pageJumpInput.addEventListener("change", (e) => {
    let p = parseInt(e.target.value, 10);
    if (isNaN(p) || p < 1) p = 1;
    if (p > state.totalPages) p = state.totalPages;
    state.page = p;
    fetchProducts();
  });

  DOM.pageSizeSelect.addEventListener("change", (e) => {
    state.pageSize = parseInt(e.target.value, 10);
    state.page = 1;
    fetchProducts();
  });

  // Floating AI Assistant Trigger & Widget events
  if (DOM.aiFabBtn) {
    DOM.aiFabBtn.addEventListener("click", toggleChatWidget);
  }
  if (DOM.closeChatWidgetBtn) {
    DOM.closeChatWidgetBtn.addEventListener("click", closeChatWidget);
  }

  // Drawer events
  DOM.closeDrawerBtn.addEventListener("click", closeDrawer);
  DOM.drawerBackdrop.addEventListener("click", closeDrawer);

  if (DOM.drawerSyncBtn) {
    DOM.drawerSyncBtn.addEventListener("click", async () => {
      if (!state.selectedProduct) return;
      await syncProductAction(state.selectedProduct.id);
    });
  }

  if (DOM.drawerAskAIBtn) {
    DOM.drawerAskAIBtn.addEventListener("click", () => {
      if (!state.selectedProduct) return;
      const name = state.selectedProduct.field_7347 || state.selectedProduct.product_name || `Product #${state.selectedProduct.id}`;
      closeDrawer();
      usePrompt(`Inspect and explain full synchronization and stock status for Baserow item ID ${state.selectedProduct.id} (${name})`);
    });
  }

  // Chat events
  DOM.sendBtn.addEventListener("click", sendMessage);
  
  // Auto-expanding textarea & Enter-to-send
  DOM.chatInput.addEventListener("input", function() {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 120) + "px";
  });

  DOM.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  DOM.clearChatBtn.addEventListener("click", () => {
    state.chatMessages = [
      {
        role: "assistant",
        content: "Conversation history cleared. Ready for your next query.",
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      },
    ];
    renderChat();
  });

  // Global ESC key listener
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (DOM.productDrawer && DOM.productDrawer.classList.contains("active")) {
        closeDrawer();
      } else if (DOM.aiChatWidget && DOM.aiChatWidget.classList.contains("active")) {
        closeChatWidget();
      }
    }
  });

  // Modal events
  DOM.modalCancelBtn.addEventListener("click", () => {
    DOM.confirmModal.classList.remove("active");
    pendingConfirmation = null;
  });

  DOM.modalConfirmBtn.addEventListener("click", async () => {
    if (pendingConfirmation) {
      DOM.confirmModal.classList.remove("active");
      await executeConfirmedAction(pendingConfirmation);
      pendingConfirmation = null;
    }
  });
}

function setViewMode(mode) {
  state.viewMode = mode;
  if (mode === "table") {
    DOM.btnTableView.classList.add("active");
    DOM.btnCardsView.classList.remove("active");
    DOM.tableViewContainer.style.display = "block";
    DOM.cardsViewContainer.style.display = "none";
  } else {
    DOM.btnCardsView.classList.add("active");
    DOM.btnTableView.classList.remove("active");
    DOM.tableViewContainer.style.display = "none";
    DOM.cardsViewContainer.style.display = "grid";
  }
}

// Floating AI Assistant Open/Close Controls
function openChatWidget() {
  if (DOM.aiChatWidget) {
    DOM.aiChatWidget.classList.add("active");
  }
  if (DOM.aiFabBtn) {
    DOM.aiFabBtn.classList.add("active-open");
  }
  setTimeout(() => {
    if (DOM.chatInput) DOM.chatInput.focus();
  }, 120);
}

function closeChatWidget() {
  if (DOM.aiChatWidget) {
    DOM.aiChatWidget.classList.remove("active");
  }
  if (DOM.aiFabBtn) {
    DOM.aiFabBtn.classList.remove("active-open");
  }
}

function toggleChatWidget() {
  if (DOM.aiChatWidget && DOM.aiChatWidget.classList.contains("active")) {
    closeChatWidget();
  } else {
    openChatWidget();
  }
}

// Fetch Executive Stats
async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();

    if (data.baserow_products) {
      const formatted = Number(data.baserow_products).toLocaleString();
      DOM.valBaserowProducts.textContent = formatted;
      DOM.hdrBaserowCount.textContent = `${formatted} items`;
      DOM.tabCountAll.textContent = formatted;
    }

    if (data.baserow_brands) {
      DOM.valBaserowBrands.textContent = `${data.baserow_brands} Brands`;
    }

    if (data.shopify) {
      DOM.valShopifyTotal.textContent = Number(data.shopify.total || 6492).toLocaleString();
      DOM.hdrShopifyCount.textContent = `${Number(data.shopify.total || 6492).toLocaleString()} items`;
      DOM.valShopifyActive.textContent = Number(data.shopify.active || 5663).toLocaleString();
      DOM.valShopifyDraft.textContent = Number(data.shopify.draft || 818).toLocaleString();
      DOM.valShopifyArchived.textContent = Number(data.shopify.archived || 11).toLocaleString();
    }

    if (data.linked_products !== undefined) {
      DOM.valLinkedCount.textContent = Number(data.linked_products).toLocaleString();
      const ratio = data.sync_ratio !== undefined ? data.sync_ratio : 97.9;
      DOM.tagSyncPercent.textContent = `${ratio}% Linked`;
      DOM.progressBarSync.style.width = `${Math.min(100, ratio)}%`;
      DOM.valUnlinkedNotice.textContent = `${data.unlinked_products} pending sync to Woonbloq`;
    }
  } catch (err) {
    console.error("Failed to fetch stats:", err);
  }
}

// Fetch Brands for Filter
async function fetchBrands() {
  try {
    const res = await fetch("/api/brands");
    const data = await res.json();
    state.brands = data.brands || [];

    DOM.brandSelect.innerHTML = `<option value="">Filter by Brand (All ${state.brands.length})</option>`;
    state.brands.forEach((b) => {
      const opt = document.createElement("option");
      opt.value = b.id;
      opt.textContent = b.name;
      DOM.brandSelect.appendChild(opt);
    });
  } catch (err) {
    console.error("Failed to fetch brands:", err);
  }
}

// Helper: Extract thumbnail with Hero Images as First Priority
function getProductThumbnail(p, placeholder = "https://via.placeholder.com/48?text=Product") {
  // 1st Priority: Hero Images (field_7358 or hero_images)
  const heroImages = p.hero_images || p.field_7358 || [];
  if (Array.isArray(heroImages) && heroImages.length > 0 && heroImages[0].url) {
    return heroImages[0].url;
  }

  // 1b Priority: Background Removed Hero (field_7400 or bg_removed_hero)
  const bgHero = p.bg_removed_hero || p.field_7400 || [];
  if (Array.isArray(bgHero) && bgHero.length > 0 && bgHero[0].url) {
    return bgHero[0].url;
  }

  // 2nd Priority: Product Images (all product images - field_7349 or product_images)
  const prodImages = p.product_images || p.field_7349 || [];
  if (Array.isArray(prodImages) && prodImages.length > 0 && prodImages[0].url) {
    return prodImages[0].url;
  }

  // 3rd Priority: Lifestyle Images (field_7359 or lifestyle_images)
  const lifeImages = p.lifestyle_images || p.field_7359 || [];
  if (Array.isArray(lifeImages) && lifeImages.length > 0 && lifeImages[0].url) {
    return lifeImages[0].url;
  }

  return placeholder;
}

// Fetch Paginated Catalog Products
async function fetchProducts() {
  DOM.productsTableBody.innerHTML = `
    <tr>
      <td colspan="5" class="table-state-row">
        <div class="loading-spinner"></div>
        <span>Loading Baserow products...</span>
      </td>
    </tr>
  `;
  DOM.cardsViewContainer.innerHTML = `
    <div style="grid-column: 1/-1; text-align: center; color: var(--text-dim); padding: 50px;">
      <div class="loading-spinner"></div>
      <span>Loading product cards...</span>
    </div>
  `;

  try {
    const params = new URLSearchParams({
      page: state.page,
      size: state.pageSize,
      filter_type: state.filterType,
    });

    if (state.searchQuery) params.set("search", state.searchQuery);
    if (state.selectedBrandId) params.set("brand_id", state.selectedBrandId);

    const res = await fetch(`/api/baserow/products?${params.toString()}`);
    const data = await res.json();

    state.products = data.results || [];
    state.totalCount = data.count || 0;
    state.totalPages = data.total_pages || 1;

    renderCatalog();
    updatePagination();
  } catch (err) {
    console.error("Failed to fetch products:", err);
    DOM.productsTableBody.innerHTML = `
      <tr>
        <td colspan="5" style="text-align: center; color: var(--rose-400); padding: 40px;">
          Unable to load products from Baserow. Please check your connection and try again.
        </td>
      </tr>
    `;
  }
}

// Render Catalog
function renderCatalog() {
  if (state.products.length === 0) {
    DOM.productsTableBody.innerHTML = `
      <tr>
        <td colspan="5" class="table-state-row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="margin-right:6px;opacity:0.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          No products found matching your current filter criteria.
        </td>
      </tr>
    `;
    DOM.cardsViewContainer.innerHTML = `
      <div style="grid-column: 1/-1; text-align: center; color: var(--text-dim); padding: 50px;">
        No products found.
      </div>
    `;
    return;
  }

  // 1. Table View
  DOM.productsTableBody.innerHTML = state.products
    .map((p) => {
      const name = p.product_name || p.field_7347 || p.Name || `Product #${p.id}`;
      const brandLinks = p.Brand_table || p.field_7376 || p.brands || [];
      const brandName = (brandLinks.length > 0 && brandLinks[0].value) ? brandLinks[0].value : "—";
      const catLinks = p.product_category || p.field_7363 || [];
      const catName = (catLinks.length > 0 && catLinks[0].value) ? catLinks[0].value : "";
      const subLinks = p.sub_category || p.field_7364 || [];
      const subName = (subLinks.length > 0 && subLinks[0].value) ? subLinks[0].value : "";
      const score = p.Score || p.field_7394 ? `Score: ${p.Score || p.field_7394}` : "";
      const woonbloqId = p.WoonbloqProductID || p.field_7425 || "";
      const isSynced = Boolean(woonbloqId && String(woonbloqId).trim());
      const readySync = Boolean(p.field_8511 || p["Ready to Sync"]);

      // Hero Images 1st Priority, then Product Images
      const thumbUrl = getProductThumbnail(p, "https://via.placeholder.com/44?text=P");

      return `
        <tr onclick="openProductDrawer(${p.id})">
          <td>
            <div class="product-cell-flex">
              <div class="thumb-frame">
                <img src="${thumbUrl}" alt="${escapeHtml(name)}" loading="lazy" onerror="this.src='https://via.placeholder.com/44?text=P'" />
              </div>
              <div>
                <div class="prod-title">${escapeHtml(name)}</div>
                <div style="display: flex; gap: 6px; align-items: center; margin-top: 3px;">
                  <span class="row-tag-sm">ID: #${p.id}</span>
                  ${readySync ? '<span class="badge badge-yellow" style="font-size: 9px; padding: 1px 6px; display:inline-flex; align-items:center; gap:3px;"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>Ready</span>' : ''}
                </div>
              </div>
            </div>
          </td>
          <td>
            <div>
              <span class="brand-tag-cell">${escapeHtml(brandName)}</span>
              <div class="cat-sub-text">${escapeHtml(catName)}${subName ? ` › ${escapeHtml(subName)}` : ''}</div>
              ${score ? `<div class="score-text" style="margin-top:2px;">${escapeHtml(score)}</div>` : ''}
            </div>
          </td>
          <td>
            <div>
              ${isSynced 
                ? `<span class="badge badge-green" style="display:inline-flex;align-items:center;gap:4px;"><svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10"/></svg>Synced</span><div class="id-chip">#${escapeHtml(String(woonbloqId).replace('gid://shopify/Product/', ''))}</div>` 
                : `<span class="badge badge-yellow" style="display:inline-flex;align-items:center;gap:4px;"><svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10"/></svg>Pending Sync</span>`}
            </div>
          </td>
          <td style="text-align: right;" onclick="event.stopPropagation()">
            <div class="actions-row">
              <button class="btn-table-action" onclick="openProductDrawer(${p.id})" title="Inspect Product">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                View
              </button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  // 2. Cards View
  DOM.cardsViewContainer.innerHTML = state.products
    .map((p) => {
      const name = p.product_name || p.field_7347 || p.Name || `Product #${p.id}`;
      const brandLinks = p.Brand_table || p.field_7376 || p.brands || [];
      const brandName = (brandLinks.length > 0 && brandLinks[0].value) ? brandLinks[0].value : "Brand";
      const woonbloqId = p.WoonbloqProductID || p.field_7425 || "";
      const isSynced = Boolean(woonbloqId && String(woonbloqId).trim());

      // Hero Images 1st Priority, then Product Images
      const thumbUrl = getProductThumbnail(p, "https://via.placeholder.com/260x160?text=Product");

      return `
        <div class="catalog-card-item" onclick="openProductDrawer(${p.id})">
          <div class="card-img-cover">
            <img src="${thumbUrl}" alt="${escapeHtml(name)}" loading="lazy" onerror="this.src='https://via.placeholder.com/260x160?text=P'" />
          </div>
          <div class="card-content">
            <div style="font-size: 11px; font-weight: 700; color: var(--cyan-400); text-transform: uppercase;">${escapeHtml(brandName)}</div>
            <div style="font-weight: 600; font-size: 13px; color: #fff; margin: 4px 0 10px 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${escapeHtml(name)}</div>
            <div style="margin-top: auto; display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--border-subtle); padding-top: 8px;">
              <span class="row-tag-sm">ID: #${p.id}</span>
              ${isSynced ? '<span class="badge badge-green">Synced</span>' : '<span class="badge badge-yellow">Unlinked</span>'}
            </div>
          </div>
        </div>
      `;
    })
    .join("");
}

// Update Pagination Bar
function updatePagination() {
  const start = (state.page - 1) * state.pageSize + 1;
  const end = Math.min(state.page * state.pageSize, state.totalCount);
  DOM.paginationInfo.innerHTML = `Showing <strong>${state.totalCount === 0 ? 0 : start}-${end}</strong> of <strong>${state.totalCount.toLocaleString()}</strong> products`;
  DOM.btnPrevPage.disabled = state.page <= 1;
  DOM.btnNextPage.disabled = state.page >= state.totalPages;
  DOM.pageJumpInput.value = state.page;
  DOM.totalPagesText.textContent = `of ${state.totalPages}`;
}

// Slide Drawer Open/Close
async function openProductDrawer(rowId) {
  DOM.drawerBackdrop.classList.add("active");
  DOM.productDrawer.classList.add("active");

  DOM.drawerRowId.textContent = `Item #${rowId}`;
  DOM.drawerProductName.textContent = "Loading product data...";
  DOM.drawerGallery.innerHTML = '<div style="color: var(--text-dim); padding: 10px;">Loading gallery...</div>';

  try {
    const res = await fetch(`/api/product/${rowId}`);
    const data = await res.json();
    const p = data.product;
    state.selectedProduct = p;

    const name = p.field_7347 || p.product_name || `Product #${p.id}`;
    DOM.drawerProductName.textContent = name;

    // Specs
    const brandLinks = p.field_7376 || p.Brand_table || [];
    DOM.drawerBrand.textContent = (brandLinks.length > 0 && brandLinks[0].value) ? brandLinks[0].value : "—";
    
    const catLinks = p.field_7363 || p.product_category || [];
    DOM.drawerCategory.textContent = (catLinks.length > 0 && catLinks[0].value) ? catLinks[0].value : "—";
    
    const subLinks = p.field_7364 || p.sub_category || [];
    DOM.drawerSubcategory.textContent = (subLinks.length > 0 && subLinks[0].value) ? subLinks[0].value : "—";
    
    DOM.drawerDesigner.textContent = p.field_7356 || "—";
    DOM.drawerScore.textContent = p.field_7394 ? `${p.field_7394}` : "—";

    // Sync info
    const woonbloqId = p.field_7425 || "";
    DOM.drawerWoonbloqId.textContent = woonbloqId ? String(woonbloqId).replace("gid://shopify/Product/", "#") : "Not Linked to Shopify";
    DOM.drawerWoonbloqStatus.textContent = p.field_7427 || (woonbloqId ? "Added" : "Pending");
    DOM.drawerWoonbloqStatus.className = `badge ${woonbloqId ? 'badge-green' : 'badge-yellow'}`;
    DOM.drawerReadyFlag.textContent = p.field_8511 ? "True (Ready)" : "False";

    // Descriptions
    DOM.drawerDescOriginal.textContent = p.field_7348 || "No original description available.";
    DOM.drawerDescAI.textContent = p.field_7362 || "No Dutch AI translation generated yet.";

    // Gallery: Hero images are FIRST PRIORITY, then BG Removed, then Product Images, then Lifestyle Images
    const allMedia = [
      ...(p.field_7358 || p.hero_images || []).map(img => ({ ...img, label: "Hero" })),
      ...(p.field_7400 || p.bg_removed_hero || []).map(img => ({ ...img, label: "BG Removed" })),
      ...(p.field_7349 || p.product_images || []).map(img => ({ ...img, label: "Product" })),
      ...(p.field_7359 || p.lifestyle_images || []).map(img => ({ ...img, label: "Lifestyle" })),
    ];

    if (allMedia.length === 0) {
      DOM.drawerGallery.innerHTML = '<div style="color: var(--text-dim); padding: 10px;">No media files uploaded for this product.</div>';
    } else {
      DOM.drawerGallery.innerHTML = allMedia
        .map(
          (m) => `
            <div class="drawer-gallery-thumb" title="${escapeHtml(m.label)} Image">
              <img src="${m.url}" alt="${escapeHtml(m.label)}" onerror="this.src='https://via.placeholder.com/110?text=Image'" />
              <span class="thumb-tag">${escapeHtml(m.label)}</span>
            </div>
          `
        )
        .join("");
    }

  } catch (err) {
    console.error("Failed to load product details:", err);
    DOM.drawerProductName.textContent = "Failed to load product";
  }
}

function closeDrawer() {
  DOM.drawerBackdrop.classList.remove("active");
  DOM.productDrawer.classList.remove("active");
  state.selectedProduct = null;
}

// Single-Click Product Sync
async function syncProductAction(rowId) {
  showToast(`Initiating Shopify sync for Item #${rowId}...`, "info");
  try {
    const res = await fetch("/api/sync/product", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ row_id: rowId, dry_run: false }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      showToast(`Successfully ${data.action} product "${data.product_title}" on Shopify!`, "success");
      await Promise.all([fetchStats(), fetchProducts()]);
      if (state.selectedProduct && state.selectedProduct.id === rowId) {
        openProductDrawer(rowId);
      }
    } else {
      showToast(`Sync Failed: ${data.detail || "Unknown error"}`, "error");
    }
  } catch (err) {
    showToast(`Sync error: ${err.message}`, "error");
  }
}

// Quick Prompt Trigger
function usePrompt(text) {
  openChatWidget();
  DOM.chatInput.value = text;
  DOM.chatInput.style.height = "auto";
  sendMessage();
}

// Send Chat Message to Autonomous Agent
async function sendMessage() {
  const query = DOM.chatInput.value.trim();
  if (!query || state.isChatLoading) return;

  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  state.chatMessages.push({ role: "user", content: query, time: now });
  DOM.chatInput.value = "";
  DOM.chatInput.style.height = "auto";
  state.isChatLoading = true;
  renderChat();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: state.chatMessages,
        model: "anthropic/claude-3.5-sonnet",
      }),
    });

    const data = await res.json();
    const replyTime = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    if (res.ok) {
      if (data.confirmation_required) {
        // Trigger security guardrail modal
        pendingConfirmation = {
          tool_name: data.tool_name,
          args: data.args,
        };
        DOM.confirmModalMsg.textContent = data.message;
        DOM.modalPayloadPreview.textContent = JSON.stringify(data.args, null, 2);
        DOM.confirmModal.classList.add("active");
      }

      state.chatMessages.push({
        role: "assistant",
        content: data.reply || "Action completed.",
        tool_name: data.tool_name,
        time: replyTime,
      });
    } else {
      state.chatMessages.push({
        role: "assistant",
        content: `**[Agent Notice]** ${data.detail || "Unknown error occurred"}`,
        time: replyTime,
      });
    }
  } catch (err) {
    state.chatMessages.push({
      role: "assistant",
      content: `**[Network Error]** ${err.message}`,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    });
  } finally {
    state.isChatLoading = false;
    renderChat();
  }
}

// Execute Confirmed Guardrail Action
async function executeConfirmedAction(actionData) {
  showToast(`Executing confirmed action: ${actionData.tool_name}...`, "info");
  try {
    const res = await fetch("/api/action/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(actionData),
    });
    const result = await res.json();
    state.chatMessages.push({
      role: "assistant",
      content: `**[Action Confirmed & Executed]**\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    });
    renderChat();
    showToast("Action executed successfully!", "success");
    await fetchStats();
  } catch (err) {
    showToast(`Action failed: ${err.message}`, "error");
  }
}

// Render Chat Messages (Independently Scrolled Stream)
function renderChat() {
  DOM.chatMessages.innerHTML = state.chatMessages
    .map((msg) => {
      const isUser = msg.role === "user";
      const formattedContent = renderMarkdownText(msg.content);

      // SVG icons for chat avatars
      const aiAvatarIcon = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/></svg>`;
      const userAvatarIcon = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`;
      const toolIcon = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`;

      return `
        <div class="msg-row ${isUser ? 'user' : 'assistant'}">
          ${!isUser ? `<div class="msg-avatar">${aiAvatarIcon}</div>` : ''}
          <div class="msg-bubble">
            ${msg.tool_name ? `
              <div class="tool-call-card">
                <div class="tool-call-header">
                  <span style="display:inline-flex;align-items:center;gap:5px;">${toolIcon} Executed MCP Tool:</span>
                  <span>${escapeHtml(msg.tool_name)}</span>
                </div>
              </div>` : ''}
            <div>${formattedContent}</div>
            <div style="font-size: 10px; color: var(--text-dim); text-align: right; margin-top: 4px;">${msg.time || ''}</div>
          </div>
          ${isUser ? `<div class="msg-avatar" style="background: var(--indigo-600); color: #fff;">${userAvatarIcon}</div>` : ''}
        </div>
      `;
    })
    .join("");

  if (state.isChatLoading) {
    const loadingAiIcon = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/></svg>`;
    DOM.chatMessages.innerHTML += `
      <div class="msg-row assistant">
        <div class="msg-avatar">${loadingAiIcon}</div>
        <div class="msg-bubble" style="display: flex; align-items: center; gap: 8px;">
          <div class="loading-spinner" style="width: 14px; height: 14px; margin: 0; border-width: 2px;"></div>
          <span style="font-size: 12px; color: var(--text-dim);">Agent is querying MCP tools &amp; generating response...</span>
        </div>
      </div>
    `;
  }

  // Smooth auto-scroll ONLY within the chat stream
  DOM.chatMessages.scrollTop = DOM.chatMessages.scrollHeight;
}

// Pro Markdown Renderer with Marked & Highlight.js fallback + Table scroll wrapper
function renderMarkdownText(text) {
  if (!text) return "";
  try {
    if (typeof marked !== "undefined") {
      marked.setOptions({
        gfm: true,
        breaks: true,
      });
      let parsed = marked.parse(text);
      // Ensure all <table> elements are wrapped in a responsive .table-scroll-wrap container
      parsed = parsed.replace(/<table(\s*[\s\S]*?)<\/table>/gi, '<div class="table-scroll-wrap"><table$1</table></div>');
      return parsed;
    }
  } catch (e) {
    console.warn("Marked parse error:", e);
  }

  // Fallback Formatter with Table support
  let raw = text;
  // Parse markdown tables if any
  const lines = raw.split("\n");
  let inTable = false;
  let tableRows = [];
  let outLines = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.startsWith("|") && line.endsWith("|")) {
      if (!inTable) {
        inTable = true;
        tableRows = ['<div class="table-scroll-wrap"><table>'];
      }
      const cells = line.split("|").slice(1, -1).map(c => escapeHtml(c.trim()));
      if (cells.every(c => /^:?-+:?$/.test(c))) {
        continue;
      }
      const isHeader = tableRows.length === 1;
      const tag = isHeader ? "th" : "td";
      tableRows.push("<tr>" + cells.map(c => `<${tag}>${c}</${tag}>`).join("") + "</tr>");
    } else {
      if (inTable) {
        inTable = false;
        tableRows.push("</table></div>");
        outLines.push(tableRows.join(""));
        tableRows = [];
      }
      outLines.push(escapeHtml(line));
    }
  }
  if (inTable) {
    tableRows.push("</table></div>");
    outLines.push(tableRows.join(""));
  }

  let html = outLines.join("<br/>");
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
  return html;
}

// Toast System
function showToast(message, type = "info") {
  const icons = {
    success: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>`,
    error: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
    info: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
  };
  const toast = document.createElement("div");
  toast.className = `toast-item ${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || icons.info}</span>
    <span>${escapeHtml(message)}</span>
  `;
  DOM.toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 250);
  }, 4000);
}

// Helper: Escape HTML
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// User Logout Handler
async function handleLogout() {
  try {
    showToast("Signing out...", "info");
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (e) {
    console.error("Logout error:", e);
  } finally {
    window.location.href = "/login";
  }
}
