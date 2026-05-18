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
        _maybe_first_run_check()
        _maybe_update_check()
        return

    _maybe_first_run_check()
    _maybe_update_check()

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
# First-run dependency check
# ---------------------------------------------------------------------------

def _maybe_first_run_check():
    """On first run, do a quick dependency check and warn about problems."""
    from nettest.utils.doctor import needs_doctor, quick_check, mark_doctor_passed

    if not needs_doctor():
        return

    all_ok, problems = quick_check()

    if all_ok:
        mark_doctor_passed()
        return

    console.print("\n[bold yellow]First-run dependency check[/]\n")
    console.print("[yellow]Some required dependencies are missing:[/]")
    for p in problems:
        console.print(f"[red]{p}[/]")
    console.print()
    console.print("[dim]Run [bold]nettest doctor[/bold] for a full diagnostic and auto-install.[/]")
    console.print("[dim]Run [bold]nettest doctor --fix[/bold] to auto-install everything possible.[/]\n")


# ---------------------------------------------------------------------------
# Update check (on every run)
# ---------------------------------------------------------------------------

def _maybe_update_check():
    """Check for updates, prompt the user if one is available."""
    try:
        from nettest.utils.updater import check_for_update, run_update

        update_available, local_ver, remote_ver = check_for_update()

        if not update_available:
            return

        console.print(f"\n[bold yellow]Update available:[/] {local_ver} → [bold green]{remote_ver}[/]")

        try:
            response = console.input("[yellow]Update now? (Y/n): [/]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return

        if response in ("", "y", "yes"):
            console.print("[dim]Downloading update from GitHub...[/]")
            success, message = run_update()
            if success:
                console.print(f"[bold green]{message}[/]\n")
                sys.exit(0)
            else:
                console.print(f"[bold red]{message}[/]")
                console.print("[dim]Continuing with current version...[/]\n")
        else:
            console.print("[dim]Skipping update.[/]\n")

    except Exception:
        # Never let the update check break normal usage
        pass


# ---------------------------------------------------------------------------
# Update command
# ---------------------------------------------------------------------------

@main.command("update")
@click.option("--check", "check_only", is_flag=True, default=False, help="Only check, don't install")
@click.option("--force", is_flag=True, default=False, help="Force reinstall even if version matches")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Show pip output during install")
def update(check_only, force, verbose):
    """
    Check for and install updates.

    Checks GitHub for a newer version and offers to install it.
    Downloads the repo as a zip — no git or caching involved.

    \b
    Examples:
        nettest update           # Check and install if available
        nettest update --check   # Just check, don't install
        nettest update --force   # Force reinstall from GitHub
    """
    from nettest.utils.updater import check_for_update, run_update

    console.print("[dim]Checking GitHub for latest version...[/]")
    update_available, local_ver, remote_ver = check_for_update()

    console.print(f"  Installed: [bold]{local_ver}[/]")
    console.print(f"  Latest:    [bold]{remote_ver}[/]")
    console.print()

    # If we couldn't reach GitHub, say so clearly
    if remote_ver == "unknown":
        console.print("[bold red]Could not reach GitHub to check for updates.[/]")
        console.print("[dim]Check your internet connection, or reinstall manually:[/]")
        console.print("[dim]  curl -fsSL https://raw.githubusercontent.com/rockgod407/BTW-NTS/main/install.sh | bash[/]\n")
        if not force:
            return
        console.print("[dim]Proceeding with --force reinstall anyway...[/]\n")

    if not update_available and not force:
        console.print("[bold green]You're on the latest version![/]")
        console.print("[dim]Use --force to reinstall anyway.[/]\n")
        return

    if update_available:
        console.print(f"[bold yellow]Update available: {local_ver} → {remote_ver}[/]\n")
    else:
        console.print(f"[bold yellow]Force reinstalling {remote_ver}...[/]\n")

    if check_only:
        console.print(f"[dim]Run [bold]nettest update[/bold] to install.[/]\n")
        return

    if not force:
        try:
            response = console.input("[yellow]Install update? (Y/n): [/]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return

        if response not in ("", "y", "yes"):
            console.print("[dim]Update cancelled.[/]\n")
            return

    console.print("\n[dim]Downloading zip from GitHub and installing...[/]\n")
    success, message = run_update(verbose=verbose)
    if success:
        console.print(f"[bold green]{message}[/]\n")
    else:
        console.print(f"[bold red]{message}[/]\n")


# ---------------------------------------------------------------------------
# Doctor command
# ---------------------------------------------------------------------------

@main.command("doctor")
@click.option("--fix", is_flag=True, default=False, help="Auto-install missing pip dependencies")
@click.option("--json-out", is_flag=True, default=False, help="Output as JSON")
def doctor(fix, json_out):
    """
    Check that all dependencies are installed and working.

    Verifies core dependencies, optional AV protocol libraries,
    and useful system tools. Use --fix to auto-install anything
    that's missing.

    \b
    Examples:
        nettest doctor          # Check everything
        nettest doctor --fix    # Check and auto-install missing deps
        nettest doctor --json-out  # Machine-readable output
    """
    from nettest.utils.doctor import (
        DepStatus, check_all, check_system_tools,
        install_missing, mark_doctor_passed,
    )

    results = check_all()
    sys_tools = check_system_tools()

    if json_out:
        import json
        data = {
            "dependencies": [
                {
                    "name": r.name,
                    "required": r.required,
                    "status": r.status.value,
                    "installed_version": r.installed_version,
                    "min_version": r.min_version,
                    "notes": r.notes,
                }
                for r in results
            ],
            "system_tools": sys_tools,
        }
        console.print(json.dumps(data, indent=2))
        return

    # Pretty output
    from nettest.utils.output import print_header
    print_header("nettest dependency check")

    # --- Python info ---
    import platform
    console.print(f"  [bold]Python:[/] {platform.python_version()} ({sys.executable})")
    console.print(f"  [bold]Platform:[/] {platform.platform()}")
    console.print()

    # --- Core dependencies ---
    table = Table(
        title="[bold cyan]Core Dependencies[/]",
        box=box.ROUNDED, show_header=True, header_style="bold",
    )
    table.add_column("Package", style="cyan", min_width=12)
    table.add_column("Status", min_width=10)
    table.add_column("Installed", justify="right", min_width=10)
    table.add_column("Required", justify="right", min_width=10)
    table.add_column("Description", max_width=40)

    core_ok = True
    for r in results:
        if not r.required:
            continue
        if r.status == DepStatus.OK:
            status_str = "[green]OK[/]"
        elif r.status == DepStatus.MISSING:
            status_str = "[bold red]MISSING[/]"
            core_ok = False
        elif r.status == DepStatus.VERSION_LOW:
            status_str = "[yellow]OUTDATED[/]"
            core_ok = False
        else:
            status_str = "[red]BROKEN[/]"
            core_ok = False

        table.add_row(
            r.name,
            status_str,
            r.installed_version or "—",
            f">={r.min_version}" if r.min_version else "",
            r.notes,
        )
    console.print(table)
    console.print()

    # --- Optional dependencies ---
    opt_table = Table(
        title="[bold cyan]Optional AV Dependencies[/]",
        box=box.ROUNDED, show_header=True, header_style="bold",
    )
    opt_table.add_column("Package", style="cyan", min_width=14)
    opt_table.add_column("Status", min_width=10)
    opt_table.add_column("Installed", justify="right", min_width=10)
    opt_table.add_column("Description", max_width=50)

    for r in results:
        if r.required:
            continue
        if r.status == DepStatus.OK:
            status_str = "[green]OK[/]"
        elif r.status == DepStatus.MISSING:
            status_str = "[dim yellow]NOT INSTALLED[/]"
        elif r.status == DepStatus.VERSION_LOW:
            status_str = "[yellow]OUTDATED[/]"
        else:
            status_str = "[red]BROKEN[/]"

        opt_table.add_row(
            r.name,
            status_str,
            r.installed_version or "—",
            r.notes,
        )
    console.print(opt_table)
    console.print()

    # --- System tools ---
    tools_table = Table(
        title="[bold cyan]System Tools[/]",
        box=box.ROUNDED, show_header=True, header_style="bold",
    )
    tools_table.add_column("Tool", style="cyan")
    tools_table.add_column("Status", min_width=10)
    tools_table.add_column("Path", style="dim", max_width=50)
    tools_table.add_column("Used For")

    for t in sys_tools:
        status_str = "[green]OK[/]" if t["installed"] else "[dim yellow]NOT FOUND[/]"
        tools_table.add_row(t["name"], status_str, t["path"], t["description"])
    console.print(tools_table)
    console.print()

    # --- Install hints for missing deps ---
    missing = [r for r in results if r.status in (DepStatus.MISSING, DepStatus.VERSION_LOW)]
    missing_required = [r for r in missing if r.required]
    missing_optional = [r for r in missing if not r.required]

    if fix and missing:
        console.print("[bold]Auto-installing missing dependencies...[/]\n")
        install_results = install_missing(results)
        for name, success, msg in install_results:
            if success:
                console.print(f"  [green]✔[/] {name}: {msg}")
            else:
                console.print(f"  [red]✘[/] {name}: {msg}")

        # Show manual install hints for optional deps
        if missing_optional:
            console.print()
            console.print("[bold]Optional dependencies (manual install):[/]")
            for r in missing_optional:
                console.print(f"\n  [cyan]{r.name}[/] — {r.notes}")
                console.print(f"    [dim]{r.install_hint}[/]")

        # Re-check
        console.print("\n[dim]Re-checking...[/]\n")
        recheck = check_all()
        still_missing = [r for r in recheck if r.required and r.status != DepStatus.OK]
        if not still_missing:
            mark_doctor_passed()
            console.print("[bold green]All core dependencies are now installed![/]\n")
        else:
            console.print("[bold red]Some core dependencies are still missing.[/]")
            for r in still_missing:
                console.print(f"  [red]{r.name}[/]: {r.install_hint}")
            console.print()

    elif missing_required:
        console.print("[bold red]Missing required dependencies![/]")
        console.print("[dim]Run [bold]nettest doctor --fix[/bold] to auto-install, or manually:[/]\n")
        for r in missing_required:
            console.print(f"  [cyan]{r.name}[/]: {r.install_hint}")
        console.print()

        if missing_optional:
            console.print("[bold yellow]Optional (for AV protocol testing):[/]")
            for r in missing_optional:
                console.print(f"\n  [cyan]{r.name}[/] — {r.notes}")
                console.print(f"    [dim]{r.install_hint}[/]")
            console.print()

    elif missing_optional:
        console.print("[bold green]All core dependencies OK![/]\n")
        console.print("[bold yellow]Optional (for AV protocol testing):[/]")
        for r in missing_optional:
            console.print(f"\n  [cyan]{r.name}[/] — {r.notes}")
            console.print(f"    [dim]{r.install_hint}[/]")
        console.print()
        mark_doctor_passed()

    else:
        mark_doctor_passed()
        console.print("[bold green]Everything looks good! All dependencies installed.[/]\n")


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
# Session discovery helper (for receiver auto-connect)
# ---------------------------------------------------------------------------

def _discover_session_interactive(protocol_filter=None):
    """
    Scan for active sender sessions on the LAN via beacon.

    Returns a SessionInfo if one is found/selected, or None if nothing
    was discovered.
    """
    from nettest.utils.beacon import discover_sessions

    console.print(f"\n[bold cyan]Scanning for active sessions on the LAN...[/]")
    if protocol_filter:
        console.print(f"[dim]Filtering for protocol: {protocol_filter}[/]")
    console.print(f"[dim]Listening for beacons (up to 6 seconds)...[/]\n")

    sessions = discover_sessions(timeout=6.0, protocol_filter=protocol_filter)

    if not sessions:
        return None

    if len(sessions) == 1:
        s = sessions[0]
        console.print(f"[bold green]Found 1 active session:[/]\n")
        _print_session_table(sessions)
        return s

    # Multiple sessions — let the user pick
    console.print(f"[bold green]Found {len(sessions)} active session(s):[/]\n")
    _print_session_table(sessions)

    console.print()
    while True:
        try:
            choice = console.input(
                f"[yellow]Select session (1-{len(sessions)}, or 'q' to quit): [/]"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None

        if choice.lower() in ("q", "quit"):
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]
        except ValueError:
            pass
        console.print(f"[red]Invalid choice. Enter 1-{len(sessions)} or 'q'.[/]")


def _print_session_table(sessions):
    """Print a Rich table of discovered sessions."""
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    table.add_column("#", style="bold", justify="right", width=3)
    table.add_column("Session", style="cyan", min_width=8)
    table.add_column("Protocol", min_width=8)
    table.add_column("Preset", min_width=10)
    table.add_column("Sender", min_width=12)
    table.add_column("IP", min_width=12)
    table.add_column("Duration", min_width=8)
    table.add_column("Started", min_width=8)

    for i, s in enumerate(sessions, 1):
        table.add_row(
            str(i),
            str(s.session_id),
            s.protocol.upper(),
            s.preset or "—",
            s.hostname,
            s.sender_ip,
            s.duration or "—",
            s.age_display,
        )

    console.print(table)


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

    Run this on Machine A, then run 'nettest receive' on Machine B.
    The receiver auto-discovers the session on the LAN — no need to
    copy session IDs.

    \b
    Examples:
      Machine A:  nettest send --protocol ndi --preset 1080p60 --duration 1h
      Machine B:  nettest receive                    # auto-discovers!

      Machine A:  nettest send --protocol sacn --preset concert
      Machine B:  nettest receive --protocol sacn    # filter by protocol

      Machine A:  nettest send --protocol dante --preset concert-foh
      Machine B:  nettest receive
    """
    from nettest.tests.av.verification import generate_session_id
    from nettest.tests.av.presets import NDI_PRESETS, SACN_PRESETS, DANTE_PRESETS
    from nettest.utils.output import print_header, print_result
    from nettest.utils.beacon import BeaconSender

    if session is None:
        session = generate_session_id()

    # Resolve preset name early so we can include it in the beacon
    preset_name = preset or ""
    if protocol == "ndi":
        preset_name = preset or "1080p30"
    elif protocol == "sacn":
        preset_name = preset or "concert"

    console.print(f"\n[bold yellow]━━━ SESSION ID: {session} ━━━[/]")
    console.print(f"[dim]Start the receiver on the other machine with:[/]")
    console.print(f"[bold]  nettest receive --protocol {protocol} --duration {_format_duration(duration)}[/]")
    console.print(f"[dim]The receiver will auto-discover this session on the LAN.[/]\n")

    # Start the LAN beacon so receivers can auto-discover this session
    beacon = BeaconSender(
        session_id=session,
        protocol=protocol,
        preset=preset_name,
        duration=_format_duration(duration),
    )
    beacon.start()
    console.print(f"[dim]Broadcasting session beacon on LAN (port 5557)...[/]\n")

    def _snap(s):
        elapsed = s.get("elapsed_s", 0)
        frames = s.get("frames_sent", s.get("packets_sent", 0))
        extra = ""
        if "mbps" in s:
            extra = f" bw={s['mbps']:.1f}Mbps"
        console.print(f"  [dim][{_format_duration(int(elapsed))}] sent={frames}{extra}[/]")

    results = []

    try:
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
    finally:
        # Always stop the beacon when we're done
        beacon.stop()

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
    default=None,
    help="Protocol to receive (auto-detected from session if omitted)",
)
@click.option("--session", default=None, type=int, help="Session ID (auto-discovered from LAN if omitted)")
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

    Run this on Machine B after starting 'nettest send' on Machine A.
    The receiver auto-discovers active sessions on the LAN — no need
    to remember session IDs.

    \b
    Validates every frame/packet for:
      - Dropped frames (sequence gaps)
      - Corrupted data (CRC mismatch)
      - Reordered packets
      - End-to-end latency

    \b
    Final verdict: PERFECT / EXCELLENT / GOOD / MARGINAL / FAILING

    \b
    Examples:
      nettest receive                          # Auto-discover sender on LAN
      nettest receive --protocol ndi           # Only look for NDI sessions
      nettest receive --session 12345          # Connect to a specific session
      nettest receive --protocol sacn --universe 1
      nettest receive --protocol dante --port 4321
    """
    from nettest.utils.output import print_header, print_result
    from nettest.tests.av.verification import generate_session_id

    # ------------------------------------------------------------------
    # Auto-discover sessions from LAN beacons if --session not provided
    # ------------------------------------------------------------------
    if session is None:
        session_info = _discover_session_interactive(protocol_filter=protocol)
        if session_info is not None:
            session = session_info.session_id
            # Auto-fill protocol from discovered session if not specified
            if protocol is None:
                protocol = session_info.protocol
            # Override duration with sender's duration if user didn't specify one
            console.print(f"\n[bold green]Connecting to session {session}[/]")
            console.print(f"  [dim]Sender: {session_info.hostname} ({session_info.sender_ip})[/]")
            console.print(f"  [dim]Protocol: {session_info.protocol} | Preset: {session_info.preset} | Duration: {session_info.duration}[/]\n")
        else:
            # No session discovered — need at least a protocol to continue
            if protocol is None:
                console.print("[bold red]No active sessions found and --protocol not specified.[/]")
                console.print("[dim]Start a sender first:  nettest send --protocol ndi --duration 5m[/]")
                console.print("[dim]Or specify a protocol: nettest receive --protocol ndi[/]\n")
                sys.exit(1)
            session = generate_session_id()
            console.print(f"\n[bold yellow]No active sessions found on the LAN.[/]")
            console.print(f"[dim]Generated session ID: {session}[/]")
            console.print(f"[dim]Start the sender on the other machine with:[/]")
            console.print(f"[bold]  nettest send --protocol {protocol} --session {session} --duration {_format_duration(duration)}[/]\n")
    else:
        # Session was provided explicitly
        if protocol is None:
            console.print("[bold red]--protocol is required when using --session.[/]")
            sys.exit(1)

    console.print(f"[bold yellow]━━━ SESSION {session} | {protocol.upper()} ━━━[/]")

    _waiting_printed = [False]

    def _snap(s):
        elapsed = s.get("elapsed_s", 0)
        status = s.get("status", "")

        if status == "waiting_for_sender":
            if not _waiting_printed[0]:
                console.print(f"  [bold yellow]Waiting for sender to appear on the network...[/]")
                console.print(f"  [dim]Start the sender on the other machine now.[/]")
                _waiting_printed[0] = True
            else:
                console.print(f"  [dim][{_format_duration(int(elapsed))}] searching for NDI source...[/]")
            return

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
        console.print(f"  [bold yellow]Waiting for sender to appear on the network...[/]")
        console.print(f"  [dim]Start the sender on the other machine now.[/]\n")
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


# ---------------------------------------------------------------------------
# Session discovery (standalone scan)
# ---------------------------------------------------------------------------

@main.command("sessions")
@click.option(
    "--protocol",
    type=click.Choice(["ndi", "sacn", "dante", "udp"]),
    default=None,
    help="Filter by protocol",
)
@click.option("--timeout", default=6, type=int, help="How long to listen for beacons (default: 6s)")
@click.option("--watch", is_flag=True, default=False, help="Keep scanning (Ctrl+C to stop)")
def sessions(protocol, timeout, watch):
    """
    Scan the LAN for active nettest sender sessions.

    Shows all active senders broadcasting beacons. Use this to see
    what's running before starting a receiver.

    \b
    Examples:
        nettest sessions                   # Scan once
        nettest sessions --watch           # Keep scanning
        nettest sessions --protocol ndi    # Only NDI sessions
    """
    from nettest.utils.beacon import discover_sessions
    from nettest.utils.output import print_header

    print_header("Session Scanner")

    if not watch:
        console.print(f"[dim]Listening for beacons ({timeout}s)...[/]\n")
        found = discover_sessions(timeout=float(timeout), protocol_filter=protocol)
        if found:
            console.print(f"[bold green]Found {len(found)} active session(s):[/]\n")
            _print_session_table(found)
            console.print()
            console.print(f"[dim]To connect:  nettest receive --session <ID>[/]")
            console.print(f"[dim]Or just run: nettest receive  (auto-connects)[/]\n")
        else:
            console.print("[bold yellow]No active sessions found.[/]")
            console.print("[dim]Start a sender first:  nettest send --protocol ndi --duration 5m[/]\n")
        return

    # Watch mode — keep scanning
    console.print(f"[dim]Watching for sessions (Ctrl+C to stop)...[/]\n")
    try:
        while True:
            found = discover_sessions(timeout=float(timeout), protocol_filter=protocol)
            # Clear and redraw
            if found:
                console.print(f"[bold green]Active sessions ({len(found)}):[/]")
                _print_session_table(found)
            else:
                console.print("[dim]No active sessions. Scanning...[/]")
            console.print()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]\n")


if __name__ == "__main__":
    main()
