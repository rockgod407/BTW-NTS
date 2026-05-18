"""
Pro DJ Link protocol testing.

Pioneer DJ's Pro DJ Link protocol connects CDJs, mixers, and
rekordbox over Ethernet. Communication happens on:
  - Port 50000: Device status / keep-alive broadcasts (UDP)
  - Port 50001: Beat sync / tempo data (UDP)
  - Port 50002: Device status detail (UDP)

The protocol has been well reverse-engineered by the Deep Symmetry
project (https://djl-analysis.deepsymmetry.org/).

Packet structure:
  - Bytes 0-9: Magic header (varies by packet type)
  - Byte 10: Packet type identifier
  - Byte 11+: Type-specific payload

Common packet types:
  - 0x06: CDJ keep-alive
  - 0x0a: CDJ status
  - 0x29: Beat packet
"""
from __future__ import annotations

import socket
import struct
import time
import threading
from typing import Any, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.base import LongFormMonitor, LongFormTestConfig, StreamStats

# Pro DJ Link constants
PRODJLINK_KEEPALIVE_PORT = 50000
PRODJLINK_BEAT_PORT = 50001
PRODJLINK_STATUS_PORT = 50002

# Known magic bytes for CDJ keep-alive packets
PRODJLINK_MAGIC = bytes([
    0x51, 0x73, 0x70, 0x74, 0x31, 0x57,
    0x6d, 0x4a, 0x4f, 0x4c,  # "Qspt1WmJOL"
])


def discover_prodjlink_devices(timeout_seconds: int = 10) -> TestResult:
    """
    Discover Pro DJ Link devices on the network.

    Listens for CDJ/mixer keep-alive broadcasts on port 50000.
    These are sent approximately every 1.5 seconds by each device.
    """
    name = "Pro DJ Link Device Discovery"
    start = time.monotonic()
    found_devices: Dict[int, Dict[str, Any]] = {}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(1.0)
        sock.bind(("", PRODJLINK_KEEPALIVE_PORT))

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)

                # Verify Pro DJ Link magic header
                if len(data) >= 36 and data[:10] == PRODJLINK_MAGIC:
                    packet_type = data[10]

                    if packet_type == 0x06:  # Keep-alive
                        # Byte 24: device number (1-6)
                        device_num = data[24] if len(data) > 24 else 0
                        # Bytes 12-31: device name (null-terminated)
                        device_name = data[12:32].split(b"\x00")[0].decode(
                            "utf-8", errors="replace"
                        )

                        found_devices[device_num] = {
                            "device_number": device_num,
                            "device_name": device_name.strip(),
                            "ip": addr[0],
                            "mac": _extract_mac(data) if len(data) > 42 else "",
                            "packet_type": hex(packet_type),
                        }

            except socket.timeout:
                continue

        sock.close()
        elapsed = (time.monotonic() - start) * 1000

        if found_devices:
            devices = sorted(found_devices.values(), key=lambda d: d["device_number"])
            names = [f"#{d['device_number']} {d['device_name']} ({d['ip']})" for d in devices]
            return TestResult(
                name=name,
                category="prodjlink",
                status=Status.PASS,
                message=f"Found {len(devices)} device(s): {', '.join(names)}",
                duration_ms=elapsed,
                details={"devices": devices},
            )
        else:
            return TestResult(
                name=name,
                category="prodjlink",
                status=Status.WARN,
                message="No Pro DJ Link devices found (ensure CDJs/mixer are on and linked)",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="prodjlink",
            status=Status.ERROR,
            message=f"Discovery error: {e}",
            duration_ms=elapsed,
        )


def test_beat_sync(duration_seconds: int = 30) -> TestResult:
    """
    Monitor Pro DJ Link beat packets for timing consistency.

    Beat packets are sent on port 50001 and contain BPM, beat position,
    and timing information critical for sync.
    """
    name = "Pro DJ Link Beat Sync"
    start = time.monotonic()

    beat_count = 0
    intervals: List[float] = []
    last_beat_time = 0.0
    devices_seen: set = set()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(1.0)
        sock.bind(("", PRODJLINK_BEAT_PORT))

        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
                now = time.monotonic()

                if len(data) >= 36 and data[:10] == PRODJLINK_MAGIC:
                    packet_type = data[10]
                    if packet_type == 0x28 or packet_type == 0x29:  # Beat
                        beat_count += 1
                        devices_seen.add(addr[0])
                        if last_beat_time > 0:
                            intervals.append((now - last_beat_time) * 1000)
                        last_beat_time = now

            except socket.timeout:
                continue

        sock.close()
        elapsed = (time.monotonic() - start) * 1000

        if beat_count > 0:
            import statistics
            avg = statistics.mean(intervals) if intervals else 0
            stddev = statistics.stdev(intervals) if len(intervals) > 1 else 0

            return TestResult(
                name=name,
                category="prodjlink",
                status=Status.PASS if stddev < avg * 0.15 else Status.WARN,
                message=f"{beat_count} beats from {len(devices_seen)} device(s), avg interval={avg:.1f}ms, jitter={stddev:.1f}ms",
                duration_ms=elapsed,
                details={
                    "beat_count": beat_count,
                    "devices": list(devices_seen),
                    "avg_interval_ms": round(avg, 2),
                    "stddev_ms": round(stddev, 2),
                },
            )
        else:
            return TestResult(
                name=name,
                category="prodjlink",
                status=Status.WARN,
                message=f"No beat packets received in {duration_seconds}s",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="prodjlink",
            status=Status.ERROR,
            message=f"Beat sync error: {e}",
            duration_ms=elapsed,
        )


class ProDJLinkMonitor(LongFormMonitor):
    """Long-form Pro DJ Link status monitoring."""

    def __init__(self, config: LongFormTestConfig):
        super().__init__(config)
        self.stats.protocol = "Pro DJ Link"
        self._devices_seen: Dict[int, int] = {}  # device_num -> packet_count

    def run(self) -> StreamStats:
        """Monitor Pro DJ Link traffic for the configured duration."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass
            sock.settimeout(1.0)
            sock.bind(("", PRODJLINK_STATUS_PORT))

            self.start()
            last_time = 0.0

            while not self.should_stop:
                try:
                    data, addr = sock.recvfrom(2048)
                    now = time.monotonic()
                    self.stats.total_frames += 1
                    self.stats.total_bytes += len(data)

                    if last_time > 0:
                        self.stats.frame_intervals_ms.append(
                            (now - last_time) * 1000
                        )
                    last_time = now

                    # Track per-device packets
                    if len(data) > 24 and data[:10] == PRODJLINK_MAGIC:
                        dev = data[24]
                        self._devices_seen[dev] = self._devices_seen.get(dev, 0) + 1

                except socket.timeout:
                    continue

            self.stop()
            sock.close()

            self.stats.properties["devices"] = dict(self._devices_seen)

        except Exception as e:
            self.stats.errors.append({
                "time": time.time(),
                "error": f"Pro DJ Link monitor error: {e}",
            })

        return self.stats


def run_prodjlink_tests(timeout: int = 10) -> List[TestResult]:
    """Run Pro DJ Link discovery tests."""
    return [discover_prodjlink_devices(timeout)]


def _extract_mac(data: bytes) -> str:
    """Extract MAC address from Pro DJ Link keep-alive packet."""
    try:
        mac_bytes = data[38:44]
        return ":".join(f"{b:02x}" for b in mac_bytes)
    except Exception:
        return ""
