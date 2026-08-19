import os
import sys
import json
import time
import requests
import base64
import io

from baserow_client import BaserowClient
from config import load_settings
from fal_image_processor import process_master

settings = load_settings()
baserow = BaserowClient(settings)
TABLE_ID = 742

FIELD_PRODUCT_NAME = "field_7347"
FIELD_PRODUCT_IMAGES = "field_7349"
FIELD_HERO_IMAGES = "field_7358"
FIELD_LIFESTYLE_IMAGES = "field_7359"
FIELD_DETAIL_IMAGE = "field_7360"

def classify_images_batch(image_urls: list[str]) -> list[str]:
    """Classifies a batch of images for the same product at once, providing better context."""
    if not image_urls:
        return []
        
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"')
    if not api_key:
        print("Warning: OPENROUTER_API_KEY missing, defaulting to hero.")
        return ["hero"] * len(image_urls)
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = (
        "You are an expert product image classifier for a high-end furniture store.\n"
        "You are given a set of images for ONE single product.\n"
        "Classify EACH image into EXACTLY ONE of these three categories:\n"
        "1. 'hero': The MAIN, fully visible product shot on a plain, white, studio, or transparent background. There are no other props or room backgrounds. The entire product is visible.\n"
        "2. 'detail': A zoomed-in, close-up shot focusing on a specific part of the product (like fabric texture, stitching, the corner of a pillow, or a single leg). CRITICAL RULE: If the image is heavily zoomed in on a specific part of the product, it MUST be classified as 'detail' EVEN IF it is taken inside a room or on a couch. The close-up framing takes priority!\n"
        "3. 'lifestyle': The full product placed in a real-world setting (a living room, next to a person, surrounded by other objects). The background is a real environment (wall, floor, plants).\n"
        "Respond ONLY with a valid JSON array of strings in the exact same order as the images provided. For example: [\"hero\", \"detail\", \"lifestyle\"]"
    )
    
    content = [{"type": "text", "text": prompt}]
    for img_url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": img_url}})
        
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }
    
    # Adjust prompt to ensure clean JSON output
    payload["messages"][0]["content"][0]["text"] += "\nReturn ONLY a JSON object with a single key 'classifications' containing the array."
    
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        print(f"Failed to classify: {resp.text}")
        return ["hero"] * len(image_urls)
        
    try:
        data = resp.json()
        result_text = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(result_text)
        classes = parsed.get("classifications", [])
        
        # Sanitize and ensure length matches
        final_classes = []
        for i in range(len(image_urls)):
            if i < len(classes):
                c = classes[i].lower()
                if "hero" in c: final_classes.append("hero")
                elif "detail" in c: final_classes.append("detail")
                elif "lifestyle" in c: final_classes.append("lifestyle")
                else: final_classes.append("hero")
            else:
                final_classes.append("hero")
        return final_classes
    except Exception as e:
        print(f"Error parsing classification response: {e}")
        return ["hero"] * len(image_urls)

def upload_pil_to_baserow(pil_img, filename: str) -> dict:
    """Uploads a PIL image to Baserow and returns the file dict."""
    buf = io.BytesIO()
    ext = filename.split('.')[-1].upper()
    fmt = "JPEG" if ext == "JPG" else ext
    pil_img.save(buf, format=fmt)
    buf.seek(0)
    
    url = f"{settings.api_base}/user-files/upload-file/"
    headers = {"Authorization": f"Token {settings.baserow_token}"}
    files = {"file": (filename, buf, f"image/{fmt.lower()}")}
    
    resp = requests.post(url, headers=headers, files=files)
    resp.raise_for_status()
    return resp.json()

def get_or_create_copy_brand(baserow):
    print("Ensuring 'Copy' brand exists in Brands table...")
    brand_table_id = 745
    for row in baserow.list_table_rows(brand_table_id):
        if row.get("field_7446", "").lower() == "copy":
            return row["id"]
    new_brand = baserow.create_row(brand_table_id, {"field_7446": "Copy", "field_7447": "Copy"})
    return new_brand["id"]

def main():
    import random
    
    print("Fetching Table 742 fields to identify read-only fields...")
    fields_resp = baserow.session.get(baserow._url(f"/database/fields/table/{TABLE_ID}/"))
    fields = fields_resp.json()
    read_only_fields = {f"field_{f['id']}" for f in fields if f.get("read_only", False)}
    
    brand_id = get_or_create_copy_brand(baserow)

    print("Fetching rows from Table 742 to find candidates...")
    generator = baserow.list_table_rows(TABLE_ID)
    
    candidates = []
    for row in generator:
        images = row.get(FIELD_PRODUCT_IMAGES) or []
        if len(images) > 1 and "- COPY" not in str(row.get(FIELD_PRODUCT_NAME, "")):
            candidates.append(row)
        if len(candidates) >= 50:
            break
            
    if len(candidates) < 20:
        print(f"Could only find {len(candidates)} products with multiple images.")
        
    if not candidates:
        return
        
    # Pick 20 random candidates
    selected_candidates = random.sample(candidates, min(20, len(candidates)))
        
    for idx, row in enumerate(selected_candidates):
        orig_name = str(row.get(FIELD_PRODUCT_NAME) or f"Product {row['id']}")
        new_name = f"{orig_name} - COPY"
        print(f"\n[{idx+1}/{len(selected_candidates)}] Copying product: {orig_name} -> {new_name}")
        
        # Clone all writeable fields
        new_row_data = {}
        for k, v in row.items():
            if k.startswith("field_") and k not in read_only_fields:
                # For linked rows, Baserow accepts a list of IDs. Let's safely extract IDs if it's a linked row dict.
                if isinstance(v, list):
                    cleaned_list = []
                    for item in v:
                        if isinstance(item, dict) and "id" in item and "value" in item:
                            cleaned_list.append(item["id"])
                        else:
                            cleaned_list.append(item)
                    new_row_data[k] = cleaned_list
                else:
                    new_row_data[k] = v
        
        # Overrides
        new_row_data[FIELD_PRODUCT_NAME] = new_name
        new_row_data["field_7376"] = [brand_id] # Brand field in 742
        
        new_row = baserow.create_row(TABLE_ID, new_row_data)
        new_row_id = new_row["id"]
        print(f"Created copy with Row ID: {new_row_id} and Brand ID: {brand_id}")
        
        hero_files = []
        lifestyle_files = []
        detail_files = []
        
        images = new_row.get(FIELD_PRODUCT_IMAGES) or []
        image_urls = [img["url"] for img in images]
        
        print(f"  -> Batch classifying {len(image_urls)} images...")
        classifications = classify_images_batch(image_urls)
        print(f"  -> Classifications: {classifications}")
        
        for img_idx, img in enumerate(images):
            img_url = img["url"]
            img_class = classifications[img_idx]
            print(f"  -> Processing image {img_idx+1}/{len(images)}: {img_url}")
            print(f"     Classified as: {img_class.upper()}")
            
            # Process via fal.ai
            try:
                processed, ext = process_master(img_url, img_class)
                filename = f"{img_class}_{new_row_id}_{img_idx}.{ext}"
                uploaded = upload_pil_to_baserow(processed, filename)
                
                if img_class == "hero":
                    hero_files.append({"name": uploaded["name"]})
                elif img_class == "detail":
                    detail_files.append({"name": uploaded["name"]})
                elif img_class == "lifestyle":
                    lifestyle_files.append({"name": uploaded["name"]})
            except Exception as e:
                print(f"     Error processing image: {e}")
                
        # Update row with new images
        print(f"Updating row {new_row_id} with separated images...")
        update_data = {}
        if hero_files: update_data[FIELD_HERO_IMAGES] = hero_files
        if lifestyle_files: update_data[FIELD_LIFESTYLE_IMAGES] = lifestyle_files
        if detail_files: update_data[FIELD_DETAIL_IMAGE] = detail_files
        
        if update_data:
            baserow.update_row(TABLE_ID, new_row_id, update_data)
            print("Successfully updated row!")
        else:
            print("No new images were generated to update.")

if __name__ == "__main__":
    main()
