"""Ctrl+C exits 130 and leaves no partial output, on both encode paths."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

from videocompress import cli, encode
from videocompress.hwpipeline import Tier

ENCODERS = ["libx265", "hevc_nvenc"]


def cancel_encode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, encoder: str, writes: bool) -> int:
    """Run the CLI, press Ctrl+C while FFmpeg runs, return the exit code."""
    source, output = tmp_path / "in.mp4", tmp_path / "out.mp4"
    source.write_bytes(b"x")

    def interrupted(*args: object, **kwargs: object) -> NoReturn:
        if writes:
            output.write_bytes(b"partial")
        raise KeyboardInterrupt

    finished = SimpleNamespace(poll=lambda: 0)
    monkeypatch.setattr(encode, "get_video_info", lambda p: (10.0, 500 * 1024 * 1024, 60.0, 128, 1920, 1080, "aac"))
    monkeypatch.setattr(encode, "get_hdr_source", lambda p: None)
    monkeypatch.setattr(encode, "find_missing_runtime_library", lambda p: None)
    monkeypatch.setattr(encode, "select_best_encoder", lambda c: (encoder, [(encoder, "selected")]))
    monkeypatch.setattr(encode, "probe_pipeline_tier", lambda *a, **k: Tier.LEGACY)
    monkeypatch.setattr(encode, "get_smart_split_point", lambda p, d: 5.0)
    monkeypatch.setattr(encode, "check_bsf_available", lambda name: True)
    monkeypatch.setattr(encode.subprocess, "Popen", lambda *a, **k: finished)
    monkeypatch.setattr(encode.subprocess, "run", interrupted)  # GPU: cancelled while stitching
    monkeypatch.setattr(encode, "run_dual_progress", lambda *a, **k: True)
    monkeypatch.setattr(encode, "run_single_progress", interrupted)  # CPU: cancelled while encoding
    monkeypatch.setattr(sys, "argv", ["videocompress", str(source), str(output)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize("encoder", ENCODERS)
def test_cancel_exits_130_and_removes_partial_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, encoder: str
) -> None:
    assert cancel_encode(monkeypatch, tmp_path, encoder, writes=True) == 130
    assert not (tmp_path / "out.mp4").exists()


@pytest.mark.parametrize("encoder", ENCODERS)
def test_cancel_keeps_a_file_ffmpeg_never_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, encoder: str
) -> None:
    (tmp_path / "out.mp4").write_bytes(b"from an earlier run")
    assert cancel_encode(monkeypatch, tmp_path, encoder, writes=False) == 130
    assert (tmp_path / "out.mp4").read_bytes() == b"from an earlier run"
