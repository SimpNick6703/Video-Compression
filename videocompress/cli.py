"""
Command-line interface for the video compression package.

Usage:
    python -m videocompress <input.mp4> [output.mp4] [target_mb] [hevc/h264]
"""

import sys
import os
import logging
from typing import Optional, List

from videocompress.encode import compress_video

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

    if "--verbose" in sys.argv or "-v" in sys.argv or os.environ.get("DEBUG") == "1":
        logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(name)s: %(message)s")

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
            sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
