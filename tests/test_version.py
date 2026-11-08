"""Tests for version resolution in source runs and built executables."""

import importlib.util
from pathlib import Path

import pytest

from videocompress import version

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_build_module():  # type: ignore[no-untyped-def]
    """Import the repository's build.py as a module."""
    spec = importlib.util.spec_from_file_location("build_script", REPO_ROOT / "build.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_embedded_build_version_wins() -> None:
    assert version.resolve_version("1.2.0-rc1") == "1.2.0-rc1"


def test_unknown_without_build_version_or_metadata() -> None:
    assert version.resolve_version(None, "no-such-package-xyz") == "unknown"


def test_app_version_uses_assigned_build_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version, "BUILD_VERSION", "9.9.9")
    assert version.app_version() == "9.9.9"


@pytest.mark.parametrize(("tag", "expected"), [("v1.2.0-rc1", "1.2.0-rc1"), ("1.3.0", "1.3.0")])
def test_build_version_comes_from_release_tag(monkeypatch: pytest.MonkeyPatch, tag: str, expected: str) -> None:
    monkeypatch.setenv("BUILD_VERSION", tag)
    assert load_build_module().resolve_build_version() == expected


def test_preset_wrapper_embeds_version(tmp_path: Path) -> None:
    script = Path(load_build_module().create_preset_script(100, "hevc", str(tmp_path), "1.2.0-rc1"))
    text = script.read_text(encoding="utf-8")
    assert "videocompress.version.BUILD_VERSION = '1.2.0-rc1'" in text
    assert text.index("BUILD_VERSION") < text.index("videocompress.main()")


@pytest.mark.parametrize(("tags", "expected"), [
    (["ffmpeg-n9.0.2", "ffmpeg-n9.0.2-r2", "ffmpeg-n8.1"], "ffmpeg-n9.0.2-r2"),
    (["ffmpeg-n9.0.2-r3", "ffmpeg-n9.1"], "ffmpeg-n9.1"),
    (["ffmpeg-n9.0.10", "ffmpeg-n9.0.9-r5"], "ffmpeg-n9.0.10"),
    (["ffmpeg-n9.0.2-r10", "ffmpeg-n9.0.2-r9"], "ffmpeg-n9.0.2-r10"),
    (["v1.2.0", "nightly"], None),
    ([], None),
])
def test_latest_ffmpeg_tag_by_version_then_revision(tags: list, expected: object) -> None:
    assert load_build_module().select_latest_ffmpeg_tag(tags) == expected


def test_pinned_ffmpeg_tag_skips_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FFMPEG_RELEASE_TAG", "ffmpeg-n8.1")
    build = load_build_module()
    monkeypatch.setattr(build, "fetch_ffmpeg_release_tags", lambda repo: pytest.fail("should not query"))
    assert build.resolve_ffmpeg_release_tag() == "ffmpeg-n8.1"


def test_lookup_failure_falls_back_to_github_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FFMPEG_RELEASE_TAG", raising=False)
    build = load_build_module()

    def offline(repo: str) -> list:
        raise build.urllib.error.URLError("offline")

    monkeypatch.setattr(build, "fetch_ffmpeg_release_tags", offline)
    assert build.resolve_ffmpeg_release_tag() == "latest"
