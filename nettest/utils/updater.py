"""
Auto-update checker for nettest.

Compares the locally installed version against the latest on GitHub.
Prompts the user to update if a newer version is available.
Caches the check result so it only hits the network once per day.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Optional, Tuple

REPO_OWNER = "rockgod407"
REPO_NAME = "BTW-NTS"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}.git"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/main"

_STATE_DIR = pathlib.Path.home() / ".nettest"
_UPDATE_CACHE = _STATE_DIR / ".update_check"

# How often to check (seconds) — once per day
CHECK_INTERVAL = 86400


def _get_local_version() -> Optional[str]:
    """Get the installed package version."""
    try:
        from importlib.metadata import version
        return version("nettest")
    except Exception:
        pass
    # Fallback: read from the package directly
    try:
        from nettest import __version__
        return __version__
    except Exception:
        pass
    return None


def _get_local_commit() -> Optional[str]:
    """
    Get the git commit hash that's baked into the installed package.
    Returns None if not available.
    """
    try:
        commit_file = pathlib.Path(__file__).parent.parent / ".installed_commit"
        if commit_file.exists():
            return commit_file.read_text().strip()
    except Exception:
        pass
    return None


def _get_remote_commit() -> Optional[str]:
    """Fetch the latest commit SHA from GitHub (fast, single API call)."""
    try:
        import urllib.request
        req = urllib.request.Request(
            API_URL,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "nettest-updater",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("sha", "")[:12]
    except Exception:
        return None


def _get_remote_version() -> Optional[str]:
    """
    Fetch the version string from setup.cfg on GitHub.

    Uses the GitHub API (not raw.githubusercontent.com) to avoid
    CDN caching that can return stale content for up to 5 minutes.
    """
    try:
        import urllib.request
        # Use the API endpoint which doesn't have CDN caching issues
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/setup.cfg"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github.v3.raw",
                "User-Agent": "nettest-updater",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("version"):
                    # version = 0.3.0
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass

    # Fallback to raw.githubusercontent.com with cache-busting
    try:
        import urllib.request
        cache_bust = int(time.time())
        url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/setup.cfg?cb={cache_bust}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "nettest-updater",
            "Cache-Control": "no-cache",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def _read_cache() -> Optional[dict]:
    """Read the cached update check result."""
    try:
        if _UPDATE_CACHE.exists():
            data = json.loads(_UPDATE_CACHE.read_text())
            return data
    except Exception:
        pass
    return None


def _write_cache(data: dict):
    """Write update check result to cache."""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _UPDATE_CACHE.write_text(json.dumps(data))
    except Exception:
        pass


def check_for_update(force: bool = False) -> Tuple[bool, str, str]:
    """
    Check if an update is available.

    Returns (update_available, local_version, remote_version).
    Uses a daily cache to avoid hitting the network on every run.
    """
    # Check cache first (unless forced)
    if not force:
        cache = _read_cache()
        if cache:
            last_check = cache.get("timestamp", 0)
            if time.time() - last_check < CHECK_INTERVAL:
                return (
                    cache.get("update_available", False),
                    cache.get("local_version", "unknown"),
                    cache.get("remote_version", "unknown"),
                )

    local_ver = _get_local_version() or "unknown"
    remote_ver = _get_remote_version()
    remote_commit = _get_remote_commit()

    if remote_ver is None:
        # Network error — skip silently
        _write_cache({
            "timestamp": time.time(),
            "update_available": False,
            "local_version": local_ver,
            "remote_version": local_ver,
        })
        return (False, local_ver, local_ver)

    update_available = (remote_ver != local_ver) if remote_ver else False

    _write_cache({
        "timestamp": time.time(),
        "update_available": update_available,
        "local_version": local_ver,
        "remote_version": remote_ver or local_ver,
        "remote_commit": remote_commit or "",
    })

    return (update_available, local_ver, remote_ver or local_ver)


def run_update(verbose: bool = False) -> Tuple[bool, str]:
    """
    Run the update by reinstalling from GitHub.

    Uses --force-reinstall and --no-cache-dir to ensure pip actually
    re-downloads and reinstalls even if it thinks the version matches.

    Returns (success, message).
    """
    git_url = f"git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git"

    # Try with --user first (standard on macOS), then without
    for use_user in (True, False):
        cmd = [
            sys.executable, "-m", "pip", "install",
            "--upgrade", "--force-reinstall", "--no-cache-dir",
        ]
        if use_user:
            cmd.append("--user")
        cmd.append(git_url)

        try:
            # Show pip output so the user can see progress
            proc = subprocess.run(
                cmd,
                capture_output=not verbose,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                # Clear the update cache so next check is fresh
                clear_cache()
                return (True, "Update successful! Restart nettest to use the new version.")

            # If --user failed, try without it
            if use_user:
                continue

            # Both attempts failed — report the error
            error_msg = ""
            if hasattr(proc, "stderr") and proc.stderr:
                # Show last few meaningful lines
                lines = [l for l in proc.stderr.strip().splitlines() if l.strip()]
                error_msg = "\n".join(lines[-3:])

            return (False, f"Update failed:\n{error_msg or 'unknown error'}\n\nTry manually:\n  pip3 install --upgrade --force-reinstall git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git")

        except subprocess.TimeoutExpired:
            return (False, f"Update timed out. Try manually:\n  pip3 install --upgrade --force-reinstall git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git")
        except Exception as e:
            if use_user:
                continue
            return (False, f"Update error: {e}")

    return (False, f"Update failed. Try manually:\n  pip3 install --upgrade --force-reinstall git+https://github.com/{REPO_OWNER}/{REPO_NAME}.git")


def clear_cache():
    """Clear the update check cache (forces a fresh check next time)."""
    try:
        if _UPDATE_CACHE.exists():
            _UPDATE_CACHE.unlink()
    except Exception:
        pass
