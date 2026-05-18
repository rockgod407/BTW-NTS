"""Rich terminal output formatting."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from nettest.core.result import Status, TestResult, TestSuite

console = Console()

STATUS_STYLES = {
    Status.PASS: ("PASS", "bold green"),
    Status.FAIL: ("FAIL", "bold red"),
    Status.WARN: ("WARN", "bold yellow"),
    Status.SKIP: ("SKIP", "dim"),
    Status.ERROR: ("ERR ", "bold red on white"),
}


def status_text(status: Status) -> Text:
    label, style = STATUS_STYLES[status]
    return Text(f" {label} ", style=style)


def print_header(title: str) -> None:
    console.print()
    console.print(Panel(f"[bold cyan]{title}[/]", box=box.DOUBLE, expand=False))
    console.print()


def print_result(result: TestResult) -> None:
    st = status_text(result.status)
    duration = f"[dim]{result.duration_ms:>8.1f}ms[/]"
    console.print(st, f"  {duration}  {result.name}: {result.message}")


def print_category_header(category: str) -> None:
    console.print()
    console.rule(f"[bold]{category.upper()} Tests[/]", style="cyan")
    console.print()


def print_summary(suite: TestSuite) -> None:
    console.print()
    console.rule("[bold]Summary[/]", style="cyan")
    console.print()

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Category", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("Pass", justify="right", style="green")
    table.add_column("Fail", justify="right", style="red")
    table.add_column("Warn", justify="right", style="yellow")
    table.add_column("Error", justify="right", style="red")
    table.add_column("Skip", justify="right", style="dim")

    categories = sorted(set(r.category for r in suite.results))
    for cat in categories:
        results = suite.by_category(cat)
        table.add_row(
            cat.upper(),
            str(len(results)),
            str(sum(1 for r in results if r.status == Status.PASS)),
            str(sum(1 for r in results if r.status == Status.FAIL)),
            str(sum(1 for r in results if r.status == Status.WARN)),
            str(sum(1 for r in results if r.status == Status.ERROR)),
            str(sum(1 for r in results if r.status == Status.SKIP)),
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/]",
        f"[bold]{suite.total}[/]",
        f"[bold green]{suite.passed}[/]",
        f"[bold red]{suite.failed}[/]",
        f"[bold yellow]{suite.warnings}[/]",
        f"[bold red]{suite.errors}[/]",
        f"[dim]{suite.skipped}[/]",
    )

    console.print(table)

    if suite.all_passed:
        console.print("\n[bold green]All tests passed![/]\n")
    else:
        console.print(
            f"\n[bold red]{suite.failed + suite.errors} test(s) failed or errored.[/]\n"
        )


def print_detail_table(title: str, rows: list[dict]) -> None:
    """Print a detail sub-table for verbose output."""
    if not rows:
        return
    table = Table(title=title, box=box.ROUNDED, show_header=True, header_style="bold")
    keys = list(rows[0].keys())
    for k in keys:
        table.add_column(k)
    for row in rows:
        table.add_row(*[str(row.get(k, "")) for k in keys])
    console.print(table)
