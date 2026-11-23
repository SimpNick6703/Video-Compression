"""Tests for the interactive custom target size used by the Windows context menu."""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import pytest

from videocompress import cli

Call = Dict[str, Union[str, float, None]]


def run_cli(monkeypatch: pytest.MonkeyPatch, argv: List[str], answers: List[str]) -> Call:
    calls: List[Call] = []
    replies = iter(answers)

    def fake_compress(input_path: str, output_path: Optional[str], target_size_mb: float, codec_type: str) -> tuple:
        calls.append({"input": input_path, "output": output_path, "mb": target_size_mb, "codec": codec_type})
        return True, "out.mp4"

    def fake_input(prompt: str = "") -> str:
        try:
            return next(replies)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(cli, "compress_video", fake_compress)
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(sys, "argv", ["videocompress", *argv])
    cli.main()
    return calls[0]


def test_custom_prompts_for_size(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    call = run_cli(monkeypatch, [str(source), "--custom", "hevc"], ["35"])
    assert call == {"input": str(source), "output": None, "mb": 35.0, "codec": "hevc"}


def test_custom_reprompts_until_valid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    call = run_cli(monkeypatch, [str(source), "--custom"], ["", "abc", "-5", "0", "12.5"])
    assert call["mb"] == 12.5


def test_custom_without_answer_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, [str(source), "--custom"], [])
    assert exc.value.code == 1


def test_ctrl_c_at_the_prompt_exits_130(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")

    def interrupted(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    monkeypatch.setattr(sys, "argv", ["videocompress", str(source), "--custom"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 130
