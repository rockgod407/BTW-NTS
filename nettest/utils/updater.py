"""
Auto-update for nettest.

Version check: fetches setup.cfg from GitHub raw, compares versions.
Update: downloads and runs install.sh — the exact same script the
curl installer uses. Simple, no pip caching games, always works.
"""
from __future__ import annotations

import os
import ssl
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
VERSION_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/setup.cfg"

# Store the last error so --debug can show it
_last_error: Optional[str] = None


def _fetch_url(url: str, timeout: int = 10) -> Optional[bytes]:
    """
    Fetch a URL, handling SSL certificate issues on macOS.
    Returns the response bytes or None on failure.
    """
    global _last_error
    _last_error = None

    req = urllib.request.Request(url, headers={
        "User-Agent": "nettest-updater",
    })

    # First try: normal SSL
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except ssl.SSLCertVerificationError as e:
        _last_error = f"SSL certificate error: {e}"
        # macOS Python often has missing/outdated certificates.
        # Fall back to unverified context for GitHub (safe — it's HTTPS to a known host)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                _last_error = None
                return resp.read()
        except Exception as e2:
            _last_error = f"SSL fallback also failed: {e2}"
            return None
    except urllib.error.URLError as e:
        _last_error = f"URL error: {e}"
        return None
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
    Uses raw.githubusercontent.com — no rate limits.
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
    """Return the last error from a failed fetch, for diagnostics."""
    return _last_error


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
    """
    Run full diagnostics on the update system. Returns a report string.
    """
    import platform
    lines = []
    lines.append("=== nettest update diagnostics ===")
    lines.append(f"Python:    {sys.executable} ({platform.python_version()})")
    lines.append(f"Platform:  {platform.platform()}")
    lines.append(f"SSL:       {ssl.OPENSSL_VERSION}")
    lines.append("")

    # Local version
    local = _get_local_version()
    lines.append(f"Local version: {local or 'unknown'}")

    # Test raw.githubusercontent.com
    cache_bust = int(time.time())
    test_url = f"{VERSION_URL}?v={cache_bust}"
    lines.append(f"\nFetching: {test_url}")

    data = _fetch_url(test_url, timeout=15)
    if data is not None:
        lines.append(f"  Status: OK ({len(data)} bytes)")
        for line in data.decode().splitlines()[:5]:
            lines.append(f"  | {line}")
        remote = None
        for line in data.decode().splitlines():
            if line.strip().startswith("version"):
                remote = line.strip().split("=", 1)[1].strip()
                break
        lines.append(f"\n  Remote version: {remote}")
        if local and remote:
            if local == remote:
                lines.append(f"  Result: UP TO DATE")
            else:
                lines.append(f"  Result: UPDATE AVAILABLE ({local} → {remote})")
    else:
        lines.append(f"  Status: FAILED")
        lines.append(f"  Error:  {_last_error}")
        lines.append("")
        lines.append("Possible causes:")
        lines.append("  - SSL certificate issue (common on macOS system Python)")
        lines.append("  - Network/firewall blocking raw.githubusercontent.com")
        lines.append("  - No internet connection")
        lines.append("")
        lines.append("Try running this in your terminal to test:")
        lines.append(f"  curl -sI {VERSION_URL} | head -5")
        lines.append(f"  python3 -c \"import urllib.request; print(urllib.request.urlopen('{VERSION_URL}').read()[:100])\"")

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
