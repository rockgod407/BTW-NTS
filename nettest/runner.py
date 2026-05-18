"""Test runner - orchestrates all test modules."""
from __future__ import annotations

from typing import List, Optional, Set

from nettest.core.config import Config
from nettest.core.result import TestResult, TestSuite
from nettest.utils.output import (
    console,
    print_category_header,
    print_header,
    print_result,
    print_summary,
)

# Network test categories
NETWORK_CATEGORIES = {"http", "tcp", "udp", "icmp", "dns", "ntp", "security", "performance"}

# AV protocol categories
AV_CATEGORIES = {"ndi", "sacn", "dante", "tcnet", "prodjlink", "manet"}

# All available test categories
ALL_CATEGORIES = NETWORK_CATEGORIES | AV_CATEGORIES


def run_all(
    config: Config,
    categories: Optional[Set[str]] = None,
    verbose: bool = False,
) -> TestSuite:
    """
    Run the network test suite.

    Args:
        config: Test configuration
        categories: Set of categories to run (None = all network tests)
        verbose: Print detailed output
    """
    if categories is None:
        categories = NETWORK_CATEGORIES  # AV tests require explicit opt-in

    suite = TestSuite()
    print_header("Network Test Suite")

    # --- Standard Network Tests ---

    if "http" in categories and config.http_targets:
        print_category_header("http")
        from nettest.tests.test_http import run_http_tests

        results = run_http_tests(config.http_targets)
        _collect(suite, results, verbose)

    if "tcp" in categories and config.tcp_targets:
        print_category_header("tcp")
        from nettest.tests.test_tcp import run_tcp_tests

        results = run_tcp_tests(config.tcp_targets)
        _collect(suite, results, verbose)

    if "udp" in categories and config.udp_targets:
        print_category_header("udp")
        from nettest.tests.test_udp import run_udp_tests

        results = run_udp_tests(config.udp_targets)
        _collect(suite, results, verbose)

    if "icmp" in categories and config.icmp_targets:
        print_category_header("icmp")
        from nettest.tests.test_icmp import run_icmp_tests

        results = run_icmp_tests(config.icmp_targets)
        _collect(suite, results, verbose)

    if "dns" in categories and config.dns_targets:
        print_category_header("dns")
        from nettest.tests.test_dns import run_dns_tests

        results = run_dns_tests(config.dns_targets)
        _collect(suite, results, verbose)

    if "ntp" in categories and config.ntp_targets:
        print_category_header("ntp")
        from nettest.tests.test_ntp import run_ntp_tests

        results = run_ntp_tests(config.ntp_targets)
        _collect(suite, results, verbose)

    if "security" in categories and config.http_targets:
        print_category_header("security")
        from nettest.tests.test_security import run_security_tests

        results = run_security_tests(config.http_targets, config.security)
        _collect(suite, results, verbose)

    if "performance" in categories and config.http_targets:
        print_category_header("performance")
        console.print("[dim]Running performance tests (this may take a while)...[/]")
        from nettest.tests.test_performance import run_performance_tests

        results = run_performance_tests(config.http_targets, config.performance)
        _collect(suite, results, verbose)

    # --- AV Protocol Tests (discovery only in standard run) ---

    if "ndi" in categories:
        print_category_header("ndi")
        from nettest.tests.av.test_ndi import run_ndi_discovery_test

        results = run_ndi_discovery_test()
        _collect(suite, results, verbose)

    if "sacn" in categories:
        print_category_header("sacn")
        universes = config.av.get("sacn_universes", [1]) if hasattr(config, "av") else [1]
        from nettest.tests.av.test_sacn import discover_sacn_sources

        results = [discover_sacn_sources(universes)]
        _collect(suite, results, verbose)

    if "dante" in categories:
        print_category_header("dante")
        from nettest.tests.av.test_dante import run_dante_tests

        results = run_dante_tests()
        _collect(suite, results, verbose)

    if "tcnet" in categories:
        print_category_header("tcnet")
        from nettest.tests.av.test_tcnet import run_tcnet_tests

        results = run_tcnet_tests()
        _collect(suite, results, verbose)

    if "prodjlink" in categories:
        print_category_header("prodjlink")
        from nettest.tests.av.test_prodjlink import run_prodjlink_tests

        results = run_prodjlink_tests()
        _collect(suite, results, verbose)

    if "manet" in categories:
        print_category_header("manet / art-net")
        from nettest.tests.av.test_manet import run_manet_tests

        results = run_manet_tests()
        _collect(suite, results, verbose)

    # Summary
    print_summary(suite)
    return suite


def _collect(
    suite: TestSuite,
    results: List[TestResult],
    verbose: bool,
) -> None:
    """Add results to the suite and print them."""
    for result in results:
        suite.add(result)
        print_result(result)
        if verbose and result.details:
            for key, value in result.details.items():
                if key == "raw_output":
                    continue  # Skip verbose raw output unless needed
                console.print(f"        [dim]{key}: {value}[/]")
