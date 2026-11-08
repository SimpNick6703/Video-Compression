"""
Application version lookup for logs and error reports.

Frozen executables have no package metadata, so `build.py` writes the release
version into each preset wrapper, which assigns `BUILD_VERSION` before the CLI
starts. Source runs fall back to the installed package metadata.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Optional

PACKAGE_NAME = "video-compression"
UNKNOWN_VERSION = "unknown"

BUILD_VERSION: Optional[str] = None


def resolve_version(build_version: Optional[str], package_name: str = PACKAGE_NAME) -> str:
    """Prefer an embedded build version, then package metadata, then 'unknown'.

    Args:
        build_version: Version embedded at build time, if any.
        package_name: Distribution name to look up in package metadata.

    Returns:
        A version string such as `1.2.0`.

    Example:
        >>> resolve_version("1.2.0-rc1")
        '1.2.0-rc1'
        >>> resolve_version(None, "no-such-package-xyz")
        'unknown'
    """
    if build_version:
        return build_version
    try:
        return version(package_name)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def app_version() -> str:
    """Return the running application's version.

    Example:
        >>> isinstance(app_version(), str)
        True
    """
    return resolve_version(BUILD_VERSION)
