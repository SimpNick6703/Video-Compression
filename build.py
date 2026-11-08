#!/usr/bin/env python3
"""
Build script for Video-Compression executables.

Generates preset executables (20mb, 50mb, 100mb, 1024mb) from the videocompress package
by creating temporary copies with hardcoded target sizes, then compiling with PyInstaller.

Usage:
    python build.py              # Build all presets (downloads FFmpeg, cleans after)
    python build.py --verbose    # Build and keep build/, *.spec, and downloaded FFmpeg
"""

import os
import sys
import re
import shutil
import subprocess
import tempfile
import tarfile
import zipfile
import json
import logging
import platform
import sysconfig
import importlib.metadata
import urllib.error
import urllib.request
from typing import Optional
from pathlib import Path
import concurrent.futures

# --- Configuration ---
PRESET_SIZES = [20, 50, 100, 1024]
PRESET_CODECS = ["hevc", "h264"]
SOURCE_PACKAGE = "videocompress"
FFMPEG_BINARIES = ["ffmpeg", "ffprobe"]
OUTPUT_DIR = "dist"

# Filters the hardware-resident pipelines rely on, per platform
REQUIRED_FILTERS = {
    "linux": ("scale_cuda", "hwupload_cuda", "scale_vaapi", "hwupload", "fps"),
    "win32": ("scale_cuda", "hwupload_cuda", "vpp_amf", "vpp_qsv", "hwmap", "hwupload", "fps"),
    "darwin": ("scale_vt", "hwupload", "fps"),
}
FILTER_FLAGS_PATTERN = re.compile(r"[TSC.|]{2,3}")
FILTER_CHECK_TIMEOUT_S = 30

# Minimal FFmpeg repository. FFMPEG_RELEASE_TAG pins a release; unset or "latest"
# selects the highest FFmpeg version, then the highest revision (e.g. ffmpeg-n9.0.2-r2).
FFMPEG_REPO = os.environ.get("FFMPEG_REPO", "SimpNick6703/ffmpeg-minimal-builds")
FFMPEG_RELEASE_TAG = os.environ.get("FFMPEG_RELEASE_TAG", "latest")
FFMPEG_TAG_PATTERN = re.compile(r"^ffmpeg-n(\d+(?:\.\d+)*)(?:-r(\d+))?$")
RELEASES_API_TIMEOUT_S = 30

# License notices shipped next to the executables
FFMPEG_NOTICES_DIR = "ffmpeg-licenses"
LICENSE_FILE_PATTERN = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)
BUNDLED_DISTRIBUTIONS = (
    ("pyinstaller", "bootloader: GPL-2.0-or-later with a distribution exception"),
    ("rich", "MIT"),
    ("markdown-it-py", "MIT"),
    ("mdurl", "MIT"),
    ("Pygments", "BSD-2-Clause"),
)


# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def get_macos_arch() -> str:
    """Return architecture string for macOS (arm64 or x86_64)."""
    import platform
    machine = platform.machine().lower()
    return "arm64" if "arm" in machine or "aarch64" in machine else "x86_64"


def parse_ffmpeg_release_tag(tag: str) -> Optional[tuple[tuple[int, ...], int]]:
    """Parse an ffmpeg-minimal-builds tag into (FFmpeg version, revision).

    A tag without a revision suffix counts as revision 1.

    Example:
        >>> parse_ffmpeg_release_tag("ffmpeg-n9.0.2-r2")
        ((9, 0, 2), 2)
        >>> parse_ffmpeg_release_tag("ffmpeg-n9.0.2")
        ((9, 0, 2), 1)
        >>> parse_ffmpeg_release_tag("v1.2.0") is None
        True
    """
    match = FFMPEG_TAG_PATTERN.match(tag)
    if not match:
        return None
    version_parts = tuple(int(part) for part in match.group(1).split("."))
    return version_parts, int(match.group(2) or 1)


def select_latest_ffmpeg_tag(tags: list[str]) -> Optional[str]:
    """Pick the tag with the highest FFmpeg version, then the highest revision.

    Example:
        >>> select_latest_ffmpeg_tag(["ffmpeg-n9.0.2", "ffmpeg-n9.0.2-r2", "ffmpeg-n8.1"])
        'ffmpeg-n9.0.2-r2'
    """
    parsed = [(key, tag) for tag in tags if (key := parse_ffmpeg_release_tag(tag)) is not None]
    return max(parsed)[1] if parsed else None


def fetch_ffmpeg_release_tags(repo: str) -> list[str]:
    """List published (non-draft, non-prerelease) release tags of the FFmpeg repository.

    Uses GITHUB_TOKEN or GH_TOKEN when set, which avoids the low anonymous
    API rate limit on shared CI runners.

    Raises:
        urllib.error.URLError: On network or HTTP failure.
        ValueError: If the response is not the expected JSON list.
    """
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "video-compression-build"},
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=RELEASES_API_TIMEOUT_S) as response:
        releases = json.loads(response.read().decode("utf-8"))
    if not isinstance(releases, list):
        raise ValueError("Unexpected releases API response")
    return [
        release["tag_name"] for release in releases
        if not release.get("draft") and not release.get("prerelease") and "tag_name" in release
    ]


def resolve_ffmpeg_release_tag() -> str:
    """Return the release tag to download: the pinned tag, else the highest-version release.

    Falls back to GitHub's "latest" alias if the releases cannot be listed.
    """
    if FFMPEG_RELEASE_TAG != "latest":
        return FFMPEG_RELEASE_TAG
    try:
        tag = select_latest_ffmpeg_tag(fetch_ffmpeg_release_tags(FFMPEG_REPO))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        log.warning("Could not list FFmpeg releases (%s); using GitHub's latest release", e)
        return "latest"
    if tag is None:
        log.warning("No ffmpeg-n<version> releases found; using GitHub's latest release")
        return "latest"
    return tag


def get_ffmpeg_download_info(tag: str) -> tuple[str, str]:
    """Resolve FFmpeg download URL and archive type for the current platform."""
    tag_path = f"download/{tag}" if tag != "latest" else "latest/download"
    base_url = f"https://github.com/{FFMPEG_REPO}/releases/{tag_path}"

    if sys.platform == "win32":
        return f"{base_url}/ffmpeg-minimal-win-x86_64.zip", "zip"
    elif sys.platform == "darwin":
        arch = get_macos_arch()
        return f"{base_url}/ffmpeg-minimal-macos-{arch}.tar.gz", "tar"
    return f"{base_url}/ffmpeg-minimal-linux-x86_64.tar.gz", "tar"


def get_platform_suffix() -> str:
    """Return platform-specific suffix for executable names."""
    if sys.platform == "win32":
        return "win64"
    elif sys.platform == "darwin":
        return f"macos-{get_macos_arch()}"
    return "linux"


def download_file(url: str, dest: str) -> None:
    """Download a file from URL to destination with curl or urllib with retries."""
    import time
    short_url = (url[:60] + "...") if len(url) > 60 else url
    log.info("  Downloading from %s", short_url)
    
    # Use curl if available (handles Cloudflare and redirects smoothly)
    if shutil.which("curl"):
        cmd = [
            "curl", "-fsSL", "--retry", "5", "--retry-delay", "3",
            "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "-o", dest, url
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            return
        log.warning("  curl download failed: %s, falling back to urllib...", res.stderr.strip())

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as response, open(dest, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            return
        except Exception as e:
            if attempt == 3:
                raise
            log.warning("  Download attempt %d failed (%s), retrying...", attempt, e)
            time.sleep(2)


def notice_destination(member_name: str) -> Optional[Path]:
    """Map an FFmpeg archive member to its local path if it is a license or build-info file.

    Only `BUILD_INFO.txt` and plain files directly inside `licenses/` are kept;
    anything else (including path traversal) returns None.

    Example:
        >>> notice_destination("licenses/x264-COPYING").as_posix()
        'ffmpeg-licenses/licenses/x264-COPYING'
        >>> notice_destination("../evil") is None
        True
    """
    parts = Path(member_name).parts
    if parts == ("BUILD_INFO.txt",):
        return Path(FFMPEG_NOTICES_DIR) / "BUILD_INFO.txt"
    if len(parts) == 2 and parts[0] == "licenses" and parts[1] not in ("", ".", ".."):
        return Path(FFMPEG_NOTICES_DIR) / "licenses" / parts[1]
    return None


def _read_text(path: Path) -> str:
    """Read a text file leniently (license files are not always UTF-8)."""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def python_license_file() -> Optional[Path]:
    """Locate the running Python's LICENSE file (it also covers the libraries CPython bundles)."""
    candidates = [Path(sys.base_prefix) / "LICENSE.txt", Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"]
    return next((path for path in candidates if path.is_file()), None)


def distribution_license_texts(name: str) -> list[tuple[str, str]]:
    """Return (file name, text) for each license/notice file of an installed distribution.

    Example:
        >>> distribution_license_texts("no-such-dist-xyz")
        []
    """
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return []
    texts = []
    for file in dist.files or []:
        if ".dist-info" in str(file) and LICENSE_FILE_PATTERN.match(file.name):
            texts.append((file.name, _read_text(Path(dist.locate_file(file)))))
    return texts


def _section(title: str, body: str) -> str:
    return f"{'=' * 78}\n{title}\n{'=' * 78}\n\n{body.strip()}\n\n"


def build_third_party_notices(app_version: str, ffmpeg_notices: Path) -> str:
    """Assemble the third-party notices for the executables built on this platform.

    Covers the bundled FFmpeg (license texts and exact source revisions from its
    archive), the Python runtime, the PyInstaller bootloader and the bundled
    Python packages.

    Args:
        app_version: Version of the executables being built.
        ffmpeg_notices: Directory holding the FFmpeg archive's BUILD_INFO.txt and licenses/.

    Returns:
        The notices text.
    """
    parts = [
        f"Video-Compression {app_version} third-party notices\n\n"
        "Video-Compression is licensed under GPL-3.0-or-later (full text at the end of this file). Its executables\n"
        "bundle the components below. Source code: https://github.com/SimpNick6703/Video-Compression\n\n"
    ]

    build_info = ffmpeg_notices / "BUILD_INFO.txt"
    if build_info.is_file():
        ffmpeg_body = _read_text(build_info)
        for license_file in sorted((ffmpeg_notices / "licenses").glob("*")):
            ffmpeg_body += f"\n\n--- {license_file.name} ---\n\n{_read_text(license_file)}"
    else:
        ffmpeg_body = (
            "FFmpeg and FFprobe from https://github.com/SimpNick6703/ffmpeg-minimal-builds, built from\n"
            "FFmpeg with x264 and x265 under GPL-3.0-or-later. Exact source revisions and license texts\n"
            "are listed in BUILD_INFO.txt and licenses/ inside the matching release archive."
        )
    parts.append(_section("FFmpeg / FFprobe (GPL-3.0-or-later)", ffmpeg_body))

    python_license = python_license_file()
    parts.append(_section(
        f"Python {platform.python_version()} runtime (PSF License and bundled library licenses)",
        _read_text(python_license) if python_license else "See https://docs.python.org/3/license.html",
    ))

    for dist_name, label in BUNDLED_DISTRIBUTIONS:
        texts = distribution_license_texts(dist_name)
        try:
            dist_version = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            log.warning("  %s is not installed; its notice is missing from THIRD_PARTY_NOTICES", dist_name)
            continue
        body = "\n\n".join(f"--- {name} ---\n\n{text}" for name, text in texts) or "License file not found."
        parts.append(_section(f"{dist_name} {dist_version} ({label})", body))

    return "".join(parts)


def write_release_notices(app_version: str) -> None:
    """Write THIRD_PARTY_NOTICES-<platform>.txt (project license plus bundled licenses) next to the executables.

    One file per platform, so parallel CI jobs never upload release assets with the same name.
    """
    notices_path = Path(OUTPUT_DIR) / f"THIRD_PARTY_NOTICES-{get_platform_suffix()}.txt"
    project_license = _section("Video-Compression license (GPL-3.0-or-later)", _read_text(Path("LICENSE")))
    notices = build_third_party_notices(app_version, Path(FFMPEG_NOTICES_DIR))
    notices_path.write_text(notices + project_license, encoding="utf-8")
    log.info("Wrote %s", notices_path)


def download_ffmpeg(tag: str) -> bool:
    """Download FFmpeg binaries for the current platform from the given release tag."""
    url, archive_type = get_ffmpeg_download_info(tag)
    log.info("Downloading minimal FFmpeg (%s)...", archive_type)

    try:
        if archive_type == "zip":
            archive_path = "ffmpeg_download.zip"
            download_file(url, archive_path)

            with zipfile.ZipFile(archive_path, 'r') as zf:
                for member in zf.namelist():
                    basename = os.path.basename(member)
                    if basename in ["ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"]:
                        with zf.open(member) as src, open(basename, 'wb') as dst:
                            dst.write(src.read())
                        if sys.platform != "win32":
                            os.chmod(basename, 0o755)
                        log.info("  Extracted %s", basename)
                    elif not member.endswith("/") and (notice := notice_destination(member)) is not None:
                        notice.parent.mkdir(parents=True, exist_ok=True)
                        notice.write_bytes(zf.read(member))

            os.remove(archive_path)

        elif archive_type == "tar":
            archive_path = "ffmpeg_download.tar.gz"
            download_file(url, archive_path)

            with tarfile.open(archive_path, 'r:*') as tf:
                for member in tf.getmembers():
                    basename = os.path.basename(member.name)
                    if basename in ["ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"]:
                        src = tf.extractfile(member)
                        if src is not None:
                            with open(basename, 'wb') as dst:
                                dst.write(src.read())
                            src.close()
                            if sys.platform != "win32":
                                os.chmod(basename, 0o755)
                            log.info("  Extracted %s", basename)
                    elif member.isfile() and (notice := notice_destination(member.name)) is not None:
                        src = tf.extractfile(member)
                        if src is not None:
                            notice.parent.mkdir(parents=True, exist_ok=True)
                            notice.write_bytes(src.read())
                            src.close()

            os.remove(archive_path)

        return True

    except Exception as e:
        log.error("Failed to download minimal FFmpeg: %s", e)
        return False


def check_root_ffmpeg_present() -> bool:
    """Check if both ffmpeg and ffprobe are present directly in the root directory."""
    repo_root = Path(__file__).resolve().parent
    for binary in FFMPEG_BINARIES:
        ext = ".exe" if sys.platform == "win32" else ""
        candidate = repo_root / f"{binary}{ext}"
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            return False
    return True


def find_binary(name: str) -> str:
    """Find a binary in the root directory, falling back to system PATH.

    Args:
        name: Binary base name ('ffmpeg' or 'ffprobe').

    Returns:
        Absolute path to the resolved binary.

    Raises:
        FileNotFoundError: If binary is not found in the root directory or PATH.
    """
    repo_root = Path(__file__).resolve().parent
    extensions = [".exe", ""] if sys.platform == "win32" else ["", ".exe"]

    # 1. Root folder present
    for ext in extensions:
        candidate = repo_root / f"{name}{ext}"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    # 2. System PATH fallback
    for ext in extensions:
        lookup_name = f"{name}{ext}" if ext else name
        which_path = shutil.which(lookup_name)
        if which_path and os.path.isfile(which_path):
            return str(Path(which_path).resolve())

    raise FileNotFoundError(f"Could not find '{name}' in root directory or system PATH")


def required_filters_for(platform: str) -> tuple[str, ...]:
    """Return the FFmpeg filters required on a platform.

    Example:
        >>> "scale_vaapi" in required_filters_for("linux")
        True
    """
    return REQUIRED_FILTERS.get(platform, REQUIRED_FILTERS["linux"])


def parse_filter_names(filters_output: str) -> set[str]:
    """Extract filter names (2nd column) from `ffmpeg -filters` output.

    Example:
        >>> parse_filter_names(" T.. fps  V->V  Force constant framerate.")
        {'fps'}
    """
    names: set[str] = set()
    for line in filters_output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] != "=" and FILTER_FLAGS_PATTERN.fullmatch(parts[0]):
            names.add(parts[1])
    return names


def find_missing_filters(ffmpeg_path: str, required: tuple[str, ...]) -> list[str]:
    """Return required filters absent from an FFmpeg binary.

    If the binary cannot be run, every required filter is reported missing.

    Example:
        >>> find_missing_filters("ffmpeg", ("fps",))  # doctest: +SKIP
        []
    """
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=FILTER_CHECK_TIMEOUT_S,
        )
    except subprocess.CalledProcessError as e:
        log.warning("Could not list FFmpeg filters from %s (exit %d): %s",
                    ffmpeg_path, e.returncode, (e.stderr or "").strip() or "no error output")
        return list(required)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("Could not list FFmpeg filters from %s: %s", ffmpeg_path, e)
        return list(required)
    available = parse_filter_names(result.stdout)
    return [name for name in required if name not in available]


def verify_ffmpeg_filters() -> bool:
    """Check the resolved FFmpeg for the required hardware filters.

    Returns:
        True if nothing is missing, or if missing filters are only warned about
        (local builds). False when filters are missing and the `CI` env var is set.
    """
    try:
        ffmpeg_path = find_binary("ffmpeg")
    except FileNotFoundError as e:
        log.warning("Skipping filter check: %s", e)
        return True
    missing = find_missing_filters(ffmpeg_path, required_filters_for(sys.platform))
    if not missing:
        log.info("FFmpeg filter check passed (%s)", ffmpeg_path)
        return True
    if os.environ.get("CI"):
        log.error("FFmpeg is missing required filters: %s", ", ".join(missing))
        return False
    log.warning("FFmpeg is missing filters (hardware pipelines will fall back): %s", ", ".join(missing))
    return True


def resolve_build_version() -> str:
    """Return the version to embed in executables.

    Uses `BUILD_VERSION` (the release tag in CI, leading `v` removed), then the
    installed package metadata, then `dev`.

    Example:
        >>> isinstance(resolve_build_version(), str)
        True
    """
    tag = os.environ.get("BUILD_VERSION", "").strip().lstrip("vV")
    if tag:
        return tag
    try:
        return importlib.metadata.version("video-compression")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def create_preset_script(target_mb: int, codec: str, temp_dir: str, app_version: str) -> str:
    """Create a temporary script wrapper with hardcoded preset parameters and version.

    Args:
        target_mb: Preset target size in megabytes.
        codec: Preset codec (`hevc` or `h264`).
        temp_dir: Directory to write the wrapper into.
        app_version: Version embedded into the executable for logs and error reports.

    Returns:
        Path to the generated wrapper script.
    """
    content = f'''import sys
import videocompress
import videocompress.version

# Preset Wrapper: {target_mb}mb-{codec}
videocompress.version.BUILD_VERSION = {app_version!r}
sys.argv[1:1] = ["{codec}", "{target_mb}"]
try:
    videocompress.main()
except KeyboardInterrupt:
    sys.exit(130)
'''
    script_path = os.path.join(temp_dir, f"{target_mb}mb_{codec}.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    return script_path


def parse_version_tuple(version_str: str) -> tuple[int, int, int, int]:
    """Parse a version string into a 4-tuple of integers.

    Args:
        version_str: Raw version string (e.g. 'v1.2.0', '1.2.0.0').

    Returns:
        A 4-tuple of integers (major, minor, patch, build).

    Example:
        >>> parse_version_tuple('v1.2.0')
        (1, 2, 0, 0)
    """
    clean_version = version_str.lstrip("vV").strip()
    if not clean_version:
        clean_version = "1.2.0.0"
    parts: list[int] = []
    for segment in clean_version.split("."):
        match = re.match(r"^(\d+)", segment)
        parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def create_windows_version_file(version_str: str, output_name: str, target_mb: int, codec: str, dest_dir: str) -> str:
    """Generate a PyInstaller VSVersionInfo text file for Windows PE metadata.

    Args:
        version_str: Version string (e.g. '1.2.0.0').
        output_name: Base filename identifier of the executable.
        target_mb: Target size preset in megabytes.
        codec: Encoding codec ('hevc' or 'h264').
        dest_dir: Destination directory where the version file will be saved.

    Returns:
        The file path to the generated version information file.

    Example:
        >>> path = create_windows_version_file('1.2.0.0', '100mb-hevc-win64', 100, 'hevc', '/tmp')
        >>> os.path.exists(path)
        True
    """
    version_tuple = parse_version_tuple(version_str)
    file_description = f"Video-Compression {target_mb}MB ({codec.upper()}) Preset"

    content = f'''# UTF-8
#
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904b0',
          [
            StringStruct('CompanyName', 'SimpNick6703'),
            StringStruct('FileDescription', '{file_description}'),
            StringStruct('FileVersion', '{version_str}'),
            StringStruct('InternalName', '{output_name}'),
            StringStruct('LegalCopyright', 'Copyright (c) 2026 SimpNick6703. Licensed under GPL-3.0-or-later.'),
            StringStruct('OriginalFilename', '{output_name}.exe'),
            StringStruct('ProductName', 'Video-Compression'),
            StringStruct('ProductVersion', '{version_str}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
'''
    version_file_path = os.path.join(dest_dir, f"{output_name}_version.txt")
    with open(version_file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return version_file_path


def build_executable(script_path: str, target_mb: int, codec: str, work_dir: str, config_dir: str) -> bool:
    """Build a single executable using PyInstaller with isolation.

    Args:
        script_path: Path to the target Python script wrapper.
        target_mb: Target size preset in megabytes.
        codec: Encoding codec ('hevc' or 'h264').
        work_dir: PyInstaller work directory.
        config_dir: PyInstaller config directory for cache isolation.

    Returns:
        True if the build succeeded, False otherwise.
    """
    platform_suffix = get_platform_suffix()
    
    version = os.environ.get("BUILD_VERSION")
    if version:
        # Sanitize version for filename (allow alphanumeric, dot, hyphen, underscore)
        # Using standard versioning (e.g. v1.1.0) is safer than stripping dots (110) to avoid ambiguity.
        safe_version = re.sub(r'[^\w\-\.]', '', version)
        output_name = f"{target_mb}mb-{codec}-{platform_suffix}-{safe_version}"
    else:
        output_name = f"{target_mb}mb-{codec}-{platform_suffix}"
    
    log.info("Building %s...", output_name)
    
    # Find FFmpeg binaries
    add_binary_args = []
    separator = ";" if sys.platform == "win32" else ":"
    
    for binary in FFMPEG_BINARIES:
        try:
            binary_path = find_binary(binary)
            add_binary_args.extend(["--add-binary", f"{binary_path}{separator}."])
            log.info("  Bundling %s (from %s)", binary, binary_path)
        except FileNotFoundError as e:
            log.warning("  %s", e)

    extra_args = []
    if sys.platform == "win32":
        raw_version = os.environ.get("BUILD_VERSION", "1.2.0.0")
        clean_version = raw_version.lstrip("vV").strip() if raw_version else "1.2.0.0"
        if not clean_version:
            clean_version = "1.2.0.0"
        version_file_path = create_windows_version_file(
            version_str=clean_version,
            output_name=output_name,
            target_mb=target_mb,
            codec=codec,
            dest_dir=work_dir,
        )
        extra_args.append(f"--version-file={version_file_path}")
    
    repo_root = str(Path(".").resolve())
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",
        f"--paths={repo_root}",
        f"--name={output_name}",
        f"--workpath={work_dir}",
        f"--distpath={OUTPUT_DIR}",
        "--clean",
        "--noconfirm",
    ] + add_binary_args + extra_args + [script_path]
    
    # Isolate PyInstaller's global metadata/bincache to avoid race conditions
    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = config_dir
    
    try:
        subprocess.run(cmd, check=True, env=env)
        log.info("  Successfully built: %s", output_name)
        return True
    except Exception as e:
        log.error("  Failed to build %s: %s", output_name, e)
        return False


def clean_build_artifacts(include_ffmpeg: bool = False):
    """Remove build artifacts (build/, *.spec) and optionally FFmpeg binaries."""
    dirs_to_remove = ["build"]
    files_to_remove = list(Path(".").glob("*.spec"))
    
    if include_ffmpeg:
        # Also remove downloaded FFmpeg binaries and their license files if explicitly requested
        dirs_to_remove.append(FFMPEG_NOTICES_DIR)
        for binary in FFMPEG_BINARIES:
            if sys.platform == "win32":
                files_to_remove.append(Path(f"{binary}.exe"))
            else:
                files_to_remove.append(Path(binary))
    
    for d in dirs_to_remove:
        if os.path.exists(d):
            log.info("Removing %s/", d)
            shutil.rmtree(d)
    
    for f in files_to_remove:
        if f.exists():
            log.info("Removing %s", f)
            f.unlink()


def main() -> int:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    clean_ffmpeg = "--clean-ffmpeg" in sys.argv
    
    # Validate source package exists
    if not os.path.isdir(SOURCE_PACKAGE):
        log.error("Source package directory '%s' not found", SOURCE_PACKAGE)
        return 1
    
    # Check if FFmpeg is already present in the root folder, download minimal build if not
    if check_root_ffmpeg_present():
        log.info("FFmpeg and FFprobe already present in root folder (skipping download)")
    else:
        ffmpeg_tag = resolve_ffmpeg_release_tag()
        log.info("FFmpeg binaries not found in root folder. Downloading from %s/releases/%s...", FFMPEG_REPO, ffmpeg_tag)
        if not download_ffmpeg(ffmpeg_tag):
            if all(shutil.which(b) for b in FFMPEG_BINARIES):
                log.warning("Download failed, but found FFmpeg in system PATH. Continuing with PATH fallback...")
            else:
                log.error("Failed to obtain FFmpeg binaries")
                return 1
    
    if not verify_ffmpeg_filters():
        return 1

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Build each preset
    app_version = resolve_build_version()
    log.info("Building presets: %s (version %s)", PRESET_SIZES, app_version)
    results: dict[str, bool] = {}
    
    try:
        with tempfile.TemporaryDirectory(prefix="vidcomp_build_") as temp_dir:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = {}
                for size in PRESET_SIZES:
                    for codec in PRESET_CODECS:
                        task_name = f"{size}mb_{codec}"
                        task_dir = os.path.join(temp_dir, task_name)
                        task_work = os.path.join(task_dir, "work")
                        task_config = os.path.join(task_dir, "config")
                        os.makedirs(task_work, exist_ok=True)
                        os.makedirs(task_config, exist_ok=True)
                        
                        script_path = create_preset_script(size, codec, task_dir, app_version)
                        future = executor.submit(build_executable, script_path, size, codec, task_work, task_config) # type: ignore
                        futures[future] = f"{size}mb-{codec}"
                
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    results[name] = future.result()
    except Exception as e:
        log.error("Failed allocating preset staging area: %s", e)
        return 1

    # Clean build artifacts by default (unless --verbose)
    if not verbose:
        log.info("Cleaning build artifacts...")
        clean_build_artifacts(include_ffmpeg=clean_ffmpeg)
    
    # Summary
    log.info("=" * 40)
    log.info("Build Summary")
    log.info("=" * 40)
    
    for size, success in results.items():
        status = "Success" if success else "Failed"
        log.info("  %s: %s", size, status)
    
    failed = [s for s, ok in results.items() if not ok]
    if failed:
        log.error("Failed builds: %s", failed)
        return 1

    write_release_notices(app_version)
    log.info("All executables written to: %s/", OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
