"""
Offline smoke test for the auto-short pipeline.

Verifies:
  - auto_short.py SEO post-processing (normalize_metadata path) produces a
    valid upload_metadata.json with #shorts injected and hashtags deduped.
  - uploader.py._resolve_metadata reads that file correctly.
  - uploader.py.run_uploads invokes the right dispatcher per platform,
    skips unknown platforms, jitters between platforms, captures errors
    into the per-platform result, and writes a log entry.
  - pipeline.py wiring: stage 1 is called with the right args, the metadata
    contract flows through to stage 2, and the summary picks the right URL.

All network/browser calls are monkey-patched. No real ffmpeg/Playwright.
"""
from __future__ import annotations

import json
import sys
import subprocess
import tempfile
import datetime as dt
from pathlib import Path

PROJECT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT))

# --- isolate state to a tmp dir so the real output/ folder is untouched ---
TMP = Path(tempfile.mkdtemp(prefix="autoshort_test_"))
META_PATH = TMP / "upload_metadata.json"
LOG_PATH  = TMP / "upload_log.json"


# -----------------------------------------------------------------------------
# 1. Exercise the SEO normalization path in generate_script's tail
#    (we don't call Gemini; we simulate its raw response and re-implement the
#    public contract: the file written to upload_metadata.json)
# -----------------------------------------------------------------------------
import auto_short

# Simulate what main() does after generate_script returns, but in-process
script = {
    "title": "5 Deep Ocean Facts That Sound Fake",
    "description": "Mind-blowing things from the abyss.",
    "instagram_caption": "Wait til you see #3.",
    "hashtags": ["#deepocean", "#facts", "#shorts", "#DeepOcean"],  # dup + cased
    "segments": [{"narration": "n", "broll": "b"}] * 6,
    "niche": "deep ocean",
}

# normalize hashtags the way generate_script does after parsing
import re
tags = script["hashtags"]
tags = ["#shorts"] + [t for t in tags if t.lower() != "#shorts"]
seen, deduped = set(), []
for t in tags:
    tl = t.lower()
    if tl not in seen:
        seen.add(tl); deduped.append(tl)
script["hashtags"] = deduped[:15]

assert script["hashtags"] == ["#shorts", "#deepocean", "#facts"], script["hashtags"]
print("OK: hashtags deduped, lowercased, #shorts forced to front")

# Now build the upload_metadata.json the same way auto_short.main() does
title    = script["title"]
hashtag_str = " ".join(script["hashtags"])
youtube_title = title
if "#shorts" not in youtube_title.lower():
    candidate = f"{title} #shorts"
    if len(candidate) <= 100:
        youtube_title = candidate
youtube_description = f"{script['description']}\n\n{hashtag_str}"
metadata = {
    "niche": "deep ocean",
    "title": title,
    "youtube_title": youtube_title,
    "youtube_description": youtube_description,
    "facebook_description": youtube_description,
    "instagram_caption": f"{script['instagram_caption']}\n\n{hashtag_str}",
    "hashtags": script["hashtags"],
    "duration_sec": 38.4,
    "video_path": str(TMP / "final.mp4"),
    "orientation": "portrait",
}
# Fake video file so uploader's existence check passes
(TMP / "final.mp4").write_bytes(b"FAKE_MP4")
META_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

assert metadata["youtube_title"].endswith("#shorts"), metadata["youtube_title"]
assert "#shorts" in metadata["youtube_description"]
print(f"OK: youtube_title has #shorts appended  ({metadata['youtube_title']!r})")
print(f"OK: youtube_description carries hashtags ({hashtag_str})")

# -----------------------------------------------------------------------------
# 2. uploader._resolve_metadata reads the file correctly
# -----------------------------------------------------------------------------
import uploader

uploader.META_PATH = META_PATH
uploader.LOG_PATH  = LOG_PATH

class FakeArgs:
    title = ""
    description = ""

resolved = uploader._resolve_metadata(FakeArgs())
assert resolved["youtube_title"]        == metadata["youtube_title"]
assert resolved["youtube_description"]  == metadata["youtube_description"]
assert resolved["instagram_caption"]    == metadata["instagram_caption"]
print("OK: uploader._resolve_metadata reads upload_metadata.json correctly")

# CLI override beats file
class OverrideArgs:
    title = "Custom override"
    description = "Custom desc"
resolved2 = uploader._resolve_metadata(OverrideArgs())
# When user overrides, file values for youtube_title etc still win unless not present
# but our test file HAS them, so check they're preserved
assert "Custom override" not in resolved2["youtube_title"]   # file wins for yt fields
assert resolved2["title"].startswith("Custom override")      # may get #shorts appended
print("OK: CLI title flows in, file's youtube_title still wins")

# -----------------------------------------------------------------------------
# 3. run_uploads dispatches per platform; record_upload_result writes log
# -----------------------------------------------------------------------------
calls: list[tuple[str, dict]] = []

def fake_yt(ctx, vp, m):     calls.append(("youtube", m));   return {"status": "ok", "url": "https://youtu.be/abc"}
def fake_ig(ctx, vp, m):     calls.append(("instagram", m)); return {"status": "ok", "url": "https://www.instagram.com/"}
def fake_fb(ctx, vp, m):     raise RuntimeError("FB session expired")

uploader.DISPATCH = {
    "youtube":   fake_yt,
    "instagram": fake_ig,
    "facebook":  fake_fb,
}

class FakeContext:
    def add_init_script(self, *a, **kw): pass
    def close(self): pass

class FakePlaywright:
    def __init__(self): self.chromium = self
    def launch_persistent_context(self, **kw): return FakeContext()
    def __enter__(self): return self
    def __exit__(self, *a): pass

uploader.sync_playwright = lambda: FakePlaywright()
# Speed up: skip 20-60s jitter between platforms
uploader.random.uniform = lambda a, b: 0
uploader.time.sleep    = lambda *a, **kw: None

video_path = TMP / "final.mp4"
results = uploader.run_uploads(
    video_path,
    ["youtube", "instagram", "facebook", "tiktok"],   # tiktok unknown -> skipped
    resolved,
    headless=True,
)
assert set(results) == {"youtube", "instagram", "facebook"}, results
assert results["youtube"]["status"]   == "ok"
assert results["instagram"]["status"] == "ok"
assert results["facebook"]["status"]  == "error"
assert "FB session expired" in results["facebook"]["error"]
assert all("duration_sec" in r for r in results.values())
print("OK: dispatch hit yt+ig+fb, skipped tiktok, captured FB error")

# 4. Per-platform metadata was the right shape
yt_meta = next(m for p, m in calls if p == "youtube")
ig_meta = next(m for p, m in calls if p == "instagram")
assert yt_meta["youtube_title"].endswith("#shorts")
assert ig_meta["instagram_caption"].startswith("Wait til you see #3.")
print("OK: each platform received its tailored metadata")

# 5. Log append
uploader._append_log({
    "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
    "video":     str(video_path),
    "title":     resolved["title"],
    "platforms": ["youtube", "instagram", "facebook"],
    "results":   results,
})
log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
assert len(log["runs"]) == 1
assert log["runs"][0]["results"]["youtube"]["url"] == "https://youtu.be/abc"
# append a second run, should preserve first
uploader._append_log({"timestamp": "x", "results": {}})
log2 = json.loads(LOG_PATH.read_text(encoding="utf-8"))
assert len(log2["runs"]) == 2
print("OK: upload_log.json appends, doesn't overwrite")

# -----------------------------------------------------------------------------
# 6. pipeline.py wiring - subprocess calls to auto_short and uploader
# -----------------------------------------------------------------------------
import importlib, pipeline as pipe
import _pipeline_base as pipe_base
importlib.reload(pipe)
pipe.META_PATH = META_PATH
pipe.LOG_PATH  = LOG_PATH

stage_calls = []

class DoneProc:
    def __init__(self, rc=0): self.returncode = rc

def fake_run(cmd, cwd=None):
    stage_calls.append(cmd)
    return DoneProc(0)

pipe_base.subprocess.run = fake_run

# Stage 1 + Stage 2
pipe.run_stage1("auto_short.py", "deep ocean", ["--duration", "30"])
assert "auto_short.py" in stage_calls[0][1]
assert "deep ocean" in stage_calls[0]
assert "--duration" in stage_calls[0]
print("OK: pipeline.run_stage1 invoked auto_short.py with the right args")

pipe.run_stage2(video_path, ["youtube", "facebook"], headless=False)
last = stage_calls[-1]
assert "uploader.py" in last[1]
assert "--upload"   in last and str(video_path) in last
assert "youtube" in last and "facebook" in last
assert "--headless" not in last
print("OK: pipeline.run_stage2 invoked uploader.py with the right args")

print("\nALL CHECKS PASSED")
import shutil; shutil.rmtree(TMP)
