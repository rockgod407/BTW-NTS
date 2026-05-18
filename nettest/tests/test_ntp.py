"""NTP time synchronization tests."""
from __future__ import annotations

import time
from typing import List

import ntplib

from nettest.core.config import NtpTarget
from nettest.core.result import Status, TestResult


def run_ntp_tests(targets: List[NtpTarget]) -> List[TestResult]:
    """Run NTP time sync tests against configured targets."""
    results: List[TestResult] = []
    for target in targets:
        results.append(_test_ntp_offset(target))
        results.append(_test_ntp_reachability(target))
    return results


def _test_ntp_offset(target: NtpTarget) -> TestResult:
    """Check NTP time offset against a server."""
    name = f"NTP Offset {target.server}"
    start = time.monotonic()

    client = ntplib.NTPClient()

    try:
        response = client.request(target.server, version=3)
        elapsed = (time.monotonic() - start) * 1000

        offset_s = response.offset
        offset_ms = abs(offset_s) * 1000

        if abs(offset_s) <= target.max_offset:
            status = Status.PASS
            msg = f"Offset: {offset_s:+.4f}s ({offset_ms:.1f}ms)"
        elif abs(offset_s) <= target.max_offset * 5:
            status = Status.WARN
            msg = f"Offset: {offset_s:+.4f}s ({offset_ms:.1f}ms) - above threshold of {target.max_offset}s"
        else:
            status = Status.FAIL
            msg = f"Offset: {offset_s:+.4f}s ({offset_ms:.1f}ms) - significantly above threshold"

        return TestResult(
            name=name,
            category="ntp",
            status=status,
            message=msg,
            duration_ms=elapsed,
            details={
                "server": target.server,
                "offset_seconds": round(offset_s, 6),
                "delay_seconds": round(response.delay, 6),
                "stratum": response.stratum,
                "version": response.version,
                "max_offset": target.max_offset,
            },
        )

    except ntplib.NTPException as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="ntp",
            status=Status.FAIL,
            message=f"NTP error: {e}",
            duration_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="ntp",
            status=Status.ERROR,
            message=f"NTP request failed: {e}",
            duration_ms=elapsed,
        )


def _test_ntp_reachability(target: NtpTarget) -> TestResult:
    """Verify NTP server is reachable and responding."""
    name = f"NTP Reachability {target.server}"
    start = time.monotonic()

    client = ntplib.NTPClient()

    try:
        response = client.request(target.server, version=3)
        elapsed = (time.monotonic() - start) * 1000

        if response.stratum == 0:
            return TestResult(
                name=name,
                category="ntp",
                status=Status.WARN,
                message=f"Server responded but stratum=0 (kiss-of-death or unsynced)",
                duration_ms=elapsed,
                details={"server": target.server, "stratum": response.stratum},
            )

        return TestResult(
            name=name,
            category="ntp",
            status=Status.PASS,
            message=f"Server responding, stratum={response.stratum}, delay={response.delay*1000:.1f}ms",
            duration_ms=elapsed,
            details={
                "server": target.server,
                "stratum": response.stratum,
                "delay_ms": round(response.delay * 1000, 1),
            },
        )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="ntp",
            status=Status.FAIL,
            message=f"Server unreachable: {e}",
            duration_ms=elapsed,
        )
