"""
Native ctypes wrapper for the NDI SDK (libndi.dylib).

Bypasses the ndi-python C extension (NDIlib.cpython-*.so) which crashes
on macOS due to a bundled Python dylib conflict. Instead, we load
libndi.dylib directly via ctypes and define the C structs/functions
ourselves.

This module exposes an API compatible with how the rest of nettest uses
NDI, so it's a drop-in replacement for `import NDIlib as ndi`.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import pathlib
import site
import sys
from typing import List, Optional


# =========================================================================
# Constants (from NDI SDK headers)
# =========================================================================

# FourCC video types
FOURCC_VIDEO_TYPE_UYVY = 0x59565955
FOURCC_VIDEO_TYPE_BGRA = 0x41524742
FOURCC_VIDEO_TYPE_BGRX = 0x58524742
FOURCC_VIDEO_TYPE_RGBA = 0x41424752
FOURCC_VIDEO_TYPE_RGBX = 0x58424752
FOURCC_VIDEO_TYPE_I420 = 0x30323449
FOURCC_VIDEO_TYPE_NV12 = 0x3231564E

# Recv bandwidth
RECV_BANDWIDTH_METADATA_ONLY = -10
RECV_BANDWIDTH_AUDIO_ONLY = 10
RECV_BANDWIDTH_LOWEST = 0
RECV_BANDWIDTH_HIGHEST = 100

# Recv color format
RECV_COLOR_FORMAT_BGRX_BGRA = 0
RECV_COLOR_FORMAT_UYVY_BGRA = 1
RECV_COLOR_FORMAT_RGBX_RGBA = 2
RECV_COLOR_FORMAT_UYVY_RGBA = 3
RECV_COLOR_FORMAT_FASTEST = 100
RECV_COLOR_FORMAT_BEST = 101

# Frame types (return value from recv_capture_v2)
FRAME_TYPE_NONE = 0
FRAME_TYPE_VIDEO = 1
FRAME_TYPE_AUDIO = 2
FRAME_TYPE_METADATA = 3
FRAME_TYPE_ERROR = 4
FRAME_TYPE_STATUS_CHANGE = 100

# Frame format types
FRAME_FORMAT_TYPE_INTERLEAVED = 0
FRAME_FORMAT_TYPE_PROGRESSIVE = 1
FRAME_FORMAT_TYPE_FIELD_0 = 2
FRAME_FORMAT_TYPE_FIELD_1 = 3

# Special timecode value: let NDI synthesize
SEND_TIMECODE_SYNTHESIZE = 0x7FFFFFFFFFFFFFFF  # INT64_MAX

# Bytes per pixel for common FourCC
_BPP = {
    FOURCC_VIDEO_TYPE_BGRX: 4,
    FOURCC_VIDEO_TYPE_BGRA: 4,
    FOURCC_VIDEO_TYPE_RGBA: 4,
    FOURCC_VIDEO_TYPE_RGBX: 4,
    FOURCC_VIDEO_TYPE_UYVY: 2,
    FOURCC_VIDEO_TYPE_I420: 1,  # planar, approximate
    FOURCC_VIDEO_TYPE_NV12: 1,  # planar, approximate
}


# =========================================================================
# C struct definitions (ARM64 macOS LP64 ABI)
# =========================================================================

class NDIlib_source_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_url_address", ctypes.c_char_p),
    ]

    @property
    def ndi_name(self) -> str:
        if self.p_ndi_name:
            return self.p_ndi_name.decode("utf-8", errors="replace")
        return ""


class NDIlib_find_create_t(ctypes.Structure):
    _fields_ = [
        ("show_local_sources", ctypes.c_bool),
        ("p_groups", ctypes.c_char_p),
        ("p_extra_ips", ctypes.c_char_p),
    ]


class NDIlib_send_create_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_groups", ctypes.c_char_p),
        ("clock_video", ctypes.c_bool),
        ("clock_audio", ctypes.c_bool),
    ]


class NDIlib_video_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("xres", ctypes.c_int32),
        ("yres", ctypes.c_int32),
        ("FourCC", ctypes.c_uint32),
        ("frame_rate_N", ctypes.c_int32),
        ("frame_rate_D", ctypes.c_int32),
        ("picture_aspect_ratio", ctypes.c_float),
        ("frame_format_type", ctypes.c_uint32),
        ("timecode", ctypes.c_int64),
        ("p_data", ctypes.POINTER(ctypes.c_uint8)),
        ("line_stride_in_bytes", ctypes.c_int32),
        ("p_metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_int64),
    ]


class NDIlib_audio_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_int32),
        ("no_channels", ctypes.c_int32),
        ("no_samples", ctypes.c_int32),
        ("timecode", ctypes.c_int64),
        ("p_data", ctypes.POINTER(ctypes.c_float)),
        ("channel_stride_in_bytes", ctypes.c_int32),
        ("p_metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_int64),
    ]


class NDIlib_recv_create_v3_t(ctypes.Structure):
    _fields_ = [
        ("source_to_connect_to", NDIlib_source_t),
        ("color_format", ctypes.c_int32),
        ("bandwidth", ctypes.c_int32),
        ("allow_video_fields", ctypes.c_bool),
        ("p_ndi_recv_name", ctypes.c_char_p),
    ]


# =========================================================================
# Library loader
# =========================================================================

_lib: Optional[ctypes.CDLL] = None
_load_error: Optional[str] = None


def _find_libndi() -> Optional[str]:
    """Search for libndi.dylib / libndi.so in known locations."""
    candidates: list[str] = []

    # 1. Environment variable (standard NDI SDK convention)
    for env_var in ("NDI_RUNTIME_DIR_V6", "NDI_RUNTIME_DIR_V5",
                    "NDI_RUNTIME_DIR", "NDI_SDK_DIR"):
        env_path = os.environ.get(env_var)
        if env_path:
            candidates.append(os.path.join(env_path, "lib", "macOS", "libndi.dylib"))
            candidates.append(os.path.join(env_path, "lib", "arm64-apple-macosx", "libndi.dylib"))
            candidates.append(os.path.join(env_path, "lib", "libndi.dylib"))
            candidates.append(os.path.join(env_path, "libndi.dylib"))

    # 2. Inside ndi-python pip package (it bundles libndi.dylib)
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        candidates.append(os.path.join(sp, "NDIlib", "libndi.dylib"))
        candidates.append(os.path.join(sp, "NDIlib", "libndi.so"))

    # Also check sys.path for virtualenvs
    for p in sys.path:
        if "site-packages" in p:
            candidates.append(os.path.join(p, "NDIlib", "libndi.dylib"))

    # 3. Common macOS install locations
    candidates.extend([
        "/usr/local/lib/libndi.dylib",
        "/opt/homebrew/lib/libndi.dylib",
        "/Library/NDI SDK for Apple/lib/macOS/libndi.dylib",
        "/Library/NDI SDK for Apple/lib/arm64-apple-macosx/libndi.dylib",
        os.path.expanduser("~/lib/libndi.dylib"),
    ])

    # 4. Linux paths
    candidates.extend([
        "/usr/lib/libndi.so",
        "/usr/lib/x86_64-linux-gnu/libndi.so",
        "/usr/local/lib/libndi.so",
    ])

    for path in candidates:
        if os.path.isfile(path):
            return path

    # 5. System library search as last resort
    found = ctypes.util.find_library("ndi")
    if found:
        return found

    return None


def _load_library() -> Optional[ctypes.CDLL]:
    """Load libndi and set up function prototypes."""
    global _lib, _load_error

    if _lib is not None:
        return _lib

    path = _find_libndi()
    if path is None:
        _load_error = (
            "libndi.dylib not found. Install one of:\n"
            "  pip3 install ndi-python   (provides the library)\n"
            "  — OR — NDI SDK from https://ndi.video/tools/ndi-sdk/"
        )
        return None

    try:
        lib = ctypes.CDLL(path)
    except OSError as e:
        _load_error = f"Failed to load {path}: {e}"
        return None

    # -- Set up function prototypes --

    # bool NDIlib_initialize(void)
    lib.NDIlib_initialize.restype = ctypes.c_bool
    lib.NDIlib_initialize.argtypes = []

    # void NDIlib_destroy(void)
    lib.NDIlib_destroy.restype = None
    lib.NDIlib_destroy.argtypes = []

    # const char* NDIlib_version(void)
    lib.NDIlib_version.restype = ctypes.c_char_p
    lib.NDIlib_version.argtypes = []

    # Find API
    lib.NDIlib_find_create_v2.restype = ctypes.c_void_p
    lib.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(NDIlib_find_create_t)]

    lib.NDIlib_find_destroy.restype = None
    lib.NDIlib_find_destroy.argtypes = [ctypes.c_void_p]

    lib.NDIlib_find_wait_for_sources.restype = ctypes.c_bool
    lib.NDIlib_find_wait_for_sources.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

    lib.NDIlib_find_get_current_sources.restype = ctypes.POINTER(NDIlib_source_t)
    lib.NDIlib_find_get_current_sources.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
    ]

    # Send API
    lib.NDIlib_send_create.restype = ctypes.c_void_p
    lib.NDIlib_send_create.argtypes = [ctypes.POINTER(NDIlib_send_create_t)]

    lib.NDIlib_send_destroy.restype = None
    lib.NDIlib_send_destroy.argtypes = [ctypes.c_void_p]

    lib.NDIlib_send_send_video_v2.restype = None
    lib.NDIlib_send_send_video_v2.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(NDIlib_video_frame_v2_t),
    ]

    lib.NDIlib_send_send_audio_v2.restype = None
    lib.NDIlib_send_send_audio_v2.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(NDIlib_audio_frame_v2_t),
    ]

    # Recv API
    lib.NDIlib_recv_create_v3.restype = ctypes.c_void_p
    lib.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(NDIlib_recv_create_v3_t)]

    lib.NDIlib_recv_destroy.restype = None
    lib.NDIlib_recv_destroy.argtypes = [ctypes.c_void_p]

    lib.NDIlib_recv_connect.restype = None
    lib.NDIlib_recv_connect.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(NDIlib_source_t),
    ]

    lib.NDIlib_recv_capture_v2.restype = ctypes.c_int32
    lib.NDIlib_recv_capture_v2.argtypes = [
        ctypes.c_void_p,                              # recv instance
        ctypes.POINTER(NDIlib_video_frame_v2_t),      # video out (can be NULL)
        ctypes.POINTER(NDIlib_audio_frame_v2_t),      # audio out (can be NULL)
        ctypes.c_void_p,                              # metadata out (NULL)
        ctypes.c_uint32,                              # timeout_in_ms
    ]

    lib.NDIlib_recv_free_video_v2.restype = None
    lib.NDIlib_recv_free_video_v2.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(NDIlib_video_frame_v2_t),
    ]

    _lib = lib
    return lib


# =========================================================================
# Public API — drop-in replacement for NDIlib import
# =========================================================================

def is_available() -> bool:
    """Check if the NDI native library can be loaded (without crashing)."""
    return _load_library() is not None


def get_load_error() -> Optional[str]:
    """Return the error message if library loading failed."""
    _load_library()  # Attempt load
    return _load_error


def initialize() -> bool:
    """Initialize the NDI runtime. Returns True on success."""
    lib = _load_library()
    if lib is None:
        return False
    try:
        return lib.NDIlib_initialize()
    except Exception:
        return False


def destroy():
    """Shut down the NDI runtime."""
    if _lib is not None:
        try:
            _lib.NDIlib_destroy()
        except Exception:
            pass


def version() -> str:
    """Return the NDI SDK version string."""
    lib = _load_library()
    if lib is None:
        return "unknown"
    try:
        v = lib.NDIlib_version()
        return v.decode("utf-8") if v else "unknown"
    except Exception:
        return "unknown"


def _extract_version_number(full_version: str) -> str:
    """Extract just the numeric version from the NDI SDK version string.

    e.g. "NDI SDK APPLE 19:46:26 Feb 10 2022 5.1.1" -> "5.1.1"
    """
    import re
    match = re.search(r'(\d+\.\d+\.\d+)', full_version)
    if match:
        return match.group(1)
    return full_version


# Expose as __version__ for doctor.py compatibility
try:
    _raw = version() if _find_libndi() else "0.0.0"
    __version__ = _extract_version_number(_raw)
except Exception:
    __version__ = "0.0.0"


# =========================================================================
# Find API
# =========================================================================

def find_create_v2():
    """Create a finder instance. Returns opaque handle."""
    lib = _load_library()
    if lib is None:
        return None
    create = NDIlib_find_create_t()
    create.show_local_sources = True
    create.p_groups = None
    create.p_extra_ips = None
    return lib.NDIlib_find_create_v2(ctypes.byref(create))


def find_wait_for_sources(finder_handle, timeout_ms: int = 5000) -> bool:
    """Wait for NDI sources to appear on the network."""
    if _lib is None or finder_handle is None:
        return False
    return _lib.NDIlib_find_wait_for_sources(finder_handle, ctypes.c_uint32(timeout_ms))


class NDISource:
    """Python wrapper for a discovered NDI source."""
    __slots__ = ("ndi_name", "url_address", "_c_source")

    def __init__(self, ndi_name: str, url_address: str, c_source: NDIlib_source_t):
        self.ndi_name = ndi_name
        self.url_address = url_address
        self._c_source = c_source

    def __repr__(self):
        return f"NDISource({self.ndi_name!r})"


def find_get_current_sources(finder_handle) -> List[NDISource]:
    """Get all currently discovered NDI sources."""
    if _lib is None or finder_handle is None:
        return []
    count = ctypes.c_uint32(0)
    sources_ptr = _lib.NDIlib_find_get_current_sources(
        finder_handle, ctypes.byref(count),
    )
    results = []
    for i in range(count.value):
        src = sources_ptr[i]
        name = src.p_ndi_name.decode("utf-8", errors="replace") if src.p_ndi_name else ""
        url = src.p_url_address.decode("utf-8", errors="replace") if src.p_url_address else ""
        # Make a copy of the C struct so it stays valid
        copy = NDIlib_source_t()
        copy.p_ndi_name = src.p_ndi_name
        copy.p_url_address = src.p_url_address
        results.append(NDISource(name, url, copy))
    return results


def find_destroy(finder_handle):
    """Destroy a finder instance."""
    if _lib is not None and finder_handle is not None:
        _lib.NDIlib_find_destroy(finder_handle)


# =========================================================================
# Send API
# =========================================================================

class SendCreate:
    """Configuration for creating an NDI sender."""
    def __init__(self):
        self.ndi_name: str = "NetTest"
        self.groups: Optional[str] = None
        self.clock_video: bool = True
        self.clock_audio: bool = True


# Track data buffers to prevent GC while NDI is using them
_send_data_refs: dict = {}


def send_create(config: SendCreate):
    """Create an NDI sender instance. Returns opaque handle."""
    lib = _load_library()
    if lib is None:
        return None
    create = NDIlib_send_create_t()
    create.p_ndi_name = config.ndi_name.encode("utf-8")
    create.p_groups = config.groups.encode("utf-8") if config.groups else None
    create.clock_video = config.clock_video
    create.clock_audio = config.clock_audio
    handle = lib.NDIlib_send_create(ctypes.byref(create))
    if handle:
        _send_data_refs[handle] = []
    return handle


def send_destroy(sender_handle):
    """Destroy an NDI sender instance."""
    if _lib is not None and sender_handle is not None:
        _lib.NDIlib_send_destroy(sender_handle)
        _send_data_refs.pop(sender_handle, None)


class VideoFrameV2:
    """Python-side video frame description for sending."""
    def __init__(self):
        self.xres: int = 1920
        self.yres: int = 1080
        self.FourCC: int = FOURCC_VIDEO_TYPE_BGRX
        self.frame_rate_N: int = 30000
        self.frame_rate_D: int = 1000
        self.picture_aspect_ratio: float = 0.0
        self.frame_format_type: int = FRAME_FORMAT_TYPE_PROGRESSIVE
        self.timecode: int = SEND_TIMECODE_SYNTHESIZE
        self.data: Optional[bytes] = None
        self.line_stride_in_bytes: int = 0
        self.metadata: Optional[str] = None
        self.timestamp: int = 0


def send_send_video_v2(sender_handle, frame: VideoFrameV2):
    """Send a video frame through NDI."""
    if _lib is None or sender_handle is None or frame.data is None:
        return

    # Create ctypes buffer from the data bytes
    data_len = len(frame.data)
    c_buf = (ctypes.c_uint8 * data_len).from_buffer_copy(frame.data)

    # Keep reference alive until next frame
    refs = _send_data_refs.get(sender_handle, [])
    refs.clear()
    refs.append(c_buf)

    stride = frame.line_stride_in_bytes
    if stride == 0:
        bpp = _BPP.get(frame.FourCC, 4)
        stride = frame.xres * bpp

    c_frame = NDIlib_video_frame_v2_t()
    c_frame.xres = frame.xres
    c_frame.yres = frame.yres
    c_frame.FourCC = frame.FourCC
    c_frame.frame_rate_N = frame.frame_rate_N
    c_frame.frame_rate_D = frame.frame_rate_D
    c_frame.picture_aspect_ratio = frame.picture_aspect_ratio
    c_frame.frame_format_type = frame.frame_format_type
    c_frame.timecode = frame.timecode
    c_frame.p_data = ctypes.cast(c_buf, ctypes.POINTER(ctypes.c_uint8))
    c_frame.line_stride_in_bytes = stride
    c_frame.p_metadata = frame.metadata.encode("utf-8") if frame.metadata else None
    c_frame.timestamp = frame.timestamp

    _lib.NDIlib_send_send_video_v2(sender_handle, ctypes.byref(c_frame))


class AudioFrameV2:
    """Python-side audio frame description for sending."""
    def __init__(self):
        self.sample_rate: int = 48000
        self.no_channels: int = 2
        self.no_samples: int = 0
        self.timecode: int = SEND_TIMECODE_SYNTHESIZE
        self.data: Optional[bytes] = None  # float32 interleaved
        self.channel_stride_in_bytes: int = 0
        self.metadata: Optional[str] = None
        self.timestamp: int = 0


def send_send_audio_v2(sender_handle, frame: AudioFrameV2):
    """Send an audio frame through NDI."""
    if _lib is None or sender_handle is None or frame.data is None:
        return

    data_len = len(frame.data)
    c_buf = (ctypes.c_uint8 * data_len).from_buffer_copy(frame.data)

    refs = _send_data_refs.get(sender_handle, [])
    refs.append(c_buf)

    c_frame = NDIlib_audio_frame_v2_t()
    c_frame.sample_rate = frame.sample_rate
    c_frame.no_channels = frame.no_channels
    c_frame.no_samples = frame.no_samples
    c_frame.timecode = frame.timecode
    c_frame.p_data = ctypes.cast(c_buf, ctypes.POINTER(ctypes.c_float))
    c_frame.channel_stride_in_bytes = frame.channel_stride_in_bytes
    c_frame.p_metadata = frame.metadata.encode("utf-8") if frame.metadata else None
    c_frame.timestamp = frame.timestamp

    _lib.NDIlib_send_send_audio_v2(sender_handle, ctypes.byref(c_frame))


# =========================================================================
# Recv API
# =========================================================================

class RecvCreateV3:
    """Configuration for creating an NDI receiver."""
    def __init__(self):
        self.color_format: int = RECV_COLOR_FORMAT_BGRX_BGRA
        self.bandwidth: int = RECV_BANDWIDTH_HIGHEST
        self.allow_video_fields: bool = True
        self.ndi_recv_name: Optional[str] = None


# Per-receiver state for captured frames
_recv_state: dict = {}


def recv_create_v3(config: RecvCreateV3):
    """Create an NDI receiver instance. Returns opaque handle."""
    lib = _load_library()
    if lib is None:
        return None
    create = NDIlib_recv_create_v3_t()
    # Leave source empty — we'll connect later
    create.source_to_connect_to.p_ndi_name = None
    create.source_to_connect_to.p_url_address = None
    create.color_format = config.color_format
    create.bandwidth = config.bandwidth
    create.allow_video_fields = config.allow_video_fields
    create.p_ndi_recv_name = (
        config.ndi_recv_name.encode("utf-8") if config.ndi_recv_name else None
    )
    handle = lib.NDIlib_recv_create_v3(ctypes.byref(create))
    if handle:
        _recv_state[handle] = {
            "video_frame": NDIlib_video_frame_v2_t(),
            "has_video": False,
        }
    return handle


def recv_destroy(receiver_handle):
    """Destroy an NDI receiver instance."""
    if _lib is not None and receiver_handle is not None:
        # Free any pending frame
        state = _recv_state.pop(receiver_handle, None)
        if state and state.get("has_video"):
            try:
                _lib.NDIlib_recv_free_video_v2(
                    receiver_handle, ctypes.byref(state["video_frame"]),
                )
            except Exception:
                pass
        _lib.NDIlib_recv_destroy(receiver_handle)


def recv_connect(receiver_handle, source: NDISource):
    """Connect a receiver to an NDI source."""
    if _lib is None or receiver_handle is None:
        return
    _lib.NDIlib_recv_connect(
        receiver_handle, ctypes.byref(source._c_source),
    )


def recv_capture_v2(receiver_handle, timeout_in_ms: int = 1000) -> int:
    """
    Capture a frame from the receiver.

    Returns a FRAME_TYPE_* constant indicating what was captured.
    If FRAME_TYPE_VIDEO, call recv_capture_v2_get_video_data() to get the pixels.
    """
    if _lib is None or receiver_handle is None:
        return FRAME_TYPE_NONE

    state = _recv_state.get(receiver_handle)
    if state is None:
        return FRAME_TYPE_NONE

    # Free previous video frame if we had one
    if state["has_video"]:
        _lib.NDIlib_recv_free_video_v2(
            receiver_handle, ctypes.byref(state["video_frame"]),
        )
        state["has_video"] = False

    # Reset the frame struct
    state["video_frame"] = NDIlib_video_frame_v2_t()

    frame_type = _lib.NDIlib_recv_capture_v2(
        receiver_handle,
        ctypes.byref(state["video_frame"]),
        None,  # audio
        None,  # metadata
        ctypes.c_uint32(timeout_in_ms),
    )

    if frame_type == FRAME_TYPE_VIDEO:
        state["has_video"] = True

    return frame_type


def recv_capture_v2_get_video_data(receiver_handle) -> Optional[bytes]:
    """
    Get the pixel data from the most recently captured video frame.

    Returns bytes or None. The data is copied — the NDI buffer is freed
    on the next recv_capture_v2 call or recv_destroy.
    """
    state = _recv_state.get(receiver_handle)
    if state is None or not state["has_video"]:
        return None

    vf = state["video_frame"]
    if not vf.p_data:
        return None

    # Calculate data size
    stride = vf.line_stride_in_bytes
    if stride <= 0:
        bpp = _BPP.get(vf.FourCC, 4)
        stride = vf.xres * bpp

    data_size = stride * vf.yres
    if data_size <= 0:
        return None

    try:
        return ctypes.string_at(vf.p_data, data_size)
    except Exception:
        return None


def recv_get_performance(receiver_handle):
    """
    Get receiver performance stats.

    Returns (total_video_frames, dropped_video_frames) or (0, 0) on error.
    """
    if _lib is None or receiver_handle is None:
        return (0, 0)

    # NDIlib_recv_performance_t has: video_frames, audio_frames, metadata_frames (all int64)
    class PerfStruct(ctypes.Structure):
        _fields_ = [
            ("video_frames", ctypes.c_int64),
            ("audio_frames", ctypes.c_int64),
            ("metadata_frames", ctypes.c_int64),
        ]

    total = PerfStruct()
    dropped = PerfStruct()

    try:
        _lib.NDIlib_recv_get_performance(
            receiver_handle, ctypes.byref(total), ctypes.byref(dropped),
        )
        return (total.video_frames, dropped.video_frames)
    except Exception:
        return (0, 0)
