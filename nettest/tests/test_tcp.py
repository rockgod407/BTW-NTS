"""TCP connectivity and port tests."""
from __future__ import annotations

import socket
import time
from typing import List

from nettest.core.config import TcpTarget
from nettest.core.result import Status, TestResult


def run_tcp_tests(targets: List[TcpTarget]) -> List[TestResult]:
    """Run TCP connectivity tests against configured targets."""
    results: List[TestResult] = []
    for target in targets:
        for port in target.ports:
            results.append(_test_tcp_connect(target.host, port, target.timeout))
    return results


def _test_tcp_connect(host: str, port: int, timeout: int) -> TestResult:
    """Test TCP connection establishment to host:port."""
    name = f"TCP Connect {host}:{port}"
    start = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((host, port))
        elapsed = (time.monotonic() - start) * 1000

        if result == 0:
            # Try to detect banner
            banner = _grab_banner(sock)
            msg = f"Port {port} open"
            if banner:
                msg += f" (banner: {banner[:60]})"
            return TestResult(
                name=name,
                category="tcp",
                status=Status.PASS,
                message=msg,
                duration_ms=elapsed,
                details={
                    "host": host,
                    "port": port,
                    "connect_time_ms": round(elapsed, 1),
                    "banner": banner,
                },
            )
        else:
            return TestResult(
                name=name,
                category="tcp",
                status=Status.FAIL,
                message=f"Port {port} closed or filtered (errno={result})",
                duration_ms=elapsed,
                details={"host": host, "port": port, "errno": result},
            )
    except socket.timeout:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="tcp",
            status=Status.FAIL,
            message=f"Connection timed out after {timeout}s",
            duration_ms=elapsed,
            details={"host": host, "port": port},
        )
    except socket.gaierror as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="tcp",
            status=Status.ERROR,
            message=f"DNS resolution failed: {e}",
            duration_ms=elapsed,
        )
    except OSError as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="tcp",
            status=Status.ERROR,
            message=f"Connection error: {e}",
            duration_ms=elapsed,
        )
    finally:
        sock.close()


def _grab_banner(sock: socket.socket) -> str:
    """Try to read a banner from an open socket (best-effort)."""
    try:
        sock.settimeout(1.0)
        data = sock.recv(1024)
        return data.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def scan_ports(host: str, port_range: range, timeout: int = 2) -> List[TestResult]:
    """Scan a range of ports on a host."""
    results: List[TestResult] = []
    for port in port_range:
        results.append(_test_tcp_connect(host, port, timeout))
    return results
