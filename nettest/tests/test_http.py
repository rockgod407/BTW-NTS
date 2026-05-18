"""HTTP/HTTPS connectivity, health, and response tests."""
from __future__ import annotations

import time
from typing import List
from urllib.parse import urlparse

import requests
import requests.exceptions

from nettest.core.config import HttpTarget
from nettest.core.result import Status, TestResult


def run_http_tests(targets: List[HttpTarget]) -> List[TestResult]:
    """Run all HTTP tests against configured targets."""
    results: List[TestResult] = []
    for target in targets:
        results.append(_test_status(target))
        results.append(_test_response_time(target))
        parsed = urlparse(target.url)
        if parsed.scheme == "https":
            results.append(_test_tls_handshake(target))
        results.append(_test_headers(target))
    return results


def _test_status(target: HttpTarget) -> TestResult:
    """Verify HTTP status code matches expected."""
    name = f"HTTP Status {target.url}"
    start = time.monotonic()
    try:
        resp = requests.get(
            target.url,
            timeout=target.timeout,
            allow_redirects=target.follow_redirects,
            headers=target.headers or {},
        )
        elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == target.expected_status:
            return TestResult(
                name=name,
                category="http",
                status=Status.PASS,
                message=f"Got {resp.status_code} (expected {target.expected_status})",
                duration_ms=elapsed,
                details={"status_code": resp.status_code, "url": target.url},
            )
        else:
            return TestResult(
                name=name,
                category="http",
                status=Status.FAIL,
                message=f"Got {resp.status_code}, expected {target.expected_status}",
                duration_ms=elapsed,
                details={"status_code": resp.status_code, "url": target.url},
            )
    except requests.exceptions.RequestException as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="http",
            status=Status.ERROR,
            message=f"Request failed: {e}",
            duration_ms=elapsed,
        )


def _test_response_time(target: HttpTarget) -> TestResult:
    """Measure HTTP response time and warn if slow."""
    name = f"HTTP Response Time {target.url}"
    start = time.monotonic()
    try:
        resp = requests.get(
            target.url,
            timeout=target.timeout,
            allow_redirects=target.follow_redirects,
            headers=target.headers or {},
        )
        elapsed = (time.monotonic() - start) * 1000

        if elapsed < 500:
            status = Status.PASS
            msg = f"{elapsed:.0f}ms (< 500ms)"
        elif elapsed < 2000:
            status = Status.WARN
            msg = f"{elapsed:.0f}ms (slow, 500-2000ms)"
        else:
            status = Status.FAIL
            msg = f"{elapsed:.0f}ms (very slow, > 2000ms)"

        return TestResult(
            name=name,
            category="http",
            status=status,
            message=msg,
            duration_ms=elapsed,
            details={
                "elapsed_ms": round(elapsed, 1),
                "content_length": len(resp.content),
            },
        )
    except requests.exceptions.RequestException as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="http",
            status=Status.ERROR,
            message=f"Request failed: {e}",
            duration_ms=elapsed,
        )


def _test_tls_handshake(target: HttpTarget) -> TestResult:
    """Verify HTTPS/TLS handshake succeeds."""
    name = f"TLS Handshake {target.url}"
    start = time.monotonic()
    try:
        # requests verifies SSL by default
        resp = requests.get(target.url, timeout=target.timeout)
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="http",
            status=Status.PASS,
            message="TLS handshake succeeded",
            duration_ms=elapsed,
        )
    except requests.exceptions.SSLError as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="http",
            status=Status.FAIL,
            message=f"TLS error: {e}",
            duration_ms=elapsed,
        )
    except requests.exceptions.RequestException as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="http",
            status=Status.ERROR,
            message=f"Request failed: {e}",
            duration_ms=elapsed,
        )


def _test_headers(target: HttpTarget) -> TestResult:
    """Check for common security headers."""
    name = f"HTTP Security Headers {target.url}"
    start = time.monotonic()
    try:
        resp = requests.get(
            target.url,
            timeout=target.timeout,
            allow_redirects=target.follow_redirects,
        )
        elapsed = (time.monotonic() - start) * 1000

        security_headers = {
            "Strict-Transport-Security": False,
            "X-Content-Type-Options": False,
            "X-Frame-Options": False,
            "Content-Security-Policy": False,
        }

        present = []
        missing = []
        for header in security_headers:
            if header.lower() in {k.lower(): v for k, v in resp.headers.items()}:
                present.append(header)
            else:
                missing.append(header)

        if not missing:
            return TestResult(
                name=name,
                category="http",
                status=Status.PASS,
                message=f"All {len(security_headers)} security headers present",
                duration_ms=elapsed,
                details={"present": present, "missing": missing},
            )
        elif present:
            return TestResult(
                name=name,
                category="http",
                status=Status.WARN,
                message=f"{len(present)}/{len(security_headers)} security headers; missing: {', '.join(missing)}",
                duration_ms=elapsed,
                details={"present": present, "missing": missing},
            )
        else:
            return TestResult(
                name=name,
                category="http",
                status=Status.WARN,
                message="No security headers found",
                duration_ms=elapsed,
                details={"present": present, "missing": missing},
            )
    except requests.exceptions.RequestException as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="http",
            status=Status.ERROR,
            message=f"Request failed: {e}",
            duration_ms=elapsed,
        )
