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
import logging
import urllib.request
from pathlib import Path
import concurrent.futures

# --- Configuration ---
PRESET_SIZES = [20, 50, 100, 1024]
PRESET_CODECS = ["hevc", "h264"]
SOURCE_PACKAGE = "videocompress"
FFMPEG_BINARIES = ["ffmpeg", "ffprobe"]
OUTPUT_DIR = "dist"

# Default minimal FFmpeg repository and release tag
FFMPEG_REPO = os.environ.get("FFMPEG_REPO", "SimpNick6703/ffmpeg-minimal-builds")
FFMPEG_RELEASE_TAG = os.environ.get("FFMPEG_RELEASE_TAG", "latest")


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


def get_ffmpeg_download_info() -> tuple[str, str]:
    """Resolve FFmpeg download URL and archive type for the current platform."""
    tag_path = f"download/{FFMPEG_RELEASE_TAG}" if FFMPEG_RELEASE_TAG != "latest" else "latest/download"
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


def download_ffmpeg() -> bool:
    """Download FFmpeg binaries for the current platform."""
    url, archive_type = get_ffmpeg_download_info()
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


def create_preset_script(target_mb: int, codec: str, temp_dir: str) -> str:
    """Create a temporary script wrapper with hardcoded preset parameters."""
    content = f'''import sys
import videocompress

# Preset Wrapper: {target_mb}mb-{codec}
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
            StringStruct('LegalCopyright', 'Copyright (c) 2026 SimpNick6703. Licensed under GPL-3.0.'),
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
        # Also remove downloaded FFmpeg binaries if explicitly requested
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
        log.info("FFmpeg binaries not found in root folder. Downloading from %s/releases/%s...", FFMPEG_REPO, FFMPEG_RELEASE_TAG)
        if not download_ffmpeg():
            if all(shutil.which(b) for b in FFMPEG_BINARIES):
                log.warning("Download failed, but found FFmpeg in system PATH. Continuing with PATH fallback...")
            else:
                log.error("Failed to obtain FFmpeg binaries")
                return 1
    
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Build each preset
    log.info("Building presets: %s", PRESET_SIZES)
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
                        
                        script_path = create_preset_script(size, codec, task_dir)
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
    
    log.info("All executables written to: %s/", OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
