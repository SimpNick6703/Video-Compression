"""Tests for the segment stitch command and bitstream filter detection."""

import subprocess
from types import SimpleNamespace

import pytest

from videocompress import core


def test_concat_keeps_auto_convert_when_annexb_bsf_present() -> None:
    cmd = core.build_concat_cmd("ffmpeg", "list.txt", "out.mp4", annexb_available=True)
    assert "-auto_convert" not in cmd
    assert cmd == [
        "ffmpeg", "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
        "-c", "copy", "-movflags", "+faststart", "-y", "out.mp4",
    ]


def test_concat_disables_auto_convert_without_annexb_bsf() -> None:
    cmd = core.build_concat_cmd("ffmpeg", "list.txt", "out.mp4", annexb_available=False)
    assert cmd[cmd.index("-auto_convert") + 1] == "0"
    assert cmd.index("-auto_convert") < cmd.index("-i")


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("Bitstream filters:\naac_adtstoasc\nh264_mp4toannexb\nnull\n", True),
        ("Bitstream filters:\naac_adtstoasc\nextract_extradata\nnull\n", False),
        ("", False),
    ],
)
def test_check_bsf_available_parses_listing(
    monkeypatch: pytest.MonkeyPatch, listing: str, expected: bool
) -> None:
    core.check_bsf_available.cache_clear()
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listing))
    assert core.check_bsf_available("h264_mp4toannexb") is expected
    core.check_bsf_available.cache_clear()


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("ffmpeg", 10), OSError("missing")])
def test_check_bsf_available_handles_errors(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    core.check_bsf_available.cache_clear()

    def boom(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(core.subprocess, "run", boom)
    assert core.check_bsf_available("h264_mp4toannexb") is False
    core.check_bsf_available.cache_clear()
