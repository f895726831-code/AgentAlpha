"""
akshare version awareness utility.

Provides:
- get_installed_version(): check what's installed
- get_latest_version(): query PyPI for latest release
- check_version(): comprehensive comparison with behind_count

Usage:
  from src.utils.akshare_version import check_version
  info = check_version()
  print(f"akshare {info.installed} (latest: {info.latest}), {info.behind_count} behind")
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class VersionInfo:
    installed: str
    latest: Optional[str]  # None if PyPI unreachable
    is_latest: bool
    behind_count: int  # approximate number of releases behind (0 if latest)


def get_installed_version() -> str:
    """Get installed akshare version via importlib.metadata (no import needed)."""
    try:
        from importlib.metadata import version
        return version("akshare")
    except Exception:
        pass
    # Fallback: import and check __version__
    try:
        import akshare as ak
        return ak.__version__
    except Exception:
        return "unknown"


def get_latest_version(timeout: int = 10) -> Optional[str]:
    """Query PyPI JSON API for latest akshare version."""
    url = "https://pypi.org/pypi/akshare/json"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "akshare-health/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["info"]["version"]
    except Exception:
        return None


def check_version() -> VersionInfo:
    """Return comprehensive version comparison.

    If PyPI is unreachable, latest=None and is_latest=True (assume OK).
    """
    installed = get_installed_version()
    latest = get_latest_version()

    if latest is None:
        return VersionInfo(
            installed=installed, latest=None, is_latest=True, behind_count=0
        )

    is_latest = installed == latest

    # Approximate behind_count: parse version tuples
    behind_count = 0
    if not is_latest and installed != "unknown":
        try:
            inst_parts = [int(x) for x in installed.split(".")]
            latest_parts = [int(x) for x in latest.split(".")]
            # Compare major.minor.patch
            for i in range(min(len(inst_parts), len(latest_parts))):
                diff = latest_parts[i] - inst_parts[i]
                if diff > 0:
                    behind_count = max(behind_count, diff)
                    break
        except (ValueError, IndexError):
            behind_count = 1  # conservative: at least 1 behind

    return VersionInfo(
        installed=installed, latest=latest,
        is_latest=is_latest, behind_count=behind_count,
    )


if __name__ == "__main__":
    info = check_version()
    print(f"Installed: {info.installed}")
    if info.latest:
        print(f"Latest:    {info.latest}")
        status = "up to date" if info.is_latest else f"{info.behind_count} behind"
        print(f"Status:    {status}")
    else:
        print("Latest:    (PyPI unreachable)")
    sys.exit(0 if info.is_latest else 1)