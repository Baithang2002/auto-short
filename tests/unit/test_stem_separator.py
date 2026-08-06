"""Unit tests for StemSeparator AI audio stem separation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.unit import _path  # noqa: F401
from autovideo.audio.stem_separator import StemSeparator



def test_stem_separator_availability():
    """Verify availability check handles presence and missing executables."""
    with patch("shutil.which", return_value="/usr/bin/demucs"):
        assert StemSeparator.is_available() is True

    with patch("shutil.which", return_value=None):
        with patch.dict("sys.modules", {"demucs": None}):
            assert StemSeparator.is_available() is False


def test_separate_stems_raises_file_not_found():
    """Verify separation raises FileNotFoundError when input media is missing."""
    separator = StemSeparator()
    non_existent = Path("non_existent_video_12345.mp4")
    with pytest.raises(FileNotFoundError):
        separator.separate_stems(non_existent, "output/test_stems")


@patch("subprocess.run")
def test_separate_stems_command_construction(mock_run, tmp_path):
    """Verify correct argument construction when invoking Demucs."""
    dummy_input = tmp_path / "sample.mp4"
    dummy_input.write_bytes(b"dummy mp4 content")

    dummy_stems_folder = tmp_path / "stems" / "htdemucs" / "sample"
    dummy_stems_folder.mkdir(parents=True, exist_ok=True)
    (dummy_stems_folder / "other.wav").write_bytes(b"dummy ambient wav")
    (dummy_stems_folder / "vocals.wav").write_bytes(b"dummy vocals wav")

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    separator = StemSeparator(model_name="htdemucs")
    stems = separator.separate_stems(dummy_input, tmp_path / "stems")

    assert mock_run.called
    cmd_args = mock_run.call_args[0][0]
    assert "-n" in cmd_args
    assert "htdemucs" in cmd_args
    assert str(dummy_input) in cmd_args

    assert "ambient" in stems
    assert stems["ambient"] == dummy_stems_folder / "other.wav"
    assert "vocals" in stems
    assert stems["vocals"] == dummy_stems_folder / "vocals.wav"


@patch("subprocess.run")
def test_extract_ambient_audio(mock_run, tmp_path):
    """Verify extract_ambient_audio correctly copies the other.wav stem."""
    dummy_input = tmp_path / "nature.mp4"
    dummy_input.write_bytes(b"dummy nature clip")

    dummy_stems_folder = tmp_path / "work_dir" / "htdemucs" / "nature"
    dummy_stems_folder.mkdir(parents=True, exist_ok=True)
    ambient_file = dummy_stems_folder / "other.wav"
    ambient_file.write_bytes(b"nature river audio content")

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    separator = StemSeparator()
    target_output = tmp_path / "extracted_ambient.wav"
    result_path = separator.extract_ambient_audio(dummy_input, target_output, temp_dir=tmp_path / "work_dir")

    assert result_path == target_output
    assert target_output.exists()
    assert target_output.read_bytes() == b"nature river audio content"
