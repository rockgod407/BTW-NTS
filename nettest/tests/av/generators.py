"""
AV signal generators for stress testing.

These generators emit real protocol traffic onto the network at the
bandwidth and packet rates defined by presets. Use them to validate
that your network infrastructure can handle the load before plugging
in real gear.

WARNING: Generators produce real network traffic. Only run on networks
you control. Running high-bandwidth generators on production networks
can cause disruption.
"""
from __future__ import annotations

import math
import os
import struct
import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.base import LongFormTestConfig, StreamStats
from nettest.tests.av.presets import (
    NDIPreset,
    NDI_PRESETS,
    NDI_STRESS_PROFILES,
    SACNPreset,
    SACN_PRESETS,
    DantePreset,
    DANTE_PRESETS,
)


# =========================================================================
# NDI Test Pattern Generator
# =========================================================================

def generate_ndi_stream(
    preset: NDIPreset,
    source_name: str = "NetTest Generator",
    duration_seconds: int = 300,
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Generate an NDI test pattern stream at the specified preset's
    resolution and frame rate.

    Requires ndi-python and the NDI runtime.
    """
    results: List[TestResult] = []

    try:
        import NDIlib as ndi
    except ImportError:
        results.append(TestResult(
            name=f"NDI Generator ({preset.name})",
            category="ndi",
            status=Status.SKIP,
            message="ndi-python not installed",
            duration_ms=0,
        ))
        return results

    if not ndi.initialize():
        results.append(TestResult(
            name=f"NDI Generator ({preset.name})",
            category="ndi",
            status=Status.ERROR,
            message="NDI runtime failed to initialize",
            duration_ms=0,
        ))
        return results

    start = time.monotonic()
    frame_count = 0
    errors = 0
    total_bytes = 0

    try:
        send_create = ndi.SendCreate()
        send_create.ndi_name = source_name
        sender = ndi.send_create(send_create)

        if not sender:
            ndi.destroy()
            results.append(TestResult(
                name=f"NDI Generator ({preset.name})",
                category="ndi",
                status=Status.ERROR,
                message="Failed to create NDI sender",
                duration_ms=0,
            ))
            return results

        # Create video frame
        video_frame = ndi.VideoFrameV2()
        video_frame.xres = preset.width
        video_frame.yres = preset.height
        video_frame.FourCC = ndi.FOURCC_VIDEO_TYPE_BGRX
        video_frame.frame_rate_N = _fps_to_rational(preset.fps)[0]
        video_frame.frame_rate_D = _fps_to_rational(preset.fps)[1]

        # Generate test pattern (color bars)
        frame_data = _generate_color_bars(preset.width, preset.height)
        video_frame.data = frame_data
        frame_bytes = len(frame_data)

        # Create audio frame
        audio_frame = ndi.AudioFrameV2()
        audio_frame.sample_rate = preset.audio_sample_rate
        audio_frame.no_channels = preset.audio_channels
        audio_frame.no_samples = 1600  # ~33ms at 48kHz
        audio_data = _generate_tone(
            preset.audio_sample_rate,
            audio_frame.no_samples,
            preset.audio_channels,
        )
        audio_frame.data = audio_data

        results.append(TestResult(
            name=f"NDI Generator Start ({preset.name})",
            category="ndi",
            status=Status.PASS,
            message=(
                f"Sending {preset.width}x{preset.height}@{preset.fps}fps "
                f"as '{source_name}' (~{preset.total_bandwidth_mbps:.0f} Mbps)"
            ),
            duration_ms=(time.monotonic() - start) * 1000,
            details=preset.summary(),
        ))

        # Send loop
        interval = 1.0 / preset.fps
        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()
        next_frame_time = time.monotonic()

        while time.monotonic() < deadline:
            now = time.monotonic()

            if now >= next_frame_time:
                try:
                    ndi.send_send_video_v2(sender, video_frame)
                    ndi.send_send_audio_v2(sender, audio_frame)
                    frame_count += 1
                    total_bytes += frame_bytes
                    next_frame_time += interval
                except Exception:
                    errors += 1

                # Periodic snapshot
                if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                    elapsed = now - start
                    on_snapshot({
                        "elapsed_s": round(elapsed, 1),
                        "frames_sent": frame_count,
                        "errors": errors,
                        "avg_fps": round(frame_count / elapsed, 1) if elapsed > 0 else 0,
                        "bandwidth_mbps": round((total_bytes * 8) / (elapsed * 1_000_000), 1) if elapsed > 0 else 0,
                    })
                    last_snapshot = now
            else:
                # Sleep until next frame
                sleep_time = next_frame_time - now
                if sleep_time > 0.0005:
                    time.sleep(sleep_time * 0.8)

        ndi.send_destroy(sender)
        ndi.destroy()

        elapsed = (time.monotonic() - start) * 1000
        actual_fps = frame_count / (elapsed / 1000) if elapsed > 0 else 0

        results.append(TestResult(
            name=f"NDI Generator Result ({preset.name})",
            category="ndi",
            status=Status.PASS if errors == 0 else Status.WARN,
            message=(
                f"Sent {frame_count} frames in {elapsed/1000:.0f}s "
                f"({actual_fps:.1f} fps, {errors} errors)"
            ),
            duration_ms=elapsed,
            details={
                "frames_sent": frame_count,
                "duration_seconds": round(elapsed / 1000, 1),
                "actual_fps": round(actual_fps, 2),
                "target_fps": preset.fps,
                "errors": errors,
            },
        ))

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        try:
            ndi.destroy()
        except Exception:
            pass
        results.append(TestResult(
            name=f"NDI Generator ({preset.name})",
            category="ndi",
            status=Status.ERROR,
            message=f"Generator error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# sACN Universe Generator
# =========================================================================

def generate_sacn_stream(
    preset: SACNPreset,
    duration_seconds: int = 300,
    pattern: str = "chase",
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Generate sACN traffic at the specified preset's universe count
    and refresh rate.

    Patterns:
      - "chase": Running light chase across channels
      - "full": All channels at 255 (max bandwidth)
      - "random": Random DMX values (realistic-ish)
      - "ramp": Gradual ramp up/down across all channels
    """
    results: List[TestResult] = []

    try:
        import sacn
    except ImportError:
        results.append(TestResult(
            name=f"sACN Generator ({preset.name})",
            category="sacn",
            status=Status.SKIP,
            message="sacn not installed",
            duration_ms=0,
        ))
        return results

    start = time.monotonic()
    packets_sent = 0
    errors = 0

    try:
        sender = sacn.sACNsender(fps=preset.refresh_rate_hz)
        sender.start()

        # Activate all universes
        universe_range = list(range(1, preset.universes + 1))
        for u in universe_range:
            sender.activate_output(u)
            sender[u].multicast = True
            sender[u].priority = preset.priority

        results.append(TestResult(
            name=f"sACN Generator Start ({preset.name})",
            category="sacn",
            status=Status.PASS,
            message=(
                f"Sending {preset.universes} universe(s) at {preset.refresh_rate_hz}Hz "
                f"(~{preset.bandwidth_mbps:.1f} Mbps, {preset.packets_per_second:.0f} pkt/s)"
            ),
            duration_ms=(time.monotonic() - start) * 1000,
            details=preset.summary(),
        ))

        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()
        tick = 0

        interval = 1.0 / preset.refresh_rate_hz

        while time.monotonic() < deadline:
            loop_start = time.monotonic()

            for u in universe_range:
                try:
                    dmx_data = _generate_dmx_pattern(
                        pattern, preset.channels_per_universe, tick
                    )
                    sender[u].dmx_data = dmx_data
                    packets_sent += 1
                except Exception:
                    errors += 1

            tick += 1

            # Snapshot
            now = time.monotonic()
            if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                elapsed = now - start
                on_snapshot({
                    "elapsed_s": round(elapsed, 1),
                    "packets_sent": packets_sent,
                    "errors": errors,
                    "pps": round(packets_sent / elapsed, 0) if elapsed > 0 else 0,
                })
                last_snapshot = now

            # Pace to refresh rate
            sleep_time = interval - (time.monotonic() - loop_start)
            if sleep_time > 0:
                time.sleep(sleep_time)

        sender.stop()

        elapsed = (time.monotonic() - start) * 1000
        actual_pps = packets_sent / (elapsed / 1000) if elapsed > 0 else 0

        results.append(TestResult(
            name=f"sACN Generator Result ({preset.name})",
            category="sacn",
            status=Status.PASS if errors == 0 else Status.WARN,
            message=(
                f"Sent {packets_sent} packets in {elapsed/1000:.0f}s "
                f"({actual_pps:.0f} pkt/s, {errors} errors)"
            ),
            duration_ms=elapsed,
            details={
                "packets_sent": packets_sent,
                "duration_seconds": round(elapsed / 1000, 1),
                "actual_pps": round(actual_pps, 0),
                "target_pps": preset.packets_per_second,
                "errors": errors,
            },
        ))

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name=f"sACN Generator ({preset.name})",
            category="sacn",
            status=Status.ERROR,
            message=f"Generator error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# Dante Bandwidth Simulator (UDP stress at equivalent bandwidth)
# =========================================================================

def generate_dante_bandwidth_test(
    preset: DantePreset,
    target_ip: str = "239.255.0.1",  # Multicast group
    duration_seconds: int = 300,
    on_snapshot: Optional[Callable] = None,
    snapshot_interval: int = 10,
) -> List[TestResult]:
    """
    Simulate Dante audio bandwidth using UDP traffic.

    Since Dante is proprietary, we can't generate real Dante audio.
    Instead, this generates UDP multicast traffic at the equivalent
    bandwidth and packet rate to validate the network can handle
    the load.
    """
    results: List[TestResult] = []
    start = time.monotonic()

    target_mbps = preset.per_network_bandwidth_mbps
    target_pps = preset.packets_per_second

    # Calculate packet size to hit target bandwidth at target pps
    if target_pps > 0:
        target_bytes_per_sec = (target_mbps * 1_000_000) / 8
        packet_payload_size = int(target_bytes_per_sec / target_pps)
        packet_payload_size = max(64, min(packet_payload_size, 1400))  # Clamp to reasonable MTU
    else:
        packet_payload_size = 256
        target_pps = 1000

    results.append(TestResult(
        name=f"Dante BW Simulator Start ({preset.name})",
        category="dante",
        status=Status.PASS,
        message=(
            f"Simulating {preset.channel_count}ch @ {preset.sample_rate/1000:.0f}kHz "
            f"({target_mbps:.1f} Mbps, {target_pps:.0f} pkt/s, {packet_payload_size}B payload)"
        ),
        duration_ms=(time.monotonic() - start) * 1000,
        details=preset.summary(),
    ))

    packets_sent = 0
    total_bytes = 0
    errors = 0

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

        payload = os.urandom(packet_payload_size)
        interval = 1.0 / target_pps
        deadline = time.monotonic() + duration_seconds
        last_snapshot = time.monotonic()
        next_send = time.monotonic()

        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                try:
                    sock.sendto(payload, (target_ip, 4321))
                    packets_sent += 1
                    total_bytes += packet_payload_size
                    next_send += interval
                except Exception:
                    errors += 1
                    next_send += interval

                # Don't let next_send fall too far behind
                if next_send < now - 0.1:
                    next_send = now

                if on_snapshot and (now - last_snapshot) >= snapshot_interval:
                    elapsed = now - start
                    actual_mbps = (total_bytes * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0
                    on_snapshot({
                        "elapsed_s": round(elapsed, 1),
                        "packets_sent": packets_sent,
                        "actual_mbps": round(actual_mbps, 2),
                        "target_mbps": round(target_mbps, 2),
                        "errors": errors,
                    })
                    last_snapshot = now
            else:
                sleep_time = next_send - now
                if sleep_time > 0.0002:
                    time.sleep(sleep_time * 0.5)

        sock.close()

        elapsed_s = time.monotonic() - start
        actual_mbps = (total_bytes * 8) / (elapsed_s * 1_000_000) if elapsed_s > 0 else 0
        actual_pps = packets_sent / elapsed_s if elapsed_s > 0 else 0

        # Evaluate how close we got to target
        bw_accuracy = (actual_mbps / target_mbps * 100) if target_mbps > 0 else 0
        if bw_accuracy > 90:
            bw_status = Status.PASS
        elif bw_accuracy > 75:
            bw_status = Status.WARN
        else:
            bw_status = Status.FAIL

        results.append(TestResult(
            name=f"Dante BW Simulator Result ({preset.name})",
            category="dante",
            status=bw_status,
            message=(
                f"Achieved {actual_mbps:.1f}/{target_mbps:.1f} Mbps "
                f"({bw_accuracy:.0f}%), {actual_pps:.0f} pkt/s, {errors} errors"
            ),
            duration_ms=elapsed_s * 1000,
            details={
                "target_mbps": round(target_mbps, 2),
                "actual_mbps": round(actual_mbps, 2),
                "accuracy_pct": round(bw_accuracy, 1),
                "packets_sent": packets_sent,
                "actual_pps": round(actual_pps, 0),
                "errors": errors,
                "duration_seconds": round(elapsed_s, 1),
            },
        ))

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        results.append(TestResult(
            name=f"Dante BW Simulator ({preset.name})",
            category="dante",
            status=Status.ERROR,
            message=f"Simulator error: {e}",
            duration_ms=elapsed,
        ))

    return results


# =========================================================================
# Multi-stream stress test
# =========================================================================

def run_ndi_stress_test(
    profile_name: str,
    duration_seconds: int = 300,
    on_snapshot: Optional[Callable] = None,
) -> List[TestResult]:
    """
    Run a multi-stream NDI stress test using a named stress profile.

    Each stream in the profile runs in its own thread, all generating
    simultaneously to stress the network at the combined bandwidth.
    """
    results: List[TestResult] = []
    profile = NDI_STRESS_PROFILES.get(profile_name)

    if not profile:
        results.append(TestResult(
            name=f"NDI Stress Test '{profile_name}'",
            category="ndi",
            status=Status.ERROR,
            message=f"Unknown stress profile. Available: {', '.join(NDI_STRESS_PROFILES.keys())}",
            duration_ms=0,
        ))
        return results

    results.append(TestResult(
        name=f"NDI Stress: {profile.name}",
        category="ndi",
        status=Status.PASS,
        message=(
            f"{len(profile.streams)} streams, "
            f"~{profile.total_bandwidth_mbps:.0f} Mbps total — {profile.description}"
        ),
        duration_ms=0,
        details=profile.summary(),
    ))

    # Run all streams in parallel threads
    all_stream_results: List[List[TestResult]] = []

    def _run_stream(idx: int, preset_name: str):
        preset = NDI_PRESETS.get(preset_name)
        if not preset:
            return [TestResult(
                name=f"Stream {idx}",
                category="ndi",
                status=Status.ERROR,
                message=f"Unknown preset: {preset_name}",
                duration_ms=0,
            )]
        return generate_ndi_stream(
            preset=preset,
            source_name=f"NetTest Stress {idx} ({preset.name})",
            duration_seconds=duration_seconds,
        )

    with ThreadPoolExecutor(max_workers=len(profile.streams)) as executor:
        futures = [
            executor.submit(_run_stream, i, name)
            for i, name in enumerate(profile.streams, 1)
        ]
        for f in futures:
            all_stream_results.append(f.result())

    # Flatten results
    for stream_results in all_stream_results:
        results.extend(stream_results)

    return results


# =========================================================================
# Helper functions
# =========================================================================

def _fps_to_rational(fps: float) -> tuple:
    """Convert FPS to numerator/denominator for NDI."""
    common = {
        23.976: (24000, 1001),
        24.0: (24000, 1000),
        25.0: (25000, 1000),
        29.97: (30000, 1001),
        30.0: (30000, 1000),
        50.0: (50000, 1000),
        59.94: (60000, 1001),
        60.0: (60000, 1000),
    }
    return common.get(fps, (int(fps * 1000), 1000))


def _generate_color_bars(width: int, height: int) -> bytes:
    """Generate SMPTE-style color bars as BGRX bytes."""
    # 8 color bars: White, Yellow, Cyan, Green, Magenta, Red, Blue, Black
    colors_bgrx = [
        (255, 255, 255, 255),  # White
        (0, 255, 255, 255),    # Yellow
        (255, 255, 0, 255),    # Cyan
        (0, 255, 0, 255),      # Green
        (255, 0, 255, 255),    # Magenta
        (0, 0, 255, 255),      # Red
        (255, 0, 0, 255),      # Blue
        (0, 0, 0, 255),        # Black
    ]

    bar_width = width // 8
    row = bytearray()
    for i, color in enumerate(colors_bgrx):
        w = bar_width if i < 7 else (width - bar_width * 7)
        row.extend(bytes(color) * w)

    return bytes(row) * height


def _generate_tone(sample_rate: int, num_samples: int, channels: int) -> bytes:
    """Generate a 1kHz sine tone as float32 interleaved samples."""
    import array

    freq = 1000.0
    samples = array.array("f")
    for i in range(num_samples):
        val = 0.3 * math.sin(2 * math.pi * freq * i / sample_rate)
        for _ in range(channels):
            samples.append(val)

    return samples.tobytes()


def _generate_dmx_pattern(pattern: str, channels: int, tick: int) -> tuple:
    """Generate a DMX data pattern (tuple of 0-255 values)."""
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
        if phase < 256:
            val = phase
        else:
            val = 511 - phase
        return tuple([val] * channels)

    elif pattern == "random":
        import random
        return tuple(random.randint(0, 255) for _ in range(channels))

    else:
        return tuple([0] * channels)
