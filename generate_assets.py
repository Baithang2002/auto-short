import argparse
import shutil
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).parent
ASSETS_DIR = SCRIPT_DIR / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

ap = argparse.ArgumentParser(description="Generate channel assets from AI-produced source images")
ap.add_argument("--avatar", type=str,
                help="Path to raw AI-generated avatar/profile picture")
ap.add_argument("--banner", type=str,
                help="Path to raw AI-generated banner background image")
ap.add_argument("--watermark", type=str,
                help="Path to raw AI-generated watermark/subscribe image")
args = ap.parse_args()

RAW_AVATAR = Path(args.avatar) if args.avatar else ASSETS_DIR / "profile_picture_raw.png"
RAW_BANNER = Path(args.banner) if args.banner else ASSETS_DIR / "banner_background_raw.png"
RAW_WATERMARK = Path(args.watermark) if args.watermark else ASSETS_DIR / "watermark_raw.png"

# Target paths
AVATAR_PATH = ASSETS_DIR / "profile_picture.png"
BANNER_PATH = ASSETS_DIR / "banner_image.png"
WATERMARK_PATH = ASSETS_DIR / "watermark.png"

def copy_raw_assets():
    print("[1/3] Copying avatar and watermark...")
    if RAW_AVATAR.exists():
        shutil.copy(RAW_AVATAR, AVATAR_PATH)
        print(f"      - Avatar copied to {AVATAR_PATH.name}")
    else:
        print("      - WARNING: Raw avatar image not found.")
        
    if RAW_WATERMARK.exists():
        shutil.copy(RAW_WATERMARK, WATERMARK_PATH)
        print(f"      - Watermark copied to {WATERMARK_PATH.name}")
    else:
        print("      - WARNING: Raw watermark image not found.")

def generate_banner():
    print("[2/3] Processing banner safe zone text overlay...")
    if not RAW_BANNER.exists():
        print("      - ERROR: Raw banner background image not found!")
        return False
        
    # Open background image (must be 2560x1440)
    img = Image.open(RAW_BANNER).convert("RGBA")
    draw = ImageDraw.Draw(img)
    
    # Safe zone coordinates (1546x423 centered in 2560x1440)
    # Width: 1546, Height: 423
    # X: 507 to 2053, Y: 508 to 931
    sz_x1, sz_y1, sz_x2, sz_y2 = 507, 508, 2053, 931
    
    # Create an overlay layer for semi-transparent card (glassmorphism look)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    
    # Draw dark card with soft rounded corners inside the safe zone
    # Fill with 60% opacity black
    overlay_draw.rectangle([sz_x1 + 50, sz_y1 + 50, sz_x2 - 50, sz_y2 - 50], fill=(0, 0, 0, 160), outline=(255, 255, 255, 40), width=3)
    
    # Composite the overlay
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    # Load fonts
    def _load_font(size, bold=False):
        candidates = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"] if bold else ["C:/Windows/Fonts/arial.ttf"]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    font_title = _load_font(72, bold=True)
    font_sub = _load_font(34)
        
    # Title Text
    title_text = "Wonders of Nature"
    # Subtitle Text
    sub_text = "Daily mind-blowing facts about our world"
    
    # Calculate positions (center of canvas is 1280x720)
    # Using anchor='mm' (middle-middle) for exact centering in PIL
    draw.text((1280, 680), title_text, fill=(255, 255, 255, 255), font=font_title, anchor="mm")
    
    # Draw subtitle below title
    draw.text((1280, 770), sub_text, fill=(200, 255, 200, 255), font=font_sub, anchor="mm")
    
    # Save final image as RGB PNG
    final_img = img.convert("RGB")
    final_img.save(BANNER_PATH, "PNG")
    print(f"      - Banner image with safe-zone text saved to {BANNER_PATH.name}")
    return True

def main():
    copy_raw_assets()
    generate_banner()
    print("[3/3] Asset generation completed successfully!")

if __name__ == "__main__":
    main()
