"""Caption domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaptionCue:
    start_sec: float
    end_sec: float
    text: str
    emphasis: tuple[str, ...] = ()
