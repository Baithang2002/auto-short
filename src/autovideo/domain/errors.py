"""Domain-specific exceptions.

These exceptions are intentionally independent from provider implementations so
workflow code can make decisions without knowing which external service failed.
"""


class AutoVideoError(Exception):
    """Base class for expected application errors."""


class ValidationError(AutoVideoError):
    """Raised when a domain object or metadata payload is invalid."""


class TimelineValidationError(ValidationError):
    """Raised when a timeline violates domain invariants."""


class ProviderError(AutoVideoError):
    """Base class for external provider failures."""


class RateLimitError(ProviderError):
    """Provider rate limit or quota exhaustion."""


class AuthenticationError(ProviderError):
    """Invalid credentials or expired auth token."""


class ProviderUnavailableError(ProviderError):
    """Provider is temporarily unavailable."""


class InvalidProviderResponseError(ProviderError):
    """Provider response could not be parsed or validated."""


class AssetError(AutoVideoError):
    """Base class for asset acquisition and storage failures."""


class AssetNotFoundError(AssetError):
    """An expected local or remote asset was not found."""


class LicenseError(AssetError):
    """An asset license is missing or not usable."""


class DownloadError(AssetError):
    """An asset download failed."""


class RenderError(AutoVideoError):
    """Base class for rendering failures."""


class RendererValidationError(RenderError):
    """Timeline or render-profile input is not compatible with a renderer."""


class FFmpegError(RenderError):
    """FFmpeg or ffprobe failed."""


class CaptionSyncError(RenderError):
    """Captions could not be synchronized to the rendered timeline."""


class UploadError(AutoVideoError):
    """Base class for upload failures."""


class PlatformAuthError(UploadError):
    """Upload platform authentication failed."""


class UploadRejectedError(UploadError):
    """Platform rejected an upload package."""
