"""TLS/SSL security and compliance tests."""
from __future__ import annotations

import socket
import ssl
import time
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse

from nettest.core.config import HttpTarget, SecurityConfig
from nettest.core.result import Status, TestResult


def run_security_tests(
    http_targets: List[HttpTarget], security_config: SecurityConfig
) -> List[TestResult]:
    """Run TLS/security tests against HTTPS targets."""
    results: List[TestResult] = []
    seen_hosts: set = set()

    for target in http_targets:
        parsed = urlparse(target.url)
        if parsed.scheme != "https":
            continue
        host = parsed.hostname
        port = parsed.port or 443

        if (host, port) in seen_hosts:
            continue
        seen_hosts.add((host, port))

        results.append(_test_tls_version(host, port, security_config))
        results.append(_test_certificate_expiry(host, port, security_config))
        results.append(_test_certificate_chain(host, port))
        results.append(_test_cipher_suites(host, port, security_config))
        results.append(_test_hostname_match(host, port))

    return results


def _test_tls_version(
    host: str, port: int, config: SecurityConfig
) -> TestResult:
    """Check that the server supports the minimum required TLS version."""
    name = f"TLS Version {host}:{port}"
    start = time.monotonic()

    min_versions = {
        "TLSv1.0": ssl.TLSVersion.TLSv1,
        "TLSv1.1": ssl.TLSVersion.TLSv1_1,
        "TLSv1.2": ssl.TLSVersion.TLSv1_2,
        "TLSv1.3": ssl.TLSVersion.TLSv1_3,
    }

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.load_default_certs()

        with socket.create_connection((host, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                version = ssock.version()
                elapsed = (time.monotonic() - start) * 1000

                # Check if version meets minimum
                actual_enum = _version_string_to_enum(version)
                required_enum = min_versions.get(config.min_tls_version)

                if actual_enum and required_enum and actual_enum >= required_enum:
                    return TestResult(
                        name=name,
                        category="security",
                        status=Status.PASS,
                        message=f"Using {version} (minimum: {config.min_tls_version})",
                        duration_ms=elapsed,
                        details={"tls_version": version, "host": host},
                    )
                else:
                    return TestResult(
                        name=name,
                        category="security",
                        status=Status.FAIL,
                        message=f"Using {version}, below minimum {config.min_tls_version}",
                        duration_ms=elapsed,
                        details={"tls_version": version, "host": host},
                    )

    except ssl.SSLError as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="security",
            status=Status.FAIL,
            message=f"TLS error: {e}",
            duration_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="security",
            status=Status.ERROR,
            message=f"Connection error: {e}",
            duration_ms=elapsed,
        )


def _test_certificate_expiry(
    host: str, port: int, config: SecurityConfig
) -> TestResult:
    """Check certificate expiration date."""
    name = f"Certificate Expiry {host}:{port}"
    start = time.monotonic()

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.load_default_certs()

        with socket.create_connection((host, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                elapsed = (time.monotonic() - start) * 1000

                if not cert:
                    return TestResult(
                        name=name,
                        category="security",
                        status=Status.FAIL,
                        message="No certificate returned",
                        duration_ms=elapsed,
                    )

                not_after = ssl.cert_time_to_seconds(cert["notAfter"])
                not_after_dt = datetime.fromtimestamp(not_after, tz=timezone.utc)
                now = datetime.now(tz=timezone.utc)
                days_left = (not_after_dt - now).days

                if days_left < 0:
                    status = Status.FAIL
                    msg = f"Certificate EXPIRED {abs(days_left)} days ago"
                elif days_left < config.check_certificate_expiry_days:
                    status = Status.WARN
                    msg = f"Certificate expires in {days_left} days (threshold: {config.check_certificate_expiry_days})"
                else:
                    status = Status.PASS
                    msg = f"Certificate valid for {days_left} days (expires {not_after_dt.date()})"

                return TestResult(
                    name=name,
                    category="security",
                    status=status,
                    message=msg,
                    duration_ms=elapsed,
                    details={
                        "host": host,
                        "expires": str(not_after_dt.date()),
                        "days_remaining": days_left,
                        "subject": dict(x[0] for x in cert.get("subject", ())),
                        "issuer": dict(x[0] for x in cert.get("issuer", ())),
                    },
                )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="security",
            status=Status.ERROR,
            message=f"Certificate check failed: {e}",
            duration_ms=elapsed,
        )


def _test_certificate_chain(host: str, port: int) -> TestResult:
    """Validate the full certificate chain."""
    name = f"Certificate Chain {host}:{port}"
    start = time.monotonic()

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_default_certs()

        with socket.create_connection((host, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                elapsed = (time.monotonic() - start) * 1000

                # If we got here, the chain validated successfully
                issuer = dict(x[0] for x in cert.get("issuer", ()))
                issuer_cn = issuer.get("commonName", "Unknown")

                return TestResult(
                    name=name,
                    category="security",
                    status=Status.PASS,
                    message=f"Chain valid, issued by: {issuer_cn}",
                    duration_ms=elapsed,
                    details={"host": host, "issuer": issuer},
                )

    except ssl.SSLCertVerificationError as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="security",
            status=Status.FAIL,
            message=f"Chain validation failed: {e}",
            duration_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="security",
            status=Status.ERROR,
            message=f"Chain check error: {e}",
            duration_ms=elapsed,
        )


def _test_cipher_suites(
    host: str, port: int, config: SecurityConfig
) -> TestResult:
    """Check the negotiated cipher suite for weak algorithms."""
    name = f"Cipher Suite {host}:{port}"
    start = time.monotonic()

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.load_default_certs()

        with socket.create_connection((host, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cipher = ssock.cipher()
                elapsed = (time.monotonic() - start) * 1000

                if not cipher:
                    return TestResult(
                        name=name,
                        category="security",
                        status=Status.WARN,
                        message="Could not determine cipher suite",
                        duration_ms=elapsed,
                    )

                cipher_name, tls_version, key_bits = cipher

                # Check for forbidden ciphers
                forbidden_found = [
                    f for f in config.forbidden_ciphers
                    if f.upper() in cipher_name.upper()
                ]

                if forbidden_found:
                    return TestResult(
                        name=name,
                        category="security",
                        status=Status.FAIL,
                        message=f"Weak cipher: {cipher_name} (contains: {', '.join(forbidden_found)})",
                        duration_ms=elapsed,
                        details={
                            "cipher": cipher_name,
                            "key_bits": key_bits,
                            "forbidden_matches": forbidden_found,
                        },
                    )

                return TestResult(
                    name=name,
                    category="security",
                    status=Status.PASS,
                    message=f"{cipher_name} ({key_bits}-bit)",
                    duration_ms=elapsed,
                    details={
                        "cipher": cipher_name,
                        "tls_version": tls_version,
                        "key_bits": key_bits,
                    },
                )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="security",
            status=Status.ERROR,
            message=f"Cipher check error: {e}",
            duration_ms=elapsed,
        )


def _test_hostname_match(host: str, port: int) -> TestResult:
    """Verify certificate hostname matches the target."""
    name = f"Hostname Match {host}:{port}"
    start = time.monotonic()

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.load_default_certs()

        with socket.create_connection((host, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                elapsed = (time.monotonic() - start) * 1000

                # check_hostname=True already validates this; if we get here it passed
                san = cert.get("subjectAltName", ())
                san_names = [name for typ, name in san if typ == "DNS"]

                return TestResult(
                    name=name,
                    category="security",
                    status=Status.PASS,
                    message=f"Hostname matches (SANs: {', '.join(san_names[:3])}{'...' if len(san_names) > 3 else ''})",
                    duration_ms=elapsed,
                    details={"host": host, "san_names": san_names},
                )

    except ssl.SSLCertVerificationError as e:
        elapsed = (time.monotonic() - start) * 1000
        if "hostname" in str(e).lower():
            return TestResult(
                name=name,
                category="security",
                status=Status.FAIL,
                message=f"Hostname mismatch: {e}",
                duration_ms=elapsed,
            )
        return TestResult(
            name=name,
            category="security",
            status=Status.FAIL,
            message=f"Certificate error: {e}",
            duration_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return TestResult(
            name=name,
            category="security",
            status=Status.ERROR,
            message=f"Hostname check error: {e}",
            duration_ms=elapsed,
        )


def _version_string_to_enum(version_str: str):
    """Convert TLS version string to ssl.TLSVersion enum."""
    mapping = {
        "TLSv1": ssl.TLSVersion.TLSv1,
        "TLSv1.1": ssl.TLSVersion.TLSv1_1,
        "TLSv1.2": ssl.TLSVersion.TLSv1_2,
        "TLSv1.3": ssl.TLSVersion.TLSv1_3,
    }
    return mapping.get(version_str)
