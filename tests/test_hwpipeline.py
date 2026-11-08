"""Exact-string tests for the pure hardware pipeline builder."""

from typing import Dict, Tuple

import pytest

from videocompress.hwpipeline import (
    BACKENDS,
    Tier,
    VideoArgs,
    backend_for_encoder,
    build_video_args,
)

VAAPI_NODE = "/dev/dri/renderD128"

CUDA_DEV = ("-init_hw_device", "cuda=cu", "-filter_hw_device", "cu")
CUDA_DECODE = ("-hwaccel", "cuda", "-hwaccel_device", "cu", "-hwaccel_output_format", "cuda")
VAAPI_DEV = ("-init_hw_device", f"vaapi=va:{VAAPI_NODE}", "-filter_hw_device", "va")
VAAPI_DECODE = ("-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi")
AMF_DEV = ("-init_hw_device", "d3d11va=dx:,vendor_id=0x1002", "-filter_hw_device", "dx")
D3D11_DECODE = ("-hwaccel", "d3d11va", "-hwaccel_device", "dx", "-hwaccel_output_format", "d3d11")
QSV_DEV = (
    "-init_hw_device", "d3d11va=dx:,vendor_id=0x8086",
    "-init_hw_device", "qsv=qs@dx", "-filter_hw_device", "qs",
)
VT_DEV = ("-init_hw_device", "videotoolbox=vt", "-filter_hw_device", "vt")
VT_DECODE = ("-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld")

Key = Tuple[str, Tier]

# 1080p60 -> 720p30, hevc
SCALED_FPS: Dict[Key, VideoArgs] = {
    ("hevc_nvenc", Tier.ZERO_COPY): VideoArgs(
        CUDA_DEV + CUDA_DECODE,
        ("scale_cuda=-2:720:interp_algo=lanczos:format=nv12",), ("-r", "30.0")),
    ("hevc_nvenc", Tier.UPLOAD): VideoArgs(
        CUDA_DEV,
        ("format=nv12", "hwupload_cuda", "scale_cuda=-2:720:interp_algo=lanczos:format=nv12"),
        ("-r", "30.0")),
    ("hevc_vaapi", Tier.ZERO_COPY): VideoArgs(
        VAAPI_DEV + VAAPI_DECODE, ("fps=30.0", "scale_vaapi=w=-2:h=720:format=nv12")),
    ("hevc_vaapi", Tier.UPLOAD): VideoArgs(
        VAAPI_DEV, ("fps=30.0", "format=nv12", "hwupload", "scale_vaapi=w=-2:h=720:format=nv12")),
    ("hevc_amf", Tier.ZERO_COPY): VideoArgs(
        AMF_DEV + D3D11_DECODE, ("fps=30.0", "vpp_amf=w=-2:h=720:format=nv12:scale_type=bicubic")),
    ("hevc_amf", Tier.UPLOAD): VideoArgs(
        AMF_DEV,
        ("fps=30.0", "format=nv12", "hwupload", "vpp_amf=w=-2:h=720:format=nv12:scale_type=bicubic")),
    ("hevc_qsv", Tier.ZERO_COPY): VideoArgs(
        QSV_DEV + D3D11_DECODE + ("-extra_hw_frames", "16"),
        ("fps=30.0", "hwmap=derive_device=qsv,format=qsv",
         "vpp_qsv=w=-2:h=720:format=nv12:scale_mode=hq")),
    ("hevc_qsv", Tier.UPLOAD): VideoArgs(
        QSV_DEV,
        ("fps=30.0", "format=nv12", "hwupload=extra_hw_frames=16,format=qsv",
         "vpp_qsv=w=-2:h=720:format=nv12:scale_mode=hq")),
    ("hevc_videotoolbox", Tier.ZERO_COPY): VideoArgs(
        VT_DEV + VT_DECODE, ("fps=30.0", "scale_vt=w=-2:h=720")),
    ("hevc_videotoolbox", Tier.UPLOAD): VideoArgs(
        VT_DEV, ("fps=30.0", "format=nv12", "hwupload", "scale_vt=w=-2:h=720")),
}

# 1080p60 native, hevc
NATIVE: Dict[Key, VideoArgs] = {
    ("hevc_nvenc", Tier.ZERO_COPY): VideoArgs(CUDA_DEV + CUDA_DECODE),
    ("hevc_nvenc", Tier.UPLOAD): VideoArgs(CUDA_DEV, ("format=nv12", "hwupload_cuda")),
    ("hevc_vaapi", Tier.ZERO_COPY): VideoArgs(VAAPI_DEV + VAAPI_DECODE, ("scale_vaapi=format=nv12",)),
    ("hevc_vaapi", Tier.UPLOAD): VideoArgs(
        VAAPI_DEV, ("format=nv12", "hwupload", "scale_vaapi=format=nv12")),
    ("hevc_amf", Tier.ZERO_COPY): VideoArgs(AMF_DEV + D3D11_DECODE, ("vpp_amf=format=nv12",)),
    ("hevc_amf", Tier.UPLOAD): VideoArgs(AMF_DEV, ("format=nv12", "hwupload", "vpp_amf=format=nv12")),
    ("hevc_qsv", Tier.ZERO_COPY): VideoArgs(
        QSV_DEV + D3D11_DECODE + ("-extra_hw_frames", "16"),
        ("hwmap=derive_device=qsv,format=qsv", "vpp_qsv=format=nv12")),
    ("hevc_qsv", Tier.UPLOAD): VideoArgs(
        QSV_DEV, ("format=nv12", "hwupload=extra_hw_frames=16,format=qsv", "vpp_qsv=format=nv12")),
    ("hevc_videotoolbox", Tier.ZERO_COPY): VideoArgs(VT_DEV + VT_DECODE),
    ("hevc_videotoolbox", Tier.UPLOAD): VideoArgs(VT_DEV, ("format=nv12", "hwupload")),
}


def build(encoder: str, tier: Tier, tgt_h: int, tgt_fps: float, codec: str = "hevc") -> VideoArgs:
    return build_video_args(
        backend_for_encoder(encoder), tier, 1080, tgt_h, 60.0, tgt_fps, codec, VAAPI_NODE
    )


@pytest.mark.parametrize("key", list(SCALED_FPS), ids=lambda k: f"{k[0]}-{k[1].name}")
def test_scaled_with_fps_drop(key: Key) -> None:
    assert build(key[0], key[1], 720, 30.0) == SCALED_FPS[key]


@pytest.mark.parametrize("key", list(NATIVE), ids=lambda k: f"{k[0]}-{k[1].name}")
def test_native_resolution_and_fps(key: Key) -> None:
    assert build(key[0], key[1], 1080, 60.0) == NATIVE[key]


def test_nvenc_h264_uses_yuv420p() -> None:
    scaled = build("h264_nvenc", Tier.ZERO_COPY, 720, 60.0, "h264")
    native = build("h264_nvenc", Tier.ZERO_COPY, 1080, 60.0, "h264")
    assert scaled.filters == ("scale_cuda=-2:720:interp_algo=lanczos:format=yuv420p",)
    assert native.filters == ("scale_cuda=format=yuv420p",)


def test_nvenc_zero_copy_extends_legacy_with_explicit_device() -> None:
    legacy = build("hevc_nvenc", Tier.LEGACY, 720, 30.0)
    zero_copy = build("hevc_nvenc", Tier.ZERO_COPY, 720, 30.0)
    assert legacy.pre_input == ("-hwaccel", "cuda", "-hwaccel_output_format", "cuda")
    assert zero_copy.filters == legacy.filters
    assert zero_copy.post_input == legacy.post_input == ("-r", "30.0")
    assert zero_copy.pre_input == CUDA_DEV + CUDA_DECODE


@pytest.mark.parametrize("encoder", ["hevc_amf", "hevc_qsv", "hevc_videotoolbox"])
def test_legacy_uses_hwaccel_auto(encoder: str) -> None:
    args = build(encoder, Tier.LEGACY, 720, 30.0)
    assert args.pre_input == ("-hwaccel", "auto")
    assert args.filters == ("fps=30.0", "scale=-2:720:flags=lanczos")


def test_legacy_vaapi_pins_node_and_uploads_after_cpu_scale() -> None:
    args = build("hevc_vaapi", Tier.LEGACY, 720, 30.0)
    assert args.pre_input == VAAPI_DEV
    assert args.filters == ("fps=30.0", "scale=-2:720:flags=lanczos", "format=nv12,hwupload")


def test_vaapi_without_node_uses_default_device() -> None:
    args = build_video_args(BACKENDS["vaapi"], Tier.ZERO_COPY, 1080, 720, 60.0, 60.0, "hevc")
    assert args.pre_input[:2] == ("-init_hw_device", "vaapi=va")


def test_cpu_encoder_has_no_backend_and_legacy_filters() -> None:
    assert backend_for_encoder("libx264") is None
    args = build_video_args(None, Tier.ZERO_COPY, 1080, 720, 60.0, 30.0, "h264")
    assert args == VideoArgs(
        filters=("fps=30.0", "scale=-2:720:flags=lanczos", "format=yuv420p"))


@pytest.mark.parametrize("encoder,name", [
    ("hevc_nvenc", "nvenc"), ("h264_vaapi", "vaapi"), ("hevc_amf", "amf"),
    ("h264_qsv", "qsv"), ("hevc_videotoolbox", "videotoolbox"),
])
def test_backend_lookup(encoder: str, name: str) -> None:
    backend = backend_for_encoder(encoder)
    assert backend is not None and backend.name == name


def test_zero_target_height_skips_scaling() -> None:
    assert build("hevc_vaapi", Tier.ZERO_COPY, 0, 60.0).filters == ("scale_vaapi=format=nv12",)
