"""
Session beacon for nettest send/receive discovery.

The sender broadcasts a small UDP beacon every 2 seconds on a known
port so the receiver can auto-discover active sessions on the LAN
without needing a session ID.

Beacon format: JSON over UDP broadcast on port 5557.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

BEACON_PORT = 5557
BEACON_INTERVAL = 2.0  # seconds
BEACON_MAGIC = "NTST_BEACON"


@dataclass
class SessionInfo:
    """Discovered session information."""
    session_id: int
    protocol: str
    preset: str
    hostname: str
    sender_ip: str
    duration: str
    started_at: float
    version: str = ""
    last_seen: float = 0.0

    @property
    def age_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def age_display(self) -> str:
        age = self.age_seconds
        if age < 60:
            return f"{age:.0f}s ago"
        elif age < 3600:
            return f"{age/60:.0f}m ago"
        else:
            return f"{age/3600:.1f}h ago"


def _get_hostname() -> str:
    """Get this machine's hostname."""
    try:
        return socket.gethostname().split(".")[0]
    except Exception:
        return "unknown"


def _get_local_ip() -> str:
    """Get this machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


# =========================================================================
# Beacon Sender (runs in sender's background thread)
# =========================================================================

class BeaconSender:
    """Broadcasts session beacons so receivers can discover us."""

    def __init__(
        self,
        session_id: int,
        protocol: str,
        preset: str = "",
        duration: str = "",
    ):
        self.session_id = session_id
        self.protocol = protocol
        self.preset = preset
        self.duration = duration
        self.hostname = _get_hostname()
        self.sender_ip = _get_local_ip()
        self.started_at = time.time()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _build_beacon(self) -> bytes:
        """Build the beacon payload."""
        data = {
            "magic": BEACON_MAGIC,
            "session_id": self.session_id,
            "protocol": self.protocol,
            "preset": self.preset,
            "hostname": self.hostname,
            "sender_ip": self.sender_ip,
            "duration": self.duration,
            "started_at": self.started_at,
            "version": _get_version(),
        }
        return json.dumps(data).encode("utf-8")

    def _broadcast_loop(self):
        """Background thread that sends beacons."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.settimeout(1.0)

            beacon = self._build_beacon()

            while not self._stop.is_set():
                try:
                    sock.sendto(beacon, ("255.255.255.255", BEACON_PORT))
                    # Also send to subnet broadcast (common subnets)
                    ip = self.sender_ip
                    if ip and ip != "0.0.0.0":
                        parts = ip.split(".")
                        if len(parts) == 4:
                            subnet_broadcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                            sock.sendto(beacon, (subnet_broadcast, BEACON_PORT))
                except Exception:
                    pass
                self._stop.wait(BEACON_INTERVAL)

            sock.close()
        except Exception:
            pass

    def start(self):
        """Start broadcasting beacons."""
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop broadcasting."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)


# =========================================================================
# Beacon Listener (used by receiver to discover sessions)
# =========================================================================

def discover_sessions(
    timeout: float = 5.0,
    protocol_filter: Optional[str] = None,
) -> List[SessionInfo]:
    """
    Listen for session beacons on the LAN.

    Args:
        timeout: How long to listen (seconds)
        protocol_filter: Only return sessions matching this protocol

    Returns:
        List of discovered SessionInfo objects, deduplicated by session_id
    """
    sessions: Dict[int, SessionInfo] = {}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.settimeout(1.0)
        sock.bind(("", BEACON_PORT))

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
                payload = json.loads(data.decode("utf-8"))

                if payload.get("magic") != BEACON_MAGIC:
                    continue

                sid = payload["session_id"]
                proto = payload.get("protocol", "unknown")

                if protocol_filter and proto.lower() != protocol_filter.lower():
                    continue

                sessions[sid] = SessionInfo(
                    session_id=sid,
                    protocol=proto,
                    preset=payload.get("preset", ""),
                    hostname=payload.get("hostname", addr[0]),
                    sender_ip=payload.get("sender_ip", addr[0]),
                    duration=payload.get("duration", ""),
                    started_at=payload.get("started_at", time.time()),
                    version=payload.get("version", ""),
                    last_seen=time.time(),
                )

            except socket.timeout:
                continue
            except (json.JSONDecodeError, KeyError):
                continue

        sock.close()

    except OSError as e:
        # Port might be in use or permission denied
        pass
    except Exception:
        pass

    return sorted(sessions.values(), key=lambda s: s.last_seen, reverse=True)


def _get_version() -> str:
    """Get nettest version string."""
    try:
        from importlib.metadata import version
        return version("nettest")
    except Exception:
        return "dev"
