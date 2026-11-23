"""HDR sources: hardware HEVC keeps HDR at the source bit depth, everything else is tone-mapped to SDR."""

import json

import pytest

from videocompress import core
from videocompress.core import probe_pipeline_tier
from videocompress.core import is_high_bit_depth
from videocompress.hwpipeline import BACKENDS, TONEMAP_FILTERS, HdrSource, Tier, build_video_args

PQ = HdrSource("smpte2084", ten_bit=True)
KEEP_PQ = (
    "-profile:v", "main10",
    "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
)
SDR_TAGS = ("-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709")


def test_nvenc_hevc_zero_copy_keeps_10_bit() -> None:
    args = build_video_args(BACKENDS["nvenc"], Tier.ZERO_COPY, 2160, 1440, 60.0, 60.0, "hevc", hdr=PQ)
    assert args.filters == ("scale_cuda=-2:1440:interp_algo=lanczos:format=p010",)
    assert args.post_input == KEEP_PQ


def test_upload_tier_uploads_p010() -> None:
    args = build_video_args(BACKENDS["vaapi"], Tier.UPLOAD, 2160, 1440, 60.0, 60.0, "hevc", hdr=PQ)
    assert args.filters == ("format=p010", "hwupload", "scale_vaapi=w=-2:h=1440:format=p010")


def test_legacy_nvenc_keeps_10_bit() -> None:
    args = build_video_args(BACKENDS["nvenc"], Tier.LEGACY, 2160, 1440, 60.0, 60.0, "hevc", hdr=PQ)
    assert args.filters == ("scale_cuda=-2:1440:interp_algo=lanczos:format=p010",)
    assert args.post_input == KEEP_PQ


def test_hlg_transfer_is_kept() -> None:
    args = build_video_args(BACKENDS["qsv"], Tier.ZERO_COPY, 2160, 2160, 60.0, 60.0, "hevc", hdr=HdrSource("arib-std-b67", ten_bit=True))
    assert args.post_input[-3] == "arib-std-b67"


def test_h264_on_nvenc_is_tone_mapped_on_cpu() -> None:
    args = build_video_args(BACKENDS["nvenc"], Tier.ZERO_COPY, 2160, 720, 60.0, 30.0, "h264", hdr=PQ)
    assert args.pre_input == ("-hwaccel", "cuda")
    assert args.filters == ("fps=30.0", "scale=-2:720:flags=lanczos", *TONEMAP_FILTERS, "format=nv12")
    assert args.post_input == SDR_TAGS


def test_h264_on_vaapi_uploads_after_tone_mapping() -> None:
    args = build_video_args(BACKENDS["vaapi"], Tier.ZERO_COPY, 2160, 2160, 60.0, 60.0, "h264", "/dev/dri/renderD128", PQ)
    assert args.filters[-2:] == ("format=nv12", "hwupload")
    assert args.pre_input[0] == "-init_hw_device"


def test_cpu_hevc_is_tone_mapped() -> None:
    args = build_video_args(None, Tier.LEGACY, 2160, 2160, 60.0, 60.0, "hevc", hdr=PQ)
    assert args.filters == (*TONEMAP_FILTERS, "format=yuv420p")


def test_sdr_is_unchanged() -> None:
    sdr = build_video_args(BACKENDS["nvenc"], Tier.ZERO_COPY, 1080, 720, 60.0, 60.0, "hevc")
    assert sdr.filters == ("scale_cuda=-2:720:interp_algo=lanczos:format=nv12",)
    assert sdr.post_input == ()


def test_tone_mapping_skips_tier_probes() -> None:
    tier = probe_pipeline_tier("ffmpeg-not-run", "in.mp4", "h264_nvenc", "h264", 2160, 720, 60.0, 60.0, None, PQ)
    assert tier is Tier.LEGACY


def test_8_bit_hdr_stays_8_bit_with_hdr_tags() -> None:
    hdr8 = HdrSource("smpte2084", ten_bit=False)
    args = build_video_args(BACKENDS["nvenc"], Tier.ZERO_COPY, 2160, 1440, 60.0, 60.0, "hevc", hdr=hdr8)
    assert args.filters == ("scale_cuda=-2:1440:interp_algo=lanczos:format=nv12",)
    assert args.post_input == ("-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc")


def linearize_step(hdr: HdrSource) -> str:
    args = build_video_args(BACKENDS["nvenc"], Tier.LEGACY, 2160, 2160, 60.0, 60.0, "h264", hdr=hdr)
    return args.filters[0]


def test_hlg_applies_the_system_gamma_on_luminance() -> None:
    # zscale's default agamma=1 applies it per channel, which shifts saturated colours.
    assert linearize_step(HdrSource("arib-std-b67", ten_bit=True)) == "zscale=t=linear:npl=100:agamma=0"


def test_pq_keeps_the_fast_gamma() -> None:
    # agamma=0 changes nothing for PQ and made 4K tone mapping 2.5x slower.
    assert linearize_step(PQ) == "zscale=t=linear:npl=100"


@pytest.mark.parametrize(
    ("has_primaries", "has_matrix", "extra"),
    [
        (False, True, ":pin=bt2020:p=bt2020"),
        (True, False, ":min=bt2020nc"),
        (False, False, ":pin=bt2020:p=bt2020:min=bt2020nc"),
    ],
)
def test_missing_color_tags_are_assumed_bt2020(has_primaries: bool, has_matrix: bool, extra: str) -> None:
    pq = HdrSource("smpte2084", True, has_primaries=has_primaries, has_matrix=has_matrix)
    hlg = pq._replace(transfer="arib-std-b67")
    assert linearize_step(pq) == f"zscale=t=linear:npl=100{extra}"
    assert linearize_step(hlg) == f"zscale=t=linear:npl=100:agamma=0{extra}"


def test_cpu_and_vaapi_get_the_same_hlg_chain() -> None:
    hlg = HdrSource("arib-std-b67", ten_bit=True)
    for backend in (None, BACKENDS["vaapi"]):
        args = build_video_args(backend, Tier.LEGACY, 2160, 2160, 60.0, 60.0, "h264", "/dev/dri/renderD128", hdr=hlg)
        assert "zscale=t=linear:npl=100:agamma=0" in args.filters


@pytest.mark.parametrize(
    ("tags", "has_primaries", "has_matrix"),
    [
        ({"color_primaries": "bt2020", "color_space": "bt2020nc"}, True, True),
        ({"color_primaries": "smpte432", "color_space": "bt709"}, True, True),
        ({}, False, False),
        ({"color_primaries": "unknown", "color_space": "bt2020nc"}, False, True),
        ({"color_primaries": "bt2020", "color_space": "reserved"}, True, False),
    ],
)
def test_hdr_source_notes_missing_color_tags(
    monkeypatch: pytest.MonkeyPatch, tags: dict, has_primaries: bool, has_matrix: bool
) -> None:
    stream = {"color_transfer": "arib-std-b67", "pix_fmt": "yuv420p10le", **tags}

    def fake_probe(cmd: list, **kwargs: object) -> str:
        if "frame=color_transfer,color_primaries,color_space" in cmd:
            return json.dumps({"frames": [stream]})
        return json.dumps({"streams": [stream]})

    monkeypatch.setattr(core.subprocess, "check_output", fake_probe)
    hdr = core.get_hdr_source("in.mp4")
    assert hdr is not None
    assert (hdr.has_primaries, hdr.has_matrix) == (has_primaries, has_matrix)


def test_container_only_tags_count_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # ffprobe's stream entry shows the container's tags, but zscale only sees the frames'.
    stream = {
        "color_transfer": "arib-std-b67", "color_primaries": "bt2020", "color_space": "bt2020nc",
        "pix_fmt": "yuv420p10le",
    }
    frame = {"color_transfer": "unknown"}

    def fake_probe(cmd: list, **kwargs: object) -> str:
        if "frame=color_transfer,color_primaries,color_space" in cmd:
            return json.dumps({"frames": [frame]})
        return json.dumps({"streams": [stream]})

    monkeypatch.setattr(core.subprocess, "check_output", fake_probe)
    hdr = core.get_hdr_source("in.mp4")
    assert hdr is not None
    assert (hdr.has_transfer, hdr.has_primaries, hdr.has_matrix) == (False, False, False)
    assert linearize_step(hdr) == "zscale=t=linear:npl=100:agamma=0:tin=arib-std-b67:pin=bt2020:p=bt2020:min=bt2020nc"


def test_stream_tags_are_used_when_no_frame_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = {"color_transfer": "smpte2084", "color_primaries": "bt2020", "color_space": "bt2020nc"}

    def fake_probe(cmd: list, **kwargs: object) -> str:
        if "frame=color_transfer,color_primaries,color_space" in cmd:
            return json.dumps({"frames": []})
        return json.dumps({"streams": [stream]})

    monkeypatch.setattr(core.subprocess, "check_output", fake_probe)
    assert core.get_hdr_source("in.mp4") == HdrSource("smpte2084", ten_bit=False)


def test_pq_with_untagged_transfer_is_assumed_pq() -> None:
    assert linearize_step(PQ._replace(has_transfer=False)) == "zscale=t=linear:npl=100:tin=smpte2084"


def test_bit_depth_from_pix_fmt() -> None:
    assert is_high_bit_depth("yuv420p10le") and is_high_bit_depth("p010le") and is_high_bit_depth("yuv420p12le")
    assert not is_high_bit_depth("yuv420p") and not is_high_bit_depth("yuvj420p") and not is_high_bit_depth("nv12")
