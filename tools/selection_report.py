"""Write a compact media selection report from a timeline JSON file."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _scene_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("items")
    if isinstance(items, list):
        return [
            item
            for item in items
            if isinstance(item, dict) and item.get("track_type") == "video"
        ]
    for key in ("scenes", "cuts"):
        items = data.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _media_metadata(scene: dict[str, Any]) -> dict[str, Any]:
    properties = scene.get("properties")
    if isinstance(properties, dict) and (
        "selection" in properties or "provider" in properties or "portrait_score" in properties
    ):
        return properties
    media = scene.get("media") or scene.get("media_asset") or {}
    if isinstance(media, dict):
        metadata = media.get("metadata") or {}
        if isinstance(metadata, dict):
            return metadata
    metadata = scene.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def write_report(timeline_path: Path, report_path: Path) -> None:
    data = json.loads(timeline_path.read_text(encoding="utf-8"))
    lines = [f"Selection Report: {timeline_path}", ""]
    for index, scene in enumerate(_scene_items(data), start=1):
        metadata = _media_metadata(scene)
        selection = metadata.get("selection") if isinstance(metadata.get("selection"), dict) else {}
        scene_type = selection.get("scene_type") or metadata.get("scene_type") or "unknown"
        importance = selection.get("scene_importance") or metadata.get("scene_importance") or "supporting"
        provider = selection.get("provider") or metadata.get("provider") or "unknown"
        confidence = selection.get("confidence_level") or selection.get("confidence") or "unknown"
        portrait = selection.get("portrait_score") or metadata.get("portrait_score")
        relevance = selection.get("relevance_score") or metadata.get("relevance_score")
        reason = selection.get("selection_reason") or selection.get("rejection_reason") or ""
        lines.append(f"Scene {index} ({str(importance).upper()})")
        lines.append(f"Selected Provider: {provider}")
        lines.append(f"Scene Type: {scene_type}")
        lines.append(f"Portrait Score: {portrait}/10")
        lines.append(f"Relevance Score: {relevance}/10")
        lines.append(f"Confidence: {str(confidence).upper()}")
        if reason:
            lines.append(f"Reason: {reason}")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: selection_report.py <timeline.json> <report.txt>")
    write_report(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
