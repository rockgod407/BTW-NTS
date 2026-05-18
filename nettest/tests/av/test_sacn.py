"""
sACN (E1.31 / Streaming ACN) long-form stream testing.

Tests sACN universe reception, sequence number tracking, and long-form
stability for DMX-over-Ethernet lighting networks.

sACN uses multicast UDP on port 5568. Each universe is sent to a
specific multicast group (239.255.X.X).

Requires: sacn (pip install sacn)
"""
from __future__ import annotations

import time
import threading
from typing import Any, Callable, Dict, List, Optional

from nettest.core.result import Status, TestResult
from nettest.tests.av.base import LongFormMonitor, LongFormTestConfig, StreamStats

try:
    import sacn

    SACN_AVAILABLE = True
except ImportError:
    SACN_AVAILABLE = False


def discover_sacn_sources(
    universes: List[int],
    listen_seconds: int = 5,
) -> TestResult:
    """Listen for sACN data on the specified universes."""
    name = "sACN Universe Discovery"
    start = time.monotonic()

    if not SACN_AVAILABLE:
        return TestResult(
            name=name,
            category="sacn",
            status=Status.SKIP,
            message="sacn not installed (pip install sacn)",
            duration_ms=0,
        )

    found: Dict[int, Dict[str, Any]] = {}
    lock = threading.Lock()

    def _on_data(packet):
        with lock:
            universe = packet.universe
            if universe not in found:
                found[universe] = {
                    "universe": universe,
                    "source_name": packet.sourceName,
                    "priority": packet.priority,
                    "first_seen": time.time(),
                    "packet_count": 0,
                }
            found[universe]["packet_count"] += 1

    try:
        receiver = sacn.sACNreceiver()

        for u in universes:
            receiver.register_listener("universe", _on_data, universe=u)
            receiver.join_multicast(u)

        receiver.start()
        time.sleep(listen_seconds)
        receiver.stop()

        elapsed = (time.monotonic() - start) * 1000
        active = [info for info in found.values() if info["packet_count"] > 0]

        if active:
            msgs = [f"U{a['universe']}({a['source_name']}, {a['packet_count']} pkts)" for a in active]
            return TestResult(
                name=name,
                category="sacn",
                status=Status.PASS,
                message=f"Active on {len(active)} universe(s): {', '.join(msgs)}",
                duration_ms=elapsed,
                details={"active_universes": active, "requested": universes},
            )
        else:
            return TestResult(
                name=name,
                category="sacn",
                status=Status.WARN,
                message=f"No sACN data received on universes {universes}",
                duration_ms=elapsed,
                details={"requested": universes},
            )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="sacn",
            status=Status.ERROR,
            message=f"sACN error: {e}",
            duration_ms=elapsed,
        )


class SACNStreamMonitor(LongFormMonitor):
    """
    Monitor sACN universes for long-form stability.

    Tracks:
    - Packet count per universe
    - Sequence number gaps (dropped packets)
    - Packet interval consistency
    - Source priority changes
    - DMX data changes (optionally)
    """

    def __init__(
        self,
        universes: List[int],
        config: LongFormTestConfig,
    ):
        super().__init__(config)
        self.universes = universes
        self.stats.protocol = "sACN"
        self.stats.source_name = f"Universes {universes}"

        # Per-universe tracking
        self._universe_stats: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        for u in universes:
            self._universe_stats[u] = {
                "packet_count": 0,
                "last_sequence": -1,
                "sequence_gaps": 0,
                "dropped_packets": 0,
                "last_packet_time": 0.0,
                "intervals_ms": [],
                "source_name": "",
            }

    def run(self) -> StreamStats:
        """Run the long-form sACN monitoring test."""
        if not SACN_AVAILABLE:
            self.stats.errors.append({
                "time": time.time(),
                "error": "sacn not installed",
            })
            return self.stats

        try:
            receiver = sacn.sACNreceiver()

            for u in self.universes:
                receiver.register_listener("universe", self._on_packet, universe=u)
                receiver.join_multicast(u)

            receiver.start()
            self.start()

            # Block until duration or stop
            while not self.should_stop:
                time.sleep(0.5)

            self.stop()
            receiver.stop()

        except Exception as e:
            self.stats.errors.append({
                "time": time.time(),
                "error": f"sACN error: {e}",
            })

        # Aggregate per-universe stats
        with self._lock:
            total_packets = sum(u["packet_count"] for u in self._universe_stats.values())
            total_drops = sum(u["dropped_packets"] for u in self._universe_stats.values())
            self.stats.total_frames = total_packets
            self.stats.dropped_frames = total_drops
            self.stats.properties["universe_stats"] = dict(self._universe_stats)

            # Collect all intervals for aggregate timing stats
            for u_stats in self._universe_stats.values():
                self.stats.frame_intervals_ms.extend(u_stats["intervals_ms"])

        return self.stats

    def _on_packet(self, packet) -> None:
        """Handle an incoming sACN packet."""
        now = time.monotonic()

        with self._lock:
            u = packet.universe
            if u not in self._universe_stats:
                return

            us = self._universe_stats[u]
            us["packet_count"] += 1
            us["source_name"] = packet.sourceName
            self.stats.total_frames += 1

            # Sequence number tracking (sACN sequence is 0-255, wrapping)
            if us["last_sequence"] >= 0:
                expected = (us["last_sequence"] + 1) % 256
                if packet.sequence != expected:
                    gap = (packet.sequence - us["last_sequence"]) % 256
                    if gap > 1:
                        us["sequence_gaps"] += 1
                        us["dropped_packets"] += gap - 1
                        self.stats.dropped_frames += gap - 1
                        self.stats.errors.append({
                            "time": time.time(),
                            "elapsed_s": round(self.stats.elapsed_seconds, 1),
                            "error": f"Universe {u}: sequence gap, dropped ~{gap-1} packet(s)",
                        })

            us["last_sequence"] = packet.sequence

            # Interval tracking
            if us["last_packet_time"] > 0:
                interval_ms = (now - us["last_packet_time"]) * 1000
                us["intervals_ms"].append(interval_ms)
            us["last_packet_time"] = now


def run_sacn_longform_test(
    universes: List[int],
    duration_seconds: int = 300,
    snapshot_interval: int = 10,
    max_drop_rate: float = 0.1,
    on_snapshot: Optional[Callable] = None,
) -> List[TestResult]:
    """
    Run a long-form sACN stability test.

    Args:
        universes: List of sACN universe numbers to monitor (1-63999)
        duration_seconds: How long to run the test
        snapshot_interval: Seconds between snapshots
        max_drop_rate: Max acceptable drop rate percentage
        on_snapshot: Callback for snapshots
    """
    results: List[TestResult] = []

    if not SACN_AVAILABLE:
        results.append(TestResult(
            name="sACN Long-form Test",
            category="sacn",
            status=Status.SKIP,
            message="sacn not installed (pip install sacn)",
            duration_ms=0,
        ))
        return results

    # Phase 1: Discovery
    disc_result = discover_sacn_sources(universes, listen_seconds=3)
    results.append(disc_result)

    # Phase 2: Long-form monitor
    test_config = LongFormTestConfig(
        duration_seconds=duration_seconds,
        snapshot_interval_seconds=snapshot_interval,
        max_acceptable_drop_rate=max_drop_rate,
        on_snapshot=on_snapshot,
    )

    monitor = SACNStreamMonitor(universes=universes, config=test_config)
    start = time.monotonic()
    stats = monitor.run()
    elapsed = (time.monotonic() - start) * 1000
    summary = stats.summary()

    # Packet reception
    if stats.total_frames > 0:
        results.append(TestResult(
            name=f"sACN Reception (Universes {universes})",
            category="sacn",
            status=Status.PASS,
            message=f"{stats.total_frames} packets in {stats.elapsed_seconds:.0f}s",
            duration_ms=elapsed,
            details=summary,
        ))
    else:
        results.append(TestResult(
            name=f"sACN Reception (Universes {universes})",
            category="sacn",
            status=Status.FAIL,
            message="No sACN packets received",
            duration_ms=elapsed,
        ))
        return results

    # Drop analysis
    if stats.drop_rate <= max_drop_rate:
        drop_status = Status.PASS
    elif stats.drop_rate <= max_drop_rate * 5:
        drop_status = Status.WARN
    else:
        drop_status = Status.FAIL

    results.append(TestResult(
        name=f"sACN Sequence Drops (Universes {universes})",
        category="sacn",
        status=drop_status,
        message=f"{stats.dropped_frames} drops ({stats.drop_rate:.4f}%) over {stats.elapsed_seconds:.0f}s",
        duration_ms=elapsed,
        details={
            "total_packets": stats.total_frames,
            "dropped": stats.dropped_frames,
            "drop_rate_pct": round(stats.drop_rate, 4),
        },
    ))

    # Overall stability
    error_count = len(stats.errors)
    if stats.drop_rate <= max_drop_rate and error_count == 0:
        overall = Status.PASS
        msg = f"STABLE over {stats.elapsed_seconds:.0f}s"
    elif stats.drop_rate <= max_drop_rate * 5:
        overall = Status.WARN
        msg = f"MARGINAL: {error_count} issues over {stats.elapsed_seconds:.0f}s"
    else:
        overall = Status.FAIL
        msg = f"UNSTABLE: {stats.drop_rate:.2f}% drops, {error_count} errors"

    results.append(TestResult(
        name=f"sACN Stability Verdict (Universes {universes})",
        category="sacn",
        status=overall,
        message=msg,
        duration_ms=elapsed,
        details=summary,
    ))

    return results
