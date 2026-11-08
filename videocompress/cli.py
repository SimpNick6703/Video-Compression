"""
Command-line interface for the video compression package.

Usage:
    python -m videocompress <input.mp4> [output.mp4] [target_mb] [hevc/h264]
"""

import sys
import os
import logging
from typing import Optional, List

from videocompress.core import get_resource_path
from videocompress.diagnostics import (
    MemoryLogHandler,
    build_report,
    configure_logging,
    read_ffmpeg_version,
    write_error_log,
)

from videocompress.encode import compress_video
from videocompress.version import app_version

log = logging.getLogger(__name__)

DEFAULT_TARGET_MB: float = 100.0


def _try_parse_positive_float(value: str) -> Optional[float]:
    """Attempt to parse a string into a positive float.

    Args:
        value: Input string to parse.

    Returns:
        Float value if successfully parsed and > 0, else None.
    """
    try:
        f = float(value)
        return f if f > 0.0 else None
    except ValueError:
        return None


def main() -> None:
    """Entry point for the command-line interface.

    Parses positional arguments, flags, target size, and invokes the
    compression engine.
    """
    if len(sys.argv) < 2:
        sys.stdout.write("Usage: videocompress <input.mp4> [output.mp4] [size_in_mb] [hevc/h264] [--verbose]\n")
        sys.exit(1)

    verbose = "--verbose" in sys.argv or "-v" in sys.argv or os.environ.get("DEBUG") == "1"
    log_buffer = configure_logging(verbose)
    log.debug("Video-Compression %s", app_version())

    input_file: Optional[str] = None
    output_file: Optional[str] = None
    target_mb: float = DEFAULT_TARGET_MB
    codec_type: str = "hevc"

    raw_args = [a for a in sys.argv[1:] if a not in ("--verbose", "-v")]
    potential_paths: List[str] = []

    for arg_s in raw_args:
        arg_lower = arg_s.lower()

        if arg_lower in ("hevc", "h264"):
            codec_type = arg_lower
        elif os.path.exists(arg_s):
            potential_paths.append(arg_s)
        else:
            num = _try_parse_positive_float(arg_s)
            if num is not None:
                target_mb = num
            else:
                potential_paths.append(arg_s)

    # First pass: Identify input file (must exist)
    for path in potential_paths:
        if os.path.exists(path):
            input_file = path
            break

    # Second pass: Identify output file (first non-input path)
    for path in potential_paths:
        if path != input_file:
            output_file = path
            break

    if input_file is None:
        sys.stderr.write("Error: Valid input video file not provided or found.\n")
        sys.exit(1)

    inp_path = str(input_file)

    try:
        success, result = compress_video(
            input_path=inp_path,
            output_path=output_file,
            target_size_mb=target_mb,
            codec_type=codec_type,
        )
        if not success:
            if result and "Cancelled" not in result:
                sys.stderr.write(f"Compression failed: {result}\n")
            _save_error_log(log_buffer, inp_path)
            sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        log.exception("Unexpected error")
        _save_error_log(log_buffer, inp_path)
        raise


def _save_error_log(log_buffer: MemoryLogHandler, input_path: str) -> None:
    """Write the buffered run log next to the input when an error was logged.

    Args:
        log_buffer: Handler holding this run's log records.
        input_path: Input video path, used to place the log file.
    """
    if not log_buffer.has_errors:
        return
    ffmpeg_exe = get_resource_path("ffmpeg")
    report = build_report(
        log_buffer.lines, sys.argv, ffmpeg_exe, read_ffmpeg_version(ffmpeg_exe), app_version()
    )
    path = write_error_log(report, input_path)
    if path is not None:
        sys.stderr.write(f"Details saved to: {path}\n")


if __name__ == "__main__":
    main()
