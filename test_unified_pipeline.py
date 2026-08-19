import os
import argparse
import json
import io
import requests
import random
from PIL import Image

from baserow_client import BaserowClient
from config import load_settings
from fal_image_processor import process_master, fal_call

TABLE_ID = 742

FIELD_PRODUCT_NAME = "field_7347"
FIELD_PRODUCT_DESC = "field_7348"
FIELD_PRODUCT_IMAGES = "field_7349"
FIELD_HERO_IMAGES = "field_7358"
FIELD_LIFESTYLE_IMAGES = "field_7359"
FIELD_DETAIL_IMAGE = "field_7360"

def classify_images_batch(image_urls: list[str], api_key: str) -> list[str]:
    """Classifies a batch of images for the same product at once, providing better context."""
    if not image_urls:
        return []
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = (
        "You are an expert product image classifier for a high-end furniture store.\n"
        "You are given a set of images for ONE single product.\n"
        "Classify EACH image into EXACTLY ONE of these three categories:\n"
        "1. 'hero': The MAIN, fully visible product shot on a plain, white, studio, or transparent background. CRITICAL RULE: The image MUST clearly show the full shape of the furniture. If the image is just a flat wood texture, fabric swatch, close-up material, diagram, or line-drawing, it is NOT a hero image. There must be ABSOLUTELY NO tables, vases, lamps, or other furniture in the image.\n"
        "2. 'detail': A zoomed-in shot of fabric, wood grain, stitching, legs, OR a schematic diagram/drawing. CRITICAL RULE: All flat textures, close-ups of wood/fabric, and drawings MUST be classified as 'detail'.\n"
        "3. 'lifestyle': The full product placed in a real-world setting. CRITICAL RULE: If the product is on a plain background but HAS PROPS (like a table, vase, or lamp in front of it), it MUST be classified as 'lifestyle'.\n"
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

def upload_pil_to_baserow(pil_img, filename: str, settings) -> dict:
    buf = io.BytesIO()
    ext = filename.split('.')[-1].upper()
    fmt = "JPEG" if ext == "JPG" else ext
    pil_img.save(buf, format=fmt)
    buf.seek(0)
    
    url = f"{settings.baserow_url.rstrip('/')}/api/user-files/upload-file/"
    headers = {"Authorization": f"Token {settings.baserow_token}"}
    files = {"file": (filename, buf, f"image/{fmt.lower()}")}
    
    resp = requests.post(url, headers=headers, files=files)
    resp.raise_for_status()
    return resp.json()

def generate_visual_prompt_from_image(name: str, description: str, image_url: str, api_key: str) -> str:
    print(f"Generating visual prompt from image using GPT-4o Vision for '{name}'...")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    sys_prompt = (
        f"You are a highly detailed visual describer. You are looking at a lifestyle image of a product named '{name}'. "
        f"Description: {description}\n\n"
        "Describe the MAIN product in this image in excruciating detail so an image generator can recreate it PERFECTLY. "
        "Mention the exact color, fabric texture, shape, cushions, base/legs, and overall style. "
        "DO NOT mention any tables, vases, room backgrounds, people, or any other objects. ONLY describe the product itself. "
        "Start your response with: 'A perfect, professional studio product shot of a...'"
    )
    
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": sys_prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }
        ],
        "temperature": 0.2
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        print(f"Vision API Error: {resp.text}")
        return f"A perfect, professional studio product shot of {name}, pure white background."
        
    return resp.json()["choices"][0]["message"]["content"].strip()

def generate_clean_image_with_fal(image_url: str, prompt: str, fal_key: str) -> str:
    print("Generating clean image using fal-ai/flux/dev/image-to-image...")
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json"
    }
    url = "https://fal.run/fal-ai/flux/dev/image-to-image"
    
    final_prompt = prompt + " The product is COMPLETELY ISOLATED. There are absolutely NO tables, NO vases, NO props, NO people, and NO other furniture anywhere in the image. The foreground is completely empty, providing an unobstructed full view of the product."
    print(f"\n[Final Img2Img Prompt]: {final_prompt}\n")
    
    payload = {
        "prompt": final_prompt,
        "image_url": image_url,
        "strength": 0.75, # Lower strength to preserve exact shape, relying on prompt to remove tables
        "guidance_scale": 7.5,
        "num_inference_steps": 28
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if not resp.ok:
        raise Exception(f"FAL Flux API Error: {resp.text}")
    return resp.json()["images"][0]["url"]

def generate_from_scratch_with_fal(name: str, description: str, fal_key: str, openrouter_key: str) -> str:
    print("Generating image from scratch using fal-ai/flux/dev...")
    
    url_gpt = "https://openrouter.ai/api/v1/chat/completions"
    headers_gpt = {"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"}
    sys_prompt = "Write a highly detailed image generation prompt for a professional studio product shot based on this name and description. Start with 'A perfect, professional studio product shot of...'"
    payload_gpt = {"model": "openai/gpt-4o", "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"Name: {name}\nDesc: {description}"}]}
    resp_gpt = requests.post(url_gpt, headers=headers_gpt, json=payload_gpt)
    prompt = resp_gpt.json()["choices"][0]["message"]["content"].strip()
    
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json"
    }
    url = "https://fal.run/fal-ai/flux/dev"
    
    final_prompt = prompt + " The product is COMPLETELY ISOLATED. There are absolutely NO tables, NO vases, NO props, NO people, and NO other furniture anywhere in the image. The foreground is completely empty, providing an unobstructed full view of the product."
    
    payload = {
        "prompt": final_prompt,
        "guidance_scale": 7.5,
        "num_inference_steps": 28
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if not resp.ok:
        raise Exception(f"FAL Flux API Error: {resp.text}")
    return resp.json()["images"][0]["url"]

def remove_background_and_format(image_url: str) -> Image.Image:
    print("Removing background of the generated image to make it transparent...")
    res = fal_call("fal-ai/imageutils/rembg", {"image_url": image_url})
    if not res or "image" not in res:
        raise Exception(f"Failed to remove background. API Response: {res}")
        
    img_data = requests.get(res["image"]["url"]).content
    img_no_bg = Image.open(io.BytesIO(img_data)).convert("RGBA")
    
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
    
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    
    canvas.paste(img_scaled, (paste_x, paste_y), img_scaled)
    return canvas

def get_or_create_copy_brand(baserow):
    brand_table_id = 745
    for row in baserow.list_table_rows(brand_table_id):
        if row.get("field_7446", "").lower() == "copy":
            return row["id"]
    return baserow.create_row(brand_table_id, {"field_7446": "Copy", "field_7447": "Copy"})["id"]

def main():
    parser = argparse.ArgumentParser(description="Unified Master AI Pipeline")
    parser.add_argument("--id", type=int, required=True, help="Baserow Row ID in Source Table 742")
    args = parser.parse_args()
    
    settings = load_settings()
    baserow = BaserowClient(settings)
    
    openrouter_key = settings.openrouter_api_key
    fal_key = os.getenv("FAL_KEY", "").strip()
    
    if not openrouter_key or not fal_key:
        print("Missing API keys.")
        return
        
    SRC_TABLE_ID = 742
    print(f"Fetching row {args.id} from Source Table {SRC_TABLE_ID}...")
    try:
        row = baserow.get_row(SRC_TABLE_ID, args.id)
    except Exception as e:
        print(f"Failed to fetch row: {e}")
        return
        
    brand_id = get_or_create_copy_brand(baserow)
    
    orig_name = str(row.get("field_7347") or f"Product {row['id']}")
    orig_desc = str(row.get("field_7348") or "")
    new_name = f"{orig_name} - UNIFIED PIPELINE"
    print(f"Copying product: {orig_name} -> {new_name}")
    
    product_images = row.get("field_7349") or []
    lifestyle_images_orig = row.get("field_7359") or []
    
    all_source_images = product_images + lifestyle_images_orig
    
    # Create the copy first
    new_row_data = {
        FIELD_PRODUCT_NAME: new_name,
        "field_8253": [brand_id],
        FIELD_PRODUCT_IMAGES: [{"name": img["name"]} for img in all_source_images],
        FIELD_HERO_IMAGES: [],
        FIELD_LIFESTYLE_IMAGES: [],
        FIELD_DETAIL_IMAGE: []
    }
    
    new_row = baserow.create_row(TABLE_ID, new_row_data)
    new_row_id = new_row["id"]
    print(f"Created copy with Row ID: {new_row_id} in Table {TABLE_ID}")
    
    hero_files = []
    lifestyle_files = []
    detail_files = []
    
    # 1. Process Existing Images
    if all_source_images:
        image_urls = [img["url"] for img in all_source_images]
        print(f"  -> Batch classifying {len(image_urls)} images...")
        classifications = classify_images_batch(image_urls, openrouter_key)
        
        for img_idx, img in enumerate(all_source_images):
            img_url = img["url"]
            img_class = classifications[img_idx]
            print(f"  -> Processing image {img_idx+1}/{len(all_source_images)} as {img_class.upper()}")
            
            try:
                processed, ext = process_master(img_url, img_class)
                filename = f"{img_class}_{new_row_id}_{img_idx}.{ext}"
                uploaded = upload_pil_to_baserow(processed, filename, settings)
                
                if img_class == "hero":
                    hero_files.append({"name": uploaded["name"]})
                elif img_class == "detail":
                    detail_files.append({"name": uploaded["name"]})
                elif img_class == "lifestyle":
                    lifestyle_files.append({"name": uploaded["name"]})
            except Exception as e:
                print(f"     Error processing image: {e}")

    # 2. Check if we need to Generate a Hero Image
    if not hero_files:
        print("\nNo hero images found after categorization. Generating one using AI...")
        try:
            if lifestyle_files and len(all_source_images) > 0:
                # Find a lifestyle URL from the original images we classified as lifestyle
                # Or just use the first source image available to run img2img
                source_url = all_source_images[0]["url"]
                print(f"Using Lifestyle->Hero generative pipeline on {source_url}...")
                
                # NEW LOGIC: Use Vision to get exact description from the image itself
                prompt = generate_visual_prompt_from_image(orig_name, orig_desc, source_url, openrouter_key)
                print(f"Generated Visual AI Prompt: {prompt}")
                
                clean_url = generate_clean_image_with_fal(source_url, prompt, fal_key)
            else:
                print("No images exist at all! Generating hero from scratch (Text-to-Image)...")
                clean_url = generate_from_scratch_with_fal(orig_name, orig_desc, fal_key, openrouter_key)
                
            final_hero = remove_background_and_format(clean_url)
            filename = f"hero_gen_{new_row_id}.png"
            uploaded = upload_pil_to_baserow(final_hero, filename, settings)
            hero_files.append({"name": uploaded["name"]})
            print("Successfully generated and formatted hero image.")
            
        except Exception as e:
            print(f"Error generating hero image: {e}")

    # 3. Update Row
    update_data = {}
    if hero_files: update_data[FIELD_HERO_IMAGES] = hero_files
    if lifestyle_files: update_data[FIELD_LIFESTYLE_IMAGES] = lifestyle_files
    if detail_files: update_data[FIELD_DETAIL_IMAGE] = detail_files
    
    if update_data:
        print(f"\nUpdating row {new_row_id} with final organized images...")
        baserow.update_row(TABLE_ID, new_row_id, update_data)
        print("Successfully updated row! You can now check it in Baserow Table 742.")
    else:
        print("No images were uploaded.")

if __name__ == "__main__":
    main()
