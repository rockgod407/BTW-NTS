"""DNS resolution and record tests."""
from __future__ import annotations

import time
from typing import List

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.exception

from nettest.core.config import DnsTarget
from nettest.core.result import Status, TestResult


def run_dns_tests(targets: List[DnsTarget]) -> List[TestResult]:
    """Run DNS resolution tests against configured targets."""
    results: List[TestResult] = []
    for target in targets:
        for rtype in target.record_types:
            results.append(_test_dns_resolve(target, rtype))
        results.append(_test_dns_timing(target))
    return results


def _test_dns_resolve(target: DnsTarget, record_type: str) -> TestResult:
    """Resolve a DNS record and verify we get results."""
    name = f"DNS {record_type} {target.domain} @{target.nameserver}"
    start = time.monotonic()

    resolver = dns.resolver.Resolver()
    resolver.nameservers = [target.nameserver]
    resolver.timeout = target.timeout
    resolver.lifetime = target.timeout

    try:
        answers = resolver.resolve(target.domain, record_type)
        elapsed = (time.monotonic() - start) * 1000

        records = [str(rdata) for rdata in answers]

        return TestResult(
            name=name,
            category="dns",
            status=Status.PASS,
            message=f"{len(records)} record(s): {', '.join(records[:3])}{'...' if len(records) > 3 else ''}",
            duration_ms=elapsed,
            details={
                "domain": target.domain,
                "record_type": record_type,
                "records": records,
                "nameserver": target.nameserver,
                "ttl": answers.rrset.ttl if answers.rrset else None,
            },
        )

    except dns.resolver.NXDOMAIN:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dns",
            status=Status.FAIL,
            message=f"Domain {target.domain} does not exist (NXDOMAIN)",
            duration_ms=elapsed,
        )

    except dns.resolver.NoAnswer:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dns",
            status=Status.WARN,
            message=f"No {record_type} records found for {target.domain}",
            duration_ms=elapsed,
        )

    except dns.resolver.NoNameservers:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dns",
            status=Status.FAIL,
            message=f"No nameservers available for {target.domain}",
            duration_ms=elapsed,
        )

    except dns.exception.Timeout:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dns",
            status=Status.FAIL,
            message=f"DNS query timed out after {target.timeout}s",
            duration_ms=elapsed,
        )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dns",
            status=Status.ERROR,
            message=f"DNS error: {e}",
            duration_ms=elapsed,
        )


def _test_dns_timing(target: DnsTarget) -> TestResult:
    """Measure DNS resolution time and flag slow lookups."""
    name = f"DNS Resolution Time {target.domain} @{target.nameserver}"

    resolver = dns.resolver.Resolver()
    resolver.nameservers = [target.nameserver]
    resolver.timeout = target.timeout
    resolver.lifetime = target.timeout

    start = time.monotonic()
    try:
        resolver.resolve(target.domain, "A")
        elapsed = (time.monotonic() - start) * 1000

        if elapsed < 50:
            status = Status.PASS
            msg = f"{elapsed:.1f}ms (fast)"
        elif elapsed < 200:
            status = Status.PASS
            msg = f"{elapsed:.1f}ms (normal)"
        elif elapsed < 500:
            status = Status.WARN
            msg = f"{elapsed:.1f}ms (slow)"
        else:
            status = Status.FAIL
            msg = f"{elapsed:.1f}ms (very slow)"

        return TestResult(
            name=name,
            category="dns",
            status=status,
            message=msg,
            duration_ms=elapsed,
            details={
                "domain": target.domain,
                "nameserver": target.nameserver,
                "resolution_ms": round(elapsed, 1),
            },
        )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dns",
            status=Status.ERROR,
            message=f"DNS resolution failed: {e}",
            duration_ms=elapsed,
        )


def test_reverse_dns(ip: str, nameserver: str = "8.8.8.8") -> TestResult:
    """Perform reverse DNS lookup on an IP address."""
    name = f"Reverse DNS {ip}"
    start = time.monotonic()

    resolver = dns.resolver.Resolver()
    resolver.nameservers = [nameserver]

    try:
        rev_name = dns.reversename.from_address(ip)
        answers = resolver.resolve(rev_name, "PTR")
        elapsed = (time.monotonic() - start) * 1000

        records = [str(rdata) for rdata in answers]
        return TestResult(
            name=name,
            category="dns",
            status=Status.PASS,
            message=f"PTR: {', '.join(records)}",
            duration_ms=elapsed,
            details={"ip": ip, "ptr_records": records},
        )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dns",
            status=Status.WARN,
            message=f"No PTR record: {e}",
            duration_ms=elapsed,
        )
