import os
import argparse
import io
import requests

from baserow_client import BaserowClient
from config import load_settings
from fal_image_processor import process_master

SRC_TABLE_ID = 742
DEST_TABLE_ID = 742

SRC_FIELD_PRODUCT_NAME = "field_7347"
SRC_FIELD_PRODUCT_IMAGES = "field_7349"
SRC_FIELD_LIFESTYLE_IMAGES = "field_7359"

DEST_FIELD_PRODUCT_NAME = "field_7347"
DEST_FIELD_PRODUCT_IMAGES = "field_7349"
DEST_FIELD_HERO_IMAGES = "field_7358"
DEST_FIELD_LIFESTYLE_IMAGES = "field_7359"

def upload_pil_to_baserow(pil_img, filename: str, settings) -> dict:
    buf = io.BytesIO()
    ext = filename.split('.')[-1].upper()
    fmt = "JPEG" if ext == "JPG" else ext
    if fmt == "JPG": fmt = "JPEG"
    
    if pil_img.mode in ("RGBA", "P") and fmt == "JPEG":
        pil_img = pil_img.convert("RGB")
        
    pil_img.save(buf, format=fmt)
    buf.seek(0)
    
    url = f"{settings.api_base}/user-files/upload-file/"
    headers = {"Authorization": f"Token {settings.baserow_token}"}
    files = {"file": (filename, buf, f"image/{fmt.lower()}")}
    
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

def main():
    parser = argparse.ArgumentParser(description="Test converting lifestyle images to hero images via fal.ai")
    parser.add_argument("--id", type=int, required=True, help="Baserow Row ID in source table to copy and test")
    args = parser.parse_args()
    
    settings = load_settings()
    baserow = BaserowClient(settings)
    
    brand_id = get_or_create_copy_brand(baserow)
    
    print(f"Fetching row {args.id} from Source Table {SRC_TABLE_ID}...")
    row = baserow.get_row(SRC_TABLE_ID, args.id)
    
    orig_name = str(row.get(SRC_FIELD_PRODUCT_NAME) or f"Product {row['id']}")
    new_name = f"{orig_name} - LIFESTYLE TO HERO COPY"
    print(f"Copying product: {orig_name} -> {new_name} into Dest Table {DEST_TABLE_ID}")
    
    product_images = row.get(SRC_FIELD_PRODUCT_IMAGES) or []
    lifestyle_images = row.get(SRC_FIELD_LIFESTYLE_IMAGES) or []
    
    new_row_data = {
        DEST_FIELD_PRODUCT_NAME: new_name,
        "field_7376": [brand_id], # Brand field in 742
        DEST_FIELD_PRODUCT_IMAGES: clean_files_for_upload(product_images),
        DEST_FIELD_LIFESTYLE_IMAGES: clean_files_for_upload(lifestyle_images),
        DEST_FIELD_HERO_IMAGES: []
    }
    
    new_row = baserow.create_row(DEST_TABLE_ID, new_row_data)
    new_row_id = new_row["id"]
    print(f"Created copy with Row ID: {new_row_id} in Table {DEST_TABLE_ID}")
    
    # Process images
    images_to_process = product_images
    if not images_to_process:
        images_to_process = lifestyle_images
        
    if not images_to_process:
        print("No images found in product_images or lifestyle_images.")
        return
        
    hero_files = []
    
    print(f"Processing {len(images_to_process)} images to generate hero images...")
    for img_idx, img in enumerate(images_to_process):
        img_url = img["url"]
        print(f"  -> Processing image {img_idx+1}/{len(images_to_process)}: {img_url}")
        
        try:
            processed, ext = process_master(img_url, "hero")
            filename = f"hero_from_lifestyle_{new_row_id}_{img_idx}.{ext}"
            uploaded = upload_pil_to_baserow(processed, filename, settings)
            hero_files.append({"name": uploaded["name"]})
            print(f"     Successfully uploaded {filename}")
        except Exception as e:
            print(f"     Error processing image: {e}")
            
    if hero_files:
        print(f"Updating row {new_row_id} with new hero images...")
        baserow.update_row(DEST_TABLE_ID, new_row_id, {DEST_FIELD_HERO_IMAGES: hero_files})
        print("Successfully updated row! You can now check it in Baserow Table 742.")
    else:
        print("No hero images were generated.")

if __name__ == "__main__":
    main()
