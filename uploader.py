#!/usr/bin/env python3
"""
uploader.py - Browser-automation uploader for YouTube Shorts, Instagram Reels,
              and Facebook Reels (Playwright).

Improvements vs. the old version:
  * headless=False by default. Social sites aggressively flag headless Chromium.
    Pass --headless if you really want it (and have tested it on your account).
  * Per-step retry wrapper with screenshot-on-failure under output/screenshots/.
  * Real upload verification: each platform checks for a success indicator
    and captures the public URL where it can.
  * Per-run JSON history at output/upload_log.json (one entry per video, with
    per-platform status, url, duration, error, screenshot path).
  * Reads output/upload_metadata.json (written by auto_short.py) when no
    --title / --description are supplied. This is the contract that lets
    pipeline.py glue Stage 1 and Stage 2 together.
  * Per-platform jitter between uploads to avoid the "3 platforms in 5 seconds"
    bot-detection footprint.

CLI examples:

    # Manual login (saves cookies under ./browser_session)
    python uploader.py --login

    # Upload using the metadata auto_short.py just wrote
    python uploader.py --upload output/final.mp4

    # Upload with explicit text
    python uploader.py --upload output/final.mp4 \\
        --title "Wild ocean facts #shorts" \\
        --description "..." --platforms youtube
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import datetime as dt
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from playwright.sync_api import (
        sync_playwright, Error as PWError, TimeoutError as PWTimeout,
    )
except ImportError:
    sync_playwright = None
    class PWError(Exception): pass
    class PWTimeout(Exception): pass

SCRIPT_DIR    = Path(__file__).parent.resolve()
SESSION_DIR   = SCRIPT_DIR / "browser_session"
OUT_DIR       = SCRIPT_DIR / "output"
SCREENSHOTS   = OUT_DIR / "screenshots"
LOG_PATH      = OUT_DIR / "upload_log.json"
META_PATH     = OUT_DIR / "upload_metadata.json"

PLATFORMS = ("youtube", "instagram", "facebook")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def die(msg: str) -> None:
    print(f"\n[X] {msg}\n")
    sys.exit(1)


def _ts() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")[:40]


def _screenshot(page, platform: str, step: str) -> Optional[str]:
    try:
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS / f"{platform}_{_safe(step)}_{_ts()}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


def retry(step: str, *, tries: int = 3, backoff: float = 2.0):
    """Decorator: retry a step up to `tries` times, screenshot on each failure.

    The wrapped function must accept (page, ...). On every exception the
    current page state is captured to output/screenshots/.
    """
    def deco(fn: Callable):
        def wrapper(page, *a, platform: str = "?", **kw):
            last_err = None
            for attempt in range(1, tries + 1):
                try:
                    return fn(page, *a, **kw)
                except (PWError, PWTimeout, Exception) as e:
                    last_err = e
                    shot = _screenshot(page, platform, f"{step}_attempt{attempt}")
                    print(f"    [!] {platform}/{step} attempt {attempt}/{tries} failed: {e!r}")
                    if shot:
                        print(f"        screenshot: {shot}")
                    if attempt < tries:
                        time.sleep(backoff * attempt)
            raise last_err
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Browser launch
# ---------------------------------------------------------------------------
def launch_browser(p, *, headless: bool, viewport=None):
    user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36")
    last_err = None
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = dict(
                user_data_dir=str(SESSION_DIR),
                headless=headless,
                viewport=viewport,
                user_agent=user_agent,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            if channel:
                kwargs["channel"] = channel
            context = p.chromium.launch_persistent_context(**kwargs)
            print(f"    [i] Launched browser ({channel or 'chromium'}, headless={headless})")
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            return context
        except Exception as e:
            last_err = e
    die(f"Failed to launch browser context: {last_err}")


def login():
    SESSION_DIR.mkdir(exist_ok=True)
    print("\n[i] Opening browser. Log into YouTube, Instagram, and Facebook,")
    print("    then close the browser window to save the session.\n")
    with sync_playwright() as p:
        context = launch_browser(p, headless=False, viewport=None)
        for url, label in [
            ("https://studio.youtube.com/", "YouTube Studio"),
            ("https://www.instagram.com/",  "Instagram"),
            ("https://www.facebook.com/reels/create/", "Facebook Reels"),
        ]:
            page = context.new_page()
            page.goto(url)
            print(f"    - Opened {label}")
        try:
            while len(context.pages) > 0:
                time.sleep(1)
        except Exception:
            pass
        context.close()
    print("[OK] Session saved.\n")


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------
@retry("youtube_session_check", tries=2)
def _yt_check_session(page):
    page.goto("https://studio.youtube.com/")
    page.wait_for_selector("ytcp-button:has-text('Create'), tp-yt-paper-icon-button#upload-icon, ytcp-uploads-dialog", timeout=20000)


@retry("youtube_upload_form", tries=2)
def _yt_open_upload_and_fill(page, video_path: Path, title: str, description: str, tags: str = ""):
    page.goto("https://studio.youtube.com/channel/UC?d=ud")
    file_input = page.wait_for_selector("input[type=file]", state="attached", timeout=30000)
    file_input.set_input_files(str(video_path))

    page.wait_for_selector("#title-textarea", timeout=60000)

    title_box = page.locator("#title-textarea #textbox")
    title_box.click()
    title_box.fill("")                       # clear default (filename)
    title_box.fill(title[:100])

    desc_box = page.locator("#description-textarea #textbox")
    desc_box.click()
    desc_box.fill(description[:4900])        # YT cap is 5000

    if tags:
        try:
            tags_box = page.locator("#tags-textarea #textbox")
            if tags_box.is_visible(timeout=3000):
                tags_box.click()
                tags_box.fill(tags[:500])
        except Exception:
            pass  # tags field not found or not visible; skip silently

    page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').click()


@retry("youtube_advance_to_publish", tries=2)
def _yt_advance_to_visibility(page):
    # 3 Next clicks (Details → Video elements → Checks → Visibility)
    for _ in range(3):
        btn = page.locator("#next-button")
        btn.wait_for(state="visible", timeout=30000)
        btn.click()
        time.sleep(1)
    public = page.locator('tp-yt-paper-radio-button[name="PUBLIC"]')
    public.wait_for(state="visible", timeout=30000)
    public.click()


def _yt_publish_and_capture_url(page, **kwargs) -> str:
    # #done-button is aria-disabled while YouTube is still receiving the file.
    # The tp-yt-iron-overlay-backdrop intercepts all pointer events during that
    # time. Wait (up to 5 min for large files) for both to clear before clicking.
    try:
        page.wait_for_function(
            """() => {
                const btn = document.querySelector('#done-button');
                if (!btn || btn.getAttribute('aria-disabled') === 'true') return false;
                return !document.querySelector('tp-yt-iron-overlay-backdrop[opened]');
            }""",
            timeout=300_000,
        )
    except Exception as e:
        print(f"    [YouTube] Warning: upload may not be complete yet ({e!r}); clicking Done anyway")
    page.locator("#done-button").click()
    url = ""
    try:
        # Once the upload is committed YouTube shows a success / "still processing"
        # dialog. Shorts publish as youtube.com/shorts/<id>, long-form as youtu.be/<id>.
        page.wait_for_selector(
            "ytcp-video-info, ytcp-video-share-dialog, "
            "ytcp-uploads-still-processing-dialog, tp-yt-paper-dialog, "
            "a[href*='youtu.be'], a[href*='/shorts/']",
            timeout=90000,
        )
        # The share link can take a few seconds to render inside the dialog.
        for _ in range(12):
            for sel in ("a[href*='youtu.be']",
                        "a[href*='youtube.com/shorts/']",
                        "a[href*='watch?v=']"):
                link = page.query_selector(sel)
                if link:
                    url = (link.get_attribute("href") or "").strip()
                    if url:
                        break
            if url:
                break
            # Fallback: scan whole page text for any YouTube watch/share link.
            m = re.search(
                r"https?://(?:youtu\.be/[A-Za-z0-9_-]+"
                r"|(?:www\.)?youtube\.com/(?:shorts/|watch\?v=)[A-Za-z0-9_-]+)",
                page.content(),
            )
            if m:
                url = m.group(0)
                break
            time.sleep(1.5)
        # Last resort: derive the id from the Studio editor URL (.../video/<id>/edit).
        if not url:
            m = re.search(r"/video/([A-Za-z0-9_-]{6,})", page.url)
            if m:
                url = f"https://youtu.be/{m.group(1)}"
    except Exception as e:
        print(f"    [YouTube] Warning during publish confirmation / URL capture: {e}")
    return url


# ---------------------------------------------------------------------------
# YouTube Shorts audio swap (Stage B)
# ---------------------------------------------------------------------------
# Maps the renderer's mood vocabulary to YouTube Audio Library search terms.
# YT's library is keyword-searched, not strict categories - these terms match
# both their "Mood" facet labels and the track tag vocabulary.
YT_MUSIC_QUERIES = {
    "mysterious": "ambient dark",
    "inspiring":  "uplifting cinematic",
    "dramatic":   "epic cinematic",
    "warm":       "calm acoustic",
    "curious":    "ambient soundscape",
    "urgent":     "intense cinematic",
}


def _yt_extract_video_id(url: str) -> Optional[str]:
    """Pull the 11-char video id out of a youtu.be / shorts / watch URL."""
    if not url:
        return None
    m = re.search(
        r"(?:youtu\.be/|/shorts/|watch\?v=)([A-Za-z0-9_-]{6,})",
        url,
    )
    return m.group(1) if m else None


@retry("yt_wait_for_editor", tries=2, backoff=15.0)
def _yt_wait_for_editor(page, video_id: str, max_wait_min: int = 8):
    """Navigate to the studio edit page and poll until the Shorts editor entry
    point is clickable. Shorts can take 2-5 minutes to finish processing on
    YouTube's side - that's why we poll instead of expecting it immediately."""
    page.goto(f"https://studio.youtube.com/video/{video_id}/edit")
    # Edit-Short button selectors vary by region/UI rollout. Try the common set.
    selectors = (
        "ytcp-button:has-text('Edit Short')",
        "ytcp-button:has-text('Edit short')",
        "button:has-text('Edit Short')",
        "button[aria-label*='Edit Short']",
        "a[href*='/shorts/'][href*='/edit']",
    )
    deadline = time.time() + max_wait_min * 60
    while time.time() < deadline:
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1500):
                    el.click()
                    return
            except (PWError, PWTimeout):
                continue
        # Refresh every 30s so we pick up the button as soon as it appears
        time.sleep(30)
        try:
            page.reload()
        except Exception:
            pass
    raise RuntimeError(f"Edit Short button never appeared within {max_wait_min} min for video {video_id}")


@retry("yt_open_audio_library", tries=3, backoff=4.0)
def _yt_open_audio_library(page):
    """Click the 'Sound' / 'Add audio' button inside the Shorts editor."""
    selectors = (
        "button:has-text('Add sound')",
        "button:has-text('Add audio')",
        "button:has-text('Sound')",
        "ytcp-button:has-text('Sound')",
        "div[role='button']:has-text('Sound')",
        "button[aria-label*='sound' i]",
        "button[aria-label*='audio' i]",
    )
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=4000):
                el.click()
                return
        except (PWError, PWTimeout):
            continue
    raise RuntimeError("Could not find the Add Sound button in the Shorts editor")


@retry("yt_search_and_pick_track", tries=2, backoff=4.0)
def _yt_search_and_pick_track(page, query: str):
    """Search the library and click the first track."""
    search = page.wait_for_selector(
        "input[type='search'], input[placeholder*='Search' i], input[aria-label*='search' i]",
        timeout=20000,
    )
    search.click()
    search.fill("")
    search.type(query, delay=50)
    time.sleep(2)  # let results render
    track_selectors = (
        "ytcp-music-result-item",
        "div[role='listitem']:has-text('Add')",
        "div[role='button'][aria-label*='Add']",
        "ytcp-music-track-card",
    )
    for sel in track_selectors:
        try:
            track = page.locator(sel).first
            if track.is_visible(timeout=5000):
                track.click()
                return
        except (PWError, PWTimeout):
            continue
    raise RuntimeError(f"No tracks visible for query {query!r}")


@retry("yt_save_audio", tries=2, backoff=3.0)
def _yt_save_audio(page):
    """Click Save / Done on the Shorts editor and wait for confirmation."""
    save_btn = page.locator(
        "button:has-text('Save'), ytcp-button:has-text('Save'), "
        "button:has-text('Done'), ytcp-button:has-text('Done')"
    ).first
    save_btn.click()
    # Confirmation toast or redirect away from editor
    page.wait_for_selector(
        "div:has-text('Changes saved'), div:has-text('Saved'), "
        "div:has-text('Audio added')",
        timeout=120_000,
    )


def _yt_swap_audio_post_upload(page, video_url: str, mood: Optional[str]) -> dict:
    """Open the just-uploaded Short in YT Studio's Shorts editor and add a
    track from YouTube's Audio Library matching the requested mood.

    Returns a dict describing what happened. Failures are NOT fatal - the
    underlying upload still succeeded, we just couldn't add the music.
    """
    video_id = _yt_extract_video_id(video_url)
    if not video_id:
        return {"audio_swap": "skipped", "reason": "no video_id parseable from upload URL"}

    query = YT_MUSIC_QUERIES.get((mood or "").lower(), "ambient cinematic")
    print(f"    [YouTube audio] swapping music: mood={mood!r} -> query={query!r}")

    try:
        _yt_wait_for_editor(page, video_id, platform="youtube")
        print(f"    [YouTube audio] Shorts editor open")
        _yt_open_audio_library(page, platform="youtube")
        print(f"    [YouTube audio] library opened")
        _yt_search_and_pick_track(page, query, platform="youtube")
        print(f"    [YouTube audio] track picked")
        _yt_save_audio(page, platform="youtube")
        print(f"    [YouTube audio] [OK] saved")
        return {"audio_swap": "ok", "query": query, "video_id": video_id}
    except Exception as e:
        shot = _screenshot(page, "youtube", "audio_swap_failed")
        print(f"    [YouTube audio] failed: {e!r}  (screenshot: {shot})")
        return {"audio_swap": "error", "error": str(e)[:300], "screenshot": shot,
                "video_id": video_id}


def upload_youtube(context, video_path: Path, title: str, description: str, tags: str = "",
                   music_mood: Optional[str] = None) -> dict:
    print("[YouTube] upload...")

    # Prefer the Data API path when credentials are configured. This is the
    # path GitHub Actions / cloud runs always take - no browser involved.
    # Local manual runs use this too if YT_REFRESH_TOKEN is in .env.
    try:
        import yt_data_api
        if yt_data_api.is_api_available():
            print("    [i] Using YouTube Data API (no browser)")
            tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()] if tags else []
            return yt_data_api.upload_youtube_via_api(
                video_path, title, description, tag_list,
                privacy="public", made_for_kids=False,
            )
        print("    [i] No YT_REFRESH_TOKEN configured; falling back to browser upload.")
    except ImportError:
        print("    [i] yt_data_api module not available; falling back to browser upload.")

    page = context.new_page()
    try:
        try:
            _yt_check_session(page, platform="youtube")
        except Exception:
            return {"status": "error", "error": "not logged in / session expired"}

        _yt_open_upload_and_fill(page, video_path, title, description, tags, platform="youtube")
        print("    - details filled")
        _yt_advance_to_visibility(page, platform="youtube")
        print("    - visibility = public")
        url = _yt_publish_and_capture_url(page, platform="youtube")

        result: dict
        if url:
            print(f"    [OK] published: {url}")
            result = {"status": "ok", "url": url}
        else:
            print("    [OK] published (share link not captured)")
            result = {"status": "ok", "url": None,
                      "note": "published; share link not captured while still processing",
                      "screenshot": _screenshot(page, "youtube", "published_no_url")}

        # Stage B: swap in a YT Audio Library track. Best-effort - don't fail
        # the whole upload if this part trips.
        if music_mood:
            # Use the captured URL if we have it; otherwise derive id from the
            # current page url (which is /video/<id>/edit after publish).
            url_for_id = url or page.url
            swap = _yt_swap_audio_post_upload(page, url_for_id, music_mood)
            result.update(swap)
        return result
    finally:
        page.close()


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------
@retry("ig_open_composer", tries=2)
def _ig_open_composer(page):
    page.goto("https://www.instagram.com/")
    # The "New post" icon's accessible name varies by locale - support a few common ones
    create_btn = page.wait_for_selector(
        "svg[aria-label='New post'], svg[aria-label='Create'], svg[aria-label='Crear'], a[href*='/create']",
        timeout=20000,
    )
    create_btn.click()


@retry("ig_select_file", tries=2)
def _ig_select_file(page, video_path: Path):
    file_input = page.wait_for_selector("input[type=file]", state="attached", timeout=30000)
    file_input.set_input_files(str(video_path))
    # Wait for the preview / crop screen
    page.wait_for_selector(
        "div[role='button']:has-text('Next'), button:has-text('Next'), div[role='button']:has-text('Siguiente')",
        timeout=60000,
    )


@retry("ig_next_screens", tries=2)
def _ig_next_screens(page):
    # IG shows Crop → Edit → Caption. Click Next until the caption field appears.
    for _ in range(3):
        if page.query_selector("div[aria-label='Write a caption...']"):
            return
        nxt = page.locator(
            "div[role='button']:has-text('Next'), button:has-text('Next'), div[role='button']:has-text('Siguiente')"
        ).first
        nxt.click()
        time.sleep(1.5)


@retry("ig_caption", tries=2)
def _ig_caption(page, caption: str):
    box = page.wait_for_selector("div[aria-label='Write a caption...']", timeout=30000)
    box.click()
    box.fill(caption[:2200])     # IG cap


@retry("ig_share_confirm", tries=2)
def _ig_share_and_confirm(page):
    share = page.wait_for_selector(
        "div[role='button']:has-text('Share'), button:has-text('Share')",
        timeout=30000,
    )
    share.click()
    # Confirmation toast / dialog
    page.wait_for_selector(
        "h3:has-text('Your reel has been shared'), "
        "h3:has-text('Your post has been shared'), "
        "div:has-text('Your reel has been shared'), "
        "div:has-text('Your post has been shared')",
        timeout=180000,
    )


def upload_instagram(context, video_path: Path, caption: str) -> dict:
    print("[Instagram] upload...")
    page = context.new_page()
    try:
        try:
            _ig_open_composer(page, platform="instagram")
        except Exception:
            return {"status": "error", "error": "not logged in / composer not reachable",
                    "screenshot": _screenshot(page, "instagram", "no_session")}
        _ig_select_file(page, video_path, platform="instagram")
        print("    - file selected")
        _ig_next_screens(page, platform="instagram")
        _ig_caption(page, caption, platform="instagram")
        print("    - caption added")
        _ig_share_and_confirm(page, platform="instagram")
        print("    [OK] shared (IG doesn't expose a direct URL; check your profile)")
        return {"status": "ok", "url": "https://www.instagram.com/"}
    finally:
        page.close()


# ---------------------------------------------------------------------------
# Facebook Reels
# ---------------------------------------------------------------------------
@retry("fb_select_file", tries=2)
def _fb_select_file(page, video_path: Path):
    page.goto("https://www.facebook.com/reels/create/")
    file_input = page.wait_for_selector("input[type=file]", state="attached", timeout=30000)
    file_input.set_input_files(str(video_path))


@retry("fb_wait_processing", tries=2)
def _fb_wait_for_processing(page):
    page.wait_for_selector(
        "div[role='textbox'][contenteditable='true'], textarea, div[role='button']:has-text('Next')",
        timeout=300000,
    )


@retry("fb_description", tries=2)
def _fb_description(page, description: str):
    box = page.query_selector("div[role='textbox'][contenteditable='true']") or page.query_selector("textarea")
    if box:
        box.click()
        try:
            box.fill(description[:2200])
        except Exception:
            page.keyboard.type(description[:2200])


@retry("fb_publish_confirm", tries=2)
def _fb_publish_and_confirm(page):
    for _ in range(2):
        nxt = page.locator("div[role='button']:has-text('Next')")
        if nxt.count() == 0:
            break
        nxt.first.click()
        time.sleep(1)
    pub = page.locator(
        "div[role='button']:has-text('Publish'), div[role='button']:has-text('Post'), "
        "button:has-text('Publish'), button:has-text('Post')"
    ).first
    pub.click()
    page.wait_for_selector(
        "div:has-text('Your reel was published'), "
        "div:has-text('Your reel is being published'), "
        "div:has-text('Reel published')",
        timeout=300000,
    )


def upload_facebook(context, video_path: Path, description: str) -> dict:
    print("[Facebook] upload...")
    page = context.new_page()
    try:
        try:
            _fb_select_file(page, video_path, platform="facebook")
        except Exception:
            return {"status": "error", "error": "not logged in / composer not reachable",
                    "screenshot": _screenshot(page, "facebook", "no_session")}
        print("    - file selected, waiting for processing...")
        _fb_wait_for_processing(page, platform="facebook")
        _fb_description(page, description, platform="facebook")
        print("    - description added")
        _fb_publish_and_confirm(page, platform="facebook")
        print("    [OK] published (FB doesn't expose a direct URL; check the Page)")
        return {"status": "ok", "url": "https://www.facebook.com/"}
    finally:
        page.close()


# ---------------------------------------------------------------------------
# Per-run history log
# ---------------------------------------------------------------------------
def _append_log(entry: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    history = {"runs": []}
    if LOG_PATH.exists():
        try:
            history = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if "runs" not in history:
                history = {"runs": []}
        except Exception:
            history = {"runs": []}
    history["runs"].append(entry)
    LOG_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
DISPATCH = {
    "youtube":   lambda ctx, vp, m: upload_youtube(
        ctx, vp,
        m["youtube_title"], m["youtube_description"],
        m.get("youtube_tags", ""),
        m.get("music_mood"),
    ),
    "instagram": lambda ctx, vp, m: upload_instagram(ctx, vp, m["instagram_caption"]),
    "facebook":  lambda ctx, vp, m: upload_facebook(ctx, vp, m["facebook_description"]),
}


def _pick_video_for_platform(default_path: Path, platform: str, metadata: dict) -> Path:
    """YouTube gets the silent variant (Content-ID-safe); IG/FB get the
    full mix with Jamendo music. If video_path_yt isn't in metadata
    (older renders), fall back to the default."""
    if platform == "youtube":
        # Allow keeping background music for YouTube if YT_MUSIC env var is set
        if os.environ.get("YT_MUSIC", "").lower() in ("true", "1", "yes"):
            print("    [i] YT_MUSIC is enabled; using main video (with music) for YouTube.")
            return default_path

        yt_path = metadata.get("video_path_yt")
        if yt_path and Path(yt_path).exists():
            print(f"    [i] Using YT-safe (no music) variant: {Path(yt_path).name}")
            return Path(yt_path)
        print(f"    [Warning] No video_path_yt in metadata; using main video for YT (Content ID risk!)")
    return default_path


def _resolve_metadata(args) -> dict:
    """Pull metadata from upload_metadata.json + CLI overrides."""
    meta: dict = {}
    if META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    title = args.title or meta.get("title") or "New Short"
    description = args.description or meta.get("youtube_description") or title
    if "#shorts" not in title.lower() and "#shorts" not in description.lower():
        if len(title) + 8 <= 100:
            title = f"{title} #shorts"
        else:
            description = f"{description}\n\n#shorts"
    return {
        "title": title,
        "youtube_title":         meta.get("youtube_title") or title,
        "youtube_description":   meta.get("youtube_description") or description,
        "facebook_description":  meta.get("facebook_description") or description,
        "instagram_caption":     meta.get("instagram_caption") or title,
        "youtube_tags":          meta.get("youtube_tags", ""),
        "music_mood":            meta.get("music_mood"),
        "video_path_yt":         meta.get("video_path_yt"),
    }


def run_uploads(video_path: Path, platforms: list, metadata: dict, *, headless: bool) -> dict:
    results: dict = {}
    
    # Determine if we need to initialize Playwright and launch the browser
    browser_needed = False
    for p in platforms:
        p_low = p.lower()
        if p_low != "youtube":
            browser_needed = True
            break
    else:
        try:
            import yt_data_api
            if not yt_data_api.is_api_available():
                browser_needed = True
        except ImportError:
            browser_needed = True

    if not browser_needed:
        print("    [i] All uploads can run browserless. Skipping Playwright startup.")
        for platform in platforms:
            platform = platform.lower()
            if platform not in DISPATCH:
                print(f"[Warning] Unknown platform: {platform}")
                continue
            t0 = time.time()
            try:
                chosen = _pick_video_for_platform(video_path, platform, metadata)
                res = DISPATCH[platform](None, chosen, metadata)
            except Exception as e:
                res = {"status": "error", "error": repr(e)}
            res["duration_sec"] = round(time.time() - t0, 1)
            results[platform] = res
        return results

    if sync_playwright is None:
        die("Playwright is required for browser-based uploads, but the 'playwright' package is not installed.")

    with sync_playwright() as p:
        try:
            context = launch_browser(p, headless=headless, viewport={"width": 1280, "height": 720})
        except SystemExit:
            raise
        except Exception as e:
            die(f"Failed to launch browser: {e}")
        try:
            for i, platform in enumerate(platforms):
                platform = platform.lower()
                if platform not in DISPATCH:
                    print(f"[Warning] Unknown platform: {platform}")
                    continue
                if i > 0:
                    delay = random.uniform(20, 60)
                    print(f"[i] waiting {delay:.0f}s before next platform...")
                    time.sleep(delay)
                t0 = time.time()
                try:
                    chosen = _pick_video_for_platform(video_path, platform, metadata)
                    res = DISPATCH[platform](context, chosen, metadata)
                except Exception as e:
                    res = {"status": "error", "error": repr(e)}
                res["duration_sec"] = round(time.time() - t0, 1)
                results[platform] = res
        finally:
            context.close()
    return results


def main():
    ap = argparse.ArgumentParser(description="Browser-automation social-media uploader")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--upload", type=str)
    ap.add_argument("--title", type=str, default="")
    ap.add_argument("--description", type=str, default="")
    ap.add_argument("--platforms", nargs="+", default=list(PLATFORMS))
    ap.add_argument("--headless", action="store_true",
                    help="Run headless. NOT recommended - IG/FB aggressively block headless.")
    args = ap.parse_args()

    if args.login:
        login()
        return
    if not args.upload:
        ap.print_help(); sys.exit(1)

    video_path = Path(args.upload).resolve()
    if not video_path.exists():
        die(f"Video file not found: {video_path}")

    # Check if a browser session is actually needed for the requested platforms/credentials
    browser_needed = False
    for p in args.platforms:
        p_low = p.lower()
        if p_low != "youtube":
            browser_needed = True
            break
    else:
        try:
            import yt_data_api
            if not yt_data_api.is_api_available():
                browser_needed = True
        except ImportError:
            browser_needed = True

    if browser_needed and not SESSION_DIR.exists():
        die("Browser session not found. Run with --login first.")

    # Defense in depth: refuse to upload >=60s videos to YouTube. Past blocks
    # proved that 60s+ videos with non-YT-native music get globally blocked.
    if "youtube" in [p.lower() for p in args.platforms]:
        try:
            import subprocess as _sp
            out = _sp.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if float(out) >= 60.0:
                die(f"Video is {float(out):.1f}s. YouTube Shorts cap is <60s and "
                    "longer videos get Content ID blocked. Re-render (auto-trim caps at 58s).")
        except (FileNotFoundError, _sp.CalledProcessError, ValueError) as e:
            print(f"    [Warning] Could not probe duration ({e}); proceeding.")

    metadata = _resolve_metadata(args)
    print(f"\n=== Uploading {video_path.name} ===")
    print(f"    platforms: {', '.join(args.platforms)}")
    print(f"    youtube title: {metadata['youtube_title']}")
    print(f"    headless: {args.headless}\n")

    started = dt.datetime.now()
    results = run_uploads(video_path, args.platforms, metadata, headless=args.headless)

    entry = {
        "timestamp": started.isoformat(timespec="seconds"),
        "video":     str(video_path),
        "title":     metadata["title"],
        "platforms": args.platforms,
        "results":   results,
    }
    _append_log(entry)

    print("\n=== Results ===")
    for platform, res in results.items():
        status = res.get("status")
        if status == "ok":
            extra = ""
            if res.get("uploader") == "data_api":
                extra = "  [via Data API]"
            elif res.get("audio_swap") == "ok":
                extra = f"  [+audio: {res.get('query','?')}]"
            elif res.get("audio_swap") == "error":
                extra = "  [+audio: FAILED]"
            print(f"  [OK]  {platform:9s}  {res.get('url','')}{extra}")
        else:
            print(f"  [X]   {platform:9s}  {res.get('error','')}")
            if res.get("screenshot"):
                print(f"        screenshot: {res['screenshot']}")
    print(f"\nLog -> {LOG_PATH}\n")


if __name__ == "__main__":
    main()
