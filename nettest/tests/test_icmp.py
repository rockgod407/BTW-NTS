"""ICMP ping and traceroute tests."""
from __future__ import annotations

import platform
import re
import subprocess
import time
from typing import List, Optional

from nettest.core.config import IcmpTarget
from nettest.core.result import Status, TestResult


def run_icmp_tests(targets: List[IcmpTarget]) -> List[TestResult]:
    """Run ICMP ping and packet-loss tests against configured targets."""
    results: List[TestResult] = []
    for target in targets:
        results.append(_test_ping(target))
        results.append(_test_packet_loss(target))
    return results


def _test_ping(target: IcmpTarget) -> TestResult:
    """Ping a host and measure round-trip time."""
    name = f"ICMP Ping {target.host}"
    start = time.monotonic()

    is_windows = platform.system().lower() == "windows"
    count_flag = "-n" if is_windows else "-c"
    timeout_flag = "-w" if is_windows else "-W"

    cmd = ["ping", count_flag, str(target.count), timeout_flag, str(target.timeout), target.host]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=target.count * target.timeout + 5,
        )
        elapsed = (time.monotonic() - start) * 1000
        output = proc.stdout

        # Parse average RTT from ping output
        avg_rtt = _parse_ping_avg(output)

        if proc.returncode == 0 and avg_rtt is not None:
            return TestResult(
                name=name,
                category="icmp",
                status=Status.PASS,
                message=f"Avg RTT: {avg_rtt:.1f}ms ({target.count} packets)",
                duration_ms=elapsed,
                details={
                    "host": target.host,
                    "avg_rtt_ms": avg_rtt,
                    "count": target.count,
                    "raw_output": output.strip(),
                },
            )
        elif proc.returncode == 0:
            return TestResult(
                name=name,
                category="icmp",
                status=Status.PASS,
                message=f"Host reachable ({target.count} packets)",
                duration_ms=elapsed,
                details={"host": target.host, "raw_output": output.strip()},
            )
        else:
            return TestResult(
                name=name,
                category="icmp",
                status=Status.FAIL,
                message=f"Host unreachable or 100% packet loss",
                duration_ms=elapsed,
                details={
                    "host": target.host,
                    "raw_output": output.strip(),
                    "stderr": proc.stderr.strip(),
                },
            )

    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="icmp",
            status=Status.FAIL,
            message="Ping command timed out",
            duration_ms=elapsed,
        )
    except FileNotFoundError:
        return TestResult(
            name=name,
            category="icmp",
            status=Status.SKIP,
            message="ping command not found",
            duration_ms=0,
        )


def _test_packet_loss(target: IcmpTarget) -> TestResult:
    """Measure packet loss percentage."""
    name = f"ICMP Packet Loss {target.host}"
    start = time.monotonic()

    is_windows = platform.system().lower() == "windows"
    count_flag = "-n" if is_windows else "-c"
    timeout_flag = "-w" if is_windows else "-W"

    cmd = ["ping", count_flag, str(target.count), timeout_flag, str(target.timeout), target.host]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=target.count * target.timeout + 5,
        )
        elapsed = (time.monotonic() - start) * 1000
        loss = _parse_packet_loss(proc.stdout)

        if loss is not None:
            if loss == 0:
                status = Status.PASS
                msg = "0% packet loss"
            elif loss < 5:
                status = Status.WARN
                msg = f"{loss:.0f}% packet loss"
            else:
                status = Status.FAIL
                msg = f"{loss:.0f}% packet loss"

            return TestResult(
                name=name,
                category="icmp",
                status=status,
                message=msg,
                duration_ms=elapsed,
                details={"host": target.host, "packet_loss_pct": loss},
            )
        else:
            return TestResult(
                name=name,
                category="icmp",
                status=Status.ERROR,
                message="Could not parse packet loss from ping output",
                duration_ms=elapsed,
                details={"raw_output": proc.stdout.strip()},
            )

    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="icmp",
            status=Status.FAIL,
            message="Ping command timed out",
            duration_ms=elapsed,
        )
    except FileNotFoundError:
        return TestResult(
            name=name,
            category="icmp",
            status=Status.SKIP,
            message="ping command not found",
            duration_ms=0,
        )


def run_traceroute(host: str, max_hops: int = 30) -> TestResult:
    """Run a traceroute to the target host."""
    name = f"Traceroute {host}"
    start = time.monotonic()

    is_windows = platform.system().lower() == "windows"
    cmd = ["tracert" if is_windows else "traceroute", "-m", str(max_hops), host]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        elapsed = (time.monotonic() - start) * 1000

        hops = proc.stdout.strip().split("\n")
        return TestResult(
            name=name,
            category="icmp",
            status=Status.PASS if proc.returncode == 0 else Status.WARN,
            message=f"Traceroute completed: {len(hops)} lines",
            duration_ms=elapsed,
            details={"host": host, "hops": hops, "max_hops": max_hops},
        )
    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="icmp",
            status=Status.FAIL,
            message="Traceroute timed out",
            duration_ms=elapsed,
        )
    except FileNotFoundError:
        return TestResult(
            name=name,
            category="icmp",
            status=Status.SKIP,
            message="traceroute command not found",
            duration_ms=0,
        )


def _parse_ping_avg(output: str) -> Optional[float]:
    """Parse average RTT from ping output (cross-platform)."""
    # macOS/Linux: round-trip min/avg/max/stddev = 1.234/5.678/9.012/1.234 ms
    match = re.search(r"[\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms", output)
    if match:
        return float(match.group(1))
    # Windows: Average = 5ms
    match = re.search(r"Average\s*=\s*(\d+)ms", output)
    if match:
        return float(match.group(1))
    return None


def _parse_packet_loss(output: str) -> Optional[float]:
    """Parse packet loss percentage from ping output."""
    # macOS/Linux: "X% packet loss" or "X.Y% packet loss"
    match = re.search(r"([\d.]+)% packet loss", output)
    if match:
        return float(match.group(1))
    # Windows: "(X% loss)"
    match = re.search(r"\((\d+)% loss\)", output)
    if match:
        return float(match.group(1))
    return None
