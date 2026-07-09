"""License validation for selected music tracks.

Every track chosen by the planner passes through :func:`validate_license`
before it may reach the renderer. Validation is deterministic and purely
data-driven: it compares the track's structured license metadata against the
operator's configured :class:`LicensePolicy`.
"""

from __future__ import annotations

from dataclasses import dataclass

from autovideo.providers.music.base import MusicTrack


@dataclass(frozen=True)
class LicensePolicy:
    """Operator policy that selected tracks must satisfy."""

    require_commercial_use: bool = True
    allow_attribution: bool = True
    require_verified: bool = True


@dataclass(frozen=True)
class LicenseValidationResult:
    """Outcome of validating one track against the policy."""

    ok: bool
    reasons: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return "verified" if self.ok else "; ".join(self.reasons)


def validate_license(track: MusicTrack, policy: LicensePolicy) -> LicenseValidationResult:
    """Validate a track's license metadata against the operator policy.

    Silence always validates: rendering without music carries no license risk.
    """
    if track.is_silence:
        return LicenseValidationResult(ok=True)

    reasons: list[str] = []
    license_info = track.license
    if policy.require_verified and not license_info.verified:
        reasons.append(f"{track.provider}:{track.provider_track_id} has no verified license metadata")
    if policy.require_commercial_use and not license_info.commercial_use:
        reasons.append(
            f"license {license_info.license or 'unknown'} does not permit commercial use"
        )
    if not policy.allow_attribution and license_info.attribution_required:
        reasons.append(
            f"license {license_info.license or 'unknown'} requires attribution but attribution is disallowed"
        )
    return LicenseValidationResult(ok=not reasons, reasons=tuple(reasons))
