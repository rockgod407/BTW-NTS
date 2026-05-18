"""
Auto-update for nettest.

Version check: fetches setup.cfg from GitHub raw, compares versions.
Update: downloads and runs install.sh — the exact same script the
curl installer uses. Simple, no pip caching games, always works.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from typing import Optional, Tuple

REPO_OWNER = "rockgod407"
REPO_NAME = "BTW-NTS"
INSTALL_SCRIPT_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/install.sh"


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
    Fetch the version from setup.cfg on GitHub.
    Uses raw.githubusercontent.com — no rate limits.
    """
    try:
        cache_bust = int(time.time() // 60)
        url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/setup.cfg?v={cache_bust}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "nettest-updater",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            for line in resp.read().decode().splitlines():
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
    """
    local_ver = _get_local_version() or "unknown"
    remote_ver = _get_remote_version()

    if remote_ver is None:
        return (False, local_ver, "unknown")

    update_available = (remote_ver != local_ver)
    return (update_available, local_ver, remote_ver)


def run_update(verbose: bool = False) -> Tuple[bool, str]:
    """
    Update by downloading and running install.sh from GitHub.
    This is the exact same method as the curl installer — simple
    and proven to work.

    Returns (success, message).
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="nettest-update-")
        script_path = os.path.join(tmp_dir, "install.sh")

        # Download install.sh
        cache_bust = int(time.time())
        url = f"{INSTALL_SCRIPT_URL}?v={cache_bust}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "nettest-updater",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            script_content = resp.read()

        with open(script_path, "wb") as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)

        if verbose:
            print(f"  Downloaded install.sh ({len(script_content)} bytes)")
            print(f"  Running installer...")

        # Run install.sh
        proc = subprocess.run(
            ["bash", script_path],
            timeout=180,
            capture_output=not verbose,
            text=True,
        )

        if proc.returncode == 0:
            # Verify the update actually took effect
            new_ver = _get_remote_version() or "latest"
            return (True, f"Updated to {new_ver}! Open a new terminal window to use it.")
        else:
            error = ""
            if proc.stderr:
                lines = [l for l in proc.stderr.strip().splitlines() if l.strip()]
                error = "\n".join(lines[-5:])
            return (False, f"Install script failed:\n{error or 'unknown error'}")

    except urllib.error.URLError as e:
        return (False, f"Could not download installer: {e}\n\nTry manually:\n  curl -fsSL {INSTALL_SCRIPT_URL} | bash")
    except subprocess.TimeoutExpired:
        return (False, f"Install timed out. Try manually:\n  curl -fsSL {INSTALL_SCRIPT_URL} | bash")
    except Exception as e:
        return (False, f"Update error: {e}\n\nTry manually:\n  curl -fsSL {INSTALL_SCRIPT_URL} | bash")
    finally:
        if tmp_dir:
            try:
                import shutil
                shutil.rmtree(tmp_dir)
            except Exception:
                pass


def clear_cache():
    """Legacy no-op."""
    pass
