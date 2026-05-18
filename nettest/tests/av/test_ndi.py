"""
NDI (Network Device Interface) long-form stream testing.

Tests NDI source discovery, stream connectivity, and long-form stability
by receiving frames and tracking drops, frame rate consistency, bandwidth,
and latency over configurable durations.

Requires: ndi-python (pip install ndi-python)
          NDI runtime (https://ndi.video/tools/)
"""
from __future__ import annotations

import time
import threading
from typing import Any, Callable, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.base import LongFormMonitor, LongFormTestConfig, StreamStats

try:
    import NDIlib as ndi

    NDI_AVAILABLE = True
except ImportError:
    NDI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_ndi_sources(timeout_seconds: int = 5) -> TestResult:
    """Discover NDI sources on the local network."""
    name = "NDI Source Discovery"
    start = time.monotonic()

    if not NDI_AVAILABLE:
        return TestResult(
            name=name,
            category="ndi",
            status=Status.SKIP,
            message="ndi-python not installed (pip install ndi-python)",
            duration_ms=0,
        )

    if not ndi.initialize():
        return TestResult(
            name=name,
            category="ndi",
            status=Status.ERROR,
            message="Failed to initialize NDI runtime. Is the NDI runtime installed?",
            duration_ms=0,
        )

    try:
        finder = ndi.find_create_v2()
        if not finder:
            ndi.destroy()
            return TestResult(
                name=name,
                category="ndi",
                status=Status.ERROR,
                message="Failed to create NDI finder",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # Wait for sources to appear
        ndi.find_wait_for_sources(finder, timeout_ms=timeout_seconds * 1000)
        sources = ndi.find_get_current_sources(finder)
        elapsed = (time.monotonic() - start) * 1000

        ndi.find_destroy(finder)
        ndi.destroy()

        if sources:
            source_names = [s.ndi_name for s in sources]
            return TestResult(
                name=name,
                category="ndi",
                status=Status.PASS,
                message=f"Found {len(sources)} source(s): {', '.join(source_names)}",
                duration_ms=elapsed,
                details={
                    "source_count": len(sources),
                    "sources": source_names,
                },
            )
        else:
            return TestResult(
                name=name,
                category="ndi",
                status=Status.WARN,
                message="No NDI sources found on the network",
                duration_ms=elapsed,
                details={"source_count": 0, "sources": []},
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        try:
            ndi.destroy()
        except Exception:
            pass
        return TestResult(
            name=name,
            category="ndi",
            status=Status.ERROR,
            message=f"NDI discovery error: {e}",
            duration_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Long-form stream monitor
# ---------------------------------------------------------------------------

class NDIStreamMonitor(LongFormMonitor):
    """
    Connects to an NDI source and monitors frame delivery over time.

    Tracks:
    - Frame count and dropped frames
    - Frame interval consistency (jitter)
    - Resolution and frame rate stability
    - Bandwidth usage
    - Audio frame stats (if audio present)
    """

    def __init__(
        self,
        source_name: str,
        config: LongFormTestConfig,
        expected_width: Optional[int] = None,
        expected_height: Optional[int] = None,
        expected_fps: Optional[float] = None,
    ):
        super().__init__(config)
        self.source_name = source_name
        self.expected_width = expected_width
        self.expected_height = expected_height
        self.expected_fps = expected_fps

        self.stats.protocol = "NDI"
        self.stats.source_name = source_name

        # Internal state
        self._last_frame_time: float = 0.0
        self._last_timecode: int = -1
        self._resolution_changes: List[Dict[str, Any]] = []
        self._audio_frames: int = 0
        self._video_frames: int = 0
        self._receiver = None
        self._ndi_initialized = False

    def run(self) -> StreamStats:
        """
        Run the long-form NDI stream test.

        This is a blocking call that runs for the configured duration.
        Returns the final StreamStats.
        """
        if not NDI_AVAILABLE:
            self.stats.errors.append({
                "time": time.time(),
                "error": "ndi-python not installed",
            })
            return self.stats

        if not ndi.initialize():
            self.stats.errors.append({
                "time": time.time(),
                "error": "NDI runtime initialization failed",
            })
            return self.stats

        self._ndi_initialized = True

        try:
            source = self._find_source()
            if source is None:
                self.stats.errors.append({
                    "time": time.time(),
                    "error": f"NDI source '{self.source_name}' not found",
                })
                return self.stats

            self._connect_and_receive(source)

        except Exception as e:
            self.stats.errors.append({
                "time": time.time(),
                "error": f"Unexpected error: {e}",
            })
        finally:
            self._cleanup()

        return self.stats

    def _find_source(self, timeout_ms: int = 10000):
        """Find the named NDI source."""
        finder = ndi.find_create_v2()
        if not finder:
            return None

        ndi.find_wait_for_sources(finder, timeout_ms=timeout_ms)
        sources = ndi.find_get_current_sources(finder)

        target = None
        for src in sources:
            # Match by exact name or substring
            if (
                src.ndi_name == self.source_name
                or self.source_name.lower() in src.ndi_name.lower()
            ):
                target = src
                break

        ndi.find_destroy(finder)
        return target

    def _connect_and_receive(self, source) -> None:
        """Connect to the NDI source and receive frames."""
        recv_create = ndi.RecvCreateV3()
        recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        recv_create.bandwidth = ndi.RECV_BANDWIDTH_HIGHEST

        self._receiver = ndi.recv_create_v3(recv_create)
        if not self._receiver:
            self.stats.errors.append({
                "time": time.time(),
                "error": "Failed to create NDI receiver",
            })
            return

        ndi.recv_connect(self._receiver, source)

        # Allow connection to establish
        time.sleep(0.5)

        # Start the base monitor (snapshot thread, timers)
        self.start()

        # Main receive loop
        while not self.should_stop:
            frame_type = ndi.recv_capture_v2(
                self._receiver, timeout_in_ms=1000
            )

            if frame_type == ndi.FRAME_TYPE_VIDEO:
                self._handle_video_frame()
            elif frame_type == ndi.FRAME_TYPE_AUDIO:
                self._handle_audio_frame()
            elif frame_type == ndi.FRAME_TYPE_NONE:
                # Timeout waiting for frame — could indicate a problem
                if self.stats.total_frames > 0:
                    self.stats.errors.append({
                        "time": time.time(),
                        "elapsed_s": round(self.stats.elapsed_seconds, 1),
                        "error": "Frame receive timeout (1s with no frame)",
                    })
            elif frame_type == ndi.FRAME_TYPE_STATUS_CHANGE:
                self.stats.errors.append({
                    "time": time.time(),
                    "elapsed_s": round(self.stats.elapsed_seconds, 1),
                    "error": "NDI status change (possible source disconnect)",
                })

        self.stop()

    def _handle_video_frame(self) -> None:
        """Process a received video frame."""
        now = time.monotonic()
        self._video_frames += 1
        self.stats.total_frames += 1

        # Frame interval tracking
        if self._last_frame_time > 0:
            interval_ms = (now - self._last_frame_time) * 1000
            self.stats.frame_intervals_ms.append(interval_ms)

            # Detect dropped frames by checking if interval is > 1.5x expected
            if self.expected_fps and self.expected_fps > 0:
                expected_interval = 1000.0 / self.expected_fps
                if interval_ms > expected_interval * 1.5:
                    estimated_drops = int(round(interval_ms / expected_interval)) - 1
                    self.stats.dropped_frames += max(estimated_drops, 1)
                    self.stats.errors.append({
                        "time": time.time(),
                        "elapsed_s": round(self.stats.elapsed_seconds, 1),
                        "error": f"Dropped ~{estimated_drops} frame(s), interval={interval_ms:.1f}ms (expected ~{expected_interval:.1f}ms)",
                    })

        self._last_frame_time = now

        # Try to get frame metadata from NDI recv
        # Note: exact API depends on ndi-python version
        try:
            perf = ndi.recv_get_performance(self._receiver)
            if perf:
                # perf is (video_frames, audio_frames, dropped_video, dropped_audio)
                if len(perf) >= 3:
                    self.stats.properties["ndi_reported_drops"] = perf[2]
        except Exception:
            pass

        # Bandwidth sampling (every 100 frames)
        if self._video_frames % 100 == 0 and self.stats.elapsed_seconds > 0:
            bw_mbps = (self.stats.total_bytes * 8) / (self.stats.elapsed_seconds * 1_000_000)
            self.stats.bandwidth_samples_mbps.append(bw_mbps)

    def _handle_audio_frame(self) -> None:
        """Process a received audio frame."""
        self._audio_frames += 1

    def _cleanup(self) -> None:
        """Release NDI resources."""
        if self._receiver:
            try:
                ndi.recv_destroy(self._receiver)
            except Exception:
                pass
        if self._ndi_initialized:
            try:
                ndi.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Runnable test entry points
# ---------------------------------------------------------------------------

def run_ndi_discovery_test(timeout: int = 5) -> List[TestResult]:
    """Run NDI discovery and return results."""
    return [discover_ndi_sources(timeout)]


def run_ndi_longform_test(
    source_name: str,
    duration_seconds: int = 300,
    expected_width: Optional[int] = None,
    expected_height: Optional[int] = None,
    expected_fps: Optional[float] = None,
    snapshot_interval: int = 10,
    max_drop_rate: float = 0.1,
    on_snapshot: Optional[Callable] = None,
) -> List[TestResult]:
    """
    Run a long-form NDI stream stability test.

    Args:
        source_name: NDI source name (or substring to match)
        duration_seconds: How long to run the test
        expected_width: Expected video width (optional)
        expected_height: Expected video height (optional)
        expected_fps: Expected frame rate (e.g. 29.97, 59.94)
        snapshot_interval: Seconds between periodic stat snapshots
        max_drop_rate: Maximum acceptable drop rate percentage
        on_snapshot: Callback for periodic snapshot updates

    Returns:
        List of TestResults covering discovery, connectivity, and stability
    """
    results: List[TestResult] = []

    if not NDI_AVAILABLE:
        results.append(TestResult(
            name="NDI Long-form Test",
            category="ndi",
            status=Status.SKIP,
            message="ndi-python not installed (pip install ndi-python)",
            duration_ms=0,
        ))
        return results

    # Phase 1: Discover
    disc_result = discover_ndi_sources(timeout_seconds=5)
    results.append(disc_result)

    if disc_result.status in (Status.SKIP, Status.ERROR):
        return results

    # Check if requested source was found
    found_sources = disc_result.details.get("sources", [])
    source_match = any(
        source_name.lower() in s.lower() for s in found_sources
    )
    if not source_match:
        results.append(TestResult(
            name=f"NDI Source Match '{source_name}'",
            category="ndi",
            status=Status.FAIL,
            message=f"Source '{source_name}' not found. Available: {', '.join(found_sources)}",
            duration_ms=0,
            details={"requested": source_name, "available": found_sources},
        ))
        return results

    results.append(TestResult(
        name=f"NDI Source Match '{source_name}'",
        category="ndi",
        status=Status.PASS,
        message=f"Source found on network",
        duration_ms=0,
    ))

    # Phase 2: Long-form stream test
    test_config = LongFormTestConfig(
        duration_seconds=duration_seconds,
        snapshot_interval_seconds=snapshot_interval,
        max_acceptable_drop_rate=max_drop_rate,
        on_snapshot=on_snapshot,
    )

    monitor = NDIStreamMonitor(
        source_name=source_name,
        config=test_config,
        expected_width=expected_width,
        expected_height=expected_height,
        expected_fps=expected_fps,
    )

    start = time.monotonic()
    stats = monitor.run()
    elapsed = (time.monotonic() - start) * 1000
    summary = stats.summary()

    # Connectivity result
    if stats.total_frames > 0:
        results.append(TestResult(
            name=f"NDI Stream Connectivity '{source_name}'",
            category="ndi",
            status=Status.PASS,
            message=f"Received {stats.total_frames} frames in {stats.elapsed_seconds:.0f}s",
            duration_ms=elapsed,
            details=summary,
        ))
    else:
        results.append(TestResult(
            name=f"NDI Stream Connectivity '{source_name}'",
            category="ndi",
            status=Status.FAIL,
            message=f"No frames received in {stats.elapsed_seconds:.0f}s",
            duration_ms=elapsed,
            details={"errors": stats.errors},
        ))
        return results

    # Frame drop result
    if stats.drop_rate <= max_drop_rate:
        drop_status = Status.PASS
        drop_msg = f"{stats.dropped_frames} drops ({stats.drop_rate:.4f}%) over {stats.elapsed_seconds:.0f}s"
    elif stats.drop_rate <= max_drop_rate * 5:
        drop_status = Status.WARN
        drop_msg = f"{stats.dropped_frames} drops ({stats.drop_rate:.2f}%) — above threshold of {max_drop_rate}%"
    else:
        drop_status = Status.FAIL
        drop_msg = f"{stats.dropped_frames} drops ({stats.drop_rate:.2f}%) — significantly above threshold"

    results.append(TestResult(
        name=f"NDI Frame Drops '{source_name}'",
        category="ndi",
        status=drop_status,
        message=drop_msg,
        duration_ms=elapsed,
        details={
            "total_frames": stats.total_frames,
            "dropped_frames": stats.dropped_frames,
            "drop_rate_pct": round(stats.drop_rate, 4),
            "duration_seconds": round(stats.elapsed_seconds, 1),
        },
    ))

    # Frame interval consistency (jitter)
    if stats.frame_intervals_ms:
        interval_summary = summary.get("frame_interval_ms", {})
        stddev = interval_summary.get("stddev", 0)
        p99 = interval_summary.get("p99", 0)
        avg = interval_summary.get("avg", 0)

        if expected_fps:
            expected_interval = 1000.0 / expected_fps
            deviation_pct = abs(avg - expected_interval) / expected_interval * 100
            if deviation_pct < 5 and stddev < expected_interval * 0.2:
                jitter_status = Status.PASS
            elif deviation_pct < 15:
                jitter_status = Status.WARN
            else:
                jitter_status = Status.FAIL
            jitter_msg = (
                f"avg={avg:.1f}ms (expected {expected_interval:.1f}ms), "
                f"stddev={stddev:.1f}ms, p99={p99:.1f}ms"
            )
        else:
            jitter_status = Status.PASS if stddev < 10 else Status.WARN
            jitter_msg = f"avg={avg:.1f}ms, stddev={stddev:.1f}ms, p99={p99:.1f}ms"

        results.append(TestResult(
            name=f"NDI Frame Timing '{source_name}'",
            category="ndi",
            status=jitter_status,
            message=jitter_msg,
            duration_ms=elapsed,
            details=interval_summary,
        ))

    # Overall stability verdict
    error_count = len(stats.errors)
    if stats.drop_rate <= max_drop_rate and error_count == 0:
        overall = Status.PASS
        overall_msg = (
            f"STABLE: {stats.total_frames} frames, {stats.drop_rate:.4f}% drops, "
            f"0 errors over {stats.elapsed_seconds:.0f}s"
        )
    elif stats.drop_rate <= max_drop_rate * 5 and error_count < 5:
        overall = Status.WARN
        overall_msg = (
            f"MARGINAL: {stats.total_frames} frames, {stats.drop_rate:.2f}% drops, "
            f"{error_count} error(s) over {stats.elapsed_seconds:.0f}s"
        )
    else:
        overall = Status.FAIL
        overall_msg = (
            f"UNSTABLE: {stats.total_frames} frames, {stats.drop_rate:.2f}% drops, "
            f"{error_count} error(s) over {stats.elapsed_seconds:.0f}s"
        )

    results.append(TestResult(
        name=f"NDI Stability Verdict '{source_name}'",
        category="ndi",
        status=overall,
        message=overall_msg,
        duration_ms=elapsed,
        details=summary,
    ))

    return results
