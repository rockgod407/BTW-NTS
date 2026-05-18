"""
Auto-update for nettest.

Version check: fetches setup.cfg from GitHub, compares versions.
Update: downloads and runs install.sh — the exact same script the
curl installer uses.

Uses the `requests` library (already a nettest dependency) instead
of urllib to avoid SSL certificate issues on macOS.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Optional, Tuple

REPO_OWNER = "rockgod407"
REPO_NAME = "BTW-NTS"
INSTALL_SCRIPT_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/install.sh"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/setup.cfg"

# Store the last error for diagnostics
_last_error: Optional[str] = None


def _fetch_url(url: str, timeout: int = 10) -> Optional[bytes]:
    """
    Fetch a URL using the requests library.
    requests uses certifi for SSL certs, which bypasses the broken
    macOS system Python SSL certificate store.
    """
    global _last_error
    _last_error = None

    try:
        import requests
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "nettest-updater",
        })
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        _last_error = f"{type(e).__name__}: {e}"
        return None


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
    """
    cache_bust = int(time.time() // 60)
    url = f"{VERSION_URL}?v={cache_bust}"
    data = _fetch_url(url)
    if data is None:
        return None
    for line in data.decode().splitlines():
        line = line.strip()
        if line.startswith("version"):
            return line.split("=", 1)[1].strip()
    return None


def get_last_error() -> Optional[str]:
    """Return the last error from a failed fetch."""
    return _last_error


def check_for_update(force: bool = False) -> Tuple[bool, str, str]:
    """
    Check if an update is available. Always checks GitHub live.
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
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="nettest-update-")
        script_path = os.path.join(tmp_dir, "install.sh")

        # Download install.sh
        cache_bust = int(time.time())
        url = f"{INSTALL_SCRIPT_URL}?v={cache_bust}"
        script_content = _fetch_url(url, timeout=30)

        if script_content is None:
            return (False, f"Could not download installer: {_last_error}\n\nTry manually:\n  curl -fsSL {INSTALL_SCRIPT_URL} | bash")

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
            new_ver = _get_remote_version() or "latest"
            return (True, f"Updated to {new_ver}! Open a new terminal window to use it.")
        else:
            error = ""
            if proc.stderr:
                lines = [l for l in proc.stderr.strip().splitlines() if l.strip()]
                error = "\n".join(lines[-5:])
            return (False, f"Install script failed:\n{error or 'unknown error'}")

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


def run_diagnostics() -> str:
    """Run full diagnostics on the update system."""
    import platform
    lines = []
    lines.append("=== nettest update diagnostics ===")
    lines.append(f"Python:    {sys.executable} ({platform.python_version()})")
    lines.append(f"Platform:  {platform.platform()}")

    try:
        import ssl
        lines.append(f"SSL:       {ssl.OPENSSL_VERSION}")
    except Exception:
        lines.append("SSL:       unavailable")

    # Check if requests + certifi are working
    try:
        import requests
        lines.append(f"Requests:  {requests.__version__}")
    except ImportError:
        lines.append("Requests:  NOT INSTALLED (this is the problem!)")

    try:
        import certifi
        lines.append(f"Certifi:   {certifi.__version__} ({certifi.where()})")
    except ImportError:
        lines.append("Certifi:   NOT INSTALLED")

    lines.append("")

    # Local version
    local = _get_local_version()
    lines.append(f"Local version: {local or 'unknown'}")

    # Test fetch
    cache_bust = int(time.time())
    test_url = f"{VERSION_URL}?v={cache_bust}"
    lines.append(f"\nFetching: {test_url}")

    data = _fetch_url(test_url, timeout=15)
    if data is not None:
        lines.append(f"  Status: OK ({len(data)} bytes)")
        for line in data.decode().splitlines()[:3]:
            lines.append(f"  | {line}")
        remote = None
        for line in data.decode().splitlines():
            if line.strip().startswith("version"):
                remote = line.strip().split("=", 1)[1].strip()
                break
        lines.append(f"\n  Remote version: {remote}")
        if local and remote:
            if local == remote:
                lines.append("  Result: UP TO DATE")
            else:
                lines.append(f"  Result: UPDATE AVAILABLE ({local} -> {remote})")
    else:
        lines.append(f"  Status: FAILED")
        lines.append(f"  Error:  {_last_error}")

    # Test install.sh URL
    lines.append(f"\nFetching: {INSTALL_SCRIPT_URL}")
    data2 = _fetch_url(f"{INSTALL_SCRIPT_URL}?v={cache_bust}", timeout=15)
    if data2 is not None:
        lines.append(f"  Status: OK ({len(data2)} bytes)")
    else:
        lines.append(f"  Status: FAILED")
        lines.append(f"  Error:  {_last_error}")

    return "\n".join(lines)


def clear_cache():
    """Legacy no-op."""
    pass
