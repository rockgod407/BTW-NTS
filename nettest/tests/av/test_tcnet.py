"""
TCNet (Show Control / Time Code Network) testing.

TCNet is used for show control synchronization. It broadcasts time code
and control data over UDP:
  - Port 60000: TCNet management / discovery
  - Port 60001: TCNet data (time code, status)

Packets use a defined header structure with node identification,
sequence numbers, and time code data.

Reference: https://www.tc-supply.com/tcnet
"""
from __future__ import annotations

import socket
import struct
import time
import threading
from typing import Any, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.base import LongFormMonitor, LongFormTestConfig, StreamStats

# TCNet constants
TCNET_PORT_MGMT = 60000
TCNET_PORT_DATA = 60001
TCNET_HEADER_MAGIC = b"TCN"
TCNET_BROADCAST = "255.255.255.255"


def discover_tcnet_nodes(timeout_seconds: int = 5) -> TestResult:
    """
    Discover TCNet nodes by listening for broadcast traffic
    on the TCNet management and data ports.
    """
    name = "TCNet Node Discovery"
    start = time.monotonic()
    found_nodes: Dict[str, Dict[str, Any]] = {}

    try:
        # Listen on both TCNet ports
        for port in [TCNET_PORT_MGMT, TCNET_PORT_DATA]:
            _listen_tcnet_port(port, timeout_seconds // 2 + 1, found_nodes)

        elapsed = (time.monotonic() - start) * 1000

        if found_nodes:
            nodes = list(found_nodes.values())
            return TestResult(
                name=name,
                category="tcnet",
                status=Status.PASS,
                message=f"Found {len(nodes)} TCNet node(s): {', '.join(n['ip'] for n in nodes)}",
                duration_ms=elapsed,
                details={"nodes": nodes},
            )
        else:
            return TestResult(
                name=name,
                category="tcnet",
                status=Status.WARN,
                message="No TCNet traffic detected",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="tcnet",
            status=Status.ERROR,
            message=f"TCNet discovery error: {e}",
            duration_ms=elapsed,
        )


def _listen_tcnet_port(
    port: int, timeout: int, found: Dict[str, Dict[str, Any]]
) -> None:
    """Listen on a TCNet port and collect node information."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(1.0)
        sock.bind(("", port))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
                ip = addr[0]

                node_info = {"ip": ip, "port": port, "packet_count": 0}

                # Try to parse TCNet header
                if len(data) >= 24 and data[:3] == TCNET_HEADER_MAGIC:
                    node_info["protocol_confirmed"] = True
                    # Bytes 3-4: header size, byte 5: message type
                    if len(data) > 5:
                        node_info["message_type"] = data[5]

                if ip in found:
                    found[ip]["packet_count"] += 1
                else:
                    node_info["packet_count"] = 1
                    found[ip] = node_info

            except socket.timeout:
                continue

        sock.close()
    except OSError:
        pass  # Port may already be in use


class TCNetStreamMonitor(LongFormMonitor):
    """Long-form TCNet time code monitoring."""

    def __init__(self, config: LongFormTestConfig):
        super().__init__(config)
        self.stats.protocol = "TCNet"
        self._lock = threading.Lock()
        self._last_sequence: Dict[str, int] = {}

    def run(self) -> StreamStats:
        """Monitor TCNet traffic for the configured duration."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass
            sock.settimeout(1.0)
            sock.bind(("", TCNET_PORT_DATA))

            self.start()
            last_time = 0.0

            while not self.should_stop:
                try:
                    data, addr = sock.recvfrom(4096)
                    now = time.monotonic()
                    self.stats.total_frames += 1
                    self.stats.total_bytes += len(data)

                    if last_time > 0:
                        self.stats.frame_intervals_ms.append(
                            (now - last_time) * 1000
                        )
                    last_time = now

                except socket.timeout:
                    continue

            self.stop()
            sock.close()

        except Exception as e:
            self.stats.errors.append({
                "time": time.time(),
                "error": f"TCNet monitor error: {e}",
            })

        return self.stats


def run_tcnet_tests(timeout: int = 5) -> List[TestResult]:
    """Run TCNet discovery tests."""
    return [discover_tcnet_nodes(timeout)]


def run_tcnet_longform_test(
    duration_seconds: int = 300,
    snapshot_interval: int = 10,
    on_snapshot=None,
) -> List[TestResult]:
    """Run long-form TCNet monitoring."""
    results: List[TestResult] = []

    disc = discover_tcnet_nodes(timeout_seconds=5)
    results.append(disc)

    config = LongFormTestConfig(
        duration_seconds=duration_seconds,
        snapshot_interval_seconds=snapshot_interval,
        on_snapshot=on_snapshot,
    )

    monitor = TCNetStreamMonitor(config)
    start = time.monotonic()
    stats = monitor.run()
    elapsed = (time.monotonic() - start) * 1000
    summary = stats.summary()

    if stats.total_frames > 0:
        results.append(TestResult(
            name="TCNet Long-form Reception",
            category="tcnet",
            status=Status.PASS,
            message=f"{stats.total_frames} packets in {stats.elapsed_seconds:.0f}s",
            duration_ms=elapsed,
            details=summary,
        ))
    else:
        results.append(TestResult(
            name="TCNet Long-form Reception",
            category="tcnet",
            status=Status.WARN,
            message="No TCNet data packets received",
            duration_ms=elapsed,
        ))

    return results
