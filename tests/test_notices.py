"""Tests for the license notices shipped next to the built executables."""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def build():  # type: ignore[no-untyped-def]
    """Import the repository's build.py as a module."""
    spec = importlib.util.spec_from_file_location("build_script", REPO_ROOT / "build.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("member", "expected"), [
    ("BUILD_INFO.txt", "ffmpeg-licenses/BUILD_INFO.txt"),
    ("licenses/x265-COPYING", "ffmpeg-licenses/licenses/x265-COPYING"),
    ("ffmpeg", None),
    ("licenses/nested/file", None),
    ("../BUILD_INFO.txt", None),
    ("licenses/..", None),
])
def test_notice_destination_keeps_only_license_files(build, member: str, expected: object) -> None:  # type: ignore[no-untyped-def]
    result = build.notice_destination(member)
    assert (result.as_posix() if result else None) == expected


def test_notices_include_ffmpeg_build_info_and_licenses(build, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "licenses").mkdir()
    (tmp_path / "BUILD_INFO.txt").write_text("ffmpeg-minimal-builds ffmpeg-n9.0.2-r2\n  x264: commit abc\n")
    (tmp_path / "licenses" / "x264-COPYING").write_text("GNU GENERAL PUBLIC LICENSE Version 2\n")
    text = build.build_third_party_notices("1.2.0", tmp_path)
    assert text.startswith("Video-Compression 1.2.0 third-party notices")
    assert "x264: commit abc" in text and "--- x264-COPYING ---" in text
    assert "Python " in text and "runtime" in text


def test_notices_point_to_archive_without_build_info(build, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    text = build.build_third_party_notices("1.2.0", tmp_path / "missing")
    assert "BUILD_INFO.txt and licenses/ inside the matching release archive" in text


def test_bundled_package_licenses_are_included(build, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    text = build.build_third_party_notices("1.2.0", tmp_path)
    for name in ("rich", "markdown-it-py", "mdurl", "Pygments"):
        assert f"\n{name} " in text
    assert "Permission is hereby granted" in text
