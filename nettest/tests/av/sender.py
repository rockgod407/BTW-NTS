"""
End-to-end senders — emit verified AV signals.

Each sender embeds a VerificationPayload into the protocol's native
format so the receiver on the other machine can validate every
frame/packet.

Usage:
  Machine A$ nettest send --protocol ndi --preset 1080p60 --session 12345
  Machine B$ nettest receive --protocol ndi --session 12345
"""
from __future__ import annotations

import math
import os
import socket
import struct
import time
import zlib
from typing import Any, Callable, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.verification import (
    HEADER_SIZE,
    SenderState,
    VerificationPayload,
    generate_session_id,
)
from nettest.tests.av.presets import (
    NDIPreset, NDI_PRESETS,
    SACNPreset, SACN_PRESETS,
    DantePreset, DANTE_PRESETS,
    ArtNetPreset, ARTNET_PRESETS,
)


# =========================================================================
# NDI Verified Sender
# =========================================================================

def send_ndi(
    preset: NDIPreset,
    session_id: int,
    duration_seconds: int = 300,
    source_name: str = "NetTest Verified",
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Send NDI frames with verification payload embedded in pixel data.

    The first row of pixels in each frame contains the 32-byte
    verification header, allowing the receiver to validate every frame.
    """
    results: List[TestResult] = []

    try:
        import NDIlib as ndi
    except ImportError:
        results.append(TestResult(
            name="NDI Verified Sender",
            category="ndi",
            status=Status.SKIP,
            message="ndi-python not installed",
            duration_ms=0,
        ))
        return results

    if not ndi.initialize():
        results.append(TestResult(
            name="NDI Verified Sender",
            category="ndi",
            status=Status.ERROR,
            message="NDI runtime init failed",
            duration_ms=0,
        ))
        return results

    state = SenderState(
        session_id=session_id,
        protocol="NDI",
        preset_name=preset.name,
    )

    start = time.monotonic()
    state.start_time = time.time()

    try:
        send_create = ndi.SendCreate()
        send_create.ndi_name = source_name
        sender = ndi.send_create(send_create)

        if not sender:
            ndi.destroy()
            results.append(TestResult(
                name="NDI Verified Sender",
                category="ndi",
                status=Status.ERROR,
                message="Failed to create NDI sender",
                duration_ms=0,
            ))
            return results

        video_frame = ndi.VideoFrameV2()
        video_frame.xres = preset.width
        video_frame.yres = preset.height
        video_frame.FourCC = ndi.FOURCC_VIDEO_TYPE_BGRX
        video_frame.frame_rate_N = _fps_to_rational(preset.fps)[0]
        video_frame.frame_rate_D = _fps_to_rational(preset.fps)[1]

        # Base frame data (color bars)
        base_frame = _generate_color_bars_bgrx(preset.width, preset.height)
        row_bytes = preset.width * 4  # BGRX = 4 bytes per pixel

        results.append(TestResult(
            name=f"NDI Sender Start (session={session_id})",
            category="ndi",
            status=Status.PASS,
            message=(
                f"Sending {preset.width}x{preset.height}@{preset.fps}fps "
                f"as '{source_name}' | session={session_id}"
            ),
            duration_ms=(time.monotonic() - start) * 1000,
        ))

        interval = 1.0 / preset.fps
        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()
        next_frame_time = time.monotonic()

        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_frame_time:
                # Build frame with verification header in first row
                frame_data = bytearray(base_frame)
                payload = state.next_payload(base_frame[row_bytes:])
                header = payload.encode()

                # Embed 32-byte header into first 8 pixels (4 bytes each = 32 bytes)
                frame_data[:HEADER_SIZE] = header

                video_frame.data = bytes(frame_data)
                state.total_bytes += len(frame_data)

                try:
                    ndi.send_send_video_v2(sender, video_frame)
                except Exception:
                    state.errors += 1

                next_frame_time += interval

                if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                    on_snapshot({
                        "elapsed_s": round(state.elapsed_seconds, 1),
                        "frames_sent": state.sequence,
                        "errors": state.errors,
                        "session_id": session_id,
                    })
                    last_snapshot = now
            else:
                sleep_time = next_frame_time - now
                if sleep_time > 0.0005:
                    time.sleep(sleep_time * 0.8)

        ndi.send_destroy(sender)
        ndi.destroy()

        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name=f"NDI Sender Done (session={session_id})",
            category="ndi",
            status=Status.PASS if state.errors == 0 else Status.WARN,
            message=f"Sent {state.sequence} verified frames in {elapsed/1000:.0f}s ({state.errors} errors)",
            duration_ms=elapsed,
            details=state.summary(),
        ))

    except Exception as e:
        try:
            ndi.destroy()
        except Exception:
            pass
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name="NDI Verified Sender",
            category="ndi",
            status=Status.ERROR,
            message=f"Sender error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# sACN Verified Sender
# =========================================================================

def send_sacn(
    preset: SACNPreset,
    session_id: int,
    duration_seconds: int = 300,
    pattern: str = "chase",
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Send sACN data with verification payload in DMX channels.

    Uses the first universe's first 32 channels to carry the
    verification header. Remaining channels carry the test pattern.
    All additional universes carry pattern data only.
    """
    results: List[TestResult] = []

    try:
        import sacn
    except ImportError:
        results.append(TestResult(
            name="sACN Verified Sender",
            category="sacn",
            status=Status.SKIP,
            message="sacn not installed",
            duration_ms=0,
        ))
        return results

    state = SenderState(
        session_id=session_id,
        protocol="sACN",
        preset_name=preset.name,
    )

    start = time.monotonic()
    state.start_time = time.time()

    try:
        sender = sacn.sACNsender(fps=preset.refresh_rate_hz)
        sender.start()

        universe_range = list(range(1, preset.universes + 1))
        for u in universe_range:
            sender.activate_output(u)
            sender[u].multicast = True
            sender[u].priority = preset.priority

        results.append(TestResult(
            name=f"sACN Sender Start (session={session_id})",
            category="sacn",
            status=Status.PASS,
            message=f"Sending {preset.universes} universes at {preset.refresh_rate_hz}Hz | session={session_id}",
            duration_ms=(time.monotonic() - start) * 1000,
        ))

        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()
        tick = 0
        interval = 1.0 / preset.refresh_rate_hz

        while time.monotonic() < deadline:
            loop_start = time.monotonic()

            # Generate pattern data for CRC
            pattern_data = _generate_dmx_pattern(pattern, preset.channels_per_universe - HEADER_SIZE, tick)
            payload = state.next_payload(bytes(pattern_data))
            header = payload.encode()

            # First universe: header + pattern
            verification_channels = list(header) + list(pattern_data)
            sender[universe_range[0]].dmx_data = tuple(verification_channels[:512])
            state.total_bytes += 512

            # Additional universes: just pattern
            for u in universe_range[1:]:
                dmx = _generate_dmx_pattern(pattern, preset.channels_per_universe, tick + u)
                sender[u].dmx_data = dmx
                state.total_bytes += 512

            tick += 1

            now = time.monotonic()
            if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                on_snapshot({
                    "elapsed_s": round(state.elapsed_seconds, 1),
                    "frames_sent": state.sequence,
                    "session_id": session_id,
                })
                last_snapshot = now

            sleep_time = interval - (time.monotonic() - loop_start)
            if sleep_time > 0:
                time.sleep(sleep_time)

        sender.stop()
        elapsed = (time.monotonic() - start) * 1000

        results.append(TestResult(
            name=f"sACN Sender Done (session={session_id})",
            category="sacn",
            status=Status.PASS,
            message=f"Sent {state.sequence} verified packets in {elapsed/1000:.0f}s",
            duration_ms=elapsed,
            details=state.summary(),
        ))

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name="sACN Verified Sender",
            category="sacn",
            status=Status.ERROR,
            message=f"Sender error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# Generic UDP Verified Sender (Dante sim, Art-Net, TCNet, Pro DJ Link)
# =========================================================================

def send_udp(
    protocol_name: str,
    target_ip: str,
    target_port: int,
    session_id: int,
    payload_size: int = 256,
    packets_per_second: float = 1000,
    duration_seconds: int = 300,
    multicast: bool = True,
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Send verified UDP packets at a target rate and payload size.

    Works for Dante bandwidth simulation, Art-Net, TCNet, Pro DJ Link,
    or any UDP-based protocol.
    """
    results: List[TestResult] = []

    state = SenderState(
        session_id=session_id,
        protocol=protocol_name,
    )

    start = time.monotonic()
    state.start_time = time.time()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        if multicast:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

        # Payload = verification header (32 bytes) + random fill
        fill_size = max(0, payload_size - HEADER_SIZE)
        fill_data = os.urandom(fill_size)
        fill_crc = zlib.crc32(fill_data) & 0xFFFFFFFF

        results.append(TestResult(
            name=f"{protocol_name} UDP Sender Start (session={session_id})",
            category=protocol_name.lower().replace(" ", ""),
            status=Status.PASS,
            message=f"Sending {payload_size}B @ {packets_per_second:.0f} pkt/s to {target_ip}:{target_port} | session={session_id}",
            duration_ms=(time.monotonic() - start) * 1000,
        ))

        interval = 1.0 / packets_per_second
        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()
        next_send = time.monotonic()

        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                payload = state.next_payload(fill_data)
                packet = payload.encode() + fill_data
                try:
                    sock.sendto(packet, (target_ip, target_port))
                    state.total_bytes += len(packet)
                except Exception:
                    state.errors += 1

                next_send += interval
                if next_send < now - 0.1:
                    next_send = now

                if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                    elapsed = state.elapsed_seconds
                    on_snapshot({
                        "elapsed_s": round(elapsed, 1),
                        "packets_sent": state.sequence,
                        "mbps": round((state.total_bytes * 8) / (elapsed * 1_000_000), 2) if elapsed > 0 else 0,
                        "session_id": session_id,
                    })
                    last_snapshot = now
            else:
                sleep_time = next_send - now
                if sleep_time > 0.0002:
                    time.sleep(sleep_time * 0.5)

        sock.close()
        elapsed = (time.monotonic() - start) * 1000
        actual_mbps = (state.total_bytes * 8) / (elapsed / 1000 * 1_000_000) if elapsed > 0 else 0

        results.append(TestResult(
            name=f"{protocol_name} UDP Sender Done (session={session_id})",
            category=protocol_name.lower().replace(" ", ""),
            status=Status.PASS if state.errors == 0 else Status.WARN,
            message=f"Sent {state.sequence} packets ({actual_mbps:.1f} Mbps) in {elapsed/1000:.0f}s",
            duration_ms=elapsed,
            details=state.summary(),
        ))

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name=f"{protocol_name} UDP Sender",
            category=protocol_name.lower().replace(" ", ""),
            status=Status.ERROR,
            message=f"Sender error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# Helpers
# =========================================================================

def _fps_to_rational(fps: float) -> tuple:
    common = {
        23.976: (24000, 1001), 24.0: (24000, 1000), 25.0: (25000, 1000),
        29.97: (30000, 1001), 30.0: (30000, 1000), 50.0: (50000, 1000),
        59.94: (60000, 1001), 60.0: (60000, 1000),
    }
    return common.get(fps, (int(fps * 1000), 1000))


def _generate_color_bars_bgrx(width: int, height: int) -> bytes:
    colors = [
        (255, 255, 255, 255), (0, 255, 255, 255), (255, 255, 0, 255),
        (0, 255, 0, 255), (255, 0, 255, 255), (0, 0, 255, 255),
        (255, 0, 0, 255), (0, 0, 0, 255),
    ]
    bar_width = width // 8
    row = bytearray()
    for i, color in enumerate(colors):
        w = bar_width if i < 7 else (width - bar_width * 7)
        row.extend(bytes(color) * w)
    return bytes(row) * height


def _generate_dmx_pattern(pattern: str, channels: int, tick: int) -> tuple:
    if pattern == "full":
        return tuple([255] * channels)
    elif pattern == "chase":
        data = [0] * channels
        pos = tick % channels
        for i in range(min(10, channels)):
            idx = (pos + i) % channels
            data[idx] = max(0, 255 - i * 25)
        return tuple(data)
    elif pattern == "ramp":
        phase = tick % 512
        val = phase if phase < 256 else 511 - phase
        return tuple([val] * channels)
    else:
        return tuple([0] * channels)
