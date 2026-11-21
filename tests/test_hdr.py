"""HDR sources: hardware HEVC keeps 10-bit HDR, everything else is tone-mapped to SDR."""

from videocompress.core import probe_pipeline_tier
from videocompress.hwpipeline import BACKENDS, TONEMAP_FILTERS, Tier, build_video_args

PQ = "smpte2084"
KEEP_PQ = (
    "-profile:v", "main10",
    "-color_primaries", "bt2020", "-color_trc", PQ, "-colorspace", "bt2020nc",
)
SDR_TAGS = ("-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709")


def test_nvenc_hevc_zero_copy_keeps_10_bit() -> None:
    args = build_video_args(BACKENDS["nvenc"], Tier.ZERO_COPY, 2160, 1440, 60.0, 60.0, "hevc", hdr_transfer=PQ)
    assert args.filters == ("scale_cuda=-2:1440:interp_algo=lanczos:format=p010",)
    assert args.post_input == KEEP_PQ


def test_upload_tier_uploads_p010() -> None:
    args = build_video_args(BACKENDS["vaapi"], Tier.UPLOAD, 2160, 1440, 60.0, 60.0, "hevc", hdr_transfer=PQ)
    assert args.filters == ("format=p010", "hwupload", "scale_vaapi=w=-2:h=1440:format=p010")


def test_legacy_nvenc_keeps_10_bit() -> None:
    args = build_video_args(BACKENDS["nvenc"], Tier.LEGACY, 2160, 1440, 60.0, 60.0, "hevc", hdr_transfer=PQ)
    assert args.filters == ("scale_cuda=-2:1440:interp_algo=lanczos:format=p010",)
    assert args.post_input == KEEP_PQ


def test_hlg_transfer_is_kept() -> None:
    args = build_video_args(BACKENDS["qsv"], Tier.ZERO_COPY, 2160, 2160, 60.0, 60.0, "hevc", hdr_transfer="arib-std-b67")
    assert args.post_input[-3] == "arib-std-b67"


def test_h264_on_nvenc_is_tone_mapped_on_cpu() -> None:
    args = build_video_args(BACKENDS["nvenc"], Tier.ZERO_COPY, 2160, 720, 60.0, 30.0, "h264", hdr_transfer=PQ)
    assert args.pre_input == ("-hwaccel", "cuda")
    assert args.filters == ("fps=30.0", "scale=-2:720:flags=lanczos", *TONEMAP_FILTERS, "format=nv12")
    assert args.post_input == SDR_TAGS


def test_h264_on_vaapi_uploads_after_tone_mapping() -> None:
    args = build_video_args(BACKENDS["vaapi"], Tier.ZERO_COPY, 2160, 2160, 60.0, 60.0, "h264", "/dev/dri/renderD128", PQ)
    assert args.filters[-2:] == ("format=nv12", "hwupload")
    assert args.pre_input[0] == "-init_hw_device"


def test_cpu_hevc_is_tone_mapped() -> None:
    args = build_video_args(None, Tier.LEGACY, 2160, 2160, 60.0, 60.0, "hevc", hdr_transfer=PQ)
    assert args.filters == (*TONEMAP_FILTERS, "format=yuv420p")


def test_sdr_is_unchanged() -> None:
    sdr = build_video_args(BACKENDS["nvenc"], Tier.ZERO_COPY, 1080, 720, 60.0, 60.0, "hevc")
    assert sdr.filters == ("scale_cuda=-2:720:interp_algo=lanczos:format=nv12",)
    assert sdr.post_input == ()


def test_tone_mapping_skips_tier_probes() -> None:
    tier = probe_pipeline_tier("ffmpeg-not-run", "in.mp4", "h264_nvenc", "h264", 2160, 720, 60.0, 60.0, None, PQ)
    assert tier is Tier.LEGACY
