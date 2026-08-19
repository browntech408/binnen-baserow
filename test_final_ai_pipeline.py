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
    fmt = "PNG"
    
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

def generate_prompt(name: str, description: str, api_key: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    sys_prompt = (
        "You are an expert AI prompt engineer. We are using an Image-to-Image AI model to transform a messy lifestyle photo into a single, clean product shot. "
        "Your task is to write a highly detailed image generation prompt for the product based on its name and description. "
        "CRITICAL REQUIREMENTS:\n"
        "1. The image MUST contain ONLY the main product. Absolutely NO other furniture, tables, vases, people, or props.\n"
        "2. The product MUST be placed on a pure, solid white background.\n"
        "3. The prompt must accurately describe the physical characteristics of the product (shape, texture, style) so the AI retains the exact design of the original product.\n"
        "4. Start the prompt with: 'A perfect, professional studio product shot of...'\n"
        "Return ONLY the raw prompt text."
    )
    
    user_prompt = f"Product Name: {name}\nProduct Description: {description}"
    
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()

def generate_clean_image_with_fal(image_url: str, prompt: str, fal_key: str) -> str:
    print("Generating clean image using fal-ai/flux/dev/image-to-image...")
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json"
    }
    url = "https://fal.run/fal-ai/flux/dev/image-to-image"
    
    # Forcefully append instructions to the prompt so Flux knows to remove foreground items
    final_prompt = prompt + " The product is COMPLETELY ISOLATED. There are absolutely NO tables, NO vases, NO props, NO people, and NO other furniture anywhere in the image. The foreground is completely empty, providing an unobstructed full view of the product."
    print(f"\n[Final Flux Prompt]: {final_prompt}\n")
    
    payload = {
        "prompt": final_prompt,
        "image_url": image_url,
        "strength": 0.92, # Extremely high strength to obliterate the table and hallucinate the missing sofa parts
        "guidance_scale": 7.5,
        "num_inference_steps": 28
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if not resp.ok:
        raise Exception(f"FAL Flux API Error: {resp.text}")
        
    data = resp.json()
    return data["images"][0]["url"]

def remove_background_and_format(image_url: str) -> Image.Image:
    print("Removing background of the generated image to make it transparent...")
    res = fal_call("fal-ai/imageutils/rembg", {"image_url": image_url})
    if not res or "image" not in res:
        raise Exception(f"Failed to remove background. API Response: {res}")
        
    img_data = requests.get(res["image"]["url"]).content
    img_no_bg = Image.open(io.BytesIO(img_data)).convert("RGBA")
    
    # Format to standard hero size: 1000x880 with 85% margin on transparent canvas
    target_w, target_h = 1000, 880
    bbox = img_no_bg.getbbox()
    if bbox:
        img_cropped = img_no_bg.crop(bbox)
    else:
        img_cropped = img_no_bg
        
    margin_ratio = 0.85
    avail_w = int(target_w * margin_ratio)
    avail_h = int(target_h * margin_ratio)
    
    scale = min(avail_w / img_cropped.width, avail_h / img_cropped.height)
    new_w = int(img_cropped.width * scale)
    new_h = int(img_cropped.height * scale)
    
    img_scaled = img_cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    # Transparent canvas
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    
    canvas.paste(img_scaled, (paste_x, paste_y), img_scaled)
    return canvas

def main():
    parser = argparse.ArgumentParser(description="Test Final AI Pipeline: Gen AI + BG Remove")
    parser.add_argument("--id", type=int, required=True, help="Baserow Row ID")
    args = parser.parse_args()
    
    settings = load_settings()
    baserow = BaserowClient(settings)
    
    fal_key = os.getenv("FAL_KEY", "").strip()
    openrouter_key = settings.openrouter_api_key
    
    if not fal_key or not openrouter_key:
        print("Missing API keys.")
        return
        
    brand_id = get_or_create_copy_brand(baserow)
    row = baserow.get_row(SRC_TABLE_ID, args.id)
    
    orig_name = str(row.get(SRC_FIELD_PRODUCT_NAME) or f"Product {row['id']}")
    orig_desc = str(row.get(SRC_FIELD_PRODUCT_DESC) or "")
    new_name = f"{orig_name} - FINAL AI GENERATION"
    print(f"Processing: {orig_name} -> {new_name}")
    
    product_images = row.get(SRC_FIELD_PRODUCT_IMAGES) or []
    lifestyle_images = row.get(SRC_FIELD_LIFESTYLE_IMAGES) or []
    images_to_process = product_images if product_images else lifestyle_images
    
    if not images_to_process:
        print("No images found.")
        return
        
    # Create row
    new_row_data = {
        DEST_FIELD_PRODUCT_NAME: new_name,
        "field_7376": [brand_id],
        DEST_FIELD_PRODUCT_IMAGES: [{"name": img["name"]} for img in product_images],
        DEST_FIELD_LIFESTYLE_IMAGES: [{"name": img["name"]} for img in lifestyle_images],
        DEST_FIELD_HERO_IMAGES: []
    }
    new_row = baserow.create_row(DEST_TABLE_ID, new_row_data)
    new_row_id = new_row["id"]
    
    # Generate Prompt
    prompt = generate_prompt(orig_name, orig_desc, openrouter_key)
    print(f"\n[Generated Prompt]: {prompt}\n")
    
    # We will only generate 1 perfect hero image using the first available image
    source_img_url = images_to_process[0]["url"]
    
    try:
        # Step 1: Flux Gen AI (removes occlusions, makes white bg)
        gen_url = generate_clean_image_with_fal(source_img_url, prompt, fal_key)
        print(f"Generated clean image URL: {gen_url}")
        
        # Step 2: Rembg + Resize (removes white bg, resizes to 1000x880)
        final_pil = remove_background_and_format(gen_url)
        
        # Upload
        filename = f"final_ai_hero_{new_row_id}.png"
        uploaded = upload_pil_to_baserow(final_pil, filename, settings)
        
        print(f"\nUpdating row {new_row_id} with new hero image...")
        baserow.update_row(DEST_TABLE_ID, new_row_id, {DEST_FIELD_HERO_IMAGES: [{"name": uploaded["name"]}]})
        print("Successfully updated row! You can now check it in Baserow Table 742.")
        
    except Exception as e:
        print(f"Error during pipeline execution: {e}")

if __name__ == "__main__":
    main()
