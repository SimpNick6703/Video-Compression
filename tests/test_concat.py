"""Tests for the segment stitch command and bitstream filter detection."""

import subprocess
from types import SimpleNamespace

import pytest

from videocompress import core


def test_concat_keeps_auto_convert_when_annexb_bsf_present() -> None:
    cmd = core.build_concat_cmd("ffmpeg", "list.txt", "out.mp4", annexb_available=True, source_path="in.mp4", audio_kbps=0)
    assert "-auto_convert" not in cmd
    assert cmd == [
        "ffmpeg", "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
        "-i", "in.mp4", "-map", "0:v:0", "-an",
        "-c:v", "copy", "-movflags", "+faststart", "-y", "out.mp4",
    ]


def test_concat_disables_auto_convert_without_annexb_bsf() -> None:
    cmd = core.build_concat_cmd("ffmpeg", "list.txt", "out.mp4", annexb_available=False, source_path="in.mp4", audio_kbps=0)
    assert cmd[cmd.index("-auto_convert") + 1] == "0"
    assert cmd.index("-auto_convert") < cmd.index("-i")


@pytest.mark.parametrize(
    ("audio_kbps", "reencode", "expected_audio"),
    [
        (128, False, ["-map", "1:a:0?", "-c:a", "copy"]),
        (128, True, ["-map", "1:a:0?", "-c:a", "aac", "-b:a", "128k", "-ac", "2"]),
        (320, False, ["-map", "1:a:0?", "-c:a", "copy"]),
    ],
)
def test_concat_takes_audio_from_source(audio_kbps: int, reencode: bool, expected_audio: list[str]) -> None:
    # Split segments are video-only; audio comes from the source in one piece so no gap opens at the split.
    cmd = core.build_concat_cmd(
        "ffmpeg", "list.txt", "out.mp4", annexb_available=True, source_path="in.mp4",
        audio_kbps=audio_kbps, reencode_audio=reencode,
    )
    start = cmd.index("0:v:0") + 1
    assert cmd[start:start + len(expected_audio)] == expected_audio
    assert cmd[cmd.index("-c:v") + 1] == "copy"


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


def test_concat_list_pins_first_segment_duration() -> None:
    # The first segment's reported duration includes its start offset, which can differ
    # from the second segment's (a long-held first frame gave 0.47 s vs 0.02 s), so the
    # list states the true length: the split time.
    listing = core.build_concat_list("/tmp/a/p1.mp4", "/tmp/a/p2.mp4", 90.549689)
    assert listing == "file '/tmp/a/p1.mp4'\nduration 90.549689\nfile '/tmp/a/p2.mp4'\n"


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        (" A....D aac                  AAC (Advanced Audio Coding)\n", True),  # FFmpeg 9 flag layout
        (" A..... aac                  AAC (Advanced Audio Coding)\n", True),
        (" A....D aac_at               AAC (AudioToolbox)\n", False),
        (" V....D libx264              H.264\n", False),
        ("", False),
    ],
)
def test_check_audio_encoder_available_parses_listing(
    monkeypatch: pytest.MonkeyPatch, listing: str, expected: bool
) -> None:
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listing))
    assert core.check_audio_encoder_available("aac") is expected
