"""Configuration loading and validation."""
from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class HttpTarget:
    url: str
    expected_status: int = 200
    timeout: int = 10
    follow_redirects: bool = True
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class TcpTarget:
    host: str
    ports: List[int] = field(default_factory=lambda: [80, 443])
    timeout: int = 5


@dataclass
class UdpTarget:
    host: str
    port: int = 53
    timeout: int = 5


@dataclass
class IcmpTarget:
    host: str
    count: int = 5
    timeout: int = 2


@dataclass
class DnsTarget:
    domain: str
    record_types: List[str] = field(default_factory=lambda: ["A"])
    nameserver: str = "8.8.8.8"
    timeout: int = 5


@dataclass
class NtpTarget:
    server: str = "pool.ntp.org"
    max_offset: float = 1.0


@dataclass
class PerformanceConfig:
    concurrent_connections: int = 10
    duration_seconds: int = 10
    requests_per_test: int = 50


@dataclass
class SecurityConfig:
    min_tls_version: str = "TLSv1.2"
    check_certificate_expiry_days: int = 30
    forbidden_ciphers: List[str] = field(
        default_factory=lambda: ["RC4", "DES", "MD5"]
    )


@dataclass
class Config:
    http_targets: List[HttpTarget] = field(default_factory=list)
    tcp_targets: List[TcpTarget] = field(default_factory=list)
    udp_targets: List[UdpTarget] = field(default_factory=list)
    icmp_targets: List[IcmpTarget] = field(default_factory=list)
    dns_targets: List[DnsTarget] = field(default_factory=list)
    ntp_targets: List[NtpTarget] = field(default_factory=list)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)


def load_config(path: Optional[str] = None) -> Config:
    """Load configuration from a YAML file, or return defaults."""
    if path is None:
        # Try common locations
        for candidate in ["config.yaml", "config.yml", "nettest.yaml"]:
            if Path(candidate).exists():
                path = candidate
                break

    if path is None:
        return _default_config()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    return _parse_config(raw)


def _default_config() -> Config:
    """Return a sensible default config for quick testing."""
    return Config(
        http_targets=[
            HttpTarget(url="https://httpbin.org/get", expected_status=200),
            HttpTarget(url="https://example.com", expected_status=200),
        ],
        tcp_targets=[
            TcpTarget(host="example.com", ports=[80, 443]),
        ],
        udp_targets=[
            UdpTarget(host="dns.google", port=53),
        ],
        icmp_targets=[
            IcmpTarget(host="8.8.8.8", count=5),
            IcmpTarget(host="1.1.1.1", count=5),
        ],
        dns_targets=[
            DnsTarget(
                domain="example.com",
                record_types=["A", "AAAA", "MX", "TXT"],
            ),
        ],
        ntp_targets=[
            NtpTarget(server="pool.ntp.org"),
        ],
    )


def _parse_config(raw: Dict[str, Any]) -> Config:
    """Parse raw YAML dict into typed Config."""
    targets = raw.get("targets", {})

    http = [HttpTarget(**t) for t in targets.get("http", [])]
    tcp = [TcpTarget(**t) for t in targets.get("tcp", [])]
    udp = [UdpTarget(**t) for t in targets.get("udp", [])]
    icmp = [IcmpTarget(**t) for t in targets.get("icmp", [])]
    dns = [DnsTarget(**t) for t in targets.get("dns", [])]
    ntp = [NtpTarget(**t) for t in targets.get("ntp", [])]

    perf_raw = raw.get("performance", {})
    perf = PerformanceConfig(**perf_raw) if perf_raw else PerformanceConfig()

    sec_raw = raw.get("security", {})
    sec = SecurityConfig(**sec_raw) if sec_raw else SecurityConfig()

    return Config(
        http_targets=http,
        tcp_targets=tcp,
        udp_targets=udp,
        icmp_targets=icmp,
        dns_targets=dns,
        ntp_targets=ntp,
        performance=perf,
        security=sec,
    )
