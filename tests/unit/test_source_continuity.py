"""Unit tests for the Source Continuity Engine (PR #28)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.unit import _path  # noqa: F401
from autovideo.media.selection import CandidateScore, MediaSelectionResult, StockCandidate, VisualIntent
from autovideo.media.source_continuity import (
    SourceContinuityConfig,
    SourceContinuityEngine,
    SourceContinuityState,
    SourceIdentity,
    identity_from_candidate,
)
from autovideo.providers.stock.yt_clip import fetch_yt_clip


def _intent() -> VisualIntent:
    return VisualIntent(
        topic="deep sea creatures",
        narration="The anglerfish lurks in the abyss.",
        primary_subject="anglerfish",
        queries=("anglerfish",),
    )


def _candidate(provider, provider_id, source_url="", **kw):
    return StockCandidate(
        provider=provider,
        provider_id=provider_id,
        query=kw.pop("query", provider_id),
        title=kw.pop("title", provider_id),
        url=kw.pop("url", source_url),
        download_url=kw.pop("download_url", source_url),
        raw_metadata=kw.pop("raw_metadata", {}),
        **kw,
    )


def _result(candidate, score):
    return MediaSelectionResult(
        candidate,
        CandidateScore(score, breakdown={"_quality_gate_passed_value": True}),
        candidate_count=1,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_env_defaults():
    config = SourceContinuityConfig.from_env({})
    assert config.enabled is True
    assert config.minimum_continuity_score == 0.0
    assert config.preferred_dominant_ratio == 0.7
    assert config.maximum_unnecessary_switches == 2
    assert config.continuity_bonus == 0.4
    assert config.reuse_verified_source is True
    assert config.minimum_usage_before_lock == 2


def test_config_env_overrides():
    config = SourceContinuityConfig.from_env({
        "AUTO_VIDEO_SOURCE_CONTINUITY_ENABLED": "false",
        "AUTO_VIDEO_SOURCE_CONTINUITY_BONUS": "1.5",
        "AUTO_VIDEO_SOURCE_CONTINUITY_PREFERRED_RATIO": "2",  # clamped to 1.0
        "AUTO_VIDEO_SOURCE_CONTINUITY_MAX_SWITCHES": "5",
    })
    assert config.enabled is False
    assert config.continuity_bonus == 1.5
    assert config.preferred_dominant_ratio == 1.0
    assert config.maximum_unnecessary_switches == 5


# ---------------------------------------------------------------------------
# Source identity
# ---------------------------------------------------------------------------


def test_identity_youtube_normalization():
    cand = _candidate(
        "yt_clip", "abcDEF12345",
        source_url="https://www.youtube.com/shorts/abcDEF12345",
    )
    identity = identity_from_candidate(cand)
    assert identity.provider == "yt_clip"
    assert identity.source_key == "abcDEF12345"
    assert "youtube:abcDEF12345" in identity.identity_key


def test_identity_matches_same_video_across_url_forms():
    a = identity_from_candidate(_candidate(
        "yt_clip", "vid123456789", source_url="https://youtu.be/vid123456789"
    ))
    b = identity_from_candidate(_candidate(
        "yt_clip", "vid123456789", source_url="https://www.youtube.com/watch?v=vid123456789"
    ))
    assert a.matches(b)


def test_identity_does_not_match_different_video():
    a = identity_from_candidate(_candidate(
        "yt_clip", "aaaaaaaaaaa", source_url="https://youtu.be/aaaaaaaaaaa"
    ))
    b = identity_from_candidate(_candidate(
        "yt_clip", "bbbbbbbbbbb", source_url="https://youtu.be/bbbbbbbbbbb"
    ))
    assert not a.matches(b)


def test_identity_from_candidate_reads_attribution_and_collection():
    cand = _candidate(
        "pexels", "stock-123",
        raw_metadata={"attribution": "OceanFilms", "collection": "abyss reel"},
    )
    identity = identity_from_candidate(cand)
    assert identity.creator == "OceanFilms"
    assert identity.collection == "abyss reel"


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


def test_state_records_switches_and_dominant():
    state = SourceContinuityState()
    a = SourceIdentity(provider="pexels", source_key="a")
    b = SourceIdentity(provider="pixabay", source_key="b")
    state.record(0, a)
    state.record(1, a)
    state.record(2, b)
    state.record(3, a)
    counts = state.source_counts()
    assert counts == {a.identity_key: 3, b.identity_key: 1}
    assert state.dominant_identity().matches(a)
    assert len(state.switches) == 2  # scene 2 switched away, scene 3 switched back


# ---------------------------------------------------------------------------
# Engine -- prefer_continuity
# ---------------------------------------------------------------------------


def test_prefer_disabled_returns_original():
    engine = SourceContinuityEngine(SourceContinuityConfig(enabled=False))
    state = SourceContinuityState()
    cand = _candidate("pexels", "dom")
    result = _result(cand, 6.0)
    state.record(0, identity_from_candidate(cand))
    with patch("autovideo.media.source_continuity.select_best_candidate") as sbc:
        out, reason = engine.prefer_continuity(_intent(), [cand], result, state, scene_index=1)
    assert reason == "disabled"
    assert out is result
    sbc.assert_not_called()


def test_prefer_no_dominant_yet():
    engine = SourceContinuityEngine()
    state = SourceContinuityState()
    cand = _candidate("pexels", "x")
    result = _result(cand, 6.0)
    out, reason = engine.prefer_continuity(_intent(), [cand], result, state, scene_index=1)
    assert reason == "no dominant source yet"
    assert out is result


def test_prefer_reranks_to_dominant_source():
    state = SourceContinuityState()
    dom = _candidate("pexels", "dom", title="anglerfish")
    state.record(0, identity_from_candidate(dom))
    other = _candidate("pixabay", "other")
    other_result = _result(other, 7.0)
    alt_result = _result(dom, 6.8)
    engine = SourceContinuityEngine(SourceContinuityConfig(continuity_bonus=0.5))
    with patch(
        "autovideo.media.source_continuity.select_best_candidate",
        return_value=alt_result,
    ) as sbc:
        out, reason = engine.prefer_continuity(
            _intent(), [dom, other], other_result, state, scene_index=1
        )
    assert out is alt_result
    assert reason == "preferred dominant source for continuity"
    sbc.assert_called_once()


def test_prefer_accuracy_wins_over_continuity():
    state = SourceContinuityState()
    dom = _candidate("pexels", "dom")
    state.record(0, identity_from_candidate(dom))
    other = _candidate("pixabay", "other")
    other_result = _result(other, 9.0)
    alt_result = _result(dom, 3.0)  # far worse than independent best
    engine = SourceContinuityEngine(SourceContinuityConfig(continuity_bonus=0.5))
    with patch(
        "autovideo.media.source_continuity.select_best_candidate",
        return_value=alt_result,
    ) as sbc:
        out, reason = engine.prefer_continuity(
            _intent(), [dom, other], other_result, state, scene_index=1
        )
    assert out is other_result
    assert "accuracy wins" in reason
    sbc.assert_called_once()


def test_prefer_dominant_not_in_pool():
    state = SourceContinuityState()
    state.record(0, SourceIdentity(provider="noaa", source_key="z"))
    cand = _candidate("pixabay", "other")
    result = _result(cand, 7.0)
    engine = SourceContinuityEngine()
    with patch("autovideo.media.source_continuity.select_best_candidate") as sbc:
        out, reason = engine.prefer_continuity(_intent(), [cand], result, state, scene_index=1)
    assert out is result
    assert "not in candidate pool" in reason
    sbc.assert_not_called()


def test_prefer_already_dominant_source():
    state = SourceContinuityState()
    dom = _candidate("pexels", "dom")
    result = _result(dom, 6.0)
    state.record(0, identity_from_candidate(dom))
    engine = SourceContinuityEngine()
    out, reason = engine.prefer_continuity(_intent(), [dom], result, state, scene_index=1)
    assert out is result
    assert reason == "already dominant source"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_build_report():
    state = SourceContinuityState()
    a = SourceIdentity(provider="pexels", source_key="a")
    b = SourceIdentity(provider="pixabay", source_key="b")
    state.record(0, a)
    state.record(1, a)
    state.record(2, a)
    state.record(3, b)
    engine = SourceContinuityEngine()
    report = engine.build_report(state, total_scenes=4)
    assert report.continuity_score == 0.75
    assert len(report.scenes_per_source) == 2
    assert report.dominant_source["provider"] == "pexels"
    assert report.enabled is True
    assert isinstance(report.to_dict(), dict)


def test_build_report_counts_unnecessary_switches():
    state = SourceContinuityState()
    a = SourceIdentity(provider="pexels", source_key="a")
    state.record(0, a)
    state.record(
        1,
        SourceIdentity(provider="pixabay", source_key="b"),
        reason="provider switched without continuity benefit",
    )
    engine = SourceContinuityEngine()
    report = engine.build_report(state, total_scenes=2)
    assert len(report.unnecessary_switches) == 1
    assert len(report.source_switches) == 1


# ---------------------------------------------------------------------------
# Provider -- segment-offset continuation reuse
# ---------------------------------------------------------------------------


@patch("autovideo.providers.stock.yt_clip._yt_clip_vision_verifier_for", return_value=None)
@patch("autovideo.providers.stock.yt_clip.is_yt_dlp_available", return_value=True)
@patch("subprocess.run")
def test_fetch_yt_clip_continuation_reuses_used_source(mock_run, mock_avail, mock_vision, tmp_path):
    """Continuation lifts the used_set guard and slices a later segment."""
    captured_sections = {}

    def fake_run(cmd, *args, **kwargs):
        argv = list(cmd)
        if "--download-sections" in argv:
            captured_sections["cmd"] = argv
            out_idx = argv.index("-o") + 1
            Path(argv[out_idx]).write_bytes(b"raw")
            return MagicMock(returncode=0, stdout="", stderr="")
        if "-t" in argv and "libx264" in argv:
            (tmp_path / "broll_0.mp4").write_bytes(b"broll")
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    used_set = {"abcDEF12345"}
    out = fetch_yt_clip(
        ["anglerfish"],
        idx=0,
        output_dir=tmp_path,
        target_duration=5.0,
        used_set=used_set,
        expected_entity=None,
        source_url="https://www.youtube.com/watch?v=abcDEF12345",
        segment_offset_sec=39.0,
    )
    assert out is not None and out.exists()
    # The same video id is in used_set, yet continuation still proceeded.
    sections = " ".join(captured_sections.get("cmd", []))
    assert "39.0-" in sections