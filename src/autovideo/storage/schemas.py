"""Lightweight JSON metadata validation for the filesystem queue."""

from __future__ import annotations

from autovideo.domain.errors import ValidationError


REQUIRED_METADATA_FIELDS = ("id", "title", "video_path", "status")


def validate_upload_metadata_dict(data: dict) -> None:
    missing = [field for field in REQUIRED_METADATA_FIELDS if not data.get(field)]
    if missing:
        raise ValidationError(f"Missing required metadata fields: {', '.join(missing)}")
