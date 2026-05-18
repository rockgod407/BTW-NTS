"""
AV protocol presets — signal profiles, bandwidth calculations, and
stress-test configurations for NDI, sACN, Dante, and more.

Every preset includes calculated bandwidth so you know what the network
needs to sustain before you even start the test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# =========================================================================
# NDI
# =========================================================================

class NDICodec(Enum):
    """NDI transport modes."""
    FULL = "NDI|Full"          # Uncompressed / SpeedHQ — highest bandwidth
    HX = "NDI|HX"             # H.264 long-GOP — low bandwidth
    HX2 = "NDI|HX2"           # H.264/H.265 — mid bandwidth
    HX3 = "NDI|HX3"           # H.265/HEVC — newest, efficient


@dataclass
class NDIPreset:
    """A single NDI signal configuration."""
    name: str
    width: int
    height: int
    fps: float
    codec: NDICodec = NDICodec.FULL
    interlaced: bool = False
    color_depth_bits: int = 8       # 8 or 10
    chroma_subsampling: str = "4:2:2"  # 4:2:0, 4:2:2, 4:4:4
    alpha: bool = False
    audio_channels: int = 2
    audio_sample_rate: int = 48000
    audio_bit_depth: int = 16
    description: str = ""

    @property
    def pixels_per_frame(self) -> int:
        return self.width * self.height

    @property
    def effective_fps(self) -> float:
        """Effective field/frame rate for bandwidth calc."""
        return self.fps

    @property
    def bits_per_pixel(self) -> float:
        """Bits per pixel based on subsampling and depth."""
        chroma_map = {"4:2:0": 1.5, "4:2:2": 2.0, "4:4:4": 3.0}
        bpp = chroma_map.get(self.chroma_subsampling, 2.0) * self.color_depth_bits
        if self.alpha:
            bpp += self.color_depth_bits
        return bpp

    @property
    def video_bandwidth_mbps(self) -> float:
        """Raw uncompressed video bandwidth in Mbps."""
        raw = self.pixels_per_frame * self.bits_per_pixel * self.effective_fps
        # Apply codec compression ratio estimates
        ratios = {
            NDICodec.FULL: 0.55,   # SpeedHQ ~1.8:1 average
            NDICodec.HX: 0.04,     # H.264 long-GOP ~25:1
            NDICodec.HX2: 0.03,    # H.264/H.265 ~33:1
            NDICodec.HX3: 0.025,   # H.265/HEVC ~40:1
        }
        ratio = ratios.get(self.codec, 0.55)
        return (raw * ratio) / 1_000_000

    @property
    def audio_bandwidth_mbps(self) -> float:
        """Audio bandwidth in Mbps."""
        return (self.audio_channels * self.audio_sample_rate * self.audio_bit_depth) / 1_000_000

    @property
    def total_bandwidth_mbps(self) -> float:
        """Total estimated bandwidth in Mbps (video + audio + ~5% overhead)."""
        return (self.video_bandwidth_mbps + self.audio_bandwidth_mbps) * 1.05

    @property
    def expected_frame_interval_ms(self) -> float:
        """Expected milliseconds between frames."""
        return 1000.0 / self.fps if self.fps > 0 else 0

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "resolution": f"{self.width}x{self.height}{'i' if self.interlaced else 'p'}{self.fps}",
            "codec": self.codec.value,
            "color": f"{self.chroma_subsampling} {self.color_depth_bits}-bit",
            "video_mbps": round(self.video_bandwidth_mbps, 1),
            "audio_mbps": round(self.audio_bandwidth_mbps, 2),
            "total_mbps": round(self.total_bandwidth_mbps, 1),
            "description": self.description,
        }


# Common NDI presets
NDI_PRESETS: Dict[str, NDIPreset] = {
    # --- 720p ---
    "720p30": NDIPreset(
        name="720p30", width=1280, height=720, fps=29.97,
        description="HD 720p @ 29.97fps — budget streaming / confidence monitors",
    ),
    "720p60": NDIPreset(
        name="720p60", width=1280, height=720, fps=59.94,
        description="HD 720p @ 59.94fps — sports / fast motion",
    ),

    # --- 1080p ---
    "1080p24": NDIPreset(
        name="1080p24", width=1920, height=1080, fps=23.976,
        description="Full HD @ 23.976fps — cinematic / film",
    ),
    "1080p25": NDIPreset(
        name="1080p25", width=1920, height=1080, fps=25.0,
        description="Full HD @ 25fps — PAL broadcast",
    ),
    "1080p30": NDIPreset(
        name="1080p30", width=1920, height=1080, fps=29.97,
        description="Full HD @ 29.97fps — NTSC broadcast standard",
    ),
    "1080p50": NDIPreset(
        name="1080p50", width=1920, height=1080, fps=50.0,
        description="Full HD @ 50fps — PAL high frame rate",
    ),
    "1080p60": NDIPreset(
        name="1080p60", width=1920, height=1080, fps=59.94,
        description="Full HD @ 59.94fps — NTSC high frame rate / esports",
    ),

    # --- 1080i ---
    "1080i50": NDIPreset(
        name="1080i50", width=1920, height=1080, fps=25.0, interlaced=True,
        description="Full HD interlaced @ 50 fields — PAL broadcast",
    ),
    "1080i60": NDIPreset(
        name="1080i60", width=1920, height=1080, fps=29.97, interlaced=True,
        description="Full HD interlaced @ 59.94 fields — NTSC broadcast",
    ),

    # --- 4K / UHD ---
    "4k30": NDIPreset(
        name="4K30", width=3840, height=2160, fps=29.97,
        description="4K UHD @ 29.97fps — high-end production",
    ),
    "4k60": NDIPreset(
        name="4K60", width=3840, height=2160, fps=59.94,
        description="4K UHD @ 59.94fps — premium live production",
    ),

    # --- NDI|HX variants (lower bandwidth) ---
    "1080p30-hx": NDIPreset(
        name="1080p30 HX", width=1920, height=1080, fps=29.97,
        codec=NDICodec.HX,
        description="Full HD @ 29.97fps NDI|HX — PTZ cameras, WiFi-friendly",
    ),
    "1080p60-hx": NDIPreset(
        name="1080p60 HX", width=1920, height=1080, fps=59.94,
        codec=NDICodec.HX,
        description="Full HD @ 59.94fps NDI|HX",
    ),
    "4k30-hx3": NDIPreset(
        name="4K30 HX3", width=3840, height=2160, fps=29.97,
        codec=NDICodec.HX3,
        description="4K UHD @ 29.97fps NDI|HX3 — efficient 4K transport",
    ),
    "4k60-hx3": NDIPreset(
        name="4K60 HX3", width=3840, height=2160, fps=59.94,
        codec=NDICodec.HX3,
        description="4K UHD @ 59.94fps NDI|HX3",
    ),

    # --- 10-bit / 4:4:4 variants ---
    "1080p30-10bit": NDIPreset(
        name="1080p30 10-bit", width=1920, height=1080, fps=29.97,
        color_depth_bits=10, chroma_subsampling="4:2:2",
        description="Full HD 10-bit 4:2:2 — color-critical production",
    ),
    "1080p30-444": NDIPreset(
        name="1080p30 4:4:4", width=1920, height=1080, fps=29.97,
        chroma_subsampling="4:4:4",
        description="Full HD 4:4:4 — graphics / keying",
    ),
    "1080p30-444a": NDIPreset(
        name="1080p30 4:4:4:4", width=1920, height=1080, fps=29.97,
        chroma_subsampling="4:4:4", alpha=True,
        description="Full HD 4:4:4 + alpha — compositing / overlays",
    ),

    # --- Multi-audio variants ---
    "1080p30-16ch": NDIPreset(
        name="1080p30 16ch Audio", width=1920, height=1080, fps=29.97,
        audio_channels=16,
        description="Full HD with 16-channel embedded audio",
    ),
}


# Stress test combos (multiple simultaneous streams)
@dataclass
class NDIStressProfile:
    """A stress test scenario with multiple NDI streams."""
    name: str
    description: str
    streams: List[str]  # preset names
    duration_seconds: int = 300

    @property
    def total_bandwidth_mbps(self) -> float:
        return sum(NDI_PRESETS[s].total_bandwidth_mbps for s in self.streams if s in NDI_PRESETS)

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "stream_count": len(self.streams),
            "total_bandwidth_mbps": round(self.total_bandwidth_mbps, 1),
            "streams": [NDI_PRESETS[s].summary() for s in self.streams if s in NDI_PRESETS],
        }


NDI_STRESS_PROFILES: Dict[str, NDIStressProfile] = {
    "light": NDIStressProfile(
        name="Light",
        description="Small setup — 2 cameras, 1 graphics source",
        streams=["1080p30", "1080p30", "1080p30-444a"],
    ),
    "medium": NDIStressProfile(
        name="Medium",
        description="Mid-size production — 4 cameras + program out",
        streams=["1080p60", "1080p60", "1080p60", "1080p60", "1080p60"],
    ),
    "heavy": NDIStressProfile(
        name="Heavy",
        description="Large production — 8 HD sources + 2 replays",
        streams=[
            "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p30", "1080p30",
        ],
    ),
    "4k-production": NDIStressProfile(
        name="4K Production",
        description="4K production — 4 cameras at 4K30",
        streams=["4k30", "4k30", "4k30", "4k30"],
    ),
    "broadcast-truck": NDIStressProfile(
        name="Broadcast Truck",
        description="OB van — 12 HD sources, mixed formats",
        streams=[
            "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p60", "1080p60", "1080p30", "1080p30",
            "1080p30-444a", "1080p30-444a",
            "1080p30-16ch", "1080p30-16ch",
        ],
    ),
    "esports-arena": NDIStressProfile(
        name="Esports Arena",
        description="Esports — 10 player POVs + 4 cameras + overlays",
        streams=[
            "1080p60", "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p60", "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p30-444a", "1080p30-444a",
        ],
    ),
    "hx-wifi": NDIStressProfile(
        name="NDI|HX over WiFi",
        description="Wireless scenario — 4 HX cameras on WiFi",
        streams=["1080p30-hx", "1080p30-hx", "1080p30-hx", "1080p30-hx"],
    ),
    "max-gigabit": NDIStressProfile(
        name="Max Gigabit",
        description="Push a 1Gbps link to its limit",
        streams=[
            "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p60", "1080p60", "1080p60", "1080p60",
            "1080p60",
        ],
    ),
    "max-10g": NDIStressProfile(
        name="Max 10GbE",
        description="Stress test a 10GbE link with 4K sources",
        streams=[
            "4k60", "4k60", "4k60", "4k60",
            "4k30", "4k30", "4k30", "4k30",
            "1080p60", "1080p60", "1080p60", "1080p60",
        ],
    ),
}


# =========================================================================
# sACN / E1.31
# =========================================================================

@dataclass
class SACNPreset:
    """sACN universe configuration preset."""
    name: str
    description: str
    universes: int
    channels_per_universe: int = 512  # DMX standard
    refresh_rate_hz: float = 44.0     # typical sACN rate
    priority: int = 100

    @property
    def total_channels(self) -> int:
        return self.universes * self.channels_per_universe

    @property
    def total_fixtures_approx(self) -> int:
        """Rough fixture count assuming ~20 channels per fixture."""
        return self.total_channels // 20

    @property
    def bandwidth_mbps(self) -> float:
        """Bandwidth in Mbps (each universe packet ≈ 638 bytes)."""
        packet_bytes = 638  # sACN packet with full 512 channels
        packets_per_sec = self.universes * self.refresh_rate_hz
        return (packets_per_sec * packet_bytes * 8) / 1_000_000

    @property
    def packets_per_second(self) -> float:
        return self.universes * self.refresh_rate_hz

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "universes": self.universes,
            "total_channels": self.total_channels,
            "approx_fixtures": self.total_fixtures_approx,
            "refresh_rate_hz": self.refresh_rate_hz,
            "bandwidth_mbps": round(self.bandwidth_mbps, 2),
            "packets_per_second": round(self.packets_per_second, 0),
            "description": self.description,
        }


SACN_PRESETS: Dict[str, SACNPreset] = {
    "small-venue": SACNPreset(
        name="Small Venue",
        description="Bar / small club — a few LED pars and a moving head or two",
        universes=2,
    ),
    "club": SACNPreset(
        name="Club",
        description="Medium club — LED strips, moving heads, strobes",
        universes=8,
    ),
    "theater": SACNPreset(
        name="Theater",
        description="Theater / performing arts — conventionals + LED + some movers",
        universes=16,
    ),
    "concert": SACNPreset(
        name="Concert",
        description="Concert touring rig — full moving light rig + LED video",
        universes=32,
    ),
    "arena": SACNPreset(
        name="Arena",
        description="Arena show — large moving light rig + LED walls + effects",
        universes=64,
    ),
    "festival": SACNPreset(
        name="Festival",
        description="Multi-stage festival — main + side stages + site lighting",
        universes=128,
    ),
    "mega-show": SACNPreset(
        name="Mega Show",
        description="Stadium / mega-event — pixel-mapped LED + full rig",
        universes=256,
    ),
    "pixel-heavy": SACNPreset(
        name="Pixel Heavy",
        description="Heavy pixel mapping — large LED installation / immersive",
        universes=512,
    ),
    "max-sacn": SACNPreset(
        name="Max sACN",
        description="Protocol stress test — near maximum universe count",
        universes=1000,
    ),

    # High refresh rate variants
    "concert-hfr": SACNPreset(
        name="Concert HFR",
        description="Concert rig at high refresh — reduced flicker on camera",
        universes=32,
        refresh_rate_hz=88.0,
    ),
    "pixel-heavy-hfr": SACNPreset(
        name="Pixel Heavy HFR",
        description="Heavy pixel mapping at high refresh rate",
        universes=512,
        refresh_rate_hz=88.0,
    ),
}


# =========================================================================
# Dante Audio
# =========================================================================

class DanteSampleRate(Enum):
    SR_44100 = 44100
    SR_48000 = 48000
    SR_88200 = 88200
    SR_96000 = 96000
    SR_176400 = 176400
    SR_192000 = 192000


@dataclass
class DantePreset:
    """Dante audio channel configuration preset."""
    name: str
    description: str
    channel_count: int
    sample_rate: int = 48000
    bit_depth: int = 24             # Dante uses 24-bit or 32-bit
    latency_ms: float = 1.0         # Dante device latency setting
    redundancy: bool = False        # Dante redundancy (doubles bandwidth)
    flow_count: int = 0             # 0 = auto-calculate

    @property
    def effective_flows(self) -> int:
        """Dante packs up to 8 channels per flow (multicast) or 4 (unicast)."""
        if self.flow_count > 0:
            return self.flow_count
        import math
        return math.ceil(self.channel_count / 8)

    @property
    def bandwidth_per_channel_mbps(self) -> float:
        """Bandwidth per audio channel in Mbps."""
        # Dante uses RTP over UDP: audio payload + headers
        # Per channel: sample_rate * bit_depth + protocol overhead (~25%)
        raw_bps = self.sample_rate * self.bit_depth
        with_overhead = raw_bps * 1.25  # RTP/UDP/IP headers
        return with_overhead / 1_000_000

    @property
    def total_bandwidth_mbps(self) -> float:
        """Total bandwidth for all channels in Mbps."""
        bw = self.channel_count * self.bandwidth_per_channel_mbps
        if self.redundancy:
            bw *= 2  # Primary + secondary network
        return bw

    @property
    def per_network_bandwidth_mbps(self) -> float:
        """Bandwidth per physical network (for redundancy mode)."""
        return self.channel_count * self.bandwidth_per_channel_mbps

    @property
    def packets_per_second(self) -> float:
        """Estimated Dante packets per second."""
        # Dante sends packets at intervals depending on latency setting
        # Lower latency = more packets/sec
        latency_pps = {
            0.25: 4000, 0.5: 2000, 1.0: 1000,
            2.0: 500, 5.0: 200,
        }
        pps_per_flow = latency_pps.get(self.latency_ms, 1000)
        return self.effective_flows * pps_per_flow

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "channels": self.channel_count,
            "sample_rate": f"{self.sample_rate / 1000:.1f}kHz",
            "bit_depth": self.bit_depth,
            "latency_ms": self.latency_ms,
            "redundancy": self.redundancy,
            "bandwidth_mbps": round(self.total_bandwidth_mbps, 2),
            "per_network_mbps": round(self.per_network_bandwidth_mbps, 2),
            "flows": self.effective_flows,
            "packets_per_second": round(self.packets_per_second, 0),
            "description": self.description,
        }


DANTE_PRESETS: Dict[str, DantePreset] = {
    # --- Small systems ---
    "stereo": DantePreset(
        name="Stereo",
        description="Simple stereo feed — background music / 2-channel playback",
        channel_count=2, sample_rate=48000, bit_depth=24,
    ),
    "small-pa": DantePreset(
        name="Small PA",
        description="Small PA — 8 channels (LR main + 2 monitors + 4 aux)",
        channel_count=8, sample_rate=48000, bit_depth=24,
    ),
    "podcast-studio": DantePreset(
        name="Podcast Studio",
        description="Podcast / small studio — 4 mics + 2 playback + 2 headphones",
        channel_count=8, sample_rate=48000, bit_depth=24, latency_ms=1.0,
    ),

    # --- Medium systems ---
    "conference-room": DantePreset(
        name="Conference Room",
        description="Corporate AV — 16 ceiling mics + 8 speakers + DSP",
        channel_count=32, sample_rate=48000, bit_depth=24,
    ),
    "house-of-worship": DantePreset(
        name="House of Worship",
        description="HOW — 32 inputs + 16 outputs (monitors, broadcast, recording)",
        channel_count=64, sample_rate=48000, bit_depth=24,
    ),
    "theater-audio": DantePreset(
        name="Theater Audio",
        description="Theater — 48 wireless mics + 16 playback + 32 speakers",
        channel_count=96, sample_rate=48000, bit_depth=24,
    ),

    # --- Large systems ---
    "concert-foh": DantePreset(
        name="Concert FOH",
        description="Concert — 64 stage inputs + 32 monitor mixes + recording",
        channel_count=128, sample_rate=48000, bit_depth=24,
    ),
    "arena-audio": DantePreset(
        name="Arena Audio",
        description="Arena — multi-console, 256 channels across FOH + monitors + broadcast",
        channel_count=256, sample_rate=48000, bit_depth=24,
    ),
    "festival-audio": DantePreset(
        name="Festival Audio",
        description="Festival — multi-stage sharing, 512 channels total",
        channel_count=512, sample_rate=48000, bit_depth=24,
    ),

    # --- High sample rate variants ---
    "studio-hires": DantePreset(
        name="Studio Hi-Res",
        description="Recording studio — 32 channels at 96kHz/24-bit",
        channel_count=32, sample_rate=96000, bit_depth=24,
    ),
    "mastering": DantePreset(
        name="Mastering Suite",
        description="Mastering — 8 channels at 192kHz/32-bit (maximum quality)",
        channel_count=8, sample_rate=192000, bit_depth=32,
    ),
    "orchestra-hires": DantePreset(
        name="Orchestra Hi-Res",
        description="Orchestral recording — 64 channels at 96kHz",
        channel_count=64, sample_rate=96000, bit_depth=24,
    ),

    # --- Low latency variants ---
    "live-iem": DantePreset(
        name="Live IEM",
        description="In-ear monitors — 16 stereo mixes, ultra-low latency",
        channel_count=32, sample_rate=48000, bit_depth=24, latency_ms=0.25,
    ),
    "broadcast-low-latency": DantePreset(
        name="Broadcast Low Latency",
        description="Broadcast — 64 channels, 0.5ms latency for lip sync",
        channel_count=64, sample_rate=48000, bit_depth=24, latency_ms=0.5,
    ),

    # --- Redundancy variants ---
    "concert-redundant": DantePreset(
        name="Concert Redundant",
        description="Concert with Dante redundancy — 128ch on primary + secondary",
        channel_count=128, sample_rate=48000, bit_depth=24, redundancy=True,
    ),
    "broadcast-redundant": DantePreset(
        name="Broadcast Redundant",
        description="Broadcast facility — 256ch redundant, safety-critical",
        channel_count=256, sample_rate=48000, bit_depth=24, redundancy=True,
    ),

    # --- Stress tests ---
    "max-48k": DantePreset(
        name="Max 48kHz",
        description="Maximum channel count at 48kHz — stress test",
        channel_count=512, sample_rate=48000, bit_depth=24, latency_ms=1.0,
    ),
    "max-96k": DantePreset(
        name="Max 96kHz",
        description="Maximum channel count at 96kHz — double the bandwidth",
        channel_count=256, sample_rate=96000, bit_depth=24, latency_ms=1.0,
    ),
    "max-96k-redundant": DantePreset(
        name="Max 96kHz Redundant",
        description="Maximum 96kHz with redundancy — extreme stress test",
        channel_count=256, sample_rate=96000, bit_depth=24,
        redundancy=True, latency_ms=1.0,
    ),
}


# =========================================================================
# TCNet
# =========================================================================

@dataclass
class TCNetPreset:
    """TCNet show control preset."""
    name: str
    description: str
    node_count: int
    timecode_fps: float = 30.0
    layers: int = 4

    @property
    def bandwidth_mbps(self) -> float:
        """Estimated TCNet bandwidth (relatively low)."""
        # TCNet is lightweight: ~100-200 byte packets at timecode rate
        packet_size = 200  # bytes
        pps = self.timecode_fps * self.node_count
        return (pps * packet_size * 8) / 1_000_000

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "nodes": self.node_count,
            "timecode_fps": self.timecode_fps,
            "layers": self.layers,
            "bandwidth_mbps": round(self.bandwidth_mbps, 3),
            "description": self.description,
        }


TCNET_PRESETS: Dict[str, TCNetPreset] = {
    "simple": TCNetPreset(
        name="Simple Show",
        description="1 master + 1 slave — basic timecode sync",
        node_count=2, timecode_fps=30.0, layers=1,
    ),
    "multi-media": TCNetPreset(
        name="Multi-Media",
        description="Timecode sync across lighting + video + audio",
        node_count=5, timecode_fps=30.0, layers=4,
    ),
    "touring-show": TCNetPreset(
        name="Touring Show",
        description="Complex touring show — 10+ synced nodes",
        node_count=12, timecode_fps=30.0, layers=8,
    ),
    "festival-sync": TCNetPreset(
        name="Festival Sync",
        description="Multi-stage festival — master clock to all stages",
        node_count=30, timecode_fps=30.0, layers=4,
    ),
}


# =========================================================================
# Pro DJ Link
# =========================================================================

@dataclass
class ProDJLinkPreset:
    """Pro DJ Link device configuration preset."""
    name: str
    description: str
    player_count: int       # CDJs / XDJs
    mixer_count: int = 1
    rekordbox_count: int = 0

    @property
    def total_devices(self) -> int:
        return self.player_count + self.mixer_count + self.rekordbox_count

    @property
    def bandwidth_mbps(self) -> float:
        """Pro DJ Link is very low bandwidth — mostly status packets."""
        # Keep-alive: ~54 bytes every 1.5s per device
        # Status: ~212 bytes every ~200ms per player
        # Beat: ~96 bytes per beat
        keepalive_bps = self.total_devices * (54 * 8) / 1.5
        status_bps = self.player_count * (212 * 8) * 5  # ~5 per second
        beat_bps = self.player_count * (96 * 8) * 2     # ~2 beats/sec avg
        return (keepalive_bps + status_bps + beat_bps) / 1_000_000

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "players": self.player_count,
            "mixers": self.mixer_count,
            "total_devices": self.total_devices,
            "bandwidth_mbps": round(self.bandwidth_mbps, 4),
            "description": self.description,
        }


PRODJLINK_PRESETS: Dict[str, ProDJLinkPreset] = {
    "2-deck": ProDJLinkPreset(
        name="2-Deck", description="Standard DJ setup — 2 CDJs + mixer",
        player_count=2, mixer_count=1,
    ),
    "4-deck": ProDJLinkPreset(
        name="4-Deck", description="Full setup — 4 CDJs + mixer + rekordbox",
        player_count=4, mixer_count=1, rekordbox_count=1,
    ),
    "b2b": ProDJLinkPreset(
        name="B2B", description="Back-to-back — 4 CDJs, 2 mixers, 2 rekordbox",
        player_count=4, mixer_count=2, rekordbox_count=2,
    ),
}


# =========================================================================
# Art-Net / MA-Net
# =========================================================================

@dataclass
class ArtNetPreset:
    """Art-Net DMX preset (similar to sACN but Art-Net specific)."""
    name: str
    description: str
    universes: int
    refresh_rate_hz: float = 44.0

    @property
    def bandwidth_mbps(self) -> float:
        """Art-Net packet size ≈ 530 bytes per universe."""
        packet_bytes = 530
        pps = self.universes * self.refresh_rate_hz
        return (pps * packet_bytes * 8) / 1_000_000

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "universes": self.universes,
            "refresh_rate_hz": self.refresh_rate_hz,
            "bandwidth_mbps": round(self.bandwidth_mbps, 2),
            "description": self.description,
        }


ARTNET_PRESETS: Dict[str, ArtNetPreset] = {
    "small-rig": ArtNetPreset(
        name="Small Rig", description="4 universes — small lighting rig",
        universes=4,
    ),
    "medium-rig": ArtNetPreset(
        name="Medium Rig", description="16 universes — club / theater",
        universes=16,
    ),
    "large-rig": ArtNetPreset(
        name="Large Rig", description="64 universes — concert / touring",
        universes=64,
    ),
    "max-artnet": ArtNetPreset(
        name="Max Art-Net", description="256 universes — Art-Net limit stress test",
        universes=256,
    ),
}


# =========================================================================
# Utilities
# =========================================================================

def list_all_presets() -> Dict[str, Dict[str, Any]]:
    """Return a summary of all available presets across all protocols."""
    return {
        "ndi": {k: v.summary() for k, v in NDI_PRESETS.items()},
        "ndi_stress": {k: v.summary() for k, v in NDI_STRESS_PROFILES.items()},
        "sacn": {k: v.summary() for k, v in SACN_PRESETS.items()},
        "dante": {k: v.summary() for k, v in DANTE_PRESETS.items()},
        "tcnet": {k: v.summary() for k, v in TCNET_PRESETS.items()},
        "prodjlink": {k: v.summary() for k, v in PRODJLINK_PRESETS.items()},
        "artnet": {k: v.summary() for k, v in ARTNET_PRESETS.items()},
    }


def get_preset(protocol: str, preset_name: str):
    """Look up a preset by protocol and name."""
    registries = {
        "ndi": NDI_PRESETS,
        "sacn": SACN_PRESETS,
        "dante": DANTE_PRESETS,
        "tcnet": TCNET_PRESETS,
        "prodjlink": PRODJLINK_PRESETS,
        "artnet": ARTNET_PRESETS,
    }
    registry = registries.get(protocol.lower())
    if registry is None:
        return None
    return registry.get(preset_name.lower())
