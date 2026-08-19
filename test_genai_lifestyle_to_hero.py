import os
import argparse
import io
import requests
import json
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings

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
    fmt = "JPEG" if ext == "JPG" else ext
    
    if fmt == "JPEG" and pil_img.mode in ("RGBA", "P"):
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

def generate_prompt(name: str, description: str, api_key: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    sys_prompt = (
        "You are an expert AI prompt engineer for image generation. "
        "We are using an Image-to-Image model to convert a messy lifestyle photo of a product into a clean, studio-quality 'hero' shot. "
        "Given the product name and description, write a concise, highly descriptive prompt to guide the AI. "
        "The prompt MUST specify: professional studio product shot, pure white background, perfectly lit, high resolution. "
        "Describe the product's physical appearance accurately based on the name/description so the AI doesn't hallucinate a different product. "
        "Return ONLY the raw prompt text. Do not include quotes, explanations, or introductory text."
    )
    
    user_prompt = f"Product Name: {name}\nProduct Description: {description}"
    
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3
    }
    
    print("Generating image prompt using OpenRouter (GPT-4o)...")
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    prompt = data["choices"][0]["message"]["content"].strip()
    return prompt

def generate_hero_image_with_fal(image_url: str, prompt: str, strength: float, fal_key: str) -> Image.Image:
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json"
    }
    url = "https://fal.run/fal-ai/flux/dev/image-to-image"
    payload = {
        "prompt": prompt,
        "image_url": image_url,
        "strength": strength,
        "guidance_scale": 7.5,
        "num_inference_steps": 28
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if not resp.ok:
        raise Exception(f"FAL API Error: {resp.text}")
        
    data = resp.json()
    gen_url = data["images"][0]["url"]
    
    # Download generated image
    img_resp = requests.get(gen_url)
    img_resp.raise_for_status()
    return Image.open(io.BytesIO(img_resp.content))

def main():
    parser = argparse.ArgumentParser(description="Test converting lifestyle to hero via generative AI")
    parser.add_argument("--id", type=int, required=True, help="Baserow Row ID in source table")
    parser.add_argument("--strength", type=float, default=0.8, help="Denoising strength for img2img (0.0 to 1.0, higher = more AI changes)")
    args = parser.parse_args()
    
    settings = load_settings()
    baserow = BaserowClient(settings)
    
    fal_key = os.getenv("FAL_KEY", "").strip()
    if not fal_key:
        print("Error: FAL_KEY not found in environment.")
        return
        
    openrouter_key = settings.openrouter_api_key
    if not openrouter_key:
        print("Error: OPENROUTER_API_KEY not found in environment.")
        return
    
    brand_id = get_or_create_copy_brand(baserow)
    
    print(f"Fetching row {args.id} from Source Table {SRC_TABLE_ID}...")
    row = baserow.get_row(SRC_TABLE_ID, args.id)
    
    orig_name = str(row.get(SRC_FIELD_PRODUCT_NAME) or f"Product {row['id']}")
    orig_desc = str(row.get(SRC_FIELD_PRODUCT_DESC) or "")
    new_name = f"{orig_name} - GEN AI COPY (str={args.strength})"
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
        
    ai_prompt = generate_prompt(orig_name, orig_desc, openrouter_key)
    print(f"\n[Generated Prompt]:\n{ai_prompt}\n")
    
    # Limit to maximum 3 images to focus on quality over quantity
    images_to_process = images_to_process[:3]
    
    hero_files = []
    
    print(f"Processing {len(images_to_process)} images to generate hero images with AI...")
    for img_idx, img in enumerate(images_to_process):
        img_url = img["url"]
        print(f"  -> Generating image {img_idx+1}/{len(images_to_process)}: {img_url}")
        
        try:
            pil_img = generate_hero_image_with_fal(img_url, ai_prompt, args.strength, fal_key)
            filename = f"hero_gen_{new_row_id}_{img_idx}.jpg"
            uploaded = upload_pil_to_baserow(pil_img, filename, settings)
            hero_files.append({"name": uploaded["name"]})
            print(f"     Successfully generated & uploaded {filename}")
        except Exception as e:
            print(f"     Error generating image: {e}")
            
    if hero_files:
        print(f"\nUpdating row {new_row_id} with new hero images...")
        baserow.update_row(DEST_TABLE_ID, new_row_id, {DEST_FIELD_HERO_IMAGES: hero_files})
        print("Successfully updated row! You can now check it in Baserow Table 742.")
    else:
        print("No hero images were generated.")

if __name__ == "__main__":
    main()
