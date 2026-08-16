"""
Core utilities, constants, probing, encoder detection, command building, and optimization.
"""

import sys
import subprocess
import os
import math
import json
import logging
from typing import Tuple, Optional, List, Sequence

log = logging.getLogger(__name__)

# --- Constants ---
MB_TO_BYTES = 1024 * 1024
MB_TO_BITS = 8 * 1024 * 1024
BITRATE_SAFETY_FACTOR = 0.90
MIN_BPP = 0.04  # Target Bits Per Pixel threshold
LOG_FILES_TO_CLEAN = ["ffmpeg2pass.log", "ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"]

ENCODER_PRIORITY = {
    "hevc": {
        "win32": ["hevc_nvenc", "hevc_amf", "hevc_qsv"],
        "linux": ["hevc_nvenc", "hevc_vaapi"],
        "darwin": ["hevc_videotoolbox"],
        "fallback": "libx265"
    },
    "h264": {
        "win32": ["h264_nvenc", "h264_amf", "h264_qsv"],
        "linux": ["h264_nvenc", "h264_vaapi"],
        "darwin": ["h264_videotoolbox"],
        "fallback": "libx264"
    }
}

# Create a generic chain for unknown OSes by combining all platform-specific encoders
for _codec, _config in ENCODER_PRIORITY.items():
    _win: List[str] = _config.get('win32', [])  # type: ignore
    _lin: List[str] = _config.get('linux', [])  # type: ignore
    _mac: List[str] = _config.get('darwin', []) # type: ignore
    _all_encoders: List[str] = _win + _lin + _mac
    _generic_chain: List[str] = list(dict.fromkeys(_all_encoders))
    _config['other'] = _generic_chain

ENCODER_DISPLAY_NAMES: dict[str, str] = {
    "hevc_nvenc": "HEVC NVIDIA",
    "hevc_amf": "HEVC AMD",
    "hevc_qsv": "HEVC Intel",
    "hevc_vaapi": "HEVC VA-API",
    "hevc_videotoolbox": "HEVC Apple",
    "libx265": "HEVC CPU",
    "h264_nvenc": "H.264 NVIDIA",
    "h264_amf": "H.264 AMD",
    "h264_qsv": "H.264 Intel",
    "h264_vaapi": "H.264 VA-API",
    "h264_videotoolbox": "H.264 Apple",
    "libx264": "H.264 CPU",
}


def get_display_name(encoder: str) -> str:
    """Resolve a human-readable display name for an FFmpeg encoder.

    Args:
        encoder: FFmpeg encoder name (e.g., `hevc_nvenc`).

    Returns:
        Human-readable name (e.g., `HEVC NVIDIA`).
    """
    return ENCODER_DISPLAY_NAMES.get(encoder, encoder)


# --- Utility Functions ---

def get_resource_path(filename: str) -> str:
    """Resolve the absolute path to bundled resources.

    Args:
        filename: Base executable or file name (e.g., `ffmpeg`).

    Returns:
        Absolute path to the resource, respecting PyInstaller bundling.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS # type: ignore
        if sys.platform == 'win32' and not filename.lower().endswith('.exe'):
            filename = f"{filename}.exe"
        return os.path.join(base_path, filename)
    return filename


def get_file_size(file_path: str) -> int:
    """Return the file size in bytes.

    Args:
        file_path: Path to the file.

    Returns:
        File size in bytes.
    """
    return os.path.getsize(file_path)


def format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string.

    Args:
        size_bytes: Size value in bytes.

    Returns:
        A concise human-readable string (B, KB, MB).
    """
    if size_bytes < 1024: return f"{size_bytes} B"
    elif size_bytes < MB_TO_BYTES: return f"{size_bytes/1024:.2f} KB"
    else: return f"{size_bytes/MB_TO_BYTES:.2f} MB"


def clean_log_file(prefixes: Optional[List[str]] = None) -> None:
    """Remove temporary FFmpeg log files.

    Args:
        prefixes: Optional list of 2-pass log prefixes to clean as well.
    """
    for log_file in LOG_FILES_TO_CLEAN:
        try:
            if os.path.exists(log_file): os.remove(log_file)
        except OSError: pass
    prefixes_list: List[str] = prefixes if prefixes else []
    for p in prefixes_list:
        for ext in ["-0.log", "-0.log.mbtree"]:
            try:
                log_path = f"{p}{ext}"
                if os.path.exists(log_path): os.remove(log_path)
            except OSError: pass


def calculate_video_bitrate(target_mb: float, duration: float, audio_kbps: int) -> int:
    """Calculate single-pass target video bitrate in kbps factoring audio and safety margins.

    Args:
        target_mb: Desired target file size in megabytes.
        duration: Video duration in seconds.
        audio_kbps: Probed audio bitrate in kbps.

    Returns:
        Target video bitrate in kbps (integer).

    Examples:
        >>> calculate_video_bitrate(target_mb=50.0, duration=60.0, audio_kbps=128)
        5886
    """
    audio_mb = (audio_kbps * duration * 1000) / 8 / MB_TO_BYTES
    video_mb = max(0.5, target_mb - audio_mb)
    return math.floor(((video_mb * MB_TO_BITS) / duration / 1000) * BITRATE_SAFETY_FACTOR)


def calculate_split_bitrates(target_mb: int, durations: Sequence[float], audio_kbps: int) -> List[int]:
    """Calculate per-segment video bitrates for split parallel encoding.

    Args:
        target_mb: Total desired target file size in megabytes.
        durations: Sequence of segment durations in seconds.
        audio_kbps: Probed audio bitrate in kbps.

    Returns:
        List of target video bitrates in kbps for each segment.

    Examples:
        >>> calculate_split_bitrates(target_mb=50, durations=[30.0, 30.0], audio_kbps=128)
        [5886, 5886]
    """
    part_target_mb = target_mb / len(durations)
    return [calculate_video_bitrate(part_target_mb, d, audio_kbps) for d in durations]


# --- Encoder Detection ---

def check_encoder_available(encoder_name: str) -> bool:
    """Check if a specific FFmpeg encoder can be used.

    Args:
        encoder_name: FFmpeg encoder name (e.g., `hevc_nvenc`).

    Returns:
        True if a short test encode succeeds, else False.
    """
    ffmpeg_exe = get_resource_path("ffmpeg")
    try:
        is_vaapi = "vaapi" in encoder_name
        vf_args = ["-vf", "format=nv12,hwupload"] if is_vaapi else []
        pre_args = ["-init_hw_device", "vaapi=va", "-filter_hw_device", "va"] if is_vaapi else []

        cmd = [ffmpeg_exe, "-hide_banner", "-v", "error"] + pre_args + [
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=1:d=0.1",
            "-vframes", "1", "-c:v", encoder_name
        ] + vf_args + ["-f", "null", "-"]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


def select_best_encoder(codec_type: str = "hevc") -> Tuple[str, List[Tuple[str, str]]]:
    """Detect the best available encoder based on OS and Hardware.

    Tests encoders in priority order, stops at the first available one.
    Untested encoders after selection are marked as 'skipped'.

    Args:
        codec_type: `hevc` or `h264`.

    Returns:
        Tuple of (selected_encoder_name, detection_results).
        detection_results is a list of (encoder_name, status) tuples where
        status is one of: "selected", "unavailable", "skipped".

    Raises:
        ValueError: If the configured `codec_type` is invalid.
    """
    codec_config = ENCODER_PRIORITY.get(codec_type)
    if not codec_config:
        raise ValueError(f"Invalid codec_type: {codec_type}")

    if sys.platform.startswith("linux"):
        platform_key = "linux"
    elif sys.platform == "darwin":
        platform_key = "darwin"
    elif sys.platform == "win32":
        platform_key = "win32"
    else:
        platform_key = "other"

    if platform_key in codec_config:
        priority_chain = codec_config[platform_key]
    else:
        priority_chain = codec_config["other"]
    fallback = codec_config["fallback"]

    results: List[Tuple[str, str]] = []
    selected: Optional[str] = None

    for enc in priority_chain:
        if selected is not None:
            results.append((enc, "skipped"))
            continue
        is_available = check_encoder_available(enc)
        if is_available:
            results.append((enc, "selected"))
            selected = enc
        else:
            results.append((enc, "unavailable"))

    # CPU fallback
    fallback_name = str(fallback[0]) if isinstance(fallback, list) else str(fallback)
    if selected is None:
        results.append((fallback_name, "selected"))
        selected = fallback_name
    else:
        results.append((fallback_name, "skipped"))

    return selected, results


# --- Video Probing ---

def get_video_info(input_path: str) -> Optional[Tuple[float, int, float, int, int, int]]:
    """Probe video metadata.

    Args:
        input_path: Path to the input media file.

    Returns:
        Tuple of (duration_seconds, file_size_bytes, fps, audio_kbps, width, height), or None on failure.
    """
    ffprobe_exe = get_resource_path("ffprobe")
    try:
        # Get metadata as JSON
        cmd = [ffprobe_exe, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,avg_frame_rate",
               "-show_entries", "format=duration", "-of", "json", input_path]
        res = json.loads(subprocess.check_output(cmd, text=True))

        v_stream = res['streams'][0]
        width = int(v_stream.get('width', 0))
        height = int(v_stream.get('height', 0))
        dur_out = res['format'].get('duration', 0)

        fps_val = v_stream.get('avg_frame_rate', '30/1')
        if '/' in fps_val:
            num, den = map(int, fps_val.split('/'))
            fps = num / den if den > 0 else 30
        else:
            fps = float(fps_val)

        # Audio probe
        cmd_aud = [ffprobe_exe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        try:
            aud_out = subprocess.check_output(cmd_aud, text=True).strip()
            audio_bps = int(aud_out) if aud_out.isdigit() else 128000
        except subprocess.CalledProcessError:
            audio_bps = 128000 # Default if no audio stream found or probe fails

        return float(dur_out), get_file_size(input_path), fps, math.ceil(audio_bps / 1000), width, height
    except (subprocess.CalledProcessError, ValueError, OSError, KeyError, IndexError):
        return None


def get_smart_split_point(input_path: str, duration: float) -> float:
    """Find a keyframe-aligned split point near the middle.

    Args:
        input_path: Path to the input media file.
        duration: Total duration in seconds.

    Returns:
        Timestamp in seconds to split the encode. Falls back to duration/2
        if keyframe analysis fails or no suitable keyframe is found.
    """
    log.info("Analyzing for smart split point...")
    try:
        cmd = [get_resource_path("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,size,flags", "-of", "json", input_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        packets = json.loads(res.stdout).get('packets', [])

        target = sum(int(p.get('size', 0)) for p in packets) / 2
        curr, last_k = 0, 0.0

        for p in packets:
            curr += int(p.get('size', 0))
            if 'K' in p.get('flags', ''): last_k = float(p.get('pts_time', 0))
            if curr >= target: return last_k if last_k > 0 else duration/2
    except Exception as e:
        log.warning("Smart split analysis failed (%s). Falling back to midpoint.", e)
    return duration / 2


# --- Optimization ---

def get_optimal_settings(target_mb: int, duration: float, width: int, height: int, fps: float) -> Tuple[int, float]:
    """Determine optimal resolution and frame rate based on bits-per-pixel threshold.

    Prioritizes maintaining 60+ FPS for gaming content while ensuring visual
    quality stays above `MIN_BPP` threshold.

    Args:
        target_mb: Target file size in megabytes.
        duration: Video duration in seconds.
        width: Source video width in pixels.
        height: Source video height in pixels.
        fps: Source video frame rate.

    Returns:
        Tuple of (target_height, target_fps). Values will never exceed source
        dimensions. Returns source values if no scaling is needed.
    """
    target_bits = target_mb * MB_TO_BITS
    aspect_ratio = width / height

    height_options = [2160, 1440, 1080, 720]
    fps_options = [120.0, 90.0, 60.0]

    # 1. Filter Options (Never Upscale)
    valid_heights = [h for h in height_options if h <= height]
    if height not in valid_heights: valid_heights.insert(0, height)

    valid_fps = [f for f in fps_options if f <= fps]
    if fps not in valid_fps: valid_fps.insert(0, fps)

    # 2. Generate All Valid Candidates (BPP >= Floor)
    candidates = [] # List of tuples: (fps_priority, pixels_throughput, h, f)

    for h in valid_heights:
        w = int(h * aspect_ratio)
        for f in valid_fps:
            pixels_per_sec = w * h * f
            if pixels_per_sec == 0: continue
            bpp = target_bits / (duration * pixels_per_sec)
            if bpp >= MIN_BPP:
                fps_priority = (f >= 60)
                candidates.append((fps_priority, pixels_per_sec, h, f))

    # 3. Sort Logic
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best = candidates[0]
        if best[2] == height and best[3] == fps:
            return height, fps
        return best[2], best[3]

    # Fallback: Smallest possible valid config
    return valid_heights[-1], valid_fps[-1]


# --- Command Building ---

def build_single_pass_cmd(
    ffmpeg_exe: str,
    input_path: str,
    encoder: str,
    codec_type: str,
    bitrate_k: int,
    src_fps: float,
    start: Optional[float],
    end: Optional[float],
    output_path: str,
    tgt_h: int,
    tgt_fps: float
) -> List[str]:
    """Build a single-pass FFmpeg command for the requested encoder.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        encoder: FFmpeg encoder name.
        bitrate_k: Target bitrate in kbps.
        src_fps: Source frames per second.
        start: Optional start time for segmenting.
        end: Optional end time for segmenting.
        output_path: Destination video path.
        tgt_h: Target height for scaling. Uses -2:height format to ensure
            even width. Set to 0 or equal to source height to skip scaling.
        tgt_fps: Target frames per second. Set equal to src_fps to skip
            FPS conversion.

    Returns:
        A command list ready for subprocess execution.
    """
    cmd: List[str] = [ffmpeg_exe, "-y"]
    filters = []

    if "vaapi" in encoder:
        cmd.extend(["-init_hw_device", "vaapi=va", "-filter_hw_device", "va"])

    if start is not None:
        cmd.extend(["-ss", str(start)])
    if end is not None:
        cmd.extend(["-to", str(end)])

    cmd.extend(["-i", input_path])

    # Build Filters
    if tgt_fps < src_fps: filters.append(f"fps={tgt_fps}")

    # Scale Filter: -2:height ensures width is even (divisible by 2) while keeping aspect ratio
    # If using -1, encoders often fail with odd pixel counts (e.g. 853x480). -2 gives 854x480.
    if tgt_h > 0: filters.append(f"scale=-2:{tgt_h}")

    # Encoder Specific Filter Chains
    if "vaapi" in encoder:
        filters.append("format=nv12,hwupload")
        cmd.extend(["-vf", ",".join(filters)] if filters else ["-vf", "format=nv12,hwupload"])
    elif encoder == "libx265":
        if not any("fps" in f for f in filters): filters.append(f"fps={src_fps}")
        cmd.extend(["-vf", ",".join(filters)])
    elif encoder == "libx264":
        if not any("fps" in f for f in filters): filters.append(f"fps={src_fps}")
        cmd.extend(["-vf", ",".join(filters)])
    else:
        if filters: cmd.extend(["-vf", ",".join(filters)])

    cmd.extend(["-c:v", encoder, "-b:v", f"{bitrate_k}k"])

    if "amf" in encoder:
        cmd.extend(["-usage", "transcoding", "-quality", "balanced", "-rc", "cbr"])
    elif "qsv" in encoder:
        if "hevc" in encoder: cmd.extend(["-load_plugin", "hevc_hw"])
        cmd.extend(["-preset", "medium"])
    elif "videotoolbox" in encoder:
        cmd.extend(["-allow_sw", "1", "-realtime", "0"])
    elif encoder == "libx265":
        cmd.extend(["-preset", "medium"])
    elif encoder == "libx264":
        cmd.extend(["-preset", "medium"])

    if codec_type == "hevc": cmd.extend(["-tag:v", "hvc1"])
    elif codec_type == "h264": cmd.extend(["-tag:v", "avc1"])

    cmd.extend(["-maxrate:v", f"{bitrate_k}k", "-bufsize:v", f"{bitrate_k*2}k"])
    cmd.extend(["-c:a", "copy", "-loglevel", "error", "-stats", output_path])
    return cmd
