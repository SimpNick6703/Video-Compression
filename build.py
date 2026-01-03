#!/usr/bin/env python3
"""
Build script for Video-Compression executables.

Generates preset executables (8mb, 50mb, 100mb, 500mb) from videocompress.py
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

# --- Configuration ---
PRESET_SIZES = [8, 50, 100, 500]
SOURCE_SCRIPT = "videocompress.py"
FFMPEG_BINARIES = ["ffmpeg", "ffprobe"]
OUTPUT_DIR = "dist"

# FFmpeg download URLs per platform
FFMPEG_URLS = {
    "win32": {
        "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "type": "zip"
    },
    "linux": {
        "url": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
        "type": "tar.xz"
    },
    "darwin": {
        "ffmpeg_url": "https://evermeet.cx/ffmpeg/getrelease/zip",
        "ffprobe_url": "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip",
        "type": "zip_separate"
    }
}

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def get_platform_key() -> str:
    """Return platform key for FFMPEG_URLS."""
    if sys.platform == "win32":
        return "win32"
    elif sys.platform == "darwin":
        return "darwin"
    return "linux"


def get_platform_suffix() -> str:
    """Return platform-specific suffix for executable names."""
    if sys.platform == "win32":
        return "win64"
    elif sys.platform == "darwin":
        return "macos"
    return "linux"


def download_file(url: str, dest: str):
    """Download a file from URL to destination."""
    log.info("  Downloading from %s", url[:60] + "..." if len(url) > 60 else url)
    urllib.request.urlretrieve(url, dest)


def download_ffmpeg() -> bool:
    """Download FFmpeg binaries for the current platform."""
    platform = get_platform_key()
    config = FFMPEG_URLS.get(platform)
    
    if not config:
        log.error("No FFmpeg download URL configured for platform: %s", platform)
        return False
    
    log.info("Downloading FFmpeg for %s...", platform)
    
    try:
        if config["type"] == "zip":
            # Windows: Single zip with bin folder
            archive_path = "ffmpeg_download.zip"
            download_file(config["url"], archive_path)
            
            with zipfile.ZipFile(archive_path, 'r') as zf:
                # Find and extract ffmpeg/ffprobe from bin folder
                for member in zf.namelist():
                    basename = os.path.basename(member)
                    if basename in ["ffmpeg.exe", "ffprobe.exe"]:
                        # Extract to current directory with just the filename
                        with zf.open(member) as src, open(basename, 'wb') as dst:
                            dst.write(src.read())
                        log.info("  Extracted %s", basename)
            
            os.remove(archive_path)
            
        elif config["type"] == "tar.xz":
            # Linux: tar.xz with binaries in subfolder
            archive_path = "ffmpeg_download.tar.xz"
            download_file(config["url"], archive_path)
            
            with tarfile.open(archive_path, 'r:xz') as tf:
                for member in tf.getmembers():
                    basename = os.path.basename(member.name)
                    if basename in ["ffmpeg", "ffprobe"]:
                        # Extract file content to current directory
                        with tf.extractfile(member) as src, open(basename, 'wb') as dst:
                            dst.write(src.read())
                        os.chmod(basename, 0o755)
                        log.info("  Extracted %s", basename)
            
            os.remove(archive_path)
            
        elif config["type"] == "zip_separate":
            # macOS: Separate zips for ffmpeg and ffprobe
            for binary, url_key in [("ffmpeg", "ffmpeg_url"), ("ffprobe", "ffprobe_url")]:
                archive_path = f"{binary}_download.zip"
                download_file(config[url_key], archive_path)
                
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    zf.extractall(".")
                    log.info("  Extracted %s", binary)
                
                os.chmod(binary, 0o755)
                os.remove(archive_path)
        
        return True
        
    except Exception as e:
        log.error("Failed to download FFmpeg: %s", e)
        return False


def find_binary(name: str) -> str:
    """Find a binary in current directory or PATH."""
    if sys.platform == "win32":
        local_exe = Path(f"{name}.exe")
        if local_exe.exists():
            return str(local_exe.absolute())
    
    local_path = Path(name)
    if local_path.exists():
        return str(local_path.absolute())
    
    result = shutil.which(name)
    if result:
        return result
    
    raise FileNotFoundError(f"Could not find {name} in current directory or PATH")


def check_ffmpeg_available() -> bool:
    """Check if ffmpeg and ffprobe are available."""
    for binary in FFMPEG_BINARIES:
        try:
            find_binary(binary)
        except FileNotFoundError:
            return False
    return True


def create_preset_script(target_mb: int, temp_dir: str) -> str:
    """Create a temporary script with hardcoded target size."""
    with open(SOURCE_SCRIPT, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Replace the default target_mb value in the main block
    content = re.sub(
        r'(target_mb\s*=\s*)\d+',
        f'\\g<1>{target_mb}',
        content
    )
    
    # Update the usage message to reflect the preset
    content = re.sub(
        r'Usage: python script\.py <input> \[output\] \[size_mb\]',
        f'Usage: {target_mb}mb <input> [output]',
        content
    )
    
    script_path = os.path.join(temp_dir, f"{target_mb}mb.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    return script_path


def build_executable(script_path: str, target_mb: int) -> bool:
    """Build a single executable using PyInstaller."""
    platform_suffix = get_platform_suffix()
    output_name = f"{target_mb}mb-{platform_suffix}"
    
    log.info("Building %s...", output_name)
    
    # Find FFmpeg binaries
    add_binary_args = []
    separator = ";" if sys.platform == "win32" else ":"
    
    for binary in FFMPEG_BINARIES:
        try:
            binary_path = find_binary(binary)
            add_binary_args.extend(["--add-binary", f"{binary_path}{separator}."])
            log.info("  Bundling %s", binary)
        except FileNotFoundError as e:
            log.warning("  %s", e)
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",
        f"--name={output_name}",
        f"--distpath={OUTPUT_DIR}",
        "--clean",
        "--noconfirm",
    ] + add_binary_args + [script_path]
    
    try:
        subprocess.run(cmd, check=True)
        log.info("  Successfully built: %s", output_name)
        return True
    except subprocess.CalledProcessError as e:
        log.error("  Failed to build %s: %s", output_name, e)
        return False


def clean_build_artifacts(include_ffmpeg: bool = True):
    """Remove build artifacts (build/, *.spec) and optionally FFmpeg binaries."""
    dirs_to_remove = ["build"]
    files_to_remove = list(Path(".").glob("*.spec"))
    
    if include_ffmpeg:
        # Also remove downloaded FFmpeg binaries
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
    
    # Validate source script exists
    if not os.path.exists(SOURCE_SCRIPT):
        log.error("Source script '%s' not found", SOURCE_SCRIPT)
        return 1
    
    # Check if FFmpeg is available, download if not
    ffmpeg_downloaded = False
    if not check_ffmpeg_available():
        log.info("FFmpeg not found locally, downloading...")
        if not download_ffmpeg():
            log.error("Failed to obtain FFmpeg binaries")
            return 1
        ffmpeg_downloaded = True
    else:
        log.info("FFmpeg binaries found")
    
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Build each preset
    log.info("Building presets: %s", PRESET_SIZES)
    results = {}
    
    with tempfile.TemporaryDirectory(prefix="vidcomp_build_") as temp_dir:
        for size in PRESET_SIZES:
            script_path = create_preset_script(size, temp_dir)
            results[size] = build_executable(script_path, size)
    
    # Clean build artifacts by default (unless --verbose)
    if not verbose:
        log.info("Cleaning build artifacts...")
        clean_build_artifacts(include_ffmpeg=ffmpeg_downloaded)
    
    # Summary
    log.info("=" * 40)
    log.info("Build Summary")
    log.info("=" * 40)
    
    for size, success in results.items():
        status = "Success" if success else "Failed"
        log.info("  %dMB: %s", size, status)
    
    failed = [s for s, ok in results.items() if not ok]
    if failed:
        log.error("Failed builds: %s", failed)
        return 1
    
    log.info("All executables written to: %s/", OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
