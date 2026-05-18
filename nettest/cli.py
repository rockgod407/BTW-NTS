"""CLI entry point for the network test suite."""
from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich import box

from nettest.core.config import load_config
from nettest.runner import ALL_CATEGORIES, NETWORK_CATEGORIES, AV_CATEGORIES, run_all

console = Console()


def _format_duration(seconds: int) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m{s}s" if s else f"{m}m"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h{m}m" if m else f"{h}h"


# ---------------------------------------------------------------------------
# Main CLI group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option(
    "-c", "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to YAML config file",
)
@click.option(
    "-t", "--test",
    "categories",
    multiple=True,
    type=click.Choice(sorted(ALL_CATEGORIES), case_sensitive=False),
    help="Test categories to run (repeatable; default: all network tests)",
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    default=False,
    help="Show detailed test output",
)
@click.option(
    "--quick",
    is_flag=True,
    default=False,
    help="Skip performance tests (faster)",
)
@click.option(
    "-o", "--output",
    "output_path",
    default=None,
    help="Export report to file (auto-detects format from extension: .json, .csv, .html)",
)
@click.pass_context
def main(ctx, config_path, categories, verbose, quick, output_path):
    """
    nettest - Comprehensive network & AV protocol test suite.

    Run connectivity, performance, and security tests against network
    targets. Includes long-form AV protocol testing for NDI, Dante,
    sACN, TCNet, Pro DJ Link, and MA-Net/Art-Net.

    \b
    Examples:
        nettest                            # Run all network tests with defaults
        nettest -c config.yaml             # Use custom config
        nettest -t http -t dns             # Run only HTTP and DNS tests
        nettest -t ndi -t sacn             # Run AV protocol discovery
        nettest --quick                    # Skip performance tests

    \b
    AV Long-form commands:
        nettest ndi --source "My Camera" --duration 1h
        nettest sacn --universes 1,2,3 --duration 30m
        nettest av-discover                # Discover all AV devices
    """
    if ctx.invoked_subcommand is not None:
        return

    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[bold red]Error loading config:[/] {e}")
        sys.exit(1)

    cats = set(categories) if categories else None

    if quick and cats is None:
        cats = NETWORK_CATEGORIES - {"performance"}
    elif quick and cats:
        cats.discard("performance")

    suite = run_all(config, categories=cats, verbose=verbose)

    # Export report if requested
    if output_path:
        _export_report(suite, output_path)

    sys.exit(0 if suite.all_passed else 1)


# ---------------------------------------------------------------------------
# Report export helper
# ---------------------------------------------------------------------------

def _export_report(suite, output_path: str) -> None:
    """Export the test suite to the given file path (json/csv/html)."""
    from nettest.utils.report import export_json, export_csv, export_html

    ext = output_path.rsplit(".", 1)[-1].lower() if "." in output_path else "json"

    if ext == "csv":
        path = export_csv(suite, output_path)
    elif ext == "html":
        path = export_html(suite, output_path)
    else:
        path = export_json(suite, output_path)

    console.print(f"\n[bold green]Report saved:[/] {path}")


# ---------------------------------------------------------------------------
# AV Discovery
# ---------------------------------------------------------------------------

@main.command("av-discover")
@click.option("--timeout", default=5, help="Discovery timeout in seconds (default: 5)")
def av_discover(timeout):
    """Discover all AV protocol devices on the network."""
    from nettest.utils.output import print_header, print_result, print_category_header

    print_header("AV Protocol Discovery")

    # NDI
    print_category_header("ndi")
    from nettest.tests.av.test_ndi import run_ndi_discovery_test
    for r in run_ndi_discovery_test(timeout):
        print_result(r)

    # sACN
    print_category_header("sacn")
    from nettest.tests.av.test_sacn import discover_sacn_sources
    print_result(discover_sacn_sources([1, 2, 3, 4, 5], listen_seconds=timeout))

    # Dante
    print_category_header("dante")
    from nettest.tests.av.test_dante import run_dante_tests
    for r in run_dante_tests(timeout):
        print_result(r)

    # TCNet
    print_category_header("tcnet")
    from nettest.tests.av.test_tcnet import run_tcnet_tests
    for r in run_tcnet_tests(timeout):
        print_result(r)

    # Pro DJ Link
    print_category_header("pro dj link")
    from nettest.tests.av.test_prodjlink import run_prodjlink_tests
    for r in run_prodjlink_tests(timeout):
        print_result(r)

    # MA-Net / Art-Net
    print_category_header("ma-net / art-net")
    from nettest.tests.av.test_manet import run_manet_tests
    for r in run_manet_tests(timeout):
        print_result(r)


# ---------------------------------------------------------------------------
# NDI long-form
# ---------------------------------------------------------------------------

def _parse_duration(value: str) -> int:
    """Parse a duration string like '5m', '1h', '2h30m', '90s', '300'."""
    value = value.strip().lower()
    if value.isdigit():
        return int(value)

    total = 0
    current = ""
    for ch in value:
        if ch.isdigit():
            current += ch
        elif ch == "h":
            total += int(current) * 3600 if current else 0
            current = ""
        elif ch == "m":
            total += int(current) * 60 if current else 0
            current = ""
        elif ch == "s":
            total += int(current) if current else 0
            current = ""

    if current:
        total += int(current)

    return total if total > 0 else 300


class DurationType(click.ParamType):
    name = "duration"

    def convert(self, value, param, ctx):
        try:
            return _parse_duration(value)
        except (ValueError, TypeError):
            self.fail(f"'{value}' is not a valid duration. Use e.g. '5m', '1h', '2h30m', '300'", param, ctx)


DURATION = DurationType()


@main.command()
@click.option("--source", required=True, help="NDI source name (or partial match)")
@click.option("--duration", default="5m", type=DURATION, help="Test duration (e.g. 5m, 1h, 2h30m)")
@click.option("--fps", default=None, type=float, help="Expected frame rate (e.g. 29.97, 59.94)")
@click.option("--width", default=None, type=int, help="Expected video width")
@click.option("--height", default=None, type=int, help="Expected video height")
@click.option("--max-drops", default=0.1, type=float, help="Max acceptable drop rate %% (default: 0.1)")
@click.option("--snapshot-interval", default=10, type=int, help="Seconds between stat snapshots (default: 10)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Show detailed output")
def ndi(source, duration, fps, width, height, max_drops, snapshot_interval, verbose):
    """
    Run a long-form NDI stream stability test.

    Connects to an NDI source and monitors frame delivery, drops,
    timing consistency, and bandwidth over the test duration.

    \b
    Examples:
        nettest ndi --source "My Camera" --duration 5m
        nettest ndi --source "OBS" --duration 2h --fps 59.94
        nettest ndi --source "PTZ" --duration 30m --width 1920 --height 1080
    """
    from nettest.tests.av.test_ndi import run_ndi_longform_test
    from nettest.utils.output import print_header, print_result

    print_header(f"NDI Long-form Test: {source} ({_format_duration(duration)})")

    def _on_snapshot(snap):
        elapsed = snap.get("elapsed_s", 0)
        frames = snap.get("total_frames", 0)
        drops = snap.get("dropped_frames", 0)
        drop_pct = snap.get("drop_rate_pct", 0)
        bw = snap.get("avg_bandwidth_mbps", 0)
        console.print(
            f"  [dim][{_format_duration(int(elapsed))}] "
            f"frames={frames} drops={drops} ({drop_pct:.4f}%) "
            f"bw={bw:.1f}Mbps[/]"
        )

    results = run_ndi_longform_test(
        source_name=source,
        duration_seconds=duration,
        expected_width=width,
        expected_height=height,
        expected_fps=fps,
        snapshot_interval=snapshot_interval,
        max_drop_rate=max_drops,
        on_snapshot=_on_snapshot,
    )

    console.print()
    for r in results:
        print_result(r)
        if verbose and r.details:
            for key, value in r.details.items():
                console.print(f"        [dim]{key}: {value}[/]")

    # Final verdict
    final = results[-1] if results else None
    if final and final.status == Status.PASS:
        console.print(f"\n[bold green]NDI stream STABLE over {_format_duration(duration)}[/]\n")
    elif final and final.status == Status.WARN:
        console.print(f"\n[bold yellow]NDI stream MARGINAL over {_format_duration(duration)}[/]\n")
    else:
        console.print(f"\n[bold red]NDI stream UNSTABLE[/]\n")
        sys.exit(1)


# Need Status for the verdict check above
from nettest.core.result import Status


# ---------------------------------------------------------------------------
# sACN long-form
# ---------------------------------------------------------------------------

@main.command()
@click.option("--universes", required=True, help="Comma-separated universe numbers (e.g. 1,2,3)")
@click.option("--duration", default="5m", type=DURATION, help="Test duration (e.g. 5m, 1h)")
@click.option("--max-drops", default=0.1, type=float, help="Max acceptable drop rate %%")
@click.option("--snapshot-interval", default=10, type=int, help="Seconds between snapshots")
@click.option("-v", "--verbose", is_flag=True, default=False)
def sacn(universes, duration, max_drops, snapshot_interval, verbose):
    """
    Run a long-form sACN (E1.31) stability test.

    Monitors DMX data on specified universes, tracking sequence numbers
    and packet delivery over time.

    \b
    Examples:
        nettest sacn --universes 1,2,3 --duration 30m
        nettest sacn --universes 1 --duration 2h
    """
    from nettest.tests.av.test_sacn import run_sacn_longform_test
    from nettest.utils.output import print_header, print_result

    universe_list = [int(u.strip()) for u in universes.split(",")]
    print_header(f"sACN Long-form Test: Universes {universe_list} ({_format_duration(duration)})")

    def _on_snapshot(snap):
        elapsed = snap.get("elapsed_s", 0)
        frames = snap.get("total_frames", 0)
        drops = snap.get("dropped_frames", 0)
        console.print(f"  [dim][{_format_duration(int(elapsed))}] packets={frames} drops={drops}[/]")

    results = run_sacn_longform_test(
        universes=universe_list,
        duration_seconds=duration,
        snapshot_interval=snapshot_interval,
        max_drop_rate=max_drops,
        on_snapshot=_on_snapshot,
    )

    console.print()
    for r in results:
        print_result(r)
        if verbose and r.details:
            for key, value in r.details.items():
                console.print(f"        [dim]{key}: {value}[/]")


# ---------------------------------------------------------------------------
# Dante
# ---------------------------------------------------------------------------

@main.command()
@click.option("--ptp-duration", default=30, type=int, help="PTP sync monitoring duration in seconds")
@click.option("--timeout", default=5, type=int, help="Discovery timeout")
@click.option("-v", "--verbose", is_flag=True, default=False)
def dante(ptp_duration, timeout, verbose):
    """
    Run Dante network tests (discovery + PTP sync monitoring).

    \b
    Examples:
        nettest dante
        nettest dante --ptp-duration 60
    """
    from nettest.tests.av.test_dante import discover_dante_devices, test_ptp_sync
    from nettest.utils.output import print_header, print_result, print_category_header

    print_header("Dante Network Tests")

    print_category_header("discovery")
    disc = discover_dante_devices(timeout)
    print_result(disc)

    print_category_header("ptp clock sync")
    console.print(f"[dim]Monitoring PTP for {ptp_duration}s...[/]")
    ptp = test_ptp_sync(duration_seconds=ptp_duration)
    print_result(ptp)
    if verbose and ptp.details:
        for key, value in ptp.details.items():
            console.print(f"        [dim]{key}: {value}[/]")


# ---------------------------------------------------------------------------
# Standard utility commands
# ---------------------------------------------------------------------------

@main.command()
@click.option("--host", required=True, help="Host to scan")
@click.option("--start", "port_start", default=1, help="Start port (default: 1)")
@click.option("--end", "port_end", default=1024, help="End port (default: 1024)")
@click.option("--timeout", default=1, help="Timeout per port in seconds (default: 1)")
def scan(host, port_start, port_end, timeout):
    """Scan a range of TCP ports on a host."""
    from nettest.tests.test_tcp import scan_ports
    from nettest.utils.output import print_header, print_result

    print_header(f"Port Scan: {host} ({port_start}-{port_end})")
    console.print(f"[dim]Scanning {port_end - port_start + 1} ports...[/]\n")

    results = scan_ports(host, range(port_start, port_end + 1), timeout)

    open_ports = []
    for result in results:
        if result.status == Status.PASS:
            print_result(result)
            open_ports.append(result.details.get("port"))

    console.print(
        f"\n[bold]Found {len(open_ports)} open port(s)[/]: "
        f"{', '.join(map(str, open_ports)) if open_ports else 'none'}"
    )


@main.command()
@click.option("--host", required=True, help="Host to traceroute")
@click.option("--max-hops", default=30, help="Maximum hops (default: 30)")
def traceroute(host, max_hops):
    """Run a traceroute to a host."""
    from nettest.tests.test_icmp import run_traceroute
    from nettest.utils.output import print_header, print_result

    print_header(f"Traceroute: {host}")
    result = run_traceroute(host, max_hops)
    print_result(result)

    if result.details.get("hops"):
        console.print()
        for hop in result.details["hops"]:
            console.print(f"  {hop}")


@main.command()
@click.option("--domain", required=True, help="Domain to look up")
@click.option(
    "--type", "record_type",
    default="A",
    type=click.Choice(["A", "AAAA", "MX", "CNAME", "TXT", "NS", "SOA", "PTR"]),
    help="DNS record type (default: A)",
)
@click.option("--nameserver", default="8.8.8.8", help="DNS nameserver (default: 8.8.8.8)")
def lookup(domain, record_type, nameserver):
    """Perform a DNS lookup."""
    from nettest.core.config import DnsTarget
    from nettest.tests.test_dns import run_dns_tests
    from nettest.utils.output import print_header, print_result

    print_header(f"DNS Lookup: {domain} ({record_type})")

    target = DnsTarget(domain=domain, record_types=[record_type], nameserver=nameserver)
    results = run_dns_tests([target])
    for r in results:
        print_result(r)


# ---------------------------------------------------------------------------
# Presets listing
# ---------------------------------------------------------------------------

@main.command("presets")
@click.option(
    "--protocol",
    type=click.Choice(["ndi", "sacn", "dante", "tcnet", "prodjlink", "artnet", "all"]),
    default="all",
    help="Filter by protocol",
)
@click.option("--json-out", is_flag=True, default=False, help="Output as JSON")
def presets(protocol, json_out):
    """
    List all available signal presets and their bandwidth requirements.

    \b
    Examples:
        nettest presets                  # Show all presets
        nettest presets --protocol ndi   # NDI presets only
        nettest presets --protocol dante # Dante presets only
        nettest presets --json-out       # Machine-readable JSON
    """
    from nettest.tests.av.presets import (
        NDI_PRESETS, NDI_STRESS_PROFILES,
        SACN_PRESETS, DANTE_PRESETS,
        TCNET_PRESETS, PRODJLINK_PRESETS, ARTNET_PRESETS,
    )
    from nettest.utils.output import print_header

    if json_out:
        import json
        from nettest.tests.av.presets import list_all_presets
        data = list_all_presets()
        if protocol != "all":
            data = {k: v for k, v in data.items() if protocol in k}
        console.print(json.dumps(data, indent=2))
        return

    print_header("AV Protocol Presets & Bandwidth Calculator")

    # NDI
    if protocol in ("all", "ndi"):
        table = Table(
            title="[bold cyan]NDI Signal Presets[/]",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Preset", style="cyan", min_width=18)
        table.add_column("Resolution", min_width=14)
        table.add_column("Codec", min_width=10)
        table.add_column("Color")
        table.add_column("Video Mbps", justify="right", style="yellow")
        table.add_column("Total Mbps", justify="right", style="bold yellow")
        table.add_column("Description", max_width=45)

        for name, p in NDI_PRESETS.items():
            res = f"{p.width}x{p.height}{'i' if p.interlaced else 'p'}{p.fps}"
            table.add_row(
                name, res, p.codec.value,
                f"{p.chroma_subsampling} {p.color_depth_bits}b",
                f"{p.video_bandwidth_mbps:.0f}",
                f"{p.total_bandwidth_mbps:.0f}",
                p.description,
            )
        console.print(table)
        console.print()

        # Stress profiles
        stress_table = Table(
            title="[bold cyan]NDI Stress Test Profiles[/]",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        stress_table.add_column("Profile", style="cyan")
        stress_table.add_column("Streams", justify="right")
        stress_table.add_column("Total Mbps", justify="right", style="bold yellow")
        stress_table.add_column("Description", max_width=55)

        for name, sp in NDI_STRESS_PROFILES.items():
            stress_table.add_row(
                name, str(len(sp.streams)),
                f"{sp.total_bandwidth_mbps:.0f}",
                sp.description,
            )
        console.print(stress_table)
        console.print()

    # sACN
    if protocol in ("all", "sacn"):
        table = Table(
            title="[bold cyan]sACN / E1.31 Presets[/]",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Preset", style="cyan", min_width=16)
        table.add_column("Universes", justify="right")
        table.add_column("Channels", justify="right")
        table.add_column("~Fixtures", justify="right")
        table.add_column("Refresh Hz", justify="right")
        table.add_column("Mbps", justify="right", style="bold yellow")
        table.add_column("Pkts/s", justify="right")
        table.add_column("Description", max_width=50)

        for name, p in SACN_PRESETS.items():
            table.add_row(
                name, str(p.universes),
                f"{p.total_channels:,}", str(p.total_fixtures_approx),
                f"{p.refresh_rate_hz:.0f}", f"{p.bandwidth_mbps:.1f}",
                f"{p.packets_per_second:,.0f}",
                p.description,
            )
        console.print(table)
        console.print()

    # Dante
    if protocol in ("all", "dante"):
        table = Table(
            title="[bold cyan]Dante Audio Presets[/]",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Preset", style="cyan", min_width=20)
        table.add_column("Channels", justify="right")
        table.add_column("Sample Rate", justify="right")
        table.add_column("Bit Depth", justify="right")
        table.add_column("Latency", justify="right")
        table.add_column("Redundant", justify="center")
        table.add_column("Mbps", justify="right", style="bold yellow")
        table.add_column("Pkts/s", justify="right")
        table.add_column("Description", max_width=45)

        for name, p in DANTE_PRESETS.items():
            table.add_row(
                name, str(p.channel_count),
                f"{p.sample_rate/1000:.0f}kHz", str(p.bit_depth),
                f"{p.latency_ms}ms",
                "Yes" if p.redundancy else "",
                f"{p.total_bandwidth_mbps:.1f}",
                f"{p.packets_per_second:,.0f}",
                p.description,
            )
        console.print(table)
        console.print()

    # TCNet
    if protocol in ("all", "tcnet"):
        table = Table(
            title="[bold cyan]TCNet Presets[/]",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Preset", style="cyan")
        table.add_column("Nodes", justify="right")
        table.add_column("TC FPS", justify="right")
        table.add_column("Layers", justify="right")
        table.add_column("Mbps", justify="right", style="bold yellow")
        table.add_column("Description", max_width=50)

        for name, p in TCNET_PRESETS.items():
            table.add_row(
                name, str(p.node_count), f"{p.timecode_fps:.0f}",
                str(p.layers), f"{p.bandwidth_mbps:.3f}",
                p.description,
            )
        console.print(table)
        console.print()

    # Pro DJ Link
    if protocol in ("all", "prodjlink"):
        table = Table(
            title="[bold cyan]Pro DJ Link Presets[/]",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Preset", style="cyan")
        table.add_column("Players", justify="right")
        table.add_column("Mixers", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Mbps", justify="right", style="bold yellow")
        table.add_column("Description", max_width=50)

        for name, p in PRODJLINK_PRESETS.items():
            table.add_row(
                name, str(p.player_count), str(p.mixer_count),
                str(p.total_devices), f"{p.bandwidth_mbps:.4f}",
                p.description,
            )
        console.print(table)
        console.print()

    # Art-Net
    if protocol in ("all", "artnet"):
        table = Table(
            title="[bold cyan]Art-Net / MA-Net Presets[/]",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Preset", style="cyan")
        table.add_column("Universes", justify="right")
        table.add_column("Refresh Hz", justify="right")
        table.add_column("Mbps", justify="right", style="bold yellow")
        table.add_column("Description", max_width=50)

        for name, p in ARTNET_PRESETS.items():
            table.add_row(
                name, str(p.universes), f"{p.refresh_rate_hz:.0f}",
                f"{p.bandwidth_mbps:.1f}", p.description,
            )
        console.print(table)
        console.print()


# ---------------------------------------------------------------------------
# Signal generators / stress tests
# ---------------------------------------------------------------------------

@main.command("generate")
@click.option(
    "--protocol",
    type=click.Choice(["ndi", "sacn", "dante"]),
    required=True,
    help="Protocol to generate",
)
@click.option("--preset", required=True, help="Preset name (use 'nettest presets' to list)")
@click.option("--duration", default="5m", type=DURATION, help="Duration (e.g. 5m, 1h)")
@click.option("--name", "source_name", default=None, help="Source name (NDI only)")
@click.option("--pattern", default="chase", help="DMX pattern: chase, full, random, ramp (sACN only)")
@click.option("-v", "--verbose", is_flag=True, default=False)
def generate(protocol, preset, duration, source_name, pattern, verbose):
    """
    Generate test traffic using a protocol preset.

    Emits real protocol data (NDI video, sACN DMX, or Dante-equivalent
    UDP bandwidth) onto the network for stress testing.

    \b
    WARNING: This generates real network traffic. Only use on networks
    you control.

    \b
    Examples:
        nettest generate --protocol ndi --preset 1080p60 --duration 10m
        nettest generate --protocol sacn --preset arena --duration 1h
        nettest generate --protocol dante --preset concert-foh --duration 30m
    """
    from nettest.tests.av.presets import NDI_PRESETS, SACN_PRESETS, DANTE_PRESETS
    from nettest.tests.av.generators import (
        generate_ndi_stream,
        generate_sacn_stream,
        generate_dante_bandwidth_test,
    )
    from nettest.utils.output import print_header, print_result

    if protocol == "ndi":
        p = NDI_PRESETS.get(preset)
        if not p:
            console.print(f"[bold red]Unknown NDI preset '{preset}'.[/] Use 'nettest presets --protocol ndi' to list.")
            sys.exit(1)
        print_header(f"NDI Generator: {p.name} ({_format_duration(duration)})")

        def _snap(s):
            console.print(
                f"  [dim][{_format_duration(int(s.get('elapsed_s', 0)))}] "
                f"frames={s.get('frames_sent', 0)} fps={s.get('avg_fps', 0):.1f} "
                f"bw={s.get('bandwidth_mbps', 0):.1f}Mbps[/]"
            )

        results = generate_ndi_stream(
            preset=p,
            source_name=source_name or f"NetTest {p.name}",
            duration_seconds=duration,
            on_snapshot=_snap,
        )

    elif protocol == "sacn":
        p = SACN_PRESETS.get(preset)
        if not p:
            console.print(f"[bold red]Unknown sACN preset '{preset}'.[/] Use 'nettest presets --protocol sacn' to list.")
            sys.exit(1)
        print_header(f"sACN Generator: {p.name} ({_format_duration(duration)})")

        def _snap(s):
            console.print(
                f"  [dim][{_format_duration(int(s.get('elapsed_s', 0)))}] "
                f"pkts={s.get('packets_sent', 0)} pps={s.get('pps', 0):.0f}[/]"
            )

        results = generate_sacn_stream(
            preset=p,
            duration_seconds=duration,
            pattern=pattern,
            on_snapshot=_snap,
        )

    elif protocol == "dante":
        p = DANTE_PRESETS.get(preset)
        if not p:
            console.print(f"[bold red]Unknown Dante preset '{preset}'.[/] Use 'nettest presets --protocol dante' to list.")
            sys.exit(1)
        print_header(f"Dante BW Simulator: {p.name} ({_format_duration(duration)})")

        def _snap(s):
            console.print(
                f"  [dim][{_format_duration(int(s.get('elapsed_s', 0)))}] "
                f"pkts={s.get('packets_sent', 0)} "
                f"actual={s.get('actual_mbps', 0):.1f}/{s.get('target_mbps', 0):.1f}Mbps[/]"
            )

        results = generate_dante_bandwidth_test(
            preset=p,
            duration_seconds=duration,
            on_snapshot=_snap,
        )

    console.print()
    for r in results:
        print_result(r)
        if verbose and r.details:
            for key, value in r.details.items():
                console.print(f"        [dim]{key}: {value}[/]")


@main.command("stress")
@click.option(
    "--profile",
    required=True,
    help="NDI stress profile name (use 'nettest presets --protocol ndi' to list)",
)
@click.option("--duration", default="5m", type=DURATION, help="Duration (e.g. 5m, 1h)")
@click.option("-v", "--verbose", is_flag=True, default=False)
def stress(profile, duration, verbose):
    """
    Run a multi-stream NDI stress test using a named profile.

    Launches multiple simultaneous NDI streams to push the network
    to its limits.

    \b
    Examples:
        nettest stress --profile light --duration 5m
        nettest stress --profile heavy --duration 1h
        nettest stress --profile max-gigabit --duration 30m
        nettest stress --profile max-10g --duration 2h
    """
    from nettest.tests.av.presets import NDI_STRESS_PROFILES
    from nettest.tests.av.generators import run_ndi_stress_test
    from nettest.utils.output import print_header, print_result

    if profile not in NDI_STRESS_PROFILES:
        console.print(
            f"[bold red]Unknown stress profile '{profile}'.[/]\n"
            f"Available: {', '.join(NDI_STRESS_PROFILES.keys())}"
        )
        sys.exit(1)

    sp = NDI_STRESS_PROFILES[profile]
    print_header(
        f"NDI Stress Test: {sp.name} — {len(sp.streams)} streams, "
        f"~{sp.total_bandwidth_mbps:.0f} Mbps ({_format_duration(duration)})"
    )
    console.print(f"[dim]{sp.description}[/]\n")

    results = run_ndi_stress_test(
        profile_name=profile,
        duration_seconds=duration,
    )

    console.print()
    for r in results:
        print_result(r)
        if verbose and r.details:
            for key, value in r.details.items():
                console.print(f"        [dim]{key}: {value}[/]")


# ---------------------------------------------------------------------------
# Bandwidth calculator (quick reference, no network traffic)
# ---------------------------------------------------------------------------

@main.command("calc")
@click.option("--protocol", type=click.Choice(["ndi", "sacn", "dante"]), required=True)
@click.option("--preset", required=True, help="Preset name")
@click.option("--count", default=1, type=int, help="Number of simultaneous streams/universes/channels")
def calc(protocol, preset, count):
    """
    Calculate bandwidth requirements without sending traffic.

    \b
    Examples:
        nettest calc --protocol ndi --preset 1080p60 --count 8
        nettest calc --protocol sacn --preset arena --count 1
        nettest calc --protocol dante --preset concert-foh --count 1
    """
    from nettest.tests.av.presets import NDI_PRESETS, SACN_PRESETS, DANTE_PRESETS
    from nettest.utils.output import print_header

    print_header("Bandwidth Calculator")

    if protocol == "ndi":
        p = NDI_PRESETS.get(preset)
        if not p:
            console.print(f"[bold red]Unknown preset '{preset}'[/]")
            sys.exit(1)
        single = p.total_bandwidth_mbps
        total = single * count
        console.print(f"  [bold]{p.name}[/] ({p.width}x{p.height} @ {p.fps}fps, {p.codec.value})")
        console.print(f"  Per stream:  [yellow]{single:>8.1f} Mbps[/]")
        if count > 1:
            console.print(f"  x{count} streams: [bold yellow]{total:>8.1f} Mbps[/]")
        console.print()
        if total < 1000:
            console.print(f"  [green]Fits on 1GbE ({total/1000*100:.0f}% utilization)[/]")
        elif total < 10000:
            console.print(f"  [yellow]Requires 10GbE ({total/10000*100:.0f}% utilization)[/]")
        else:
            console.print(f"  [red]Requires 25GbE+ ({total/25000*100:.0f}% of 25GbE)[/]")

    elif protocol == "sacn":
        p = SACN_PRESETS.get(preset)
        if not p:
            console.print(f"[bold red]Unknown preset '{preset}'[/]")
            sys.exit(1)
        bw = p.bandwidth_mbps * count
        console.print(f"  [bold]{p.name}[/] ({p.universes} universes @ {p.refresh_rate_hz}Hz)")
        console.print(f"  Bandwidth:   [yellow]{p.bandwidth_mbps:>8.2f} Mbps[/]")
        console.print(f"  Packets/sec: {p.packets_per_second:,.0f}")
        if count > 1:
            console.print(f"  x{count}:         [bold yellow]{bw:>8.2f} Mbps[/]")

    elif protocol == "dante":
        p = DANTE_PRESETS.get(preset)
        if not p:
            console.print(f"[bold red]Unknown preset '{preset}'[/]")
            sys.exit(1)
        bw = p.total_bandwidth_mbps * count
        console.print(f"  [bold]{p.name}[/] ({p.channel_count}ch @ {p.sample_rate/1000:.0f}kHz/{p.bit_depth}-bit)")
        console.print(f"  Per network: [yellow]{p.per_network_bandwidth_mbps:>8.2f} Mbps[/]")
        if p.redundancy:
            console.print(f"  Total (pri+sec): [bold yellow]{p.total_bandwidth_mbps:>8.2f} Mbps[/]")
        console.print(f"  Latency:     {p.latency_ms}ms")
        console.print(f"  Flows:       {p.effective_flows}")
        console.print(f"  Packets/sec: {p.packets_per_second:,.0f}")
        if count > 1:
            console.print(f"  x{count}:         [bold yellow]{bw:>8.2f} Mbps[/]")

    console.print()


# ---------------------------------------------------------------------------
# End-to-end: send + receive (two-machine verified testing)
# ---------------------------------------------------------------------------

@main.command("send")
@click.option(
    "--protocol",
    type=click.Choice(["ndi", "sacn", "dante", "udp"]),
    required=True,
    help="Protocol to send",
)
@click.option("--preset", default=None, help="Preset name (use 'nettest presets' to list)")
@click.option("--session", default=None, type=int, help="Session ID (auto-generated if omitted; share with receiver)")
@click.option("--duration", default="5m", type=DURATION, help="Duration (e.g. 5m, 1h, 2h30m)")
@click.option("--name", "source_name", default="NetTest Verified", help="Source name (NDI)")
@click.option("--target", default="239.255.0.1", help="Target IP (UDP/Dante)")
@click.option("--port", default=4321, type=int, help="Target port (UDP/Dante)")
@click.option("--pattern", default="chase", help="DMX pattern (sACN): chase, full, random, ramp")
@click.option("-o", "--output", "output_path", default=None, help="Export results to file (.json/.csv/.html)")
@click.option("-v", "--verbose", is_flag=True, default=False)
def send(protocol, preset, session, duration, source_name, target, port, pattern, output_path, verbose):
    """
    Send a verified AV signal for end-to-end testing.

    Run this on Machine A, then run 'nettest receive' on Machine B
    with the same --session ID. The receiver validates every
    frame/packet for drops, corruption, reordering, and latency.

    \b
    Examples:
      Machine A:  nettest send --protocol ndi --preset 1080p60 --session 12345 --duration 1h
      Machine B:  nettest receive --protocol ndi --session 12345 --duration 1h

      Machine A:  nettest send --protocol sacn --preset concert --session 99999
      Machine B:  nettest receive --protocol sacn --session 99999

      Machine A:  nettest send --protocol dante --preset concert-foh --session 55555
      Machine B:  nettest receive --protocol dante --session 55555
    """
    from nettest.tests.av.verification import generate_session_id
    from nettest.tests.av.presets import NDI_PRESETS, SACN_PRESETS, DANTE_PRESETS
    from nettest.utils.output import print_header, print_result

    if session is None:
        session = generate_session_id()

    console.print(f"\n[bold yellow]SESSION ID: {session}[/]")
    console.print(f"[dim]Share this ID with the receiver machine.[/]\n")

    def _snap(s):
        elapsed = s.get("elapsed_s", 0)
        frames = s.get("frames_sent", s.get("packets_sent", 0))
        extra = ""
        if "mbps" in s:
            extra = f" bw={s['mbps']:.1f}Mbps"
        console.print(f"  [dim][{_format_duration(int(elapsed))}] sent={frames}{extra}[/]")

    results = []

    if protocol == "ndi":
        from nettest.tests.av.sender import send_ndi
        p = NDI_PRESETS.get(preset or "1080p30")
        if not p:
            console.print(f"[bold red]Unknown NDI preset '{preset}'[/]")
            sys.exit(1)
        print_header(f"NDI Verified Sender: {p.name} | Session {session} | {_format_duration(duration)}")
        results = send_ndi(p, session, duration, source_name, on_snapshot=_snap)

    elif protocol == "sacn":
        from nettest.tests.av.sender import send_sacn
        p = SACN_PRESETS.get(preset or "concert")
        if not p:
            console.print(f"[bold red]Unknown sACN preset '{preset}'[/]")
            sys.exit(1)
        print_header(f"sACN Verified Sender: {p.name} | Session {session} | {_format_duration(duration)}")
        results = send_sacn(p, session, duration, pattern, on_snapshot=_snap)

    elif protocol in ("dante", "udp"):
        from nettest.tests.av.sender import send_udp
        if preset:
            p = DANTE_PRESETS.get(preset)
            if not p:
                console.print(f"[bold red]Unknown Dante preset '{preset}'[/]")
                sys.exit(1)
            label = f"Dante Verified Sender: {p.name}"
            payload_size = max(64, min(int((p.per_network_bandwidth_mbps * 1_000_000 / 8) / p.packets_per_second), 1400))
            pps = p.packets_per_second
        else:
            label = f"UDP Verified Sender"
            payload_size = 256
            pps = 1000

        print_header(f"{label} | Session {session} | {_format_duration(duration)}")
        results = send_udp(
            protocol_name="Dante" if protocol == "dante" else "UDP",
            target_ip=target,
            target_port=port,
            session_id=session,
            payload_size=payload_size,
            packets_per_second=pps,
            duration_seconds=duration,
            on_snapshot=_snap,
        )

    console.print()
    for r in results:
        print_result(r)
        if verbose and r.details:
            for key, value in r.details.items():
                console.print(f"        [dim]{key}: {value}[/]")

    if output_path and results:
        from nettest.core.result import TestSuite
        suite = TestSuite()
        for r in results:
            suite.add(r)
        _export_report(suite, output_path)


@main.command("receive")
@click.option(
    "--protocol",
    type=click.Choice(["ndi", "sacn", "dante", "udp"]),
    required=True,
    help="Protocol to receive",
)
@click.option("--session", required=True, type=int, help="Session ID (must match sender)")
@click.option("--duration", default="5m", type=DURATION, help="Duration (e.g. 5m, 1h, 2h30m)")
@click.option("--source", default="", help="NDI source name (optional, matches any if empty)")
@click.option("--universe", default=1, type=int, help="sACN universe to listen on (default: 1)")
@click.option("--port", default=4321, type=int, help="UDP port to listen on (Dante/UDP)")
@click.option("--multicast", default=None, help="Multicast group to join (Dante/UDP)")
@click.option("-o", "--output", "output_path", default=None, help="Export results to file (.json/.csv/.html)")
@click.option("-v", "--verbose", is_flag=True, default=False)
def receive(protocol, session, duration, source, universe, port, multicast, output_path, verbose):
    """
    Receive and validate a verified AV signal for end-to-end testing.

    Run this on Machine B after starting 'nettest send' on Machine A
    with the same --session ID. Validates every frame/packet for:
      - Dropped frames (sequence gaps)
      - Corrupted data (CRC mismatch)
      - Reordered packets
      - End-to-end latency

    \b
    Final verdict: PERFECT / EXCELLENT / GOOD / MARGINAL / FAILING

    \b
    Examples:
      nettest receive --protocol ndi --session 12345 --duration 1h
      nettest receive --protocol sacn --session 99999 --universe 1
      nettest receive --protocol dante --session 55555 --port 4321
    """
    from nettest.utils.output import print_header, print_result

    console.print(f"\n[bold yellow]SESSION ID: {session}[/]")
    console.print(f"[dim]Listening for sender with this session ID...[/]\n")

    def _snap(s):
        elapsed = s.get("elapsed_s", 0)
        received = s.get("received", 0)
        dropped = s.get("dropped", 0)
        corrupted = s.get("corrupted", 0)
        drop_pct = s.get("drop_rate_pct", 0)
        console.print(
            f"  [dim][{_format_duration(int(elapsed))}] "
            f"recv={received} drop={dropped}({drop_pct:.4f}%) "
            f"corrupt={corrupted}[/]"
        )

    results = []

    if protocol == "ndi":
        from nettest.tests.av.receiver import receive_ndi
        print_header(f"NDI Verified Receiver | Session {session} | {_format_duration(duration)}")
        results = receive_ndi(session, source, duration, on_snapshot=_snap)

    elif protocol == "sacn":
        from nettest.tests.av.receiver import receive_sacn
        print_header(f"sACN Verified Receiver | Universe {universe} | Session {session} | {_format_duration(duration)}")
        results = receive_sacn(session, universe, duration, on_snapshot=_snap)

    elif protocol in ("dante", "udp"):
        from nettest.tests.av.receiver import receive_udp
        label = "Dante" if protocol == "dante" else "UDP"
        print_header(f"{label} Verified Receiver | Port {port} | Session {session} | {_format_duration(duration)}")
        results = receive_udp(
            protocol_name=label,
            listen_port=port,
            session_id=session,
            duration_seconds=duration,
            multicast_group=multicast,
            on_snapshot=_snap,
        )

    console.print()
    for r in results:
        print_result(r)
        if verbose and r.details:
            for key, value in r.details.items():
                console.print(f"        [dim]{key}: {value}[/]")

    # Print the big verdict
    final = results[-1] if results else None
    if final and final.details:
        verdict = final.details.get("verdict", "UNKNOWN")
        color = {"PERFECT": "green", "EXCELLENT": "green", "GOOD": "green",
                 "MARGINAL": "yellow", "FAILING": "red"}.get(verdict, "red")
        console.print(f"\n[bold {color}]{'=' * 50}[/]")
        console.print(f"[bold {color}]  VERDICT: {verdict}[/]")
        console.print(f"[bold {color}]{'=' * 50}[/]\n")

    if output_path and results:
        from nettest.core.result import TestSuite
        suite = TestSuite()
        for r in results:
            suite.add(r)
        _export_report(suite, output_path)


if __name__ == "__main__":
    main()
