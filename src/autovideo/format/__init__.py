"""Format-shaped configuration for the video production pipeline.

A FormatProfile answers 'What is the target shape of the finished video?'
(duration bounds, scene target, narration tempo). It is intentionally
separate from the environment RenderProfile (dev/prod/test), which owns
codec, resolution, and provider-priority concerns. The two profiles
compose at runtime in the pipeline entry point.
"""

from .profiles import FormatProfile, FormatProfileName
from .registry import (
    get_default_format_profile,
    get_format_profile,
)

__all__ = [
    "FormatProfile",
    "FormatProfileName",
    "get_default_format_profile",
    "get_format_profile",
]
