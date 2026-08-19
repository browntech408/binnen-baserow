import os
import io
import requests
from PIL import Image

FAL_KEY = os.getenv("FAL_KEY", "").strip()

def fal_call(endpoint: str, payload: dict) -> dict:
    if not FAL_KEY:
        raise ValueError("FAL_KEY is not set.")
    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json"
    }
    url = f"https://fal.run/{endpoint}"
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        print(f"FAL API Error: {resp.text}")
        resp.raise_for_status()
    return resp.json()
def is_transparent_bg(img: Image.Image) -> bool:
    """Detects if an image already has a transparent background by checking its corners."""
    if img.mode not in ('RGBA', 'LA') and not (img.mode == 'P' and 'transparency' in img.info):
        return False
    
    img_rgba = img.convert("RGBA")
    w, h = img_rgba.size
    # Check corners and midpoints of edges
    points = [
        (0,0), (w-1,0), (0,h-1), (w-1,h-1),
        (w//2, 0), (w//2, h-1), (0, h//2), (w-1, h//2)
    ]
    # If any of these edge points are fully transparent, it's likely a transparent BG
    for x, y in points:
        if img_rgba.getpixel((x, y))[3] == 0:
            return True
    return False

def _process_transparent_on_white(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Takes a transparent image, scales it, and places it on a WHITE canvas."""
    bbox = img.getbbox()
    if not bbox:
        img_cropped = img
    else:
        img_cropped = img.crop(bbox)
        
    margin_ratio = 0.85
    avail_w = int(target_w * margin_ratio)
    avail_h = int(target_h * margin_ratio)
    
    scale = min(avail_w / img_cropped.width, avail_h / img_cropped.height)
    new_w = int(img_cropped.width * scale)
    new_h = int(img_cropped.height * scale)
    
    img_scaled = img_cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    # White canvas!
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    
    # Use alpha channel as mask to paste correctly
    mask = None
    if 'A' in img_scaled.getbands():
        mask = img_scaled.split()[3]
    canvas.paste(img_scaled, (paste_x, paste_y), mask)
    return canvas.convert("RGB")

def process_master(image_url: str, img_class: str) -> tuple[Image.Image, str]:
    """
    Main processor.
    Returns: (PIL Image, file_extension)
    """
    resp = requests.get(image_url)
    resp.raise_for_status()
    orig = Image.open(io.BytesIO(resp.content))
    
    target_w, target_h = 1000, 880
    if img_class in ("detail", "lifestyle"):
        target_w, target_h = 1760, 1100
        
    is_transparent = is_transparent_bg(orig)
    
    if img_class == "hero":
        if is_transparent:
            print("  [+] Image already without BG (HERO). Keeping transparent.")
            img_no_bg = orig.convert("RGBA")
        else:
            print("  [-] Image has BG (HERO). Removing BG with fal.ai...")
            res = fal_call("fal-ai/imageutils/rembg", {"image_url": image_url})
            img_data = requests.get(res["image"]["url"]).content
            img_no_bg = Image.open(io.BytesIO(img_data)).convert("RGBA")
            
        # Scale and place on TRANSPARENT canvas
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
        
        # Transparent canvas!
        canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
        paste_x = (target_w - new_w) // 2
        paste_y = (target_h - new_h) // 2
        
        canvas.paste(img_scaled, (paste_x, paste_y), img_scaled)
        return canvas, "png"
        
    else:
        if is_transparent:
            print(f"  [+] Image already without BG ({img_class.upper()}). Placing on WHITE background ({target_w}x{target_h}).")
            final_img = _process_transparent_on_white(orig.convert("RGBA"), target_w, target_h)
            return final_img, "jpg"
        else:
            print(f"  [-] Image has BG ({img_class.upper()}). Cropping/Padding smartly without removing BG...")
            final_img = _smart_crop_center(image_url, target_w, target_h)
            return final_img.convert("RGB"), "jpg"

def _smart_crop_center(image_url: str, target_w: int, target_h: int) -> Image.Image:
    """Keeps background, finds subject to center it, and crops/pads to target size."""
    resp = requests.get(image_url)
    resp.raise_for_status()
    orig = Image.open(io.BytesIO(resp.content)).convert("RGB")
    
    try:
        # Call fal to just get the bbox for centering
        res = fal_call("fal-ai/imageutils/rembg", {"image_url": image_url})
        img_data = requests.get(res["image"]["url"]).content
        mask_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        bbox = mask_img.getbbox()
    except Exception as e:
        bbox = None
        
    if not bbox:
        bbox = (0, 0, orig.width, orig.height)
        
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    
    target_ratio = target_w / float(target_h)
    orig_ratio = orig.width / orig.height
    
    if orig_ratio > target_ratio:
        new_h = orig.height
        new_w = int(new_h * target_ratio)
    else:
        new_w = orig.width
        new_h = int(new_w / target_ratio)
        
    left = cx - new_w / 2
    top = cy - new_h / 2
    right = cx + new_w / 2
    bottom = cy + new_h / 2
    
    if left < 0:
        right -= left
        left = 0
    if right > orig.width:
        left -= (right - orig.width)
        right = orig.width
        
    if top < 0:
        bottom -= top
        top = 0
    if bottom > orig.height:
        top -= (bottom - orig.height)
        bottom = orig.height
        
    left = max(0, left)
    top = max(0, top)
    right = min(orig.width, right)
    bottom = min(orig.height, bottom)
    
    cropped = orig.crop((left, top, right, bottom))
    return cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)


