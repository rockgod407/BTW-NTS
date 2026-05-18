"""
Dependency health checker for nettest.

Verifies that all required and optional dependencies are installed,
functional, and at acceptable versions. Used by `nettest doctor` and
the automatic first-run check.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class DepStatus(Enum):
    OK = "ok"
    MISSING = "missing"
    VERSION_LOW = "version_low"
    BROKEN = "broken"


@dataclass
class DepCheck:
    """Result of checking a single dependency."""
    name: str
    import_name: str
    required: bool
    status: DepStatus
    installed_version: Optional[str] = None
    min_version: Optional[str] = None
    install_hint: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Dependency registry
# ---------------------------------------------------------------------------

# (pip_name, import_name, min_version, required, install_hint, description)
CORE_DEPS: List[Tuple[str, str, str, bool, str, str]] = [
    ("click", "click", "8.1", True, "pip3 install click>=8.1", "CLI framework"),
    ("requests", "requests", "2.31", True, "pip3 install requests>=2.31", "HTTP client"),
    ("dnspython", "dns", "2.4", True, "pip3 install dnspython>=2.4", "DNS resolver"),
    ("ntplib", "ntplib", "0.4", True, "pip3 install ntplib>=0.4", "NTP client"),
    ("rich", "rich", "13.0", True, "pip3 install rich>=13.0", "Terminal formatting"),
    ("pyyaml", "yaml", "6.0", True, "pip3 install pyyaml>=6.0", "YAML config parser"),
    ("pytest", "pytest", "7.4", True, "pip3 install pytest>=7.4", "Test framework"),
    ("scapy", "scapy", "2.5", True, "pip3 install scapy>=2.5", "Packet crafting"),
    ("sacn", "sacn", "1.9", True, "pip3 install sacn>=1.9", "sACN / E1.31 protocol"),
]

OPTIONAL_DEPS: List[Tuple[str, str, str, bool, str, str]] = [
    ("NDI (libndi)", "nettest.ndi_native", "5.0", False,
     "pip3 install ndi-python   (provides libndi.dylib)\n"
     "         — OR — Install NDI SDK from https://ndi.video/tools/ndi-sdk/",
     "NDI video-over-IP (ctypes, no ndi-python extension needed)"),
]

ALL_DEPS = CORE_DEPS + OPTIONAL_DEPS


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> Tuple[int, ...]:
    """Parse '1.2.3' into (1, 2, 3) for comparison."""
    parts = []
    for p in v.split("."):
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _version_ok(installed: str, minimum: str) -> bool:
    """Return True if installed >= minimum."""
    try:
        return _parse_version(installed) >= _parse_version(minimum)
    except (ValueError, TypeError):
        return True  # Can't parse, assume OK


def _get_version(module) -> Optional[str]:
    """Try to get a version string from an imported module."""
    for attr in ("__version__", "VERSION", "version"):
        v = getattr(module, attr, None)
        if v and isinstance(v, str):
            return v
    # Try importlib.metadata
    try:
        name = getattr(module, "__name__", None) or getattr(module, "__package__", "")
        from importlib.metadata import version as meta_version
        return meta_version(name)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Check runner
# ---------------------------------------------------------------------------

def check_dependency(
    pip_name: str,
    import_name: str,
    min_version: str,
    required: bool,
    install_hint: str,
    description: str,
) -> DepCheck:
    """Check a single dependency."""
    result = DepCheck(
        name=pip_name,
        import_name=import_name,
        required=required,
        status=DepStatus.OK,
        min_version=min_version,
        install_hint=install_hint,
        notes=description,
    )

    try:
        mod = importlib.import_module(import_name)
    except ImportError:
        result.status = DepStatus.MISSING
        return result
    except Exception as e:
        result.status = DepStatus.BROKEN
        result.notes = f"{description} (import error: {e})"
        return result

    # Get version
    version = _get_version(mod)

    # For some packages, importlib.metadata is more reliable
    if version is None:
        try:
            from importlib.metadata import version as meta_version
            version = meta_version(pip_name)
        except Exception:
            pass

    result.installed_version = version

    if version and min_version and not _version_ok(version, min_version):
        result.status = DepStatus.VERSION_LOW

    return result


def check_all() -> List[DepCheck]:
    """Run all dependency checks and return results."""
    results = []
    for pip_name, import_name, min_ver, required, hint, desc in ALL_DEPS:
        results.append(check_dependency(pip_name, import_name, min_ver, required, hint, desc))
    return results


def check_system_tools() -> List[Dict]:
    """Check for useful system tools."""
    tools = [
        ("ping", "ICMP ping tests"),
        ("traceroute", "Traceroute tests"),
        ("nslookup", "DNS diagnostics"),
        ("git", "Version control"),
    ]
    results = []
    for name, desc in tools:
        path = shutil.which(name)
        results.append({
            "name": name,
            "description": desc,
            "installed": path is not None,
            "path": path or "",
        })
    return results


# ---------------------------------------------------------------------------
# First-run state
# ---------------------------------------------------------------------------

_STATE_DIR = pathlib.Path.home() / ".nettest"
_FIRST_RUN_FLAG = _STATE_DIR / ".doctor_passed"


def is_first_run() -> bool:
    """Check if this is the first time nettest is being run."""
    return not _FIRST_RUN_FLAG.exists()


def mark_doctor_passed():
    """Mark that the doctor check has passed."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _FIRST_RUN_FLAG.write_text("ok")


def needs_doctor() -> bool:
    """Return True if we should run a quick dependency check."""
    return is_first_run()


def quick_check() -> Tuple[bool, List[str]]:
    """
    Fast check for critical missing dependencies.
    Returns (all_ok, list_of_problems).
    """
    problems = []
    for pip_name, import_name, min_ver, required, hint, desc in ALL_DEPS:
        if not required:
            continue
        try:
            importlib.import_module(import_name)
        except ImportError:
            problems.append(f"  {pip_name} — {desc}\n    Install: {hint}")

    return (len(problems) == 0, problems)


def install_missing(deps: List[DepCheck]) -> List[Tuple[str, bool, str]]:
    """
    Attempt to pip-install missing required dependencies.
    Returns list of (name, success, message).
    """
    results = []
    for dep in deps:
        if dep.status not in (DepStatus.MISSING, DepStatus.VERSION_LOW):
            continue
        if not dep.required:
            continue
        # Only auto-install pip-installable packages
        pip_cmd = None
        for line in dep.install_hint.split("\n"):
            line = line.strip()
            if line.startswith("pip"):
                pip_cmd = line
                break

        if not pip_cmd:
            results.append((dep.name, False, f"Manual install required: {dep.install_hint}"))
            continue

        try:
            # Extract package spec from pip command
            parts = pip_cmd.split()
            pkg_spec = parts[-1]  # e.g., "click>=8.1"
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg_spec],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode == 0:
                results.append((dep.name, True, "Installed successfully"))
            else:
                results.append((dep.name, False, proc.stderr.strip().split("\n")[-1]))
        except Exception as e:
            results.append((dep.name, False, str(e)))

    return results
