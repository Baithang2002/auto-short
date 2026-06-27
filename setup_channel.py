#!/usr/bin/env python3
import sys
import time
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
SESSION_DIR = SCRIPT_DIR / "browser_session"
ASSETS_DIR = SCRIPT_DIR / "assets"

AVATAR_PATH = ASSETS_DIR / "profile_picture.png"
BANNER_PATH = ASSETS_DIR / "banner_image.png"
WATERMARK_PATH = ASSETS_DIR / "watermark.png"

DESCRIPTION = """Daily mind-blowing facts about nature, space, and the planet we live on.

🌍 New short every weekday at 6pm IST
🌌 No clickbait. Just real science, told well."""

TAGS = [
    "nature", "science", "space", "facts", "mindblown", 
    "shorts", "educational", "wildlife", "astronomy", 
    "earth", "biology", "geology"
]

def die(msg):
    print(f"\n[X] {msg}\n")
    sys.exit(1)

def launch_browser(p, headless=False):
    """Launch chromium persistent context with anti-detection measures."""
    channels = ["chrome", "msedge", None]
    context = None
    last_err = None
    
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    for channel in channels:
        try:
            kwargs = {
                "user_data_dir": str(SESSION_DIR),
                "headless": headless,
                "viewport": None,  # Open full screen in headful
                "user_agent": user_agent,
                "args": [
                    "--disable-blink-features=AutomationControlled", 
                    "--no-sandbox",
                    "--disable-infobars"
                ]
            }
            if channel:
                kwargs["channel"] = channel
                
            context = p.chromium.launch_persistent_context(**kwargs)
            print(f"    [i] Launched browser using channel: {channel or 'default-chromium'}")
            break
        except Exception as e:
            last_err = e
            continue
            
    if not context:
        die(f"Failed to launch browser: {last_err}")
        
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return context

def handle_crop_modal(page):
    """YouTube details crop modal can take a second to load; click Done."""
    try:
        print("    - Waiting for Crop modal 'Done' button...")
        done_btn = page.wait_for_selector(
            "ytcp-button#done-button:visible, ytcp-button:has-text('Done'):visible, button:has-text('Done'):visible", 
            timeout=15000
        )
        page.wait_for_timeout(1000)
        done_btn.click()
        print("    - Crop 'Done' clicked.")
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    - Note: No crop modal or Done button clicked ({e}). Continuing...")

def setup_profile(page):
    print("\n=== [1/2] Customizing Channel Profile (Description & Branding) ===")
    page.goto("https://studio.youtube.com/channel/UC/editing/profile")
    page.wait_for_timeout(5000)
    
    # 1. Fill Description
    print("    - Locating description textbox...")
    desc_box = page.wait_for_selector("#description-textbox #textbox", timeout=20000)
    print("    - Filling channel description...")
    desc_box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(500)
    desc_box.fill(DESCRIPTION)
    page.wait_for_timeout(1000)
    
    # 2. Upload Profile Picture (Avatar)
    if AVATAR_PATH.exists():
        print("    - Uploading profile picture...")
        avatar_input = page.locator("input.ytcp-profile-image-upload")
        avatar_input.set_input_files(str(AVATAR_PATH))
        page.wait_for_timeout(2000)
        handle_crop_modal(page)
        
    # 3. Upload Banner Image
    if BANNER_PATH.exists():
        print("    - Uploading banner image...")
        banner_input = page.locator("input.ytcp-banner-upload")
        banner_input.set_input_files(str(BANNER_PATH))
        page.wait_for_timeout(2000)
        handle_crop_modal(page)
        
    # 4. Upload Watermark
    if WATERMARK_PATH.exists():
        print("    - Uploading watermark...")
        watermark_input = page.locator("input.ytcp-video-watermark-upload")
        watermark_input.set_input_files(str(WATERMARK_PATH))
        page.wait_for_timeout(2000)
        handle_crop_modal(page)
        
        # Select Entire Video display time
        try:
            print("    - Setting watermark display time to 'Entire video'...")
            entire_video_radio = page.locator("tp-yt-paper-radio-button:has-text('Entire video'), paper-radio-button:has-text('Entire video')")
            entire_video_radio.wait_for(state="visible", timeout=5000)
            entire_video_radio.click()
        except Exception as e:
            print(f"    - Warning setting watermark display time: {e}")

    # 5. Click Publish to save everything at once
    print("    - Publishing profile and branding changes...")
    publish_btn = page.locator("#publish-button")
    if publish_btn.is_enabled():
        publish_btn.click()
        print("    [OK] Profile and branding changes published successfully!")
        page.wait_for_timeout(6000)
    else:
        print("    - No profile changes detected (already set).")

def setup_settings(page):
    import re
    print("\n=== [2/2] Configuring Channel Settings (Country, Keywords, Kids, Category) ===")
    if "studio.youtube.com" not in page.url:
        page.goto("https://studio.youtube.com/")
        page.wait_for_timeout(5000)
    
    # Open settings dialog
    print("    - Opening Settings modal...")
    settings_btn = page.wait_for_selector("#settings-item", timeout=30000)
    settings_btn.click()
    
    page.wait_for_selector("ytcp-settings-dialog", state="attached", timeout=15000)
    page.wait_for_timeout(3000)
    
    # Click Channel tab
    print("    - Navigating to Channel settings...")
    page.locator("ytcp-settings-dialog li#channel").click()
    page.wait_for_timeout(3000)
    
    # 1. Country of Residence
    try:
        print("    - Setting Country of Residence to India...")
        # Open country dropdown
        country_select = page.locator("ytcp-settings-dialog ytcp-select:has-text('Country')").first
        country_select.click()
        page.wait_for_timeout(2000)
        # Click India exactly
        india_item = page.locator("tp-yt-paper-listbox#paper-list yt-formatted-string").filter(has_text=re.compile(r"^India$")).first
        india_item.click()
        page.wait_for_timeout(1000)
        print("    - Country set to India successfully.")
    except Exception as e:
        print(f"    - Warning setting country: {e}")
        
    # 2. Keywords/Tags
    try:
        print("    - Entering channel keywords...")
        keywords_input = page.locator("ytcp-settings-dialog input#text-input").first
        keywords_input.click()
        page.wait_for_timeout(500)
        for tag in TAGS:
            keywords_input.fill(tag)
            page.wait_for_timeout(150)
            keywords_input.press("Enter")
            page.wait_for_timeout(150)
        print("    - Keywords set successfully.")
    except Exception as e:
        print(f"    - Warning setting keywords: {e}")
        
    # 3. Made for Kids (Advanced Settings)
    try:
        print("    - Setting 'Made for Kids' to No...")
        # Click Advanced settings tab
        page.locator("ytcp-settings-dialog tp-yt-paper-tab:has-text('Advanced settings')").first.click()
        page.wait_for_timeout(2000)
        # Select "No, set this channel as not made for kids"
        kids_no_radio = page.locator("ytcp-settings-dialog tp-yt-paper-radio-button[name='NO'], ytcp-settings-dialog tp-yt-paper-radio-button:has-text('not made for kids')").first
        kids_no_radio.click()
        page.wait_for_timeout(1000)
        print("    - Kids status set to No successfully.")
    except Exception as e:
        print(f"    - Warning setting Kids status: {e}")
        
    # 4. Category (Upload Defaults -> Advanced Settings)
    try:
        print("    - Navigating to Upload defaults...")
        page.locator("ytcp-settings-dialog li#uploads").click()
        page.wait_for_timeout(3000)
        
        # Click Advanced settings tab
        page.locator("ytcp-settings-dialog tp-yt-paper-tab:has-text('Advanced settings')").first.click()
        page.wait_for_timeout(2000)
        
        print("    - Setting Category to Education...")
        # Find category dropdown
        category_select = page.locator("ytcp-settings-dialog ytcp-select:has-text('Category')").first
        category_select.click()
        page.wait_for_timeout(2000)
        
        # Select Education
        education_item = page.locator("tp-yt-paper-listbox#paper-list yt-formatted-string").filter(has_text=re.compile(r"^Education$")).first
        education_item.click()
        page.wait_for_timeout(1000)
        print("    - Category set to Education successfully.")
    except Exception as e:
        print(f"    - Warning setting Category: {e}")

    # Click Save
    try:
        print("    - Saving Settings...")
        save_btn = page.locator("ytcp-settings-dialog ytcp-button#save-button, ytcp-settings-dialog ytcp-button:has-text('Save')").first
        if save_btn.is_enabled():
            save_btn.click()
            print("    [OK] Settings saved successfully!")
            page.wait_for_timeout(5000)
        else:
            print("    - No settings changes to save (already set). Closing Settings modal...")
            page.locator("ytcp-settings-dialog ytcp-button#cancel-button, ytcp-settings-dialog ytcp-button:has-text('Close')").first.click()
            page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    - Error saving settings: {e}")

def main():
    parser = argparse.ArgumentParser(description="YouTube Channel Setup Automation")
    parser.add_argument("--dry-run", action="store_true", help="Check config and files without running browser")
    args = parser.parse_args()

    # Dry run file validation
    if args.dry_run:
        print("=== Dry-Run Asset Check ===")
        print(f"Session directory: {SESSION_DIR.resolve()} (Exists: {SESSION_DIR.exists()})")
        print(f"Profile Picture: {AVATAR_PATH.resolve()} (Exists: {AVATAR_PATH.exists()})")
        print(f"Banner Image: {BANNER_PATH.resolve()} (Exists: {BANNER_PATH.exists()})")
        print(f"Video Watermark: {WATERMARK_PATH.resolve()} (Exists: {WATERMARK_PATH.exists()})")
        return

    if not SESSION_DIR.exists():
        print("[X] Browser session not found! Run uploader.py --login first to log in.")
        sys.exit(1)

    print("\n=============================================")
    print("Starting Automated YouTube Channel Customization")
    print("=============================================\n")

    with sync_playwright() as p:
        # Launch browser in headful mode so the user can monitor
        context = launch_browser(p, headless=False)
        page = context.new_page()
        
        # Open Studio Dashboard to verify login
        page.goto("https://studio.youtube.com/")
        print("    - Opened YouTube Studio Dashboard. Checking login status...")
        try:
            page.wait_for_selector("ytcp-button:has-text('Create')", timeout=20000)
            print("    [OK] Logged in successfully!")
        except Exception:
            context.close()
            die("Failed: Session expired or not logged in. Run uploader.py --login first.")
            
        error_occurred = False

        # 1. Customization Profile (Description, Avatar, Banner, Watermark)
        try:
            setup_profile(page)
        except Exception as e:
            error_occurred = True
            print(f"[X] Error during Profile and Branding setup: {e}")
            try:
                page.screenshot(path=str(SCRIPT_DIR / "profile_error.png"))
                print("    - Saved error screenshot to profile_error.png")
            except:
                pass

        # 2. Settings configuration (Country, Tags, Audience, Category)
        try:
            setup_settings(page)
        except Exception as e:
            error_occurred = True
            print(f"[X] Error during Settings setup: {e}")
            try:
                page.screenshot(path=str(SCRIPT_DIR / "settings_error.png"))
                print("    - Saved error screenshot to settings_error.png")
            except:
                pass

        context.close()
        if not error_occurred:
            print("\n[OK] YouTube channel setup completed successfully!")

if __name__ == "__main__":
    main()
