"""
Pure hardware pipeline description: tiers, backends and video argument building.

No I/O happens here, so every graph can be unit-tested as an exact string.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional, Tuple


class Tier(Enum):
    """How far frames stay on the GPU between decode and encode.

    ZERO_COPY: hardware decode -> hardware fps/scale -> hardware encode.
    UPLOAD: software decode -> hardware upload -> hardware scale -> hardware encode.
    LEGACY: the arguments used before hardware-resident pipelines existed.

    Example:
        >>> Tier.ZERO_COPY.label
        'zero-copy'
    """

    ZERO_COPY = "zero-copy"
    UPLOAD = "upload"
    LEGACY = "legacy"

    @property
    def label(self) -> str:
        """Human-readable tier name used in console output."""
        return self.value


@dataclass(frozen=True)
class VideoArgs:
    """Video-related FFmpeg arguments, split by their position in the command.

    Attributes:
        pre_input: Arguments placed before `-ss`/`-to`/`-i`.
        filters: Video filters joined into a single `-vf` chain (may be empty).
        post_input: Output options placed after `-i` and before `-vf`.
    """

    pre_input: Tuple[str, ...] = ()
    filters: Tuple[str, ...] = ()
    post_input: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HwBackend:
    """Static description of one hardware encode family.

    Attributes:
        name: Short backend name (e.g. `vaapi`).
        device_args: Maps an optional device node to `-init_hw_device` arguments.
        decode_args: Arguments enabling hardware decode with hardware output frames.
        upload_filter: Filter chain uploading nv12 system frames to the device.
        scale_filter: Maps (target height or None, pixel format) to a scaler
            filter. An empty string means no scaler is needed.
        map_filter: Filter mapping decoded frames to the encoder device (QSV only).
        output_fps: True when frame rate is limited with the `-r` output option.
    """

    name: str
    device_args: Callable[[Optional[str]], Tuple[str, ...]]
    decode_args: Tuple[str, ...]
    upload_filter: str
    scale_filter: Callable[[Optional[int], str], str]
    map_filter: Optional[str] = None
    output_fps: bool = False


def _scale_cuda(tgt_h: Optional[int], pix_fmt: str) -> str:
    """Build the `scale_cuda` filter; empty when no conversion is needed.

    Example:
        >>> _scale_cuda(720, "nv12")
        'scale_cuda=-2:720:interp_algo=lanczos:format=nv12'
    """
    if tgt_h:
        return f"scale_cuda=-2:{tgt_h}:interp_algo=lanczos:format={pix_fmt}"
    if pix_fmt == "yuv420p":
        return "scale_cuda=format=yuv420p"
    return ""


def _scale_vaapi(tgt_h: Optional[int], pix_fmt: str) -> str:
    """Build the `scale_vaapi` filter.

    Example:
        >>> _scale_vaapi(None, "nv12")
        'scale_vaapi=format=nv12'
    """
    size = f"w=-2:h={tgt_h}:" if tgt_h else ""
    return f"scale_vaapi={size}format={pix_fmt}"


def _vpp_amf(tgt_h: Optional[int], pix_fmt: str) -> str:
    """Build the `vpp_amf` filter.

    Example:
        >>> _vpp_amf(720, "nv12")
        'vpp_amf=w=-2:h=720:format=nv12:scale_type=bicubic'
    """
    if tgt_h:
        return f"vpp_amf=w=-2:h={tgt_h}:format={pix_fmt}:scale_type=bicubic"
    return f"vpp_amf=format={pix_fmt}"


def _vpp_qsv(tgt_h: Optional[int], pix_fmt: str) -> str:
    """Build the `vpp_qsv` filter.

    Example:
        >>> _vpp_qsv(720, "nv12")
        'vpp_qsv=w=-2:h=720:format=nv12:scale_mode=hq'
    """
    if tgt_h:
        return f"vpp_qsv=w=-2:h={tgt_h}:format={pix_fmt}:scale_mode=hq"
    return f"vpp_qsv=format={pix_fmt}"


def _scale_vt(tgt_h: Optional[int], pix_fmt: str) -> str:
    """Build the `scale_vt` filter. Omitted when not scaling: VideoToolbox
    surfaces reach the encoder without any format conversion.

    Example:
        >>> _scale_vt(None, "nv12")
        ''
    """
    return f"scale_vt=w=-2:h={tgt_h}" if tgt_h else ""


def _cuda_devices(node: Optional[str]) -> Tuple[str, ...]:
    """Return CUDA device arguments (the node is unused).

    Example:
        >>> _cuda_devices(None)[1]
        'cuda=cu'
    """
    return ("-init_hw_device", "cuda=cu", "-filter_hw_device", "cu")


def _vaapi_devices(node: Optional[str]) -> Tuple[str, ...]:
    """Return VA-API device arguments, pinned to `node` when given.

    Example:
        >>> _vaapi_devices("/dev/dri/renderD128")[1]
        'vaapi=va:/dev/dri/renderD128'
    """
    device = f"vaapi=va:{node}" if node else "vaapi=va"
    return ("-init_hw_device", device, "-filter_hw_device", "va")


def _amf_devices(node: Optional[str]) -> Tuple[str, ...]:
    """Return D3D11 device arguments pinned to the AMD adapter.

    Example:
        >>> _amf_devices(None)[1]
        'd3d11va=dx:,vendor_id=0x1002'
    """
    return ("-init_hw_device", "d3d11va=dx:,vendor_id=0x1002", "-filter_hw_device", "dx")


def _qsv_devices(node: Optional[str]) -> Tuple[str, ...]:
    """Return D3D11 (Intel adapter) plus derived QSV device arguments.

    Example:
        >>> _qsv_devices(None)[3]
        'qsv=qs@dx'
    """
    return (
        "-init_hw_device", "d3d11va=dx:,vendor_id=0x8086",
        "-init_hw_device", "qsv=qs@dx",
        "-filter_hw_device", "qs",
    )


def _videotoolbox_devices(node: Optional[str]) -> Tuple[str, ...]:
    """Return VideoToolbox device arguments.

    Example:
        >>> _videotoolbox_devices(None)[1]
        'videotoolbox=vt'
    """
    return ("-init_hw_device", "videotoolbox=vt", "-filter_hw_device", "vt")


BACKENDS: Dict[str, HwBackend] = {
    "nvenc": HwBackend(
        name="nvenc",
        device_args=_cuda_devices,
        decode_args=("-hwaccel", "cuda", "-hwaccel_device", "cu", "-hwaccel_output_format", "cuda"),
        upload_filter="hwupload_cuda",
        scale_filter=_scale_cuda,
        output_fps=True,
    ),
    "vaapi": HwBackend(
        name="vaapi",
        device_args=_vaapi_devices,
        decode_args=("-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi"),
        upload_filter="hwupload",
        scale_filter=_scale_vaapi,
    ),
    "amf": HwBackend(
        name="amf",
        device_args=_amf_devices,
        decode_args=("-hwaccel", "d3d11va", "-hwaccel_device", "dx", "-hwaccel_output_format", "d3d11"),
        upload_filter="hwupload",
        scale_filter=_vpp_amf,
    ),
    "qsv": HwBackend(
        name="qsv",
        device_args=_qsv_devices,
        decode_args=(
            "-hwaccel", "d3d11va", "-hwaccel_device", "dx",
            "-hwaccel_output_format", "d3d11", "-extra_hw_frames", "16",
        ),
        upload_filter="hwupload=extra_hw_frames=16,format=qsv",
        scale_filter=_vpp_qsv,
        map_filter="hwmap=derive_device=qsv,format=qsv",
    ),
    "videotoolbox": HwBackend(
        name="videotoolbox",
        device_args=_videotoolbox_devices,
        decode_args=("-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"),
        upload_filter="hwupload",
        scale_filter=_scale_vt,
    ),
}


def backend_for_encoder(encoder: str) -> Optional[HwBackend]:
    """Resolve the hardware backend for an FFmpeg encoder name.

    Args:
        encoder: FFmpeg encoder name (e.g. `hevc_vaapi`).

    Returns:
        The matching backend, or None for CPU encoders.

    Example:
        >>> backend_for_encoder("h264_nvenc").name
        'nvenc'
        >>> backend_for_encoder("libx265") is None
        True
    """
    for key, backend in BACKENDS.items():
        if encoder.endswith(f"_{key}"):
            return backend
    return None


def _needs_scale(src_h: int, tgt_h: int) -> bool:
    """Return True when the target height differs from the source.

    Example:
        >>> _needs_scale(1080, 720)
        True
    """
    return tgt_h > 0 and tgt_h != src_h


def _target_pix_fmt(backend: HwBackend, codec_type: str) -> str:
    """NVENC emits yuv420p for H.264; every other backend uses nv12."""
    return "yuv420p" if backend.name == "nvenc" and codec_type == "h264" else "nv12"


def _fps_filters(backend: HwBackend, src_fps: float, tgt_fps: float) -> Tuple[str, ...]:
    if tgt_fps < src_fps and not backend.output_fps:
        return (f"fps={tgt_fps}",)
    return ()


def _output_fps_args(backend: HwBackend, src_fps: float, tgt_fps: float) -> Tuple[str, ...]:
    if tgt_fps < src_fps and backend.output_fps:
        return ("-r", str(tgt_fps))
    return ()


def _scaler(backend: HwBackend, tgt_h: Optional[int], pix_fmt: str) -> Tuple[str, ...]:
    scaler = backend.scale_filter(tgt_h, pix_fmt)
    return (scaler,) if scaler else ()


def _zero_copy_args(
    backend: HwBackend, node: Optional[str], tgt_h: Optional[int],
    pix_fmt: str, src_fps: float, tgt_fps: float,
) -> VideoArgs:
    mapping = (backend.map_filter,) if backend.map_filter else ()
    return VideoArgs(
        pre_input=backend.device_args(node) + backend.decode_args,
        filters=_fps_filters(backend, src_fps, tgt_fps) + mapping + _scaler(backend, tgt_h, pix_fmt),
        post_input=_output_fps_args(backend, src_fps, tgt_fps),
    )


def _upload_args(
    backend: HwBackend, node: Optional[str], tgt_h: Optional[int],
    pix_fmt: str, src_fps: float, tgt_fps: float,
) -> VideoArgs:
    upload = ("format=nv12", backend.upload_filter)
    return VideoArgs(
        pre_input=backend.device_args(node),
        filters=_fps_filters(backend, src_fps, tgt_fps) + upload + _scaler(backend, tgt_h, pix_fmt),
        post_input=_output_fps_args(backend, src_fps, tgt_fps),
    )


def _cpu_filters(
    src_h: int, tgt_h: int, src_fps: float, tgt_fps: float, codec_type: str,
) -> Tuple[str, ...]:
    filters = []
    if tgt_fps < src_fps:
        filters.append(f"fps={tgt_fps}")
    if _needs_scale(src_h, tgt_h):
        filters.append(f"scale=-2:{tgt_h}:flags=lanczos")
    if codec_type == "h264":
        filters.append("format=yuv420p")
    return tuple(filters)


def _legacy_nvenc_args(
    src_h: int, tgt_h: int, src_fps: float, tgt_fps: float, codec_type: str,
) -> VideoArgs:
    pix_fmt = "yuv420p" if codec_type == "h264" else "nv12"
    filters: Tuple[str, ...] = ()
    if _needs_scale(src_h, tgt_h):
        filters = (f"scale_cuda=-2:{tgt_h}:interp_algo=lanczos:format={pix_fmt}",)
    elif codec_type == "h264":
        filters = ("scale_cuda=format=yuv420p",)
    return VideoArgs(
        pre_input=("-hwaccel", "cuda", "-hwaccel_output_format", "cuda"),
        filters=filters,
        post_input=("-r", str(tgt_fps)) if tgt_fps < src_fps else (),
    )


def _legacy_vaapi_args(
    node: Optional[str], src_h: int, tgt_h: int, src_fps: float, tgt_fps: float,
) -> VideoArgs:
    filters = list(_cpu_filters(src_h, tgt_h, src_fps, tgt_fps, "hevc"))
    filters.append("format=nv12,hwupload")
    return VideoArgs(pre_input=_vaapi_devices(node), filters=tuple(filters))


def _legacy_args(
    backend: Optional[HwBackend], node: Optional[str], src_h: int, tgt_h: int,
    src_fps: float, tgt_fps: float, codec_type: str,
) -> VideoArgs:
    if backend is None:
        return VideoArgs(filters=_cpu_filters(src_h, tgt_h, src_fps, tgt_fps, codec_type))
    if backend.name == "nvenc":
        return _legacy_nvenc_args(src_h, tgt_h, src_fps, tgt_fps, codec_type)
    if backend.name == "vaapi":
        return _legacy_vaapi_args(node, src_h, tgt_h, src_fps, tgt_fps)
    return VideoArgs(
        pre_input=("-hwaccel", "auto"),
        filters=_cpu_filters(src_h, tgt_h, src_fps, tgt_fps, codec_type),
    )


def build_video_args(
    backend: Optional[HwBackend],
    tier: Tier,
    src_h: int,
    tgt_h: int,
    src_fps: float,
    tgt_fps: float,
    codec_type: str,
    vaapi_node: Optional[str] = None,
) -> VideoArgs:
    """Build the decode, filter and upload arguments for one pipeline tier.

    Args:
        backend: Hardware backend, or None for CPU encoders (always LEGACY).
        tier: Pipeline tier to build.
        src_h: Source height in pixels.
        tgt_h: Target height. 0 or equal to `src_h` disables scaling.
        src_fps: Source frames per second.
        tgt_fps: Target frames per second. Not below `src_fps` disables fps limiting.
        codec_type: `hevc` or `h264`.
        vaapi_node: Optional VA-API render node (e.g. `/dev/dri/renderD128`).

    Returns:
        The video arguments split into pre-input, filters and post-input parts.

    Example:
        >>> args = build_video_args(BACKENDS["vaapi"], Tier.ZERO_COPY, 1080, 720, 60.0, 30.0, "hevc")
        >>> ",".join(args.filters)
        'fps=30.0,scale_vaapi=w=-2:h=720:format=nv12'
    """
    if backend is None or tier is Tier.LEGACY:
        return _legacy_args(backend, vaapi_node, src_h, tgt_h, src_fps, tgt_fps, codec_type)

    scaled_h = tgt_h if _needs_scale(src_h, tgt_h) else None
    pix_fmt = _target_pix_fmt(backend, codec_type)
    builder = _zero_copy_args if tier is Tier.ZERO_COPY else _upload_args
    return builder(backend, vaapi_node, scaled_h, pix_fmt, src_fps, tgt_fps)
