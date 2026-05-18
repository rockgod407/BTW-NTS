"""
End-to-end signal verification framework.

The sender embeds a VerificationPayload into every frame/packet of
every AV protocol. The receiver extracts it and validates:

  - Sequence continuity   → detects drops
  - CRC32 integrity       → detects corruption
  - Timestamp delta       → measures latency
  - Ordering              → detects reordering

The payload is protocol-agnostic — each protocol adapter encodes it
into the signal's native format (NDI pixel data, DMX channels, UDP
payload, etc).

Payload structure (32 bytes):
  Bytes 0-3:    Magic   (0x4E 0x54 0x53 0x54 = "NTST")
  Bytes 4-7:    Session ID (uint32, matches sender/receiver pair)
  Bytes 8-15:   Sequence number (uint64, monotonic)
  Bytes 16-23:  Sender timestamp (float64, time.time())
  Bytes 24-27:  Payload CRC32 (of the remaining protocol data)
  Bytes 28-31:  Header CRC32 (of bytes 0-27)
"""
from __future__ import annotations

import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAGIC = b"\x4e\x54\x53\x54"  # "NTST"
HEADER_SIZE = 32
HEADER_FORMAT = "!4sIQdII"   # magic(4) + session(4) + seq(8) + ts(8) + payload_crc(4) + header_crc(4)


# =========================================================================
# Payload encode / decode
# =========================================================================

@dataclass
class VerificationPayload:
    """A single verification payload to embed in a frame/packet."""
    session_id: int
    sequence: int
    sender_timestamp: float
    payload_crc: int = 0        # CRC of the protocol-specific data

    def encode(self) -> bytes:
        """Encode to 32 bytes for embedding."""
        # Pack everything except the final header CRC
        partial = struct.pack(
            "!4sIQdI",
            MAGIC,
            self.session_id,
            self.sequence,
            self.sender_timestamp,
            self.payload_crc,
        )
        # Compute header CRC over the first 28 bytes
        header_crc = zlib.crc32(partial) & 0xFFFFFFFF
        return partial + struct.pack("!I", header_crc)

    @staticmethod
    def decode(data: bytes) -> Optional["VerificationPayload"]:
        """
        Decode 32 bytes into a VerificationPayload.
        Returns None if magic doesn't match or header CRC fails.
        """
        if len(data) < HEADER_SIZE:
            return None

        header_bytes = data[:HEADER_SIZE]

        # Check magic
        if header_bytes[:4] != MAGIC:
            return None

        # Unpack
        magic, session_id, sequence, timestamp, payload_crc, header_crc = struct.unpack(
            HEADER_FORMAT, header_bytes
        )

        # Verify header CRC (computed over first 28 bytes)
        expected_crc = zlib.crc32(header_bytes[:28]) & 0xFFFFFFFF
        if header_crc != expected_crc:
            return None

        return VerificationPayload(
            session_id=session_id,
            sequence=sequence,
            sender_timestamp=timestamp,
            payload_crc=payload_crc,
        )

    @staticmethod
    def compute_payload_crc(data: bytes) -> int:
        """Compute CRC32 for the protocol payload data."""
        return zlib.crc32(data) & 0xFFFFFFFF


# =========================================================================
# Sender state
# =========================================================================

@dataclass
class SenderState:
    """Tracks what the sender has sent for final reporting."""
    session_id: int
    protocol: str
    preset_name: str = ""
    start_time: float = 0.0
    sequence: int = 0
    total_bytes: int = 0
    errors: int = 0

    def next_payload(self, payload_data: bytes = b"") -> VerificationPayload:
        """Generate the next verification payload."""
        self.sequence += 1
        crc = VerificationPayload.compute_payload_crc(payload_data) if payload_data else 0
        return VerificationPayload(
            session_id=self.session_id,
            sequence=self.sequence,
            sender_timestamp=time.time(),
            payload_crc=crc,
        )

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time if self.start_time else 0

    def summary(self) -> Dict[str, Any]:
        return {
            "role": "sender",
            "session_id": self.session_id,
            "protocol": self.protocol,
            "preset": self.preset_name,
            "frames_sent": self.sequence,
            "total_bytes": self.total_bytes,
            "errors": self.errors,
            "duration_seconds": round(self.elapsed_seconds, 1),
        }


# =========================================================================
# Receiver state — the core verification engine
# =========================================================================

@dataclass
class ReceiverState:
    """
    Tracks and validates received frames against the verification payload.

    This is where drops, corruption, reordering, and latency are detected.
    """
    session_id: int
    protocol: str

    # Counters
    frames_received: int = 0
    frames_valid: int = 0           # Header decoded + CRC OK
    frames_corrupted: int = 0       # Header decoded but CRC mismatch
    frames_foreign: int = 0         # No valid header (not our test signal)

    # Sequence tracking
    expected_sequence: int = 1
    frames_dropped: int = 0
    frames_out_of_order: int = 0
    frames_duplicate: int = 0
    _seen_sequences: set = field(default_factory=set)

    # Latency tracking (requires rough clock sync between machines)
    latency_samples_ms: List[float] = field(default_factory=list)

    # Timing
    start_time: float = 0.0
    frame_times: List[float] = field(default_factory=list)

    # Error log
    errors: List[Dict[str, Any]] = field(default_factory=list)

    # Periodic snapshots
    snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def validate_frame(
        self,
        header_data: bytes,
        payload_data: bytes = b"",
    ) -> Optional[VerificationPayload]:
        """
        Validate a received frame. Call this for every frame/packet.

        Args:
            header_data: The first 32 bytes (verification header)
            payload_data: The remaining protocol data (for CRC check)

        Returns:
            The decoded payload if valid, None if foreign/corrupt.
        """
        self.frames_received += 1
        now = time.monotonic()
        self.frame_times.append(now)

        # Decode header
        payload = VerificationPayload.decode(header_data)

        if payload is None:
            self.frames_foreign += 1
            return None

        # Session ID check
        if payload.session_id != self.session_id:
            self.frames_foreign += 1
            return None

        # CRC check on payload data
        if payload_data and payload.payload_crc != 0:
            actual_crc = VerificationPayload.compute_payload_crc(payload_data)
            if actual_crc != payload.payload_crc:
                self.frames_corrupted += 1
                self.errors.append({
                    "time": time.time(),
                    "type": "corruption",
                    "sequence": payload.sequence,
                    "expected_crc": payload.payload_crc,
                    "actual_crc": actual_crc,
                })
                return payload

        self.frames_valid += 1

        # Sequence analysis
        seq = payload.sequence

        if seq in self._seen_sequences:
            self.frames_duplicate += 1
            self.errors.append({
                "time": time.time(),
                "type": "duplicate",
                "sequence": seq,
            })
        else:
            self._seen_sequences.add(seq)

            if seq == self.expected_sequence:
                # Perfect — in order, no drops
                self.expected_sequence = seq + 1
            elif seq > self.expected_sequence:
                # Gap — frames were dropped
                gap = seq - self.expected_sequence
                self.frames_dropped += gap
                self.errors.append({
                    "time": time.time(),
                    "type": "drop",
                    "expected": self.expected_sequence,
                    "received": seq,
                    "gap": gap,
                })
                self.expected_sequence = seq + 1
            else:
                # Out of order (received older sequence after newer)
                self.frames_out_of_order += 1
                self.errors.append({
                    "time": time.time(),
                    "type": "reorder",
                    "expected": self.expected_sequence,
                    "received": seq,
                })

        # Latency (note: requires NTP-synced clocks for accuracy)
        latency_ms = (time.time() - payload.sender_timestamp) * 1000
        # Only record plausible values (clock skew can make these huge)
        if -5000 < latency_ms < 30000:
            self.latency_samples_ms.append(latency_ms)

        return payload

    def take_snapshot(self) -> Dict[str, Any]:
        """Capture a point-in-time snapshot."""
        elapsed = time.monotonic() - self.start_time if self.start_time else 0
        snap = {
            "timestamp": time.time(),
            "elapsed_s": round(elapsed, 1),
            "received": self.frames_received,
            "valid": self.frames_valid,
            "dropped": self.frames_dropped,
            "corrupted": self.frames_corrupted,
            "out_of_order": self.frames_out_of_order,
            "duplicate": self.frames_duplicate,
            "drop_rate_pct": round(self.drop_rate, 4),
        }
        self.snapshots.append(snap)
        return snap

    @property
    def drop_rate(self) -> float:
        total = self.frames_valid + self.frames_dropped
        if total == 0:
            return 0.0
        return (self.frames_dropped / total) * 100.0

    @property
    def corruption_rate(self) -> float:
        if self.frames_received == 0:
            return 0.0
        return (self.frames_corrupted / self.frames_received) * 100.0

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time if self.start_time else 0

    def summary(self) -> Dict[str, Any]:
        """Generate the final receiver report."""
        import statistics

        result = {
            "role": "receiver",
            "session_id": self.session_id,
            "protocol": self.protocol,
            "duration_seconds": round(self.elapsed_seconds, 1),
            "frames_received": self.frames_received,
            "frames_valid": self.frames_valid,
            "frames_dropped": self.frames_dropped,
            "frames_corrupted": self.frames_corrupted,
            "frames_out_of_order": self.frames_out_of_order,
            "frames_duplicate": self.frames_duplicate,
            "frames_foreign": self.frames_foreign,
            "drop_rate_pct": round(self.drop_rate, 4),
            "corruption_rate_pct": round(self.corruption_rate, 4),
            "error_count": len(self.errors),
        }

        # Frame interval stats
        if len(self.frame_times) > 1:
            intervals = [
                (self.frame_times[i] - self.frame_times[i - 1]) * 1000
                for i in range(1, len(self.frame_times))
            ]
            sorted_intervals = sorted(intervals)
            result["frame_interval_ms"] = {
                "avg": round(statistics.mean(sorted_intervals), 2),
                "min": round(min(sorted_intervals), 2),
                "max": round(max(sorted_intervals), 2),
                "stddev": round(statistics.stdev(sorted_intervals), 2) if len(sorted_intervals) > 1 else 0,
                "p99": round(_percentile(sorted_intervals, 99), 2),
            }

        # Latency stats
        if self.latency_samples_ms:
            sorted_lat = sorted(self.latency_samples_ms)
            result["latency_ms"] = {
                "avg": round(statistics.mean(sorted_lat), 2),
                "min": round(min(sorted_lat), 2),
                "max": round(max(sorted_lat), 2),
                "p95": round(_percentile(sorted_lat, 95), 2),
                "p99": round(_percentile(sorted_lat, 99), 2),
            }

        # Verdict
        if self.frames_dropped == 0 and self.frames_corrupted == 0 and self.frames_out_of_order == 0:
            result["verdict"] = "PERFECT"
        elif self.drop_rate < 0.01 and self.frames_corrupted == 0:
            result["verdict"] = "EXCELLENT"
        elif self.drop_rate < 0.1 and self.corruption_rate == 0:
            result["verdict"] = "GOOD"
        elif self.drop_rate < 1.0:
            result["verdict"] = "MARGINAL"
        else:
            result["verdict"] = "FAILING"

        return result


def generate_session_id() -> int:
    """Generate a random session ID for pairing sender/receiver."""
    import random
    return random.randint(1, 0xFFFFFFFF)


def _percentile(sorted_data: List[float], pct: float) -> float:
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    d = k - f
    return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])
