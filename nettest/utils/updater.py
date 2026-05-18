"""
Auto-update checker for nettest.

Compares the locally installed version against the latest on GitHub.
Always checks live — no caching. Updates by downloading the repo zip
and installing locally — no git required.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from typing import Optional, Tuple

REPO_OWNER = "rockgod407"
REPO_NAME = "BTW-NTS"
ZIP_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/main.zip"

# Legacy cache file — clean it up if it exists
_STATE_DIR = pathlib.Path.home() / ".nettest"
_OLD_CACHE = _STATE_DIR / ".update_check"


def _get_local_version() -> Optional[str]:
    """Get the installed package version."""
    try:
        from importlib.metadata import version
        return version("nettest")
    except Exception:
        pass
    try:
        from nettest import __version__
        return __version__
    except Exception:
        pass
    return None


def _get_remote_version() -> Optional[str]:
    """
    Fetch the version string from setup.cfg on GitHub.

    Uses raw.githubusercontent.com — no rate limits, no auth needed.
    Has a ~5 min CDN cache which is fine for manual update checks.
    """
    try:
        # Append a cache-buster based on the current minute so we get
        # a fresh response within a few minutes of any push
        cache_bust = int(time.time() // 60)
        url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/setup.cfg?v={cache_bust}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "nettest-updater",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def check_for_update(force: bool = False) -> Tuple[bool, str, str]:
    """
    Check if an update is available. Always checks GitHub live.

    Returns (update_available, local_version, remote_version).
    If we can't reach GitHub, remote_version will be "unknown".
    """
    # Clean up old cache file if it exists
    try:
        if _OLD_CACHE.exists():
            _OLD_CACHE.unlink()
    except Exception:
        pass

    local_ver = _get_local_version() or "unknown"
    remote_ver = _get_remote_version()

    if remote_ver is None:
        # Couldn't reach GitHub — return "unknown" so the caller
        # can tell the user we couldn't check, rather than lying
        # and saying they're up to date
        return (False, local_ver, "unknown")

    update_available = (remote_ver != local_ver)
    return (update_available, local_ver, remote_ver)


def run_update(verbose: bool = False) -> Tuple[bool, str]:
    """
    Update by downloading the repo zip from GitHub and installing
    from the local files. No git required, no pip cache issues.

    Returns (success, message).
    """
    tmp_dir = None
    try:
        # Download the zip
        tmp_dir = tempfile.mkdtemp(prefix="nettest-update-")
        zip_path = os.path.join(tmp_dir, "repo.zip")

        if verbose:
            print(f"  Downloading {ZIP_URL} ...")

        req = urllib.request.Request(ZIP_URL, headers={
            "User-Agent": "nettest-updater",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(zip_path, "wb") as f:
                f.write(resp.read())

        # Extract the zip
        if verbose:
            print("  Extracting...")

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)

        # The zip extracts to a folder named BTW-NTS-main/
        repo_dir = os.path.join(tmp_dir, f"{REPO_NAME}-main")
        if not os.path.isdir(repo_dir):
            dirs = [d for d in os.listdir(tmp_dir)
                    if os.path.isdir(os.path.join(tmp_dir, d))]
            if dirs:
                repo_dir = os.path.join(tmp_dir, dirs[0])
            else:
                return (False, "Update failed: could not find extracted repo directory.")

        # Install from the local directory
        # Try with --user first (standard on macOS), then without
        for use_user in (True, False):
            cmd = [
                sys.executable, "-m", "pip", "install",
                "--force-reinstall", "--no-cache-dir", "--no-deps",
            ]
            if use_user:
                cmd.append("--user")
            cmd.append(repo_dir)

            if verbose:
                print(f"  Running: {' '.join(cmd)}")

            proc = subprocess.run(
                cmd,
                capture_output=not verbose,
                text=True,
                timeout=120,
            )

            if proc.returncode == 0:
                # Now install deps separately (without --force-reinstall
                # so we don't churn existing packages)
                dep_cmd = [
                    sys.executable, "-m", "pip", "install",
                    "--no-cache-dir",
                ]
                if use_user:
                    dep_cmd.append("--user")
                dep_cmd.append(repo_dir)

                subprocess.run(
                    dep_cmd,
                    capture_output=not verbose,
                    text=True,
                    timeout=120,
                )

                return (True, "Update successful! Restart nettest to use the new version.")

            # If --user failed, try without
            if use_user:
                continue

            # Both failed
            error_msg = ""
            if proc.stderr:
                lines = [l for l in proc.stderr.strip().splitlines() if l.strip()]
                error_msg = "\n".join(lines[-3:])
            return (False, f"Update failed:\n{error_msg or 'unknown error'}")

        return (False, "Update failed. Try manually:\n  curl -fsSL https://raw.githubusercontent.com/rockgod407/BTW-NTS/main/install.sh | bash")

    except urllib.error.URLError as e:
        return (False, f"Download failed (network error): {e}")
    except subprocess.TimeoutExpired:
        return (False, "Update timed out. Try manually:\n  curl -fsSL https://raw.githubusercontent.com/rockgod407/BTW-NTS/main/install.sh | bash")
    except Exception as e:
        return (False, f"Update error: {e}")
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            try:
                shutil.rmtree(tmp_dir)
            except Exception:
                pass


def clear_cache():
    """Legacy — no-op. Kept for API compatibility."""
    try:
        if _OLD_CACHE.exists():
            _OLD_CACHE.unlink()
    except Exception:
        pass
