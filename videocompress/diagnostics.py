"""
Run diagnostics: buffer log records in memory and write them to a file only on failure.

Successful runs leave nothing behind. When a run fails with a logged error, the
buffered records are written next to the input video so they can be attached
to a bug report.
"""

import logging
import platform
import subprocess
import sys
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, List, Optional

PACKAGE_LOGGER = "videocompress"
LOG_FORMAT = "[%(levelname)s] %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s"
MAX_RECORDS = 2000
ERROR_LOG_SUFFIX = "_compress-error.log"
VERSION_TIMEOUT_S = 10


class MemoryLogHandler(logging.Handler):
    """Keep formatted log records in a bounded in-memory buffer.

    Example:
        >>> handler = MemoryLogHandler()
        >>> handler.has_errors
        False
    """

    def __init__(self, capacity: int = MAX_RECORDS) -> None:
        super().__init__(level=logging.DEBUG)
        self._lines: Deque[str] = deque(maxlen=capacity)
        self._has_errors = False
        self.setFormatter(logging.Formatter(FILE_FORMAT, datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        """Store one formatted record and remember whether any error occurred."""
        try:
            self._lines.append(self.format(record))
        except (TypeError, ValueError):
            self.handleError(record)
        if record.levelno >= logging.ERROR:
            self._has_errors = True

    @property
    def has_errors(self) -> bool:
        """True once a record at ERROR level or above has been captured."""
        return self._has_errors

    @property
    def lines(self) -> List[str]:
        """Snapshot of the buffered, formatted records."""
        return list(self._lines)


def configure_logging(verbose: bool) -> MemoryLogHandler:
    """Attach the in-memory buffer and a console handler to the package logger.

    The console shows DEBUG records with `--verbose` and only warnings otherwise;
    the buffer always captures DEBUG records for a possible error log.

    Args:
        verbose: Whether to print debug records to the console.

    Returns:
        The memory handler holding this run's records.

    Example:
        >>> buffer = configure_logging(verbose=False)
        >>> isinstance(buffer, MemoryLogHandler)
        True
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    if not verbose:
        # Errors already reach the user via the failure message and the error log file.
        console.addFilter(lambda record: record.levelno < logging.ERROR)

    buffer = MemoryLogHandler()
    logger.addHandler(console)
    logger.addHandler(buffer)
    return buffer


def read_ffmpeg_version(ffmpeg_exe: str) -> str:
    """Return the first line of `ffmpeg -version`, or a short reason it is unavailable.

    Args:
        ffmpeg_exe: Path or name of the ffmpeg executable.

    Example:
        >>> read_ffmpeg_version("/nonexistent/ffmpeg").startswith("unavailable")
        True
    """
    try:
        res = subprocess.run(
            [ffmpeg_exe, "-version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"unavailable ({e})"
    first_line = res.stdout.splitlines()[0] if res.stdout else ""
    return first_line or f"unavailable (exit code {res.returncode})"


def build_report(lines: List[str], argv: List[str], ffmpeg_exe: str, ffmpeg_version: str, app_version: str) -> str:
    """Assemble the error log text: a short environment header, then the run's records.

    Example:
        >>> "Python" in build_report(["x"], ["videocompress", "in.mp4"], "ffmpeg", "ffmpeg version n9", "1.2.0")
        True
    """
    header = [
        f"Video-Compression {app_version} error log ({datetime.now().isoformat(timespec='seconds')})",
        f"Command: {' '.join(argv)}",
        f"Platform: {platform.platform()} ({platform.machine()})",
        f"Python: {platform.python_version()}{' (frozen executable)' if getattr(sys, 'frozen', False) else ''}",
        f"FFmpeg: {ffmpeg_exe}",
        f"FFmpeg version: {ffmpeg_version}",
        "",
    ]
    return "\n".join(header + lines) + "\n"


def error_log_path(input_path: Optional[str]) -> Path:
    """Place the error log next to the input video, or in the temp directory without one.

    Example:
        >>> error_log_path("/videos/clip.mp4").name
        'clip_compress-error.log'
    """
    if input_path:
        source = Path(input_path)
        return source.with_name(f"{source.stem}{ERROR_LOG_SUFFIX}")
    return Path(tempfile.gettempdir()) / f"videocompress{ERROR_LOG_SUFFIX}"


def write_error_log(report: str, input_path: Optional[str]) -> Optional[Path]:
    """Write the report next to the input, falling back to the temp directory.

    Args:
        report: Full log text.
        input_path: Input video path used to place the log, if known.

    Returns:
        Path of the written file, or None if no location was writable.

    Example:
        >>> path = write_error_log("text\\n", None)
        >>> path is None or path.name.endswith("_compress-error.log")
        True
    """
    candidates = [error_log_path(input_path), error_log_path(None)]
    for path in dict.fromkeys(candidates):
        try:
            path.write_text(report, encoding="utf-8")
            return path
        except OSError as e:
            logging.getLogger(__name__).debug("Could not write error log to %s: %s", path, e)
    return None
