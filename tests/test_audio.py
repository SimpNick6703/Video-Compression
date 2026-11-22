"""Audio: copy by default, re-encode lossless or over-budget tracks and give the saved bits to the video."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from videocompress import core, encode


@pytest.fixture
def ffmpeg_codecs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "check_audio_encoder_available", lambda encoder_name="aac": True)
    monkeypatch.setattr(core, "check_audio_decoder_available", lambda codec: codec not in ("alac", "mp2"))


@pytest.mark.parametrize(
    ("codec", "kbps", "target_mb", "duration", "expected"),
    [
        ("aac", 192, 110, 183, (192, False)),  # 4% of the target
        ("aac", 192, 20, 183, (128, True)),  # 21% of the target
        ("ac3", 384, 110, 183, (384, False)),  # 8%: surround survives
        ("aac", 160, 8, 183, (160, False)),  # over 10%, but too little to save
        ("pcm_s16le", 1536, 1024, 60, (128, True)),  # lossless, however small a share
        ("flac", 900, 1024, 60, (128, True)),
        ("alac", 1461, 5, 24, (1461, False)),  # no decoder: copy at the real bitrate
        ("mp2", 384, 5, 24, (384, False)),
        ("", 0, 100, 60, (0, False)),
    ],
)
def test_plan_audio(
    ffmpeg_codecs: None, codec: str, kbps: int, target_mb: float, duration: float, expected: tuple
) -> None:
    assert core.plan_audio(codec, kbps, target_mb, duration) == expected


def test_plan_audio_copies_without_an_aac_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "check_audio_encoder_available", lambda encoder_name="aac": False)
    assert core.plan_audio("flac", 900, 5, 60) == (900, False)


@pytest.mark.parametrize(
    ("codec", "expected"),
    [("flac", True), ("aac", True), ("alac", False), ("pcm_s16be", False), ("missing", False)],
)
def test_check_audio_decoder_available_parses_listing(
    monkeypatch: pytest.MonkeyPatch, codec: str, expected: bool
) -> None:
    listing = " DEA.L. aac    AAC\n D.AI.S flac   FLAC\n ..AI.S alac   ALAC\n ..AI.S pcm_s16be  PCM\n"
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listing))
    assert core.check_audio_decoder_available(codec) is expected


def test_estimate_bitrate_from_packets() -> None:
    packets = [(i * 0.02, 500) for i in range(100)]
    assert core.estimate_bitrate(packets) == pytest.approx(200_000, abs=1)
    assert core.estimate_bitrate(list(reversed(packets))) == pytest.approx(200_000, abs=1)


@pytest.mark.parametrize("packets", [[], [(0.0, 500)], [(1.0, 500), (1.0, 500)]])
def test_estimate_bitrate_needs_a_time_span(packets: list) -> None:
    assert core.estimate_bitrate(packets) == 0


@pytest.mark.parametrize(
    ("packets", "expected_kbps"),
    [
        ([{"pts_time": str(i / 8), "size": "16000"} for i in range(9)], 1024),
        ([], 128),
    ],
)
def test_video_info_measures_audio_without_reported_bitrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, packets: list, expected_kbps: int
) -> None:
    source = tmp_path / "in.mkv"
    source.write_bytes(b"x")

    def fake_probe(cmd: List[str], **kwargs: object) -> str:
        if "packet=pts_time,size" in cmd:
            return json.dumps({"packets": packets})
        if "a:0" in cmd:
            return json.dumps({"streams": [{"codec_name": "flac"}]})
        return json.dumps(
            {"streams": [{"width": 1920, "height": 1080, "avg_frame_rate": "60/1"}], "format": {"duration": "10"}}
        )

    monkeypatch.setattr(core.subprocess, "check_output", fake_probe)
    info = core.get_video_info(str(source))
    assert info is not None
    assert info[3] == expected_kbps
    assert info[6] == "flac"


def test_compress_video_budgets_the_output_audio(
    ffmpeg_codecs: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, output = tmp_path / "in.mov", tmp_path / "out.mp4"
    source.write_bytes(b"x")
    seen: List[tuple] = []

    def fake_split(**kwargs: object) -> tuple:
        seen.append((kwargs["audio_kbps"], kwargs["reencode_audio"]))
        output.write_bytes(b"encoded")
        return True, ""

    info = (10.0, 500 * 1024 * 1024, 60.0, 1536, 1920, 1080, "pcm_s16le")
    monkeypatch.setattr(encode, "find_missing_runtime_library", lambda exe: None)
    monkeypatch.setattr(encode, "get_video_info", lambda p: info)
    monkeypatch.setattr(encode, "get_hdr_source", lambda p: None)
    monkeypatch.setattr(encode, "select_best_encoder", lambda c: ("hevc_nvenc", [("hevc_nvenc", "selected")]))
    monkeypatch.setattr(encode, "probe_pipeline_tier", lambda *a, **k: core.Tier.LEGACY)
    monkeypatch.setattr(encode, "get_smart_split_point", lambda p, d: 5.0)
    monkeypatch.setattr(encode, "_encode_hw_split", fake_split)

    assert encode.compress_video(str(source), str(output), 20.0, "hevc")[0]
    assert seen == [(128, True)]
