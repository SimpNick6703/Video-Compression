"""Tier selection by probing and the runtime retry with the legacy pipeline."""

import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pytest

from videocompress import core, encode
from videocompress.hwpipeline import Tier

ProbeArgs = Tuple[str, str, str, str, int, int, float, float]
PROBE_ARGS: ProbeArgs = ("ffmpeg", "in.mp4", "hevc_vaapi", "hevc", 1080, 720, 60.0, 30.0)


def tier_of(cmd: List[str]) -> Tier:
    """Classify a probe command by its tier-specific arguments."""
    if "-hwaccel" in cmd:
        return Tier.ZERO_COPY
    return Tier.UPLOAD


def install_runner(
    monkeypatch: pytest.MonkeyPatch,
    failing: Callable[[Tier], Optional[BaseException]],
) -> List[Tier]:
    """Replace `subprocess.run` in core; return the list of probed tiers."""
    seen: List[Tier] = []

    def fake_run(cmd: List[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        tier = tier_of(cmd)
        seen.append(tier)
        error = failing(tier)
        if error is not None:
            raise error
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    return seen


def call_error() -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(1, "ffmpeg", stderr="Impossible to convert between the formats")


def test_zero_copy_selected_when_probe_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_runner(monkeypatch, lambda t: None)
    assert core.probe_pipeline_tier(*PROBE_ARGS) is Tier.ZERO_COPY
    assert seen == [Tier.ZERO_COPY]


def test_upload_selected_when_zero_copy_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_runner(monkeypatch, lambda t: call_error() if t is Tier.ZERO_COPY else None)
    assert core.probe_pipeline_tier(*PROBE_ARGS) is Tier.UPLOAD
    assert seen == [Tier.ZERO_COPY, Tier.UPLOAD]


@pytest.mark.parametrize("error", [
    call_error(),
    subprocess.TimeoutExpired("ffmpeg", 20),
    OSError("exec failed"),
], ids=["called-process", "timeout", "oserror"])
def test_legacy_selected_when_all_probes_fail(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    seen = install_runner(monkeypatch, lambda t: error)
    assert core.probe_pipeline_tier(*PROBE_ARGS) is Tier.LEGACY
    assert seen == [Tier.ZERO_COPY, Tier.UPLOAD]


def test_cpu_encoder_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_runner(monkeypatch, lambda t: None)
    args = ("ffmpeg", "in.mp4", "libx265", "hevc", 1080, 720, 60.0, 30.0)
    assert core.probe_pipeline_tier(*args) is Tier.LEGACY
    assert seen == []


def test_probe_runs_one_second_null_encode_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_run(cmd: List[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"], captured["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    core.probe_pipeline_tier(*PROBE_ARGS)
    cmd = captured["cmd"]
    assert cmd.index("-t") < cmd.index("-i")
    assert cmd[-3:] == ["-f", "null", "-"]
    assert captured["kwargs"]["timeout"] == 20


def test_fallback_helper_retries_once_with_legacy() -> None:
    calls: List[Tier] = []

    def attempt(tier: Tier) -> Tuple[bool, str]:
        calls.append(tier)
        return tier is Tier.LEGACY, "boom"

    ok, _, used = encode._encode_hw_with_fallback(attempt, Tier.ZERO_COPY)
    assert (ok, used, calls) == (True, Tier.LEGACY, [Tier.ZERO_COPY, Tier.LEGACY])


def test_fallback_helper_does_not_retry_legacy() -> None:
    calls: List[Tier] = []

    def attempt(tier: Tier) -> Tuple[bool, str]:
        calls.append(tier)
        return False, "boom"

    ok, err, used = encode._encode_hw_with_fallback(attempt, Tier.LEGACY)
    assert (ok, err, used, calls) == (False, "boom", Tier.LEGACY, [Tier.LEGACY])


def test_keyboard_interrupt_propagates() -> None:
    def attempt(tier: Tier) -> Tuple[bool, str]:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        encode._encode_hw_with_fallback(attempt, Tier.ZERO_COPY)


def test_compress_video_retries_with_legacy_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    output = tmp_path / "out.mp4"
    tiers: List[Tier] = []

    def fake_split(**kwargs: object) -> Tuple[bool, str]:
        tier = kwargs["tier"]
        tiers.append(tier)  # type: ignore[arg-type]
        if tier is Tier.LEGACY:
            output.write_bytes(b"encoded")
            return True, ""
        return False, "Split encode failed"

    monkeypatch.setattr(encode, "get_video_info", lambda p: (10.0, 500 * 1024 * 1024, 60.0, 128, 1920, 1080))
    monkeypatch.setattr(encode, "select_best_encoder", lambda c: ("hevc_vaapi", [("hevc_vaapi", "selected")]))
    monkeypatch.setattr(encode, "resolve_vaapi_node", lambda enc: "/dev/dri/renderD128")
    monkeypatch.setattr(encode, "probe_pipeline_tier", lambda *a, **k: Tier.ZERO_COPY)
    monkeypatch.setattr(encode, "get_smart_split_point", lambda p, d: 5.0)
    monkeypatch.setattr(encode, "_encode_hw_split", fake_split)

    ok, result = encode.compress_video(str(source), str(output), 100.0, "hevc")
    assert ok and result == str(output)
    assert tiers == [Tier.ZERO_COPY, Tier.LEGACY]
