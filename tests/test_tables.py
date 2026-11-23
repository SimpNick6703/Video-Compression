"""Every bordered table and panel has rounded corners, including in legacy Windows consoles."""

import io

import pytest

from videocompress import progress


def test_tables_and_panels_are_rounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich swaps rounded boxes for square ones on legacy Windows unless safe boxes are off.

    Example:
        >>> # pytest tests/test_tables.py
    """
    output = io.StringIO()
    monkeypatch.setattr(progress.console, "file", output)
    monkeypatch.setattr(progress.console, "legacy_windows", True)
    monkeypatch.setattr(progress.console, "_width", 120)

    progress.show_encoder_detection([("hevc_nvenc", "selected")])
    progress.console.print(progress.build_dual_display(40, 60, 1, 1, 1, 1, 1, 1, 1, 1, "Encoding", 0))
    progress.console.print(progress.build_single_display(50, 1, 1, 1, 1, "Encoding", 0))
    progress.show_result_panel(2, 1, 1, 1.0, "hevc_nvenc", "parallel split")

    text = output.getvalue()
    assert text.count("╭") == 6  # 3 tables, 3 panels
    assert not set(text) & set("┌┐└┘┏┓┡┩")
