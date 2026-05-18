"""Performance and load testing."""
from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import requests

from nettest.core.config import HttpTarget, PerformanceConfig
from nettest.core.result import Status, TestResult


def run_performance_tests(
    http_targets: List[HttpTarget], perf_config: PerformanceConfig
) -> List[TestResult]:
    """Run performance and load tests against HTTP targets."""
    results: List[TestResult] = []
    for target in http_targets:
        results.append(_test_latency_percentiles(target, perf_config))
        results.append(_test_throughput(target, perf_config))
        results.append(_test_concurrent_connections(target, perf_config))
    return results


def _test_latency_percentiles(
    target: HttpTarget, config: PerformanceConfig
) -> TestResult:
    """Measure latency percentiles (p50, p90, p95, p99) over multiple requests."""
    name = f"Latency Percentiles {target.url}"
    latencies: List[float] = []
    errors = 0

    start = time.monotonic()
    for _ in range(config.requests_per_test):
        req_start = time.monotonic()
        try:
            requests.get(target.url, timeout=target.timeout)
            latencies.append((time.monotonic() - req_start) * 1000)
        except requests.exceptions.RequestException:
            errors += 1

    elapsed = (time.monotonic() - start) * 1000

    if not latencies:
        return TestResult(
            name=name,
            category="performance",
            status=Status.FAIL,
            message=f"All {config.requests_per_test} requests failed",
            duration_ms=elapsed,
        )

    latencies.sort()
    p50 = _percentile(latencies, 50)
    p90 = _percentile(latencies, 90)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    avg = statistics.mean(latencies)

    # Assess quality based on p95
    if p95 < 500:
        status = Status.PASS
    elif p95 < 2000:
        status = Status.WARN
    else:
        status = Status.FAIL

    msg = f"p50={p50:.0f}ms p90={p90:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms avg={avg:.0f}ms"
    if errors:
        msg += f" ({errors} errors)"

    return TestResult(
        name=name,
        category="performance",
        status=status,
        message=msg,
        duration_ms=elapsed,
        details={
            "url": target.url,
            "requests": config.requests_per_test,
            "errors": errors,
            "p50_ms": round(p50, 1),
            "p90_ms": round(p90, 1),
            "p95_ms": round(p95, 1),
            "p99_ms": round(p99, 1),
            "avg_ms": round(avg, 1),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
        },
    )


def _test_throughput(target: HttpTarget, config: PerformanceConfig) -> TestResult:
    """Measure request throughput (requests per second)."""
    name = f"Throughput {target.url}"
    completed = 0
    errors = 0

    start = time.monotonic()
    deadline = start + config.duration_seconds

    while time.monotonic() < deadline:
        try:
            requests.get(target.url, timeout=target.timeout)
            completed += 1
        except requests.exceptions.RequestException:
            errors += 1

    elapsed = (time.monotonic() - start) * 1000
    elapsed_s = elapsed / 1000
    rps = completed / elapsed_s if elapsed_s > 0 else 0

    if errors == 0:
        status = Status.PASS
        msg = f"{rps:.1f} req/s ({completed} requests in {elapsed_s:.1f}s)"
    elif errors < completed:
        status = Status.WARN
        msg = f"{rps:.1f} req/s ({completed} ok, {errors} errors in {elapsed_s:.1f}s)"
    else:
        status = Status.FAIL
        msg = f"All requests failed ({errors} errors in {elapsed_s:.1f}s)"

    return TestResult(
        name=name,
        category="performance",
        status=status,
        message=msg,
        duration_ms=elapsed,
        details={
            "url": target.url,
            "requests_per_second": round(rps, 2),
            "completed": completed,
            "errors": errors,
            "duration_seconds": round(elapsed_s, 1),
        },
    )


def _test_concurrent_connections(
    target: HttpTarget, config: PerformanceConfig
) -> TestResult:
    """Test behavior under concurrent connections."""
    name = f"Concurrent Connections ({config.concurrent_connections}x) {target.url}"

    latencies: List[float] = []
    errors = 0

    def _make_request():
        req_start = time.monotonic()
        try:
            resp = requests.get(target.url, timeout=target.timeout)
            return (time.monotonic() - req_start) * 1000, resp.status_code, None
        except requests.exceptions.RequestException as e:
            return (time.monotonic() - req_start) * 1000, None, str(e)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=config.concurrent_connections) as executor:
        futures = [
            executor.submit(_make_request)
            for _ in range(config.concurrent_connections)
        ]
        for future in as_completed(futures):
            latency, status_code, error = future.result()
            if error:
                errors += 1
            else:
                latencies.append(latency)

    elapsed = (time.monotonic() - start) * 1000

    if not latencies:
        return TestResult(
            name=name,
            category="performance",
            status=Status.FAIL,
            message=f"All {config.concurrent_connections} concurrent requests failed",
            duration_ms=elapsed,
        )

    avg = statistics.mean(latencies)
    max_lat = max(latencies)

    if errors == 0:
        status = Status.PASS
        msg = f"All {config.concurrent_connections} connected; avg={avg:.0f}ms max={max_lat:.0f}ms"
    else:
        status = Status.WARN
        msg = f"{len(latencies)}/{config.concurrent_connections} succeeded; avg={avg:.0f}ms ({errors} errors)"

    return TestResult(
        name=name,
        category="performance",
        status=status,
        message=msg,
        duration_ms=elapsed,
        details={
            "url": target.url,
            "concurrent": config.concurrent_connections,
            "succeeded": len(latencies),
            "errors": errors,
            "avg_ms": round(avg, 1),
            "max_ms": round(max_lat, 1),
        },
    )


def _percentile(sorted_data: List[float], pct: float) -> float:
    """Calculate the given percentile from sorted data."""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    d = k - f
    return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])
