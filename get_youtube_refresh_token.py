#!/usr/bin/env python3
"""
get_youtube_refresh_token.py - ONE-TIME local script to get a YouTube refresh token.

Run this ONCE on your laptop. It opens a browser, you log into your YouTube account
and approve, and it prints a refresh token. Save that token as a GitHub Secret
(YT_REFRESH_TOKEN) so GitHub Actions can upload to YouTube without your laptop.

Prereqs:
  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client

Setup:
  Save your OAuth Desktop credentials JSON as 'oauth_client.json' in this folder.
  (Downloaded from console.cloud.google.com/apis/credentials)

Run:
  python get_youtube_refresh_token.py

What it prints (save these as GitHub Secrets):
  YT_CLIENT_ID
  YT_CLIENT_SECRET
  YT_REFRESH_TOKEN
"""
import json
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("[X] Missing dependency. Run:")
    print("    pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
    sys.exit(1)

# YouTube upload + read access. Scope must match what the upload code uses.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]

CLIENT_JSON = Path(__file__).parent / "oauth_client.json"

if not CLIENT_JSON.exists():
    print(f"[X] {CLIENT_JSON.name} not found.")
    print("    Download OAuth client JSON from console.cloud.google.com/apis/credentials")
    print(f"    and save it here as: {CLIENT_JSON}")
    sys.exit(1)

print("[i] Opening browser to authorize. Log in with your channel's Google account.")
print("[i] If you see 'Google hasn't verified this app', click Advanced -> Go to ... (unsafe).")
print("    That's expected because the app is in Testing mode - only YOUR account can use it.")

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_JSON), SCOPES)
# access_type=offline + prompt=consent is required to actually get a refresh_token back
creds = flow.run_local_server(
    port=0,
    access_type="offline",
    prompt="consent",
    open_browser=True,
)

# Read client id + secret from the JSON for printing
client_data = json.loads(CLIENT_JSON.read_text())
installed = client_data.get("installed") or client_data.get("web") or {}

print("\n" + "=" * 70)
print("SUCCESS. Paste these into GitHub Secrets:")
print("  Settings -> Secrets and variables -> Actions -> New repository secret")
print("=" * 70)
print(f"\nName: YT_CLIENT_ID")
print(f"Value: {installed.get('client_id', '')}")
print(f"\nName: YT_CLIENT_SECRET")
print(f"Value: {installed.get('client_secret', '')}")
print(f"\nName: YT_REFRESH_TOKEN")
print(f"Value: {creds.refresh_token}")
print("\n" + "=" * 70)
print("Also writing these to .youtube_credentials.json (gitignored) for local testing.")
print("=" * 70)

out = {
    "client_id":     installed.get("client_id", ""),
    "client_secret": installed.get("client_secret", ""),
    "refresh_token": creds.refresh_token,
    "token_uri":     "https://oauth2.googleapis.com/token",
}
local_creds = Path(__file__).parent / ".youtube_credentials.json"
local_creds.write_text(json.dumps(out, indent=2))
print(f"\nLocal copy: {local_creds}")
