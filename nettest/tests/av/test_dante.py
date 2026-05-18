"""
Dante audio network testing.

Dante uses mDNS/DNS-SD for device discovery and PTP (IEEE 1588) for
clock synchronization. Audio streams use RTP over UDP multicast/unicast.

Since Dante is proprietary (Audinate), this module focuses on
network-level validation:
  - mDNS discovery of Dante devices (_netaudio-arc._tcp, _netaudio-dbc._tcp)
  - PTP clock sync monitoring
  - Multicast group membership
  - Network bandwidth and latency assessment

Requires: dnspython (already a dependency), zeroconf (optional for deeper mDNS)
"""
from __future__ import annotations

import socket
import struct
import time
from typing import Any, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.base import LongFormMonitor, LongFormTestConfig, StreamStats

# Dante mDNS service types
DANTE_SERVICE_TYPES = [
    "_netaudio-arc._tcp.local.",  # Dante ARC (Audio Routing Control)
    "_netaudio-dbc._tcp.local.",  # Dante DBC (Device & Broadcast Control)
    "_netaudio-cmc._tcp.local.",  # Dante CMC (Clocking & Media Clock)
    "_netaudio-chan._udp.local.", # Dante channel service
]


def discover_dante_devices(timeout_seconds: int = 5) -> TestResult:
    """
    Discover Dante devices on the network using mDNS queries.

    Sends mDNS queries for known Dante service types and collects responses.
    """
    name = "Dante Device Discovery (mDNS)"
    start = time.monotonic()

    # Try using zeroconf if available (more reliable)
    try:
        from zeroconf import Zeroconf, ServiceBrowser
        return _discover_with_zeroconf(timeout_seconds)
    except ImportError:
        pass

    # Fallback: raw mDNS query
    found_devices: Dict[str, Dict[str, Any]] = {}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        sock.settimeout(timeout_seconds)

        # Join mDNS multicast group 224.0.0.251
        mcast_group = "224.0.0.251"
        group = socket.inet_aton(mcast_group)
        mreq = struct.pack("4sL", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.bind(("", 5353))

        # Send mDNS PTR query for Dante services
        for svc_type in DANTE_SERVICE_TYPES[:2]:  # Query ARC and DBC
            query = _build_mdns_query(svc_type)
            sock.sendto(query, (mcast_group, 5353))

        # Listen for responses
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
                ip = addr[0]
                # Simple heuristic: check if response contains Dante service names
                data_str = data.decode("utf-8", errors="replace").lower()
                if "netaudio" in data_str:
                    if ip not in found_devices:
                        found_devices[ip] = {
                            "ip": ip,
                            "first_seen": time.time(),
                        }
            except socket.timeout:
                break

        sock.close()
        elapsed = (time.monotonic() - start) * 1000

        if found_devices:
            devices = list(found_devices.values())
            return TestResult(
                name=name,
                category="dante",
                status=Status.PASS,
                message=f"Found {len(devices)} Dante device(s): {', '.join(d['ip'] for d in devices)}",
                duration_ms=elapsed,
                details={"devices": devices},
            )
        else:
            return TestResult(
                name=name,
                category="dante",
                status=Status.WARN,
                message="No Dante devices found (may need longer timeout or devices may be on different VLAN)",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dante",
            status=Status.ERROR,
            message=f"Discovery error: {e}",
            duration_ms=elapsed,
        )


def _discover_with_zeroconf(timeout_seconds: int) -> TestResult:
    """Use the zeroconf library for Dante discovery."""
    from zeroconf import Zeroconf, ServiceBrowser
    import threading

    name = "Dante Device Discovery (Zeroconf)"
    start = time.monotonic()

    found = {}
    lock = threading.Lock()

    class Listener:
        def add_service(self, zc, type_, name_):
            info = zc.get_service_info(type_, name_)
            if info:
                with lock:
                    found[name_] = {
                        "name": name_,
                        "type": type_,
                        "server": info.server,
                        "port": info.port,
                        "addresses": [socket.inet_ntoa(a) for a in info.addresses],
                    }

        def remove_service(self, zc, type_, name_):
            pass

        def update_service(self, zc, type_, name_):
            pass

    try:
        zc = Zeroconf()
        listener = Listener()
        browsers = []
        for svc in DANTE_SERVICE_TYPES:
            browsers.append(ServiceBrowser(zc, svc, listener))

        time.sleep(timeout_seconds)
        zc.close()

        elapsed = (time.monotonic() - start) * 1000

        if found:
            devices = list(found.values())
            return TestResult(
                name=name,
                category="dante",
                status=Status.PASS,
                message=f"Found {len(devices)} Dante service(s)",
                duration_ms=elapsed,
                details={"services": devices},
            )
        else:
            return TestResult(
                name=name,
                category="dante",
                status=Status.WARN,
                message="No Dante services found",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dante",
            status=Status.ERROR,
            message=f"Zeroconf error: {e}",
            duration_ms=elapsed,
        )


def test_ptp_sync(interface: str = "", duration_seconds: int = 30) -> TestResult:
    """
    Monitor PTP (Precision Time Protocol) sync messages.

    Dante relies on PTP for clock synchronization. This test listens
    for PTP Sync and Follow_Up messages on the PTP multicast group
    (224.0.1.129, port 319/320) and checks for consistent timing.
    """
    name = "Dante PTP Clock Sync"
    start = time.monotonic()

    ptp_multicast = "224.0.1.129"
    ptp_event_port = 319
    sync_count = 0
    intervals: List[float] = []
    last_sync_time = 0.0

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(2.0)

        group = socket.inet_aton(ptp_multicast)
        mreq = struct.pack("4sL", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.bind(("", ptp_event_port))

        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
                now = time.monotonic()
                # PTP Sync message type = 0x00 (first nibble of first byte)
                if len(data) >= 34 and (data[0] & 0x0F) == 0x00:
                    sync_count += 1
                    if last_sync_time > 0:
                        intervals.append((now - last_sync_time) * 1000)
                    last_sync_time = now
            except socket.timeout:
                continue

        sock.close()
        elapsed = (time.monotonic() - start) * 1000

        if sync_count > 0:
            import statistics
            avg_interval = statistics.mean(intervals) if intervals else 0
            stddev = statistics.stdev(intervals) if len(intervals) > 1 else 0

            if stddev < avg_interval * 0.1:  # Less than 10% jitter
                status = Status.PASS
            elif stddev < avg_interval * 0.3:
                status = Status.WARN
            else:
                status = Status.FAIL

            return TestResult(
                name=name,
                category="dante",
                status=status,
                message=f"{sync_count} PTP Sync messages, avg interval={avg_interval:.1f}ms, jitter={stddev:.1f}ms",
                duration_ms=elapsed,
                details={
                    "sync_count": sync_count,
                    "avg_interval_ms": round(avg_interval, 2),
                    "stddev_ms": round(stddev, 2),
                    "duration_s": duration_seconds,
                },
            )
        else:
            return TestResult(
                name=name,
                category="dante",
                status=Status.WARN,
                message=f"No PTP Sync messages received in {duration_seconds}s",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="dante",
            status=Status.ERROR,
            message=f"PTP monitoring error: {e}",
            duration_ms=elapsed,
        )


def run_dante_tests(timeout: int = 5) -> List[TestResult]:
    """Run Dante network tests."""
    results = [discover_dante_devices(timeout)]
    # PTP sync is a longer test, add it separately
    return results


def _build_mdns_query(service_type: str) -> bytes:
    """Build a minimal mDNS PTR query packet."""
    # DNS header: ID=0, flags=0 (standard query), QDCOUNT=1
    header = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"

    # Encode the service name
    qname = b""
    for part in service_type.rstrip(".").split("."):
        qname += bytes([len(part)]) + part.encode()
    qname += b"\x00"

    # QTYPE=PTR(12), QCLASS=IN(1) with unicast-response bit
    qtype_class = b"\x00\x0c\x00\x01"

    return header + qname + qtype_class
