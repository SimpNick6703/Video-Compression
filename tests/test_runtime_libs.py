"""Tests for the missing system library check run before probing."""

import subprocess
from types import SimpleNamespace

import pytest

from videocompress import core

LOADER_ERROR = (
    "ffprobe: error while loading shared libraries: libva.so.2: "
    "cannot open shared object file: No such file or directory\n"
)


def test_reports_missing_library_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=127, stderr=LOADER_ERROR))
    message = core.find_missing_runtime_library("ffprobe")
    assert message is not None
    assert "libva.so.2" in message
    assert "apt install libva2 libva-drm2 libdrm2" in message


@pytest.mark.parametrize("result", [SimpleNamespace(returncode=0, stderr=""), SimpleNamespace(returncode=1, stderr="other failure")])
def test_no_message_when_ffprobe_starts(monkeypatch: pytest.MonkeyPatch, result: SimpleNamespace) -> None:
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: result)
    assert core.find_missing_runtime_library("ffprobe") is None


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("ffprobe", 10), OSError("missing")])
def test_no_message_when_ffprobe_cannot_run(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(core.subprocess, "run", boom)
    assert core.find_missing_runtime_library("ffprobe") is None


def test_compress_video_stops_with_library_message(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    from videocompress import encode

    source = tmp_path / "in.mp4"  # type: ignore[operator]
    source.write_bytes(b"x")
    monkeypatch.setattr(encode, "find_missing_runtime_library", lambda exe: "missing libva.so.2")
    ok, message = encode.compress_video(str(source), None, 20.0, "hevc")
    assert (ok, message) == (False, "missing libva.so.2")
