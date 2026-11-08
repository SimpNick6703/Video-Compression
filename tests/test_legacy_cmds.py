"""Full legacy commands that `build_single_pass_cmd` must keep producing.

Each expected command was recorded from the v1.1.x builder before the
hardware-pipeline refactor. Together they cover NVENC `-r` placement, the
VA-API CPU-scale upload chain, `-hwaccel auto`, CPU encoding, trimming and all
three audio paths (`-an`, copy, AAC re-encode).
"""

from typing import Dict, List, Optional

import pytest

from videocompress import core
from videocompress.hwpipeline import Tier

RATE_TAIL = ["-maxrate:v", "2000k", "-bufsize:v", "4000k", "-map", "0:v:0"]
OUTPUT_TAIL = ["-movflags", "+faststart", "-loglevel", "error", "-progress", "pipe:2", "-nostats", "out.mp4"]
NVENC_RC = [
    "-rc:v", "vbr", "-preset", "p5", "-multipass", "fullres",
    "-rc-lookahead", "32", "-spatial-aq", "1", "-temporal-aq", "1",
]
AMF_RC = ["-usage", "transcoding", "-quality", "quality", "-rc", "vbr_peak", "-preanalysis", "1", "-vbaq", "1"]

CASES: Dict[str, Dict[str, object]] = {
    "h264_nvenc scaled, fps drop, trimmed, audio copy": dict(
        args=dict(encoder="h264_nvenc", codec_type="h264", tgt_h=720, tgt_fps=30.0, start=10.0, end=20.0, audio_kbps=128),
        expected=[
            "ffmpeg", "-y", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
            "-ss", "10.0", "-to", "20.0", "-i", "in.mp4", "-avoid_negative_ts", "make_zero",
            "-r", "30.0", "-vf", "scale_cuda=-2:720:interp_algo=lanczos:format=yuv420p",
            "-c:v", "h264_nvenc", "-b:v", "2000k", *NVENC_RC, "-tag:v", "avc1",
            *RATE_TAIL, "-map", "0:a:0?", "-c:a", "copy", *OUTPUT_TAIL,
        ],
    ),
    "hevc_vaapi scaled, fps drop, trimmed, audio re-encode": dict(
        args=dict(encoder="hevc_vaapi", codec_type="hevc", tgt_h=720, tgt_fps=30.0, start=10.0, end=20.0, audio_kbps=192),
        expected=[
            "ffmpeg", "-y", "-init_hw_device", "vaapi=va", "-filter_hw_device", "va",
            "-ss", "10.0", "-to", "20.0", "-i", "in.mp4", "-avoid_negative_ts", "make_zero",
            "-vf", "fps=30.0,scale=-2:720:flags=lanczos,format=nv12,hwupload",
            "-c:v", "hevc_vaapi", "-b:v", "2000k", "-tag:v", "hvc1",
            *RATE_TAIL, "-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k", "-ac", "2", *OUTPUT_TAIL,
        ],
    ),
    "hevc_amf scaled, audio copy": dict(
        args=dict(encoder="hevc_amf", codec_type="hevc", tgt_h=720, tgt_fps=60.0, start=None, end=None, audio_kbps=128),
        expected=[
            "ffmpeg", "-y", "-hwaccel", "auto", "-i", "in.mp4", "-vf", "scale=-2:720:flags=lanczos",
            "-c:v", "hevc_amf", "-b:v", "2000k", *AMF_RC, "-tag:v", "hvc1",
            *RATE_TAIL, "-map", "0:a:0?", "-c:a", "copy", *OUTPUT_TAIL,
        ],
    ),
    "libx264 native, silent": dict(
        args=dict(encoder="libx264", codec_type="h264", tgt_h=1080, tgt_fps=60.0, start=None, end=None, audio_kbps=0),
        expected=[
            "ffmpeg", "-y", "-i", "in.mp4", "-vf", "format=yuv420p",
            "-c:v", "libx264", "-b:v", "2000k", "-preset", "medium", "-tag:v", "avc1",
            *RATE_TAIL, "-an", *OUTPUT_TAIL,
        ],
    ),
}


def build(args: Dict[str, object], tier: Optional[Tier] = None) -> List[str]:
    """Build a command for a 1080p60 source at 2000k with the given case args."""
    extra = {} if tier is None else {"tier": tier}
    return core.build_single_pass_cmd(
        ffmpeg_exe="ffmpeg", input_path="in.mp4", bitrate_k=2000, src_fps=60.0, src_h=1080,
        output_path="out.mp4", **args, **extra,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def stub_audio_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the AAC availability check deterministic."""
    monkeypatch.setattr(core, "check_audio_encoder_available", lambda encoder_name="aac": True)


@pytest.mark.parametrize("name", list(CASES))
def test_default_tier_is_legacy_command(name: str) -> None:
    case = CASES[name]
    assert build(case["args"]) == case["expected"]  # type: ignore[arg-type]


@pytest.mark.parametrize("name", list(CASES))
def test_explicit_legacy_tier_is_legacy_command(name: str) -> None:
    case = CASES[name]
    assert build(case["args"], Tier.LEGACY) == case["expected"]  # type: ignore[arg-type]
