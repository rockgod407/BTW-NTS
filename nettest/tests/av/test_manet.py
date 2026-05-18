"""
MA-Net (grandMA network protocol) testing.

MA-Net is used by grandMA2 and grandMA3 lighting consoles for:
  - Session management and console linking
  - DMX data distribution
  - Parameter synchronization

MA-Net uses several protocols:
  - MA-Net1: Legacy protocol (UDP broadcast)
  - MA-Net2: Current protocol (UDP, ports 6454 for Art-Net compat,
             and proprietary ports 6000-6100 range)
  - MA-Net3: grandMA3 protocol

This module also supports Art-Net detection since many MA systems
use Art-Net for DMX distribution.

Art-Net ports: 6454 (UDP)
MA-Net ports: Typically 6000-6100 range (UDP)
"""
from __future__ import annotations

import socket
import struct
import time
from typing import Any, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.base import LongFormMonitor, LongFormTestConfig, StreamStats

# Art-Net constants
ARTNET_PORT = 6454
ARTNET_MAGIC = b"Art-Net\x00"

# MA-Net port range
MANET_PORT_START = 6000
MANET_PORT_END = 6100


def discover_artnet_nodes(timeout_seconds: int = 5) -> TestResult:
    """
    Discover Art-Net nodes on the network.

    Sends an ArtPoll packet and listens for ArtPollReply responses.
    Many MA-Net devices also respond to Art-Net discovery.
    """
    name = "Art-Net / MA-Net Node Discovery"
    start = time.monotonic()
    found_nodes: Dict[str, Dict[str, Any]] = {}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(1.0)
        sock.bind(("", ARTNET_PORT))

        # Send ArtPoll (OpCode 0x2000)
        artpoll = (
            ARTNET_MAGIC
            + struct.pack("<H", 0x2000)  # OpCode: ArtPoll
            + struct.pack(">BB", 0, 14)  # ProtVer Hi, Lo
            + b"\x00\x00"  # TalkToMe, Priority
        )
        sock.sendto(artpoll, ("255.255.255.255", ARTNET_PORT))

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
                ip = addr[0]

                if len(data) >= 14 and data[:8] == ARTNET_MAGIC:
                    opcode = struct.unpack("<H", data[8:10])[0]

                    if opcode == 0x2100:  # ArtPollReply
                        node_info = _parse_artpoll_reply(data, ip)
                        found_nodes[ip] = node_info

            except socket.timeout:
                continue

        sock.close()
        elapsed = (time.monotonic() - start) * 1000

        if found_nodes:
            nodes = list(found_nodes.values())
            names = [f"{n.get('short_name', n['ip'])} ({n['ip']})" for n in nodes]
            return TestResult(
                name=name,
                category="manet",
                status=Status.PASS,
                message=f"Found {len(nodes)} node(s): {', '.join(names)}",
                duration_ms=elapsed,
                details={"nodes": nodes},
            )
        else:
            return TestResult(
                name=name,
                category="manet",
                status=Status.WARN,
                message="No Art-Net / MA-Net nodes found",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="manet",
            status=Status.ERROR,
            message=f"Discovery error: {e}",
            duration_ms=elapsed,
        )


def test_artnet_dmx_stream(
    universe: int = 0, duration_seconds: int = 30
) -> TestResult:
    """
    Monitor Art-Net DMX data stream for a specific universe.

    Listens for ArtDmx (OpCode 0x5000) packets and tracks
    sequence numbers, packet rate, and data consistency.
    """
    name = f"Art-Net DMX Stream (Universe {universe})"
    start = time.monotonic()

    packet_count = 0
    sequence_gaps = 0
    last_sequence = -1
    intervals: List[float] = []
    last_time = 0.0

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(1.0)
        sock.bind(("", ARTNET_PORT))

        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
                now = time.monotonic()

                if len(data) >= 18 and data[:8] == ARTNET_MAGIC:
                    opcode = struct.unpack("<H", data[8:10])[0]

                    if opcode == 0x5000:  # ArtDmx
                        seq = data[12]
                        pkt_universe = struct.unpack("<H", data[14:16])[0]

                        if pkt_universe == universe:
                            packet_count += 1

                            if last_sequence >= 0 and seq != 0:
                                expected = (last_sequence + 1) % 256
                                if seq != expected:
                                    sequence_gaps += 1
                            last_sequence = seq

                            if last_time > 0:
                                intervals.append((now - last_time) * 1000)
                            last_time = now

            except socket.timeout:
                continue

        sock.close()
        elapsed = (time.monotonic() - start) * 1000

        if packet_count > 0:
            import statistics
            avg = statistics.mean(intervals) if intervals else 0

            status = Status.PASS if sequence_gaps == 0 else Status.WARN
            return TestResult(
                name=name,
                category="manet",
                status=status,
                message=f"{packet_count} packets, {sequence_gaps} gaps, avg interval={avg:.1f}ms",
                duration_ms=elapsed,
                details={
                    "packets": packet_count,
                    "sequence_gaps": sequence_gaps,
                    "avg_interval_ms": round(avg, 2),
                },
            )
        else:
            return TestResult(
                name=name,
                category="manet",
                status=Status.WARN,
                message=f"No Art-Net DMX data for universe {universe}",
                duration_ms=elapsed,
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="manet",
            status=Status.ERROR,
            message=f"DMX stream error: {e}",
            duration_ms=elapsed,
        )


def run_manet_tests(timeout: int = 5) -> List[TestResult]:
    """Run MA-Net / Art-Net discovery tests."""
    return [discover_artnet_nodes(timeout)]


def _parse_artpoll_reply(data: bytes, ip: str) -> Dict[str, Any]:
    """Parse an ArtPollReply packet."""
    node = {"ip": ip}
    try:
        if len(data) >= 44:
            node["short_name"] = data[26:44].split(b"\x00")[0].decode(
                "utf-8", errors="replace"
            )
        if len(data) >= 108:
            node["long_name"] = data[44:108].split(b"\x00")[0].decode(
                "utf-8", errors="replace"
            )
        if len(data) >= 174:
            node["num_ports"] = struct.unpack(">H", data[172:174])[0]
    except Exception:
        pass
    return node
