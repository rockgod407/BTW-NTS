"""UDP connectivity tests."""
from __future__ import annotations

import socket
import time
from typing import List

from nettest.core.config import UdpTarget
from nettest.core.result import Status, TestResult


def run_udp_tests(targets: List[UdpTarget]) -> List[TestResult]:
    """Run UDP reachability tests against configured targets."""
    results: List[TestResult] = []
    for target in targets:
        results.append(_test_udp_dns_probe(target))
    return results


def _test_udp_dns_probe(target: UdpTarget) -> TestResult:
    """
    Test UDP reachability by sending a minimal DNS query.

    Since UDP is connectionless, we verify reachability by sending a DNS
    query to port 53 and checking for a response. For non-53 ports, we
    send a small probe packet and check for any response or ICMP unreachable.
    """
    name = f"UDP Probe {target.host}:{target.port}"
    start = time.monotonic()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(target.timeout)

    try:
        if target.port == 53:
            # Construct a minimal DNS query for "example.com" type A
            # Header: ID=0xAAAA, QR=0, OPCODE=0, RD=1, QDCOUNT=1
            dns_query = (
                b"\xaa\xaa"  # Transaction ID
                b"\x01\x00"  # Flags: Standard query, RD=1
                b"\x00\x01"  # Questions: 1
                b"\x00\x00"  # Answer RRs: 0
                b"\x00\x00"  # Authority RRs: 0
                b"\x00\x00"  # Additional RRs: 0
                b"\x07example\x03com\x00"  # Query: example.com
                b"\x00\x01"  # Type: A
                b"\x00\x01"  # Class: IN
            )
            sock.sendto(dns_query, (target.host, target.port))
        else:
            # Generic probe
            sock.sendto(b"\x00" * 8, (target.host, target.port))

        data, addr = sock.recvfrom(4096)
        elapsed = (time.monotonic() - start) * 1000

        return TestResult(
            name=name,
            category="udp",
            status=Status.PASS,
            message=f"Received {len(data)} bytes from {addr[0]}:{addr[1]}",
            duration_ms=elapsed,
            details={
                "host": target.host,
                "port": target.port,
                "response_bytes": len(data),
            },
        )

    except socket.timeout:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="udp",
            status=Status.WARN,
            message=f"No response within {target.timeout}s (UDP may be filtered or host silent)",
            duration_ms=elapsed,
            details={"host": target.host, "port": target.port},
        )
    except OSError as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="udp",
            status=Status.ERROR,
            message=f"Socket error: {e}",
            duration_ms=elapsed,
        )
    finally:
        sock.close()
