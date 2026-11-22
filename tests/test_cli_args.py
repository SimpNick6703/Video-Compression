"""Only names with a file extension are paths, whatever exists in the current folder."""

import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from videocompress import cli

Parsed = Tuple[str, Optional[str], float, str]


@pytest.fixture(autouse=True)
def video_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run in a folder holding a video and items named like sizes."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input.mp4").write_bytes(b"x")
    for folder in ("20", "20.5", "100", "1.5e3", "Holiday", "clips.mp4"):
        (tmp_path / folder).mkdir()
    for file in ("50", "noext"):
        (tmp_path / file).write_bytes(b"x")
    return tmp_path


def parse(monkeypatch: pytest.MonkeyPatch, args: List[str]) -> Parsed:
    """Run the CLI with encoding stubbed out and return what it would encode."""
    calls: List[Parsed] = []

    def fake_compress(input_path: str, output_path: Optional[str], target_size_mb: float, codec_type: str) -> Tuple[bool, str]:
        calls.append((input_path, output_path, target_size_mb, codec_type))
        return True, "out.mp4"

    monkeypatch.setattr(cli, "compress_video", fake_compress)
    monkeypatch.setattr(sys, "argv", ["videocompress", *args])
    cli.main()
    return calls[0]


@pytest.mark.parametrize("args, size", [
    (["input.mp4", "20"], 20.0),
    (["input.mp4", "50"], 50.0),
    (["input.mp4", "20.5"], 20.5),
    (["input.mp4", "1.5e3"], 1500.0),
    (["hevc", "100", "input.mp4", "20", "hevc"], 20.0),  # right-click menu
    (["hevc", "20", "input.mp4"], 20.0),  # preset executable
])
def test_a_size_is_never_a_path(monkeypatch: pytest.MonkeyPatch, args: List[str], size: float) -> None:
    assert parse(monkeypatch, args) == ("input.mp4", None, size, "hevc")


@pytest.mark.parametrize("name", ["20.mp4", "out.mp4", "clip.3gp", "clip.3g2", "OUT.MP4", "v1.2 final.mp4"])
def test_a_name_with_an_extension_is_a_path(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    assert parse(monkeypatch, ["input.mp4", name]) == ("input.mp4", name, 100.0, "hevc")


def test_an_input_named_like_a_size_works_with_its_extension(monkeypatch: pytest.MonkeyPatch, video_folder: Path) -> None:
    (video_folder / "20.mp4").write_bytes(b"x")
    assert parse(monkeypatch, ["20.mp4", "50"]) == ("20.mp4", None, 50.0, "hevc")


@pytest.mark.parametrize("name", ["Holiday", "clips.mp4", "noext", "./20", "newname", "0", "-5", "", "--help"])
def test_an_incomplete_file_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    with pytest.raises(SystemExit) as exc:
        parse(monkeypatch, ["input.mp4", name])
    assert exc.value.code == 1
    assert "complete file name" in capsys.readouterr().err
