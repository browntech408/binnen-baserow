# Data Scraper — Baserow brands

Python script that reads brands from a **Baserow** table and scrapes each brand’s website **one by one**, then writes results back to the same row.

---

## Quick start

### 1. Install Python 3.10+

```powershell
cd c:\projects\binnen-baserow
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure `.env`

```powershell
copy .env.example .env
```

Edit `.env`:

| Variable | What to put |
|----------|-------------|
| `BASEROW_URL` | Your server URL, e.g. `https://baserow.client.com` (from Hetzner handover) |
| `BASEROW_TOKEN` | **Database token** (not your login password) — see below |
| `BRANDS_TABLE_ID` | Number from the table URL: `.../table/123/...` → `123` |
| `FIELD_*` | Column API names — run `discover_baserow.py` |

### 3. Create a Baserow API token

1. Log in to Baserow (email from handover PDF).
2. Open the **database** that contains the brands table.
3. Click the **⋯** menu (top) → **Database API** / **Create token**.
4. Give **read + update** permission on the brands table.
5. Paste the token into `.env` as `BASEROW_TOKEN`.

> **Important:** The PDF password is for the **web UI**. The script uses a **database token**, which is safer for automation.

### 4. Discover field names

```powershell
python discover_baserow.py
```

Copy the printed `field_XXXX` keys into `.env`, for example:

```env
FIELD_BRAND_NAME=field_1234
FIELD_DOMAIN=field_5678
```

Match your real column names (`Merk`, `domain`, etc.) to the correct `field_*` IDs.

### Your brands table (from CSV export)

| CSV column | Meaning |
|------------|---------|
| `brand_name` | Display name |
| `brand` | Website URL to scrape |
| `Brand quote` | Notes when scrape failed (IP block, offline, etc.) — **not** a status column |
| `productsDetails` | Linked product row IDs (already imported) |
| `bg_remove` | Checkbox — often `True` for website brands, `False` for XML-only retailers |

There is **no** `scrape_status` column. The script uses filters instead (see `.env.example`).

### 5. Add optional columns in Baserow (only if you want extra tracking)

Create these columns on the **brands** table so the script can store progress:

| Column name (example) | Type | Purpose |
|----------------------|------|---------|
| `scrape_status` | Single select: `pending`, `done`, `error` | Filter what to scrape |
| `scrape_error` | Long text | Error message |
| `last_scraped_at` | Text or date | Last run time (ISO) |
| `page_title` | Text | `<title>` from homepage |
| `meta_description` | Long text | Meta description |

Set the same names in `.env` (`FIELD_SCRAPE_STATUS`, etc.) using the `field_*` IDs from `discover_baserow.py`.

### 6. Preview (dry-run)

```powershell
python preview_brands.py
```

Shows which brands would be scraped without calling websites.

### 7. Test: scrape products from ONE brand (text + JSON, no DB)

Matches **productsDetails** columns from website. Default: Spectrum Design, 5 products.

```powershell
python scrape_brand_products.py
python scrape_brand_products.py --max 4
python scrape_brand_products.py --from-baserow --max 3
```

Output: `output/products_Spectrum_Design.txt` and `.json`

### 8. Test: scrape FIRST brand homepage only (text + PDF report)

Does **not** change Baserow. Saves files in `output/`:

```powershell
python scrape_first_brand.py
```

Open `output/scrape_*_BrandName.txt` or `.pdf` to see title, meta, headings, sample links.

### 9. Run full scraper (all brands)

```powershell
python main.py
```

It will:

1. Load all rows from the brands table  
2. Skip rows that don’t match `SCRAPE_ONLY_STATUSES` (default: `pending` or empty)  
3. For each brand, open `domain` (or `FIELD_WEBSITE_URL`)  
4. Scrape homepage title + meta description  
5. `PATCH` the row in Baserow  
6. Wait `SCRAPE_DELAY_SECONDS` before the next brand  

---

## Integration checklist

1. **Log in to Baserow** — open the client instance in your browser (use the Hetzner URL from the handover).
2. **Create a token** — copy a Database API token into `.env` as `BASEROW_TOKEN`.
3. **Table ID** — open the brands table and take the number from the URL (`.../table/785/...` → `785`) for `BRANDS_TABLE_ID`.
4. **Run `discover_baserow.py`** — copy the correct `field_XXXX` names into `.env`.
5. **Optional columns** — if you use tracking fields, add `scrape_status`, `page_title`, etc. to the table and map them in `.env`.
6. **Run `python main.py`** — scrapes each brand domain one at a time.

## Current scope

- **`main.py`** — homepage only (title + meta description).
- **`scrape_brand_products.py`** — product pages from one brand; writes text/JSON under `output/` (no Baserow write yet).
- **Future work** — dedicated modules for product pages at scale, **XML feeds** (Pronto, Baenks, …), and **Sleepworld** scraping.

## Next steps (per client handover)

The handover PDF includes **XML feed URLs** for retailers. Those feeds are separate from brand website domains. A future `xml_feed_importer.py` should import those products into the **productsDetails** Baserow table.

---

## Project files

| File | Role |
|------|------|
| `main.py` | Entry point — loop brands, scrape, update Baserow |
| `scrape_first_brand.py` | Scrape first brand homepage → text + PDF report |
| `scrape_brand_products.py` | Scrape products from one brand → text + JSON report |
| `preview_brands.py` | Dry-run: which brands would be scraped |
| `discover_baserow.py` | Prints field IDs for your table |
| `trim_table_rows.py` | Keep first N rows in a table; delete the rest (`--confirm`) |
| `baserow_client.py` | Baserow REST API (list, update, delete rows) |
| `brand_scraper.py` | HTTP + BeautifulSoup homepage scrape |
| `product_scraper.py` | Discover and scrape product pages |
| `product_schema.py` | productsDetails column mapping |
| `config.py` | Reads `.env` |
| `.env.example` | Template — never commit real `.env` |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `401 Unauthorized` | Wrong token or token without access to that table |
| `404` on API | Wrong `BASEROW_URL` or `BRANDS_TABLE_ID` |
| Update fails | Field names in `.env` must be API keys (`field_123`), not display names |
| All rows skipped | Set `scrape_status` to `pending` or set `SCRAPE_ONLY_STATUSES=done,pending,` |
| Empty domain | Fill `domain` column in Baserow for that row |

---

## Security

- Do **not** commit `.env` or the handover PDF password.
- Use a **database token** with minimal permissions.
- Respect website `robots.txt` and rate limits (`SCRAPE_DELAY_SECONDS`).
