"""Base classes for long-form AV protocol testing."""
from __future__ import annotations

import time
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class StreamStats:
    """Rolling statistics for a long-form stream test."""
    protocol: str = ""
    source_name: str = ""
    start_time: float = 0.0
    end_time: float = 0.0

    # Frame / packet counters
    total_frames: int = 0
    dropped_frames: int = 0
    out_of_order: int = 0
    duplicates: int = 0

    # Timing
    frame_intervals_ms: List[float] = field(default_factory=list)
    latency_samples_ms: List[float] = field(default_factory=list)

    # Bandwidth
    total_bytes: int = 0
    bandwidth_samples_mbps: List[float] = field(default_factory=list)

    # Stream properties (set by protocol-specific code)
    properties: Dict[str, Any] = field(default_factory=dict)

    # Periodic snapshots for timeline analysis
    snapshots: List[Dict[str, Any]] = field(default_factory=list)

    # Errors / events
    errors: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or time.monotonic()
        return end - self.start_time if self.start_time else 0.0

    @property
    def drop_rate(self) -> float:
        if self.total_frames == 0:
            return 0.0
        return (self.dropped_frames / self.total_frames) * 100.0

    @property
    def avg_frame_interval_ms(self) -> float:
        if not self.frame_intervals_ms:
            return 0.0
        return sum(self.frame_intervals_ms) / len(self.frame_intervals_ms)

    @property
    def avg_bandwidth_mbps(self) -> float:
        if not self.bandwidth_samples_mbps:
            return 0.0
        return sum(self.bandwidth_samples_mbps) / len(self.bandwidth_samples_mbps)

    @property
    def max_frame_interval_ms(self) -> float:
        return max(self.frame_intervals_ms) if self.frame_intervals_ms else 0.0

    @property
    def avg_latency_ms(self) -> float:
        if not self.latency_samples_ms:
            return 0.0
        return sum(self.latency_samples_ms) / len(self.latency_samples_ms)

    def take_snapshot(self) -> Dict[str, Any]:
        """Capture a point-in-time snapshot of stats."""
        snap = {
            "timestamp": time.time(),
            "elapsed_s": round(self.elapsed_seconds, 1),
            "total_frames": self.total_frames,
            "dropped_frames": self.dropped_frames,
            "drop_rate_pct": round(self.drop_rate, 4),
            "avg_bandwidth_mbps": round(self.avg_bandwidth_mbps, 2),
            "total_bytes": self.total_bytes,
        }
        self.snapshots.append(snap)
        return snap

    def summary(self) -> Dict[str, Any]:
        """Generate a final summary dict."""
        import statistics

        result = {
            "protocol": self.protocol,
            "source": self.source_name,
            "duration_seconds": round(self.elapsed_seconds, 1),
            "total_frames": self.total_frames,
            "dropped_frames": self.dropped_frames,
            "drop_rate_pct": round(self.drop_rate, 4),
            "out_of_order": self.out_of_order,
            "duplicates": self.duplicates,
            "total_bytes": self.total_bytes,
            "avg_bandwidth_mbps": round(self.avg_bandwidth_mbps, 2),
            "properties": self.properties,
            "error_count": len(self.errors),
        }

        if self.frame_intervals_ms:
            sorted_intervals = sorted(self.frame_intervals_ms)
            result["frame_interval_ms"] = {
                "avg": round(statistics.mean(sorted_intervals), 2),
                "min": round(min(sorted_intervals), 2),
                "max": round(max(sorted_intervals), 2),
                "stddev": round(statistics.stdev(sorted_intervals), 2) if len(sorted_intervals) > 1 else 0,
                "p99": round(_percentile(sorted_intervals, 99), 2),
            }

        if self.latency_samples_ms:
            sorted_lat = sorted(self.latency_samples_ms)
            result["latency_ms"] = {
                "avg": round(statistics.mean(sorted_lat), 2),
                "min": round(min(sorted_lat), 2),
                "max": round(max(sorted_lat), 2),
                "p95": round(_percentile(sorted_lat, 95), 2),
                "p99": round(_percentile(sorted_lat, 99), 2),
            }

        return result


@dataclass
class LongFormTestConfig:
    """Configuration for a long-form stream test."""
    duration_seconds: int = 300  # default 5 minutes
    snapshot_interval_seconds: int = 10  # how often to take snapshots
    max_acceptable_drop_rate: float = 0.1  # percentage
    max_acceptable_latency_ms: float = 50.0
    on_snapshot: Optional[Callable[[Dict[str, Any]], None]] = None  # callback


class LongFormMonitor:
    """
    Manages the lifecycle of a long-form stream test.

    Subclasses implement protocol-specific receive loops.
    This base handles timing, snapshot scheduling, and stop signaling.
    """

    def __init__(self, config: LongFormTestConfig):
        self.config = config
        self.stats = StreamStats()
        self._stop_event = threading.Event()
        self._snapshot_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the monitoring session."""
        self.stats.start_time = time.monotonic()
        self._stop_event.clear()

        # Start periodic snapshot thread
        self._snapshot_thread = threading.Thread(
            target=self._snapshot_loop, daemon=True
        )
        self._snapshot_thread.start()

    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._stop_event.set()
        self.stats.end_time = time.monotonic()
        if self._snapshot_thread:
            self._snapshot_thread.join(timeout=5)

    @property
    def should_stop(self) -> bool:
        """Check if the test duration has elapsed or stop was requested."""
        if self._stop_event.is_set():
            return True
        if self.stats.elapsed_seconds >= self.config.duration_seconds:
            return True
        return False

    def _snapshot_loop(self) -> None:
        """Periodically take snapshots."""
        while not self._stop_event.wait(timeout=self.config.snapshot_interval_seconds):
            snap = self.stats.take_snapshot()
            if self.config.on_snapshot:
                try:
                    self.config.on_snapshot(snap)
                except Exception:
                    pass
            if self.stats.elapsed_seconds >= self.config.duration_seconds:
                break


def _percentile(sorted_data: List[float], pct: float) -> float:
    """Calculate percentile from sorted data."""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    d = k - f
    return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])
