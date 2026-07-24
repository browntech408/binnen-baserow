# Helper to print escaped jsCode for workflow JSON
BUILD = r'''
const row = $input.item.json;

const MAX_IMAGES = 8;
const PRODUCT_BUDGET = 8 * 1024 * 1024;
const MAX_SINGLE = 2 * 1024 * 1024;
const THUMB_EST = 200 * 1024;

const thumbUrl = (img) =>
  String(img?.thumbnails?.card_cover?.url || img?.thumbnails?.small?.url || '').trim();

const pickUrl = (img) => {
  const fullUrl = String(img?.url || '').trim();
  if (!fullUrl) return { skip: true, reason: 'no_url' };
  const bytes = Number(img?.size) || 0;
  const thumb = thumbUrl(img);

  if (bytes > MAX_SINGLE) {
    if (!thumb) {
      return { skip: true, reason: 'over_2mb_no_thumbnail', url: fullUrl, bytes };
    }
    return { url: thumb, source: 'thumbnail', est: THUMB_EST, src: fullUrl, bytes };
  }
  if (bytes > 0) {
    return { url: fullUrl, source: 'full', est: bytes, src: fullUrl, bytes };
  }
  if (thumb) {
    return { url: thumb, source: 'thumbnail_unknown', est: THUMB_EST, src: fullUrl, bytes: 0 };
  }
  return { skip: true, reason: 'unknown_no_thumbnail', url: fullUrl, bytes: 0 };
};

const uniqueImages = (arr) => {
  if (!Array.isArray(arr)) return [];
  const seen = new Set();
  const out = [];
  for (const img of arr) {
    const u = String(img?.url || '').trim();
    if (!u || seen.has(u)) continue;
    seen.add(u);
    out.push(img);
  }
  return out;
};

const r1 = uniqueImages(row.product_images);
const r2 = uniqueImages(row.bg_removed_hero);
const r3 = uniqueImages(row.hero_images);
const rawImages = r1.length ? r1 : r2.length ? r2 : r3;

const images = [];
const skipped = [];
let budgetLeft = PRODUCT_BUDGET;
let estTotal = 0;

for (let i = 0; i < rawImages.length; i++) {
  const img = rawImages[i];
  const choice = pickUrl(img);
  if (choice.skip) {
    skipped.push({
      index: i + 1,
      reason: choice.reason,
      src: choice.url || img?.url,
      size_bytes: choice.bytes != null ? choice.bytes : Number(img?.size) || 0,
    });
    continue;
  }
  if (images.length >= MAX_IMAGES) {
    skipped.push({ index: i + 1, reason: 'max_8_images', src: choice.src });
    continue;
  }
  if (choice.est > budgetLeft) {
    skipped.push({
      index: i + 1,
      reason: 'product_budget_8mb',
      src: choice.src,
      est_kb: Math.round(choice.est / 1024),
    });
    continue;
  }

  const mime = String(img?.mime_type || 'image/jpeg');
  const useJpg = choice.source.includes('thumbnail');
  const ext = useJpg ? 'jpg' : mime.includes('png') ? 'png' : mime.includes('webp') ? 'webp' : 'jpg';

  images.push({
    src: choice.src,
    download_url: choice.url,
    source: choice.source,
    est_bytes: choice.est,
    size_bytes: choice.bytes,
    mime_type: mime,
    filename: 'image-' + (images.length + 1) + '.' + ext,
    image_index: images.length + 1,
  });
  budgetLeft -= choice.est;
  estTotal += choice.est;
}

const title = (row.product_name || '').trim();
const description = (
  row.ai_description_translated_NL ||
  row.Accordion_Product_Description ||
  row.product_description ||
  ''
).trim();

const brand = row.Brand_table && row.Brand_table[0] ? row.Brand_table[0].value.trim() : '';
const category =
  row.product_category && row.product_category[0] ? row.product_category[0].value.trim() : '';
const subcategory =
  row.sub_category && row.sub_category[0] ? row.sub_category[0].value.trim() : '';

const hasImage = images.length > 0;
const isComplete = Boolean(title && description && hasImage && category && subcategory && brand);

const missingFields = [
  !title && 'title',
  !description && 'description',
  !hasImage && 'product_image',
  !category && 'category',
  !subcategory && 'subcategory',
  !brand && 'brand',
].filter(Boolean);

const price = String(row.price || '0').replace(/[^0-9.]/g, '') || '0.00';
const productType = category && subcategory ? `${category} / ${subcategory}` : category || 'Overig';

return {
  json: {
    baserow_row_id: row.id,
    shopify_status: isComplete ? 'active' : 'draft',
    missing_fields: missingFields,
    images,
    images_raw_count: rawImages.length,
    images_planned_count: images.length,
    images_est_total_mb: Math.round((estTotal / 1024 / 1024) * 100) / 100,
    images_skipped: skipped,
    product: {
      title: title || 'Untitled Product',
      body_html: description,
      vendor: brand || 'Unknown',
      product_type: productType,
      tags: isComplete ? '' : missingFields.join(', '),
      status: isComplete ? 'active' : 'draft',
      variants: [{ price }],
    },
  },
};
'''

COLLECT = r'''
const build = $('Build Product JSON').first().json;
const items = $input.all();
const PRODUCT_BUDGET = 8 * 1024 * 1024;
const MAX_SINGLE = 2 * 1024 * 1024;

const shopifyImages = [];
const skipped = [...(build.images_skipped || [])];
let totalBytes = 0;

for (let i = 0; i < items.length; i++) {
  const item = items[i];
  if (item.json.skip_download) {
    skipped.push({
      position: item.json.image_index,
      reason: item.json.skip_reason || 'skip_download',
      src: item.json.current_image?.src,
    });
    continue;
  }
  if (!item.json.current_image || !item.binary?.data) continue;

  const metaSize = Number(item.binary.data.fileSize || 0);
  if (metaSize > MAX_SINGLE) {
    skipped.push({
      position: item.json.image_index,
      reason: 'downloaded_over_2mb',
      size_mb: Math.round((metaSize / 1024 / 1024) * 10) / 10,
      src: item.json.current_image.src,
    });
    continue;
  }
  if (totalBytes + metaSize > PRODUCT_BUDGET && metaSize > 0) {
    skipped.push({
      position: item.json.image_index,
      reason: 'product_budget_8mb_actual',
      src: item.json.current_image.src,
    });
    continue;
  }

  const buffer = await this.helpers.getBinaryDataBuffer(i, 'data');
  const sizeBytes = buffer?.length || metaSize;
  if (!sizeBytes) continue;
  if (sizeBytes > MAX_SINGLE || totalBytes + sizeBytes > PRODUCT_BUDGET) {
    skipped.push({
      position: item.json.image_index,
      reason: 'size_budget_after_download',
      size_mb: Math.round((sizeBytes / 1024 / 1024) * 10) / 10,
      src: item.json.current_image.src,
    });
    continue;
  }

  shopifyImages.push({
    attachment: buffer.toString('base64'),
    filename: item.json.current_image.filename,
    position: item.json.image_index,
  });
  totalBytes += sizeBytes;
}

shopifyImages.sort((a, b) => a.position - b.position);

const product = { ...build.product };
if (shopifyImages.length) product.images = shopifyImages;

return {
  json: {
    baserow_row_id: build.baserow_row_id,
    shopify_status: build.shopify_status,
    missing_fields: build.missing_fields,
    images_raw_count: build.images_raw_count,
    images_planned_count: build.images_planned_count,
    images_uploaded_count: shopifyImages.length,
    images_uploaded_total_mb: Math.round((totalBytes / 1024 / 1024) * 100) / 100,
    images_skipped: skipped,
    product,
  },
};
'''

EXPAND = r'''
const build = $input.first().json;
const images = build.images || [];

if (!images.length) {
  return [{
    json: {
      baserow_row_id: build.baserow_row_id,
      shopify_status: build.shopify_status,
      images_skipped: build.images_skipped || [],
      skip_download: true,
    },
  }];
}

return images.map((img) => ({
  json: {
    baserow_row_id: build.baserow_row_id,
    shopify_status: build.shopify_status,
    images_skipped: build.images_skipped || [],
    current_image: img,
    download_url: img.download_url,
    image_index: img.image_index,
    skip_download: false,
  },
}));
'''

FILTER = r'''
const baserowRows = $('Get Baserow Products').all().flatMap((item) => item.json.results || []);
const shopifyProducts = $('Get Shopify Products').all().flatMap((item) => item.json.products || []);

const normalizeTitle = (v) => String(v || '').toLowerCase().trim().replace(/\s+/g, ' ');
const existingTitles = new Set();
for (const p of shopifyProducts) {
  const t = normalizeTitle(p.title);
  if (t) existingTitles.add(t);
}

const slim = (row) => ({
  id: row.id,
  product_name: row.product_name,
  product_description: row.product_description,
  ai_description_translated_NL: row.ai_description_translated_NL,
  Accordion_Product_Description: row.Accordion_Product_Description,
  price: row.price,
  Brand_table: row.Brand_table,
  product_category: row.product_category,
  sub_category: row.sub_category,
  product_images: row.product_images,
  bg_removed_hero: row.bg_removed_hero,
  hero_images: row.hero_images,
});

const toPush = [];
const queuedTitles = new Set();

for (const row of baserowRows) {
  if (row.BinnenProductID) continue;
  const title = normalizeTitle(row.product_name);
  if (!title || existingTitles.has(title) || queuedTitles.has(title)) continue;
  queuedTitles.add(title);
  toPush.push({ json: slim(row) });
}

return toPush;
'''

import json
print('BUILD_LEN', len(BUILD))
print('FILTER_LEN', len(FILTER))
