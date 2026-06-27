"""
review_dashboard.py  -  Local web dashboard for reviewing generated videos.

Run:
    pip install flask
    python review_dashboard.py
    open http://127.0.0.1:5000

Pending videos appear with inline playback, editable title/description/
hashtags, per-platform schedule pickers, and Approve / Reject buttons.
Approved items are moved to videos/approved/ with a schedule attached,
ready for upload_worker.py.
"""

from __future__ import annotations

import os
import datetime as dt
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, abort, flash,
)

import video_queue as q

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

ROOT = Path(__file__).parent.resolve()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    tab = request.args.get("tab", "pending")
    lists = {
        "pending":  q.list_pending(),
        "approved": q.list_approved(),
        "rejected": q.list_rejected(),
        "uploaded": q.list_uploaded(),
    }
    counts = {k: len(v) for k, v in lists.items()}
    # Suggested default schedule: tomorrow 9am
    default_when = (dt.datetime.now() + dt.timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M")
    return render_template(
        "index.html",
        tab=tab,
        items=lists.get(tab, []),
        counts=counts,
        platforms=q.PLATFORMS,
        default_when=default_when,
    )


@app.route("/video/<stage>/<video_id>/<path:filename>")
def video_file(stage: str, video_id: str, filename: str):
    """Stream the actual video file out of the videos/<stage>/<id>/ folder."""
    if stage not in ("pending", "approved", "rejected", "uploaded"):
        abort(404)
    folder = ROOT / "videos" / stage / video_id
    if not folder.exists():
        abort(404)
    return send_from_directory(folder, filename)


@app.route("/approve/<video_id>", methods=["POST"])
def approve(video_id):
    edits = {
        "title":             request.form.get("title", "").strip(),
        "description":       request.form.get("description", "").strip(),
        "instagram_caption": request.form.get("instagram_caption", "").strip(),
        "hashtags":          request.form.get("hashtags", "").strip(),
    }
    schedule = {p: request.form.get(f"schedule_{p}", "").strip() for p in q.PLATFORMS}
    try:
        q.approve(video_id, edits, schedule)
        flash(f"Approved {video_id}", "ok")
    except FileNotFoundError as e:
        flash(str(e), "err")
    return redirect(url_for("index", tab="pending"))


@app.route("/reject/<video_id>", methods=["POST"])
def reject(video_id):
    reason = request.form.get("reason", "").strip()
    try:
        q.reject(video_id, reason)
        flash(f"Rejected {video_id}", "ok")
    except FileNotFoundError as e:
        flash(str(e), "err")
    return redirect(url_for("index", tab="pending"))


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
@app.template_filter("join_tags")
def join_tags(tags):
    if not tags:
        return ""
    if isinstance(tags, str):
        return tags
    return " ".join(tags)


@app.template_filter("fmt_dt")
def fmt_dt(s):
    if not s:
        return ""
    try:
        return dt.datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


if __name__ == "__main__":
    q.ensure_dirs()
    app.run(host="127.0.0.1", port=5000, debug=True)
