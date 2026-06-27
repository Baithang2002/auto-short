#!/usr/bin/env python3
"""
yt_data_api.py - YouTube upload via the official Data API v3.

Replaces the Playwright browser flow for YouTube. No browser, no session,
no UI fragility. Works headless from GitHub Actions or any cloud runner.

Required environment variables (set in .env locally or as GitHub Secrets in CI):
  YT_CLIENT_ID       - OAuth client ID (from oauth_client.json)
  YT_CLIENT_SECRET   - OAuth client secret (from oauth_client.json)
  YT_REFRESH_TOKEN   - Refresh token from get_youtube_refresh_token.py

The uploader picks this path automatically when YT_REFRESH_TOKEN is set,
falling back to Playwright if not.

Quota note: YouTube Data API gives 10,000 units/day free. A single video
upload costs 1,600 units. So you can upload ~6 videos/day on the free tier,
which covers daily Shorts with massive headroom.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


# Public YouTube category IDs:
#   24 = Entertainment
#   27 = Education
#   28 = Science & Technology
# "Wonders of the Nature" fits Education or Science & Technology cleanly.
DEFAULT_CATEGORY_ID = "27"


def _get_creds():
    """Build credentials from env vars. Returns None if not configured."""
    client_id     = os.environ.get("YT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YT_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("YT_REFRESH_TOKEN", "").strip()

    if not all([client_id, client_secret, refresh_token]):
        return None

    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("[yt_data_api] Missing dependency. Run:")
        print("    pip install google-auth google-auth-oauthlib google-api-python-client")
        return None

    return Credentials(
        token=None,                      # will be fetched via refresh on first call
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )


def is_api_available() -> bool:
    """True if YouTube Data API can be used (creds present + deps installed)."""
    return _get_creds() is not None


def upload_youtube_via_api(
    video_path: Path,
    title: str,
    description: str,
    tags: Optional[list] = None,
    *,
    category_id: str = DEFAULT_CATEGORY_ID,
    privacy: str = "public",
    made_for_kids: bool = False,
) -> dict:
    """
    Upload a video to YouTube via the Data API v3.

    Returns:
      {"status": "ok",    "url": "https://youtu.be/...", "video_id": "..."}
      {"status": "error", "error": "..."}

    Notes:
      - YouTube Shorts classification is automatic if the video is <60s vertical
        AND the title or description contains "#shorts".
      - privacy: "public", "unlisted", or "private".
      - tags: list of strings (no hashtags - those are description-only).
    """
    creds = _get_creds()
    if creds is None:
        return {"status": "error",
                "error": "YT API creds not configured. Set YT_CLIENT_ID/SECRET/REFRESH_TOKEN."}

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError
    except ImportError:
        return {"status": "error",
                "error": "Missing dep: pip install google-api-python-client"}

    video_path = Path(video_path)
    if not video_path.exists():
        return {"status": "error", "error": f"Video file not found: {video_path}"}

    # Strip hashtags from tags list - YT tags don't use # prefix
    clean_tags = []
    for t in (tags or []):
        t = str(t).lstrip("#").strip()
        if t:
            clean_tags.append(t)
    # YouTube's tags field has a 500-char total cap
    while clean_tags and sum(len(t) + 2 for t in clean_tags) > 480:
        clean_tags.pop()

    body = {
        "snippet": {
            "title":       title[:100],          # YT title cap is 100
            "description": description[:4900],   # YT description cap is 5000
            "tags":        clean_tags,
            "categoryId":  category_id,
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
            "embeddable": True,
            "license": "youtube",
        },
    }

    try:
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        media = MediaFileUpload(
            str(video_path),
            mimetype="video/*",
            chunksize=-1,           # upload in a single request (file is small)
            resumable=True,
        )
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=True,
        )

        print(f"    [YT API] Uploading {video_path.name} ({video_path.stat().st_size / 1024 / 1024:.1f} MB)...")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"    [YT API] Progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        if not video_id:
            return {"status": "error", "error": f"Upload returned no video_id: {response}"}

        url = f"https://youtu.be/{video_id}"
        print(f"    [YT API] OK -> {url}")
        return {
            "status":   "ok",
            "url":      url,
            "video_id": video_id,
            "uploader": "data_api",
        }
    except HttpError as e:
        return {"status": "error",
                "error": f"HTTP {e.resp.status}: {e._get_reason()}",
                "uploader": "data_api"}
    except Exception as e:
        return {"status": "error",
                "error": f"{type(e).__name__}: {e}",
                "uploader": "data_api"}


if __name__ == "__main__":
    # Standalone test: upload a video by path
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="Path to .mp4 to upload")
    ap.add_argument("--title", default="Test upload")
    ap.add_argument("--description", default="Test upload via YT Data API")
    ap.add_argument("--privacy", default="private",
                    choices=["public", "unlisted", "private"])
    args = ap.parse_args()
    result = upload_youtube_via_api(
        Path(args.video), args.title, args.description, privacy=args.privacy,
    )
    print(result)
    sys.exit(0 if result["status"] == "ok" else 1)
