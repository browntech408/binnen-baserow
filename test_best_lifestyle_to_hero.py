import os
import argparse
import io
import requests
import json
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from fal_image_processor import fal_call

SRC_TABLE_ID = 742
DEST_TABLE_ID = 742

SRC_FIELD_PRODUCT_NAME = "field_7347"
SRC_FIELD_PRODUCT_DESC = "field_7348"
SRC_FIELD_PRODUCT_IMAGES = "field_7349"
SRC_FIELD_LIFESTYLE_IMAGES = "field_7359"

DEST_FIELD_PRODUCT_NAME = "field_7347"
DEST_FIELD_PRODUCT_IMAGES = "field_7349"
DEST_FIELD_HERO_IMAGES = "field_7358"
DEST_FIELD_LIFESTYLE_IMAGES = "field_7359"

def upload_pil_to_baserow(pil_img, filename: str, settings) -> dict:
    buf = io.BytesIO()
    ext = filename.split('.')[-1].upper()
    fmt = "PNG" # Force PNG for transparency
    
    pil_img.save(buf, format=fmt)
    buf.seek(0)
    
    url = f"{settings.api_base}/user-files/upload-file/"
    headers = {"Authorization": f"Token {settings.baserow_token}"}
    files = {"file": (filename, buf, "image/png")}
    
    resp = requests.post(url, headers=headers, files=files)
    resp.raise_for_status()
    return resp.json()

def get_or_create_copy_brand(baserow):
    brand_table_id = 745
    for row in baserow.list_table_rows(brand_table_id):
        if row.get("field_7446", "").lower() == "copy":
            return row["id"]
    new_brand = baserow.create_row(brand_table_id, {"field_7446": "Copy", "field_7447": "Copy"})
    return new_brand["id"]

def clean_files_for_upload(files_list):
    if not files_list:
        return []
    cleaned = []
    for item in files_list:
        if isinstance(item, dict) and "name" in item:
            cleaned.append({"name": item["name"]})
    return cleaned

def select_best_image_for_hero(image_urls: list[str], product_name: str, product_description: str, api_key: str) -> int:
    """Uses GPT-4o Vision to select the best lifestyle image with no occlusion."""
    if not image_urls:
        return 0
    if len(image_urls) == 1:
        return 0
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    sys_prompt = (
        f"You are an expert photo editor. We need to create a transparent background hero image for the following product:\n"
        f"Product Name: {product_name}\n"
        f"Product Description: {product_description}\n\n"
        "You are given a set of lifestyle photos. Your task is to select the SINGLE BEST photo to be used for background removal. "
        "CRITICAL RULES FOR SELECTION:\n"
        "1. Identify the product described above in the images.\n"
        "2. The main product must be FULLY visible.\n"
        "3. There must be NO objects (like tables, vases, lamps, or people) in front of or occluding the main product.\n"
        "4. The product should be clearly separated from the background.\n"
        "5. If multiple images are good, pick the one with the clearest, most direct view of the product.\n"
        "Return ONLY a valid JSON object with a single key 'best_index' containing the integer index (0-indexed) of the best image."
    )
    
    content = [{"type": "text", "text": sys_prompt}]
    for idx, img_url in enumerate(image_urls):
        # We add text to help it know the index
        content.append({"type": "text", "text": f"Image Index {idx}:"})
        content.append({"type": "image_url", "image_url": {"url": img_url}})
        
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "user", "content": content}
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }
    
    print(f"Asking AI to select the best image out of {len(image_urls)} options for product '{product_name}'...")
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    
    if not resp.ok:
        print(f"Failed to get AI selection, defaulting to 0. Error: {resp.text}")
        return 0
        
    try:
        data = resp.json()
        result_text = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(result_text)
        best_idx = int(parsed.get("best_index", 0))
        if 0 <= best_idx < len(image_urls):
            return best_idx
        return 0
    except Exception as e:
        print(f"Error parsing AI selection, defaulting to 0. Error: {e}")
        return 0

def remove_background_with_fal(image_url: str) -> Image.Image:
    print(f"Removing background for {image_url} using fal-ai...")
    res = fal_call("fal-ai/imageutils/rembg", {"image_url": image_url})
    if not res or "image" not in res:
        raise Exception(f"Failed to remove background. API Response: {res}")
        
    img_data = requests.get(res["image"]["url"]).content
    img_no_bg = Image.open(io.BytesIO(img_data)).convert("RGBA")
    
    # Crop to bounding box
    bbox = img_no_bg.getbbox()
    if bbox:
        img_cropped = img_no_bg.crop(bbox)
        return img_cropped
    return img_no_bg

def main():
    parser = argparse.ArgumentParser(description="Test AI Best Image Selection + Background Removal")
    parser.add_argument("--id", type=int, required=True, help="Baserow Row ID in source table")
    args = parser.parse_args()
    
    settings = load_settings()
    baserow = BaserowClient(settings)
    
    openrouter_key = settings.openrouter_api_key
    if not openrouter_key:
        print("Error: OPENROUTER_API_KEY not found in environment.")
        return
    
    brand_id = get_or_create_copy_brand(baserow)
    
    print(f"Fetching row {args.id} from Source Table {SRC_TABLE_ID}...")
    row = baserow.get_row(SRC_TABLE_ID, args.id)
    
    orig_name = str(row.get(SRC_FIELD_PRODUCT_NAME) or f"Product {row['id']}")
    orig_desc = str(row.get(SRC_FIELD_PRODUCT_DESC) or "")
    new_name = f"{orig_name} - BEST SELECTION BG REMOVE"
    print(f"Copying product: {orig_name} -> {new_name}")
    
    product_images = row.get(SRC_FIELD_PRODUCT_IMAGES) or []
    lifestyle_images = row.get(SRC_FIELD_LIFESTYLE_IMAGES) or []
    
    new_row_data = {
        DEST_FIELD_PRODUCT_NAME: new_name,
        "field_7376": [brand_id],
        DEST_FIELD_PRODUCT_IMAGES: clean_files_for_upload(product_images),
        DEST_FIELD_LIFESTYLE_IMAGES: clean_files_for_upload(lifestyle_images),
        DEST_FIELD_HERO_IMAGES: []
    }
    
    new_row = baserow.create_row(DEST_TABLE_ID, new_row_data)
    new_row_id = new_row["id"]
    print(f"Created copy with Row ID: {new_row_id} in Table {DEST_TABLE_ID}")
    
    images_to_process = product_images
    if not images_to_process:
        images_to_process = lifestyle_images
        
    if not images_to_process:
        print("No images found to process.")
        return
        
    # Get image URLs
    image_urls = [img["url"] for img in images_to_process]
    
    # 1. AI selects the BEST image without occlusions
    best_idx = select_best_image_for_hero(image_urls, orig_name, orig_desc, openrouter_key)
    best_image_url = image_urls[best_idx]
    
    print(f"\n[AI Selected Image Index]: {best_idx}")
    print(f"[Selected Image URL]: {best_image_url}\n")
    
    # 2. Remove Background using fal.ai
    hero_files = []
    try:
        pil_img = remove_background_with_fal(best_image_url)
        filename = f"hero_nobg_{new_row_id}.png"
        uploaded = upload_pil_to_baserow(pil_img, filename, settings)
        hero_files.append({"name": uploaded["name"]})
        print(f"Successfully generated & uploaded {filename}")
    except Exception as e:
        print(f"Error processing image: {e}")
            
    if hero_files:
        print(f"\nUpdating row {new_row_id} with new hero image...")
        baserow.update_row(DEST_TABLE_ID, new_row_id, {DEST_FIELD_HERO_IMAGES: hero_files})
        print("Successfully updated row! You can now check it in Baserow Table 742.")
    else:
        print("No hero images were generated.")

if __name__ == "__main__":
    main()
