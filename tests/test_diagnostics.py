"""Tests for the in-memory run log and the failure-only error log file."""

import logging
from pathlib import Path

import pytest

from videocompress import cli, diagnostics


@pytest.fixture
def buffer() -> diagnostics.MemoryLogHandler:
    """Configure package logging for one test and remove the handlers afterwards."""
    handler = diagnostics.configure_logging(verbose=False)
    yield handler
    logger = logging.getLogger(diagnostics.PACKAGE_LOGGER)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)


def test_buffer_captures_package_debug_records(buffer: diagnostics.MemoryLogHandler) -> None:
    logging.getLogger("videocompress.core").debug("Pipeline tier: %s", "zero-copy")
    assert buffer.lines and buffer.lines[-1].endswith("videocompress.core: Pipeline tier: zero-copy")
    assert buffer.has_errors is False


def test_buffer_ignores_other_loggers(buffer: diagnostics.MemoryLogHandler) -> None:
    logging.getLogger("rich").error("unrelated")
    assert buffer.lines == [] and buffer.has_errors is False


def test_error_record_marks_run_as_failed(buffer: diagnostics.MemoryLogHandler) -> None:
    logging.getLogger("videocompress.encode").error("Stitching segments failed")
    assert buffer.has_errors is True


def test_error_log_is_written_next_to_input(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    path = diagnostics.write_error_log("report\n", str(source))
    assert path == tmp_path / "clip_compress-error.log"
    assert path.read_text(encoding="utf-8") == "report\n"


def test_error_log_falls_back_to_temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics.tempfile, "gettempdir", lambda: str(tmp_path))
    path = diagnostics.write_error_log("report\n", str(tmp_path / "missing-dir" / "clip.mp4"))
    assert path == tmp_path / "videocompress_compress-error.log"


def test_report_has_header_then_records() -> None:
    report = diagnostics.build_report(
        ["[ERROR] x"], ["videocompress", "in.mp4"], "/bin/ffmpeg", "ffmpeg version n9.0.2", "1.2.0"
    )
    lines = report.splitlines()
    assert lines[0].startswith("Video-Compression 1.2.0 error log")
    assert "FFmpeg version: ffmpeg version n9.0.2" in lines
    assert lines[-1] == "[ERROR] x"


def test_no_log_file_without_logged_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, buffer: diagnostics.MemoryLogHandler
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    monkeypatch.setattr(cli, "configure_logging", lambda verbose: buffer)
    monkeypatch.setattr(cli, "compress_video", lambda **kw: (False, "Already smaller than target"))
    monkeypatch.setattr(cli.sys, "argv", ["videocompress", str(source)])
    with pytest.raises(SystemExit):
        cli.main()
    assert not (tmp_path / "clip_compress-error.log").exists()


def test_log_file_written_on_logged_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, buffer: diagnostics.MemoryLogHandler
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")

    def failing_compress(**kwargs: object) -> tuple:
        logging.getLogger("videocompress.encode").error("Stitching segments failed: no bsf")
        return False, "Stitching segments failed"

    monkeypatch.setattr(cli, "configure_logging", lambda verbose: buffer)
    monkeypatch.setattr(cli, "compress_video", failing_compress)
    monkeypatch.setattr(cli, "read_ffmpeg_version", lambda exe: "ffmpeg version test")
    monkeypatch.setattr(cli.sys, "argv", ["videocompress", str(source)])
    with pytest.raises(SystemExit):
        cli.main()
    text = (tmp_path / "clip_compress-error.log").read_text(encoding="utf-8")
    assert "ffmpeg version test" in text and "Stitching segments failed: no bsf" in text
