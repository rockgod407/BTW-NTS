"""
End-to-end receivers — validate incoming AV signals.

Each receiver extracts the VerificationPayload from incoming
frames/packets and validates sequence, CRC, and timing.

Usage:
  Machine A$ nettest send --protocol ndi --preset 1080p60 --session 12345
  Machine B$ nettest receive --protocol ndi --session 12345
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.verification import (
    HEADER_SIZE,
    ReceiverState,
    VerificationPayload,
)


# =========================================================================
# NDI Verified Receiver
# =========================================================================

def receive_ndi(
    session_id: int,
    source_name: str = "",
    duration_seconds: int = 300,
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Receive NDI frames and validate the verification payload
    embedded in the first row of pixels.
    """
    results: List[TestResult] = []

    from nettest import ndi_native as ndi

    if not ndi.is_available():
        results.append(TestResult(
            name="NDI Verified Receiver",
            category="ndi",
            status=Status.SKIP,
            message=f"NDI not available: {ndi.get_load_error() or 'libndi.dylib not found'}",
            duration_ms=0,
        ))
        return results

    if not ndi.initialize():
        results.append(TestResult(
            name="NDI Verified Receiver",
            category="ndi",
            status=Status.ERROR,
            message="NDI runtime init failed",
            duration_ms=0,
        ))
        return results

    state = ReceiverState(session_id=session_id, protocol="NDI")
    start = time.monotonic()

    try:
        # Find the source
        finder = ndi.find_create_v2()
        ndi.find_wait_for_sources(finder, timeout_ms=5000)
        sources = ndi.find_get_current_sources(finder)

        target = None
        for src in sources:
            if not source_name or source_name.lower() in src.ndi_name.lower():
                target = src
                break

        ndi.find_destroy(finder)

        if target is None:
            ndi.destroy()
            results.append(TestResult(
                name="NDI Verified Receiver",
                category="ndi",
                status=Status.FAIL,
                message=f"Source '{source_name}' not found. Available: {[s.ndi_name for s in sources]}",
                duration_ms=(time.monotonic() - start) * 1000,
            ))
            return results

        results.append(TestResult(
            name=f"NDI Receiver Connected (session={session_id})",
            category="ndi",
            status=Status.PASS,
            message=f"Connected to '{target.ndi_name}' | waiting for session {session_id}",
            duration_ms=(time.monotonic() - start) * 1000,
        ))

        # Create receiver
        recv_create = ndi.RecvCreateV3()
        recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        recv_create.bandwidth = ndi.RECV_BANDWIDTH_HIGHEST
        receiver = ndi.recv_create_v3(recv_create)
        ndi.recv_connect(receiver, target)
        time.sleep(0.5)

        state.start_time = time.monotonic()
        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()

        while time.monotonic() < deadline:
            frame_type = ndi.recv_capture_v2(receiver, timeout_in_ms=1000)

            if frame_type == ndi.FRAME_TYPE_VIDEO:
                # Get frame data — verification header is in first 32 bytes
                # (first 8 BGRX pixels)
                try:
                    frame_data = ndi.recv_capture_v2_get_video_data(receiver)
                    if frame_data and len(frame_data) >= HEADER_SIZE:
                        header_bytes = bytes(frame_data[:HEADER_SIZE])
                        payload_bytes = bytes(frame_data[HEADER_SIZE:])
                        state.validate_frame(header_bytes, payload_bytes)
                except Exception:
                    # Some ndi-python versions have different APIs
                    # Fall back to just counting frames
                    state.frames_received += 1
                    state.frames_foreign += 1

            now = time.monotonic()
            if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                snap = state.take_snapshot()
                on_snapshot(snap)
                last_snapshot = now

        ndi.recv_destroy(receiver)
        ndi.destroy()

        elapsed = (time.monotonic() - start) * 1000
        summary = state.summary()

        results.append(_build_verdict_result("NDI", session_id, elapsed, summary))

    except Exception as e:
        try:
            ndi.destroy()
        except Exception:
            pass
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name="NDI Verified Receiver",
            category="ndi",
            status=Status.ERROR,
            message=f"Receiver error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# sACN Verified Receiver
# =========================================================================

def receive_sacn(
    session_id: int,
    universe: int = 1,
    duration_seconds: int = 300,
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Receive sACN data and validate the verification payload in the
    first 32 DMX channels of the specified universe.
    """
    results: List[TestResult] = []

    try:
        import sacn
    except ImportError:
        results.append(TestResult(
            name="sACN Verified Receiver",
            category="sacn",
            status=Status.SKIP,
            message="sacn not installed",
            duration_ms=0,
        ))
        return results

    state = ReceiverState(session_id=session_id, protocol="sACN")
    lock = threading.Lock()
    start = time.monotonic()

    def _on_packet(packet):
        with lock:
            dmx = packet.dmx_data
            if dmx and len(dmx) >= HEADER_SIZE:
                header_bytes = bytes(dmx[:HEADER_SIZE])
                payload_bytes = bytes(dmx[HEADER_SIZE:])
                state.validate_frame(header_bytes, payload_bytes)

    try:
        receiver = sacn.sACNreceiver()
        receiver.register_listener("universe", _on_packet, universe=universe)
        receiver.join_multicast(universe)
        receiver.start()

        state.start_time = time.monotonic()

        results.append(TestResult(
            name=f"sACN Receiver Listening (session={session_id})",
            category="sacn",
            status=Status.PASS,
            message=f"Listening on universe {universe} | session={session_id}",
            duration_ms=(time.monotonic() - start) * 1000,
        ))

        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()

        while time.monotonic() < deadline:
            time.sleep(0.5)
            now = time.monotonic()
            if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                with lock:
                    snap = state.take_snapshot()
                on_snapshot(snap)
                last_snapshot = now

        receiver.stop()
        elapsed = (time.monotonic() - start) * 1000

        with lock:
            summary = state.summary()

        results.append(_build_verdict_result("sACN", session_id, elapsed, summary))

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name="sACN Verified Receiver",
            category="sacn",
            status=Status.ERROR,
            message=f"Receiver error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# Generic UDP Verified Receiver
# =========================================================================

def receive_udp(
    protocol_name: str,
    listen_port: int,
    session_id: int,
    duration_seconds: int = 300,
    multicast_group: Optional[str] = None,
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Receive UDP packets and validate verification payloads.

    Works for Dante bandwidth sim, Art-Net, TCNet, Pro DJ Link, etc.
    """
    results: List[TestResult] = []
    state = ReceiverState(session_id=session_id, protocol=protocol_name)
    start = time.monotonic()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(1.0)

        if multicast_group:
            group = socket.inet_aton(multicast_group)
            mreq = struct.pack("4sL", group, socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        sock.bind(("", listen_port))

        state.start_time = time.monotonic()
        category = protocol_name.lower().replace(" ", "")

        results.append(TestResult(
            name=f"{protocol_name} UDP Receiver Listening (session={session_id})",
            category=category,
            status=Status.PASS,
            message=f"Listening on :{listen_port} {'(multicast ' + multicast_group + ')' if multicast_group else ''} | session={session_id}",
            duration_ms=(time.monotonic() - start) * 1000,
        ))

        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()

        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
                if len(data) >= HEADER_SIZE:
                    header_bytes = data[:HEADER_SIZE]
                    payload_bytes = data[HEADER_SIZE:]
                    state.validate_frame(header_bytes, payload_bytes)
            except socket.timeout:
                pass

            now = time.monotonic()
            if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                snap = state.take_snapshot()
                on_snapshot(snap)
                last_snapshot = now

        sock.close()
        elapsed = (time.monotonic() - start) * 1000
        summary = state.summary()

        results.append(_build_verdict_result(protocol_name, session_id, elapsed, summary))

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name=f"{protocol_name} UDP Receiver",
            category=protocol_name.lower().replace(" ", ""),
            status=Status.ERROR,
            message=f"Receiver error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# Verdict builder
# =========================================================================

def _build_verdict_result(
    protocol: str,
    session_id: int,
    elapsed_ms: float,
    summary: Dict[str, Any],
) -> TestResult:
    """Build the final verdict TestResult from a receiver summary."""
    verdict = summary.get("verdict", "UNKNOWN")
    category = protocol.lower().replace(" ", "")

    status_map = {
        "PERFECT": Status.PASS,
        "EXCELLENT": Status.PASS,
        "GOOD": Status.PASS,
        "MARGINAL": Status.WARN,
        "FAILING": Status.FAIL,
    }
    status = status_map.get(verdict, Status.FAIL)

    received = summary.get("frames_received", 0)
    dropped = summary.get("frames_dropped", 0)
    corrupted = summary.get("frames_corrupted", 0)
    reordered = summary.get("frames_out_of_order", 0)
    drop_pct = summary.get("drop_rate_pct", 0)
    duration = summary.get("duration_seconds", 0)

    # Build latency string
    lat_info = ""
    if "latency_ms" in summary:
        lat = summary["latency_ms"]
        lat_info = f" | latency avg={lat['avg']:.1f}ms p99={lat['p99']:.1f}ms"

    message = (
        f"{verdict}: {received} received, {dropped} dropped ({drop_pct:.4f}%), "
        f"{corrupted} corrupt, {reordered} reordered over {duration:.0f}s{lat_info}"
    )

    return TestResult(
        name=f"{protocol} E2E Verdict (session={session_id})",
        category=category,
        status=status,
        message=message,
        duration_ms=elapsed_ms,
        details=summary,
    )
