"""
Core utilities, constants, probing, encoder detection, command building, and optimization.
"""

import sys
import subprocess
import os
import math
import json
import logging
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Tuple, Optional, List, Sequence, Mapping

from videocompress.hwpipeline import Tier, backend_for_encoder, build_video_args

log = logging.getLogger(__name__)

# --- Constants ---
MB_TO_BYTES = 1024 * 1024
MB_TO_BITS = 8 * 1024 * 1024
BITRATE_SAFETY_FACTOR = 0.90
MIN_BPP = 0.04  # Target Bits Per Pixel threshold
LOG_FILES_TO_CLEAN = ["ffmpeg2pass.log", "ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"]

DRI_DIR = Path("/dev/dri")
DRM_SYSFS_DIR = Path("/sys/class/drm")
VENDOR_NVIDIA = "0x10de"
VENDOR_PRIORITY = {"0x1002": 0, "0x8086": 1}
VAAPI_NODE_TIMEOUT_S = 10
PIPELINE_PROBE_TIMEOUT_S = 20
PIPELINE_PROBE_TIERS = (Tier.ZERO_COPY, Tier.UPLOAD)

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
    """Resolve the absolute path to bundled or local resources.

    Follows resolution priority:
    1. PyInstaller bundled resources (when running inside a frozen bundle)
    2. Minimal builds directory (ffmpeg-minimal-builds / minimal-builds)
    3. Folder present (current working directory / repo root)
    4. System PATH

    Args:
        filename: Base executable or file name (e.g., `ffmpeg`).

    Returns:
        Absolute path to the resource, or base filename for PATH fallback.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS # type: ignore
        if sys.platform == 'win32' and not filename.lower().endswith('.exe'):
            filename = f"{filename}.exe"
        return os.path.join(base_path, filename)

    base_name = Path(filename).stem
    extensions = [".exe", ""] if sys.platform == "win32" else ["", ".exe"]

    # Priority 1: Minimal builds directory (relative to CWD and repo root)
    search_roots = [
        Path.cwd() / "ffmpeg-minimal-builds",
        Path.cwd() / "minimal-builds",
        Path(__file__).resolve().parent.parent / "ffmpeg-minimal-builds",
        Path(__file__).resolve().parent.parent / "minimal-builds",
    ]
    subdirs = ["", "dist", "bin", "build"]

    for m_root in search_roots:
        if m_root.is_dir():
            for sub in subdirs:
                cand_dir = m_root / sub if sub else m_root
                if cand_dir.is_dir():
                    for ext in extensions:
                        candidate = cand_dir / f"{base_name}{ext}"
                        if candidate.is_file() and os.access(candidate, os.X_OK):
                            return str(candidate.resolve())

    # Priority 2: Folder present (current working directory / repo root)
    folder_roots = [Path.cwd(), Path(__file__).resolve().parent.parent]
    for f_root in folder_roots:
        for ext in extensions:
            candidate = f_root / f"{base_name}{ext}"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())

    # Priority 3: System PATH
    for ext in extensions:
        lookup_name = f"{base_name}{ext}" if ext else base_name
        which_path = shutil.which(lookup_name)
        if which_path and os.path.isfile(which_path):
            return str(Path(which_path).resolve())

    return filename


def get_clean_env() -> dict[str, str]:
    """Restore the host environment by clearing PyInstaller's LD_LIBRARY_PATH override.

    Prevents dynamically loaded GPU drivers (e.g., VA-API and CUDA) from failing
    due to missing or mismatched libraries inside PyInstaller's bundle.

    Returns:
        Sanitized environment dictionary suitable for subprocess calls.

    Examples:
        >>> env = get_clean_env()
        >>> "LD_LIBRARY_PATH" in env
        False
    """
    env = os.environ.copy()
    if sys.platform.startswith("linux"):
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


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


def calculate_split_bitrates(target_mb: float, durations: Sequence[float], audio_kbps: int) -> List[int]:
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

def rank_render_nodes(vendors: Mapping[str, str]) -> List[str]:
    """Order DRM render nodes by vendor preference, dropping NVIDIA.

    AMD comes first, then Intel, then any other vendor. Order within a vendor
    follows the node name.

    Args:
        vendors: Mapping of render node path to its sysfs vendor id (e.g. `0x8086`).

    Returns:
        Candidate node paths, best first.

    Examples:
        >>> rank_render_nodes({"/dev/dri/renderD128": "0x8086", "/dev/dri/renderD129": "0x1002",
        ...                    "/dev/dri/renderD130": "0x10de"})
        ['/dev/dri/renderD129', '/dev/dri/renderD128']
    """
    usable = [(node, vendor) for node, vendor in vendors.items() if vendor != VENDOR_NVIDIA]
    ranked = sorted(usable, key=lambda nv: (VENDOR_PRIORITY.get(nv[1], len(VENDOR_PRIORITY)), nv[0]))
    return [node for node, _ in ranked]


def _read_render_vendors() -> dict[str, str]:
    """Map each `/dev/dri/renderD*` node to its sysfs vendor id.

    Returns:
        Dictionary of node path to vendor id; empty when no render node exists.
    """
    vendors: dict[str, str] = {}
    try:
        nodes = sorted(DRI_DIR.glob("renderD*"))
    except OSError as e:
        log.debug("Could not list %s: %s", DRI_DIR, e)
        return vendors
    for node in nodes:
        vendor_file = DRM_SYSFS_DIR / node.name / "device" / "vendor"
        try:
            vendors[str(node)] = vendor_file.read_text().strip().lower()
        except OSError as e:
            log.debug("Could not read vendor for %s: %s", node, e)
            vendors[str(node)] = ""
    return vendors


def _vaapi_probe_cmd(ffmpeg_exe: str, encoder_name: str, node: str) -> List[str]:
    """Build a one-frame VA-API test encode command bound to a render node.

    Example:
        >>> "vaapi=va:/dev/dri/renderD128" in _vaapi_probe_cmd("ffmpeg", "hevc_vaapi", "/dev/dri/renderD128")
        True
    """
    return [
        ffmpeg_exe, "-hide_banner", "-v", "error",
        "-init_hw_device", f"vaapi=va:{node}", "-filter_hw_device", "va",
        "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=1:d=0.1",
        "-vframes", "1", "-vf", "format=nv12,hwupload", "-c:v", encoder_name,
        "-f", "null", "-",
    ]


def _run_probe(cmd: List[str], timeout: float, label: str) -> bool:
    """Run a probe command; True on exit code 0, False on any specific failure.

    Args:
        cmd: Command to run.
        timeout: Seconds before the probe is abandoned.
        label: Short description used in debug logging.
    """
    try:
        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, timeout=timeout, env=get_clean_env(),
        )
        return True
    except subprocess.TimeoutExpired:
        log.debug("%s probe timed out after %ss", label, timeout)
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or "").strip().splitlines()[-10:]
        log.debug("%s probe failed with exit code %s:\n%s", label, e.returncode, "\n".join(tail))
    except OSError as e:
        log.debug("%s probe error: %s", label, e)
    return False


@lru_cache(maxsize=4)
def resolve_vaapi_node(encoder_name: str) -> Optional[str]:
    """Pick the VA-API render node used for both detection and encoding.

    Candidates are `/dev/dri/renderD*` ordered by vendor (AMD, Intel, others;
    NVIDIA skipped). The first node where `encoder_name` completes a test encode
    wins, so H.264-only GPUs still resolve a node for `h264_vaapi`.

    Args:
        encoder_name: VA-API encoder to validate (e.g., `h264_vaapi`).

    Returns:
        The render node path, or None when not on Linux or no node works.

    Example:
        >>> node = resolve_vaapi_node("hevc_vaapi")
        >>> node is None or node.startswith("/dev/dri/")
        True
    """
    if not sys.platform.startswith("linux"):
        return None
    ffmpeg_exe = get_resource_path("ffmpeg")
    for node in rank_render_nodes(_read_render_vendors()):
        if _run_probe(_vaapi_probe_cmd(ffmpeg_exe, encoder_name, node), VAAPI_NODE_TIMEOUT_S, f"{encoder_name} on {node}"):
            log.debug("VA-API render node for %s: %s", encoder_name, node)
            return node
    return None


def check_encoder_available(encoder_name: str) -> bool:
    """Check if a specific FFmpeg encoder can be used.

    Args:
        encoder_name: FFmpeg encoder name (e.g., `hevc_nvenc`).

    Returns:
        True if a short test encode succeeds, else False.
    """
    ffmpeg_exe = get_resource_path("ffmpeg")
    if "vaapi" in encoder_name:
        return resolve_vaapi_node(encoder_name) is not None

    cmd = [ffmpeg_exe, "-hide_banner", "-v", "error",
           "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=1:d=0.1",
           "-vframes", "1", "-c:v", encoder_name, "-f", "null", "-"]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=get_clean_env(),
        )
        return True
    except subprocess.CalledProcessError as e:
        log.debug("Encoder %s probe failed with exit code %s: %s", encoder_name, e.returncode, e.stderr)
        return False
    except (FileNotFoundError, OSError) as e:
        log.debug("Encoder %s probe error: %s", encoder_name, e)
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

def check_audio_encoder_available(encoder_name: str = "aac") -> bool:
    """Check if an audio encoder is supported in the active FFmpeg binary.

    Args:
        encoder_name: Audio encoder to check (e.g., 'aac').

    Returns:
        True if the encoder is supported, else False.
    """
    ffmpeg_exe = get_resource_path("ffmpeg")
    try:
        res = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            env=get_clean_env(),
        )
        return f"A..... {encoder_name}" in res.stdout or f"A...D. {encoder_name}" in res.stdout
    except (subprocess.SubprocessError, OSError):
        return False


@lru_cache(maxsize=4)
def check_bsf_available(bsf_name: str) -> bool:
    """Check if a bitstream filter is compiled into the active FFmpeg binary.

    Args:
        bsf_name: Bitstream filter name (e.g., `h264_mp4toannexb`).

    Returns:
        True if the filter is listed by `ffmpeg -bsfs`, else False.

    Example:
        >>> isinstance(check_bsf_available("null"), bool)
        True
    """
    ffmpeg_exe = get_resource_path("ffmpeg")
    try:
        res = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-bsfs"],
            capture_output=True,
            text=True,
            timeout=10,
            env=get_clean_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.debug("Bitstream filter listing failed: %s", e)
        return False
    return bsf_name in (line.strip() for line in res.stdout.splitlines())


def build_concat_cmd(ffmpeg_exe: str, list_path: str, output_path: str, annexb_available: bool) -> List[str]:
    """Build the stream-copy command that stitches split segments.

    The concat demuxer converts H.264 through `h264_mp4toannexb`, which also
    tolerates differing parameter sets between segments. When the binary lacks
    that filter, conversion is disabled so H.264 segments can still be joined.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        list_path: Concat list file naming the segments in order.
        output_path: Destination video path.
        annexb_available: Whether `h264_mp4toannexb` exists in the binary.

    Returns:
        A command list ready for subprocess execution.

    Example:
        >>> build_concat_cmd("ffmpeg", "list.txt", "out.mp4", annexb_available=False)[6:9]
        ['-safe', '0', '-auto_convert']
    """
    convert_args = [] if annexb_available else ["-auto_convert", "0"]
    return [
        ffmpeg_exe, "-hide_banner", "-v", "error",
        "-f", "concat", "-safe", "0", *convert_args, "-i", list_path,
        "-c", "copy", "-movflags", "+faststart", "-y", output_path,
    ]


def get_video_info(input_path: str) -> Optional[Tuple[float, int, float, int, int, int]]:
    """Probe video metadata.

    Args:
        input_path: Path to the input media file.

    Returns:
        Tuple of (duration_seconds, file_size_bytes, fps, audio_kbps, width, height), or None on failure.
    """
    ffprobe_exe = get_resource_path("ffprobe")
    clean_env = get_clean_env()
    try:
        # Get video metadata as JSON
        cmd = [
            ffprobe_exe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate",
            "-show_entries", "format=duration", "-of", "json", input_path
        ]
        log.debug("ffprobe: %s", ffprobe_exe)
        res = json.loads(subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE, env=clean_env))

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

        # Audio probe: verify stream presence first to avoid phantom bitrate reservation
        cmd_aud = [
            ffprobe_exe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=bit_rate", "-of", "json", input_path
        ]
        audio_bps = 0
        try:
            aud_res = json.loads(subprocess.check_output(cmd_aud, text=True, env=clean_env))
            aud_streams = aud_res.get('streams', [])
            if aud_streams:
                raw_br = aud_streams[0].get('bit_rate')
                if raw_br and str(raw_br).isdigit():
                    audio_bps = int(raw_br)
                else:
                    audio_bps = 128000  # Default fallback if bitrate is VBR/unspecified
        except (subprocess.CalledProcessError, ValueError, OSError, KeyError, IndexError):
            audio_bps = 0

        audio_kbps = math.ceil(audio_bps / 1000) if audio_bps > 0 else 0
        info = (float(dur_out), get_file_size(input_path), fps, audio_kbps, width, height)
        log.debug(
            "Input: %dx%d @ %.3f fps, %.2f s, %d bytes, audio %d kbps",
            width, height, fps, info[0], info[1], audio_kbps,
        )
        return info
    except subprocess.CalledProcessError as e:
        log.error("ffprobe failed on %s (exit code %s): %s", input_path, e.returncode, (e.stderr or "").strip())
        return None
    except (ValueError, OSError, KeyError, IndexError) as e:
        log.error("Could not read video metadata from %s: %r", input_path, e)
        return None


def get_smart_split_point(input_path: str, duration: float) -> float:
    """Find a keyframe-aligned split point at 50% of the video's total byte weight.

    Streams packet metadata line-by-line in a single pass with O(1) memory,
    tracking cumulative byte weight and keyframe checkpoints.

    Args:
        input_path: Path to the input media file.
        duration: Total duration in seconds.

    Returns:
        Timestamp in seconds closest to 50% of byte load. Falls back to duration/2
        if keyframe analysis fails or no keyframes are found.
    """
    log.info("Analyzing for smart split point...")
    ffprobe_exe = get_resource_path("ffprobe")
    cmd = [
        ffprobe_exe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,size,flags", "-of", "csv=p=0", input_path
    ]
    keyframes: List[Tuple[float, int]] = []
    total_bytes = 0

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=get_clean_env()
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            parts = line.strip().split(",")
            if len(parts) >= 3:
                try:
                    pts_time = float(parts[0])
                    pkt_size = int(parts[1])
                    flags = parts[2]
                    total_bytes += pkt_size
                    if "K" in flags and pts_time > 0.0:
                        keyframes.append((pts_time, total_bytes))
                except ValueError:
                    continue
        proc.wait()

        if total_bytes > 0 and keyframes:
            target_bytes = total_bytes / 2
            best_split = min(keyframes, key=lambda kf: abs(kf[1] - target_bytes))[0]
            log.debug("Split point: %.3f s (%d keyframes, %d bytes)", best_split, len(keyframes), total_bytes)
            return best_split
        log.debug(
            "No usable keyframes (ffprobe exit %s, %d bytes); splitting at midpoint %.3f s",
            proc.returncode, total_bytes, duration / 2,
        )
    except Exception as e:
        log.warning("Smart split analysis failed (%s). Falling back to midpoint.", e)

    return duration / 2


# --- Optimization ---

def get_optimal_settings(target_mb: float, duration: float, width: int, height: int, fps: float) -> Tuple[int, float]:
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
    src_h: int,
    start: Optional[float],
    end: Optional[float],
    output_path: str,
    tgt_h: int,
    tgt_fps: float,
    audio_kbps: int = 128,
    tier: Tier = Tier.LEGACY,
    vaapi_node: Optional[str] = None,
) -> List[str]:
    """Build a single-pass FFmpeg command for hardware encoders.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        encoder: FFmpeg encoder name.
        codec_type: "hevc" or "h264".
        bitrate_k: Target bitrate ceiling in kbps.
        src_fps: Source frames per second.
        src_h: Source height in pixels.
        start: Optional start timestamp in seconds.
        end: Optional end timestamp in seconds.
        output_path: Destination video path.
        tgt_h: Target height for scaling. Set to 0 or equal to src_h to skip scaling.
        tgt_fps: Target frames per second. Set equal to src_fps to skip FPS conversion.
        audio_kbps: Probed audio bitrate in kbps (0 for silent video).
        tier: Hardware pipeline tier; LEGACY reproduces the pre-v1.2 arguments.
        vaapi_node: Optional VA-API render node (e.g., `/dev/dri/renderD128`).

    Returns:
        A command list ready for subprocess execution.

    Examples:
        >>> cmd = build_single_pass_cmd("ffmpeg", "in.mp4", "hevc_nvenc", "hevc", 2000, 60.0, 1080, None, None, "out.mp4", 720, 60.0)
        >>> "-hwaccel_output_format" in cmd
        True
    """
    video = build_video_args(
        backend_for_encoder(encoder), tier, src_h, tgt_h, src_fps, tgt_fps, codec_type, vaapi_node
    )
    cmd: List[str] = [ffmpeg_exe, "-y", *video.pre_input]

    # Fast input seeking placed before -i
    if start is not None:
        cmd.extend(["-ss", str(start)])
    if end is not None:
        cmd.extend(["-to", str(end)])

    cmd.extend(["-i", input_path])

    if start is not None or end is not None:
        cmd.extend(["-avoid_negative_ts", "make_zero"])

    cmd.extend(video.post_input)
    if video.filters:
        cmd.extend(["-vf", ",".join(video.filters)])

    cmd.extend(["-c:v", encoder, "-b:v", f"{bitrate_k}k"])

    # Hardware rate control (Constrained VBR ceiling)
    if "nvenc" in encoder:
        cmd.extend([
            "-rc:v", "vbr",
            "-preset", "p5",
            "-multipass", "fullres",
            "-rc-lookahead", "32",
            "-spatial-aq", "1",
            "-temporal-aq", "1",
        ])
    elif "amf" in encoder:
        cmd.extend([
            "-usage", "transcoding",
            "-quality", "quality",
            "-rc", "vbr_peak",
            "-preanalysis", "1",
            "-vbaq", "1",
        ])
    elif "qsv" in encoder:
        cmd.extend([
            "-preset", "medium",
            "-look_ahead", "1",
            "-look_ahead_depth", "40",
        ])
    elif "videotoolbox" in encoder:
        cmd.extend(["-allow_sw", "1", "-realtime", "0"])
    elif encoder in ("libx265", "libx264"):
        cmd.extend(["-preset", "medium"])

    if codec_type == "hevc":
        cmd.extend(["-tag:v", "hvc1"])
    elif codec_type == "h264":
        cmd.extend(["-tag:v", "avc1"])

    cmd.extend(["-maxrate:v", f"{bitrate_k}k", "-bufsize:v", f"{bitrate_k * 2}k"])

    # Stream mapping
    cmd.extend(["-map", "0:v:0"])

    # Audio Handling
    if audio_kbps == 0:
        cmd.append("-an")
    else:
        cmd.extend(["-map", "0:a:0?"])
        if audio_kbps > 160 and check_audio_encoder_available("aac"):
            cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])

    # Single-pipe progress streaming on stderr
    cmd.extend([
        "-movflags", "+faststart",
        "-loglevel", "error",
        "-progress", "pipe:2",
        "-nostats",
        output_path,
    ])
    return cmd



# --- Pipeline Tier Probing ---

def build_probe_cmd(
    ffmpeg_exe: str,
    input_path: str,
    encoder: str,
    codec_type: str,
    tier: Tier,
    src_h: int,
    tgt_h: int,
    src_fps: float,
    tgt_fps: float,
    vaapi_node: Optional[str] = None,
) -> List[str]:
    """Build a one-second null-output encode of the real input for one tier.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        encoder: FFmpeg encoder name.
        codec_type: "hevc" or "h264".
        tier: Pipeline tier to exercise.
        src_h: Source height in pixels.
        tgt_h: Target height for scaling.
        src_fps: Source frames per second.
        tgt_fps: Target frames per second.
        vaapi_node: Optional VA-API render node.

    Returns:
        A command list ready for subprocess execution.

    Examples:
        >>> cmd = build_probe_cmd("ffmpeg", "in.mp4", "hevc_vaapi", "hevc", Tier.UPLOAD, 1080, 720, 60.0, 30.0)
        >>> cmd[cmd.index("-t") + 1]
        '1'
    """
    video = build_video_args(
        backend_for_encoder(encoder), tier, src_h, tgt_h, src_fps, tgt_fps, codec_type, vaapi_node
    )
    cmd: List[str] = [ffmpeg_exe, "-hide_banner", "-v", "error", *video.pre_input]
    cmd.extend(["-t", "1", "-i", input_path, *video.post_input])
    if video.filters:
        cmd.extend(["-vf", ",".join(video.filters)])
    cmd.extend(["-c:v", encoder, "-an", "-f", "null", "-"])
    return cmd


def probe_pipeline_tier(
    ffmpeg_exe: str,
    input_path: str,
    encoder: str,
    codec_type: str,
    src_h: int,
    tgt_h: int,
    src_fps: float,
    tgt_fps: float,
    vaapi_node: Optional[str] = None,
) -> Tier:
    """Select the most GPU-resident pipeline tier that works on the real input.

    Tries ZERO_COPY, then UPLOAD, and falls back to LEGACY. CPU encoders return
    LEGACY without probing.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        encoder: FFmpeg encoder name.
        codec_type: "hevc" or "h264".
        src_h: Source height in pixels.
        tgt_h: Target height for scaling.
        src_fps: Source frames per second.
        tgt_fps: Target frames per second.
        vaapi_node: Optional VA-API render node.

    Returns:
        The first tier whose probe encode succeeds, else `Tier.LEGACY`.

    Example:
        >>> probe_pipeline_tier("ffmpeg", "in.mp4", "libx265", "hevc", 1080, 720, 60.0, 30.0)
        <Tier.LEGACY: 'legacy'>
    """
    if backend_for_encoder(encoder) is None:
        return Tier.LEGACY
    for tier in PIPELINE_PROBE_TIERS:
        cmd = build_probe_cmd(
            ffmpeg_exe, input_path, encoder, codec_type, tier,
            src_h, tgt_h, src_fps, tgt_fps, vaapi_node,
        )
        if _run_probe(cmd, PIPELINE_PROBE_TIMEOUT_S, f"{encoder} {tier.label} pipeline"):
            log.debug("Pipeline tier: %s", tier.label)
            return tier
    log.debug("Pipeline tier: %s (no hardware-resident tier passed its probe)", Tier.LEGACY.label)
    return Tier.LEGACY
