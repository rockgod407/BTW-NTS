"""
Test report export — JSON, CSV, and HTML output.

Saves full test results for comparison across runs, archiving,
or feeding into dashboards.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from nettest.core.result import Status, TestResult, TestSuite


def export_json(
    suite: TestSuite,
    path: str = "nettest-report.json",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Export a full test report as JSON."""
    report = _build_report(suite, metadata)
    out = Path(path)
    out.write_text(json.dumps(report, indent=2, default=str))
    return str(out.resolve())


def export_csv(
    suite: TestSuite,
    path: str = "nettest-report.csv",
) -> str:
    """Export test results as a flat CSV."""
    out = Path(path)
    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "category",
            "test_name",
            "status",
            "message",
            "duration_ms",
            "details",
        ])
        ts = datetime.now(tz=timezone.utc).isoformat()
        for r in suite.results:
            writer.writerow([
                ts,
                r.category,
                r.name,
                r.status.value,
                r.message,
                round(r.duration_ms, 1),
                json.dumps(r.details, default=str) if r.details else "",
            ])
    return str(out.resolve())


def export_html(
    suite: TestSuite,
    path: str = "nettest-report.html",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Export a self-contained HTML report."""
    report = _build_report(suite, metadata)
    html = _render_html(report)
    out = Path(path)
    out.write_text(html)
    return str(out.resolve())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_report(
    suite: TestSuite,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the canonical report dict."""
    now = datetime.now(tz=timezone.utc)
    return {
        "nettest_version": "0.1.0",
        "generated_at": now.isoformat(),
        "generated_at_unix": time.time(),
        "metadata": metadata or {},
        "summary": {
            "total": suite.total,
            "passed": suite.passed,
            "failed": suite.failed,
            "warnings": suite.warnings,
            "errors": suite.errors,
            "skipped": suite.skipped,
            "all_passed": suite.all_passed,
        },
        "categories": _summarize_categories(suite),
        "results": [_result_to_dict(r) for r in suite.results],
    }


def _result_to_dict(r: TestResult) -> Dict[str, Any]:
    return {
        "name": r.name,
        "category": r.category,
        "status": r.status.value,
        "message": r.message,
        "duration_ms": round(r.duration_ms, 1),
        "details": r.details,
    }


def _summarize_categories(suite: TestSuite) -> List[Dict[str, Any]]:
    categories = sorted(set(r.category for r in suite.results))
    summaries = []
    for cat in categories:
        results = suite.by_category(cat)
        summaries.append({
            "name": cat,
            "total": len(results),
            "passed": sum(1 for r in results if r.status == Status.PASS),
            "failed": sum(1 for r in results if r.status == Status.FAIL),
            "warnings": sum(1 for r in results if r.status == Status.WARN),
            "errors": sum(1 for r in results if r.status == Status.ERROR),
            "skipped": sum(1 for r in results if r.status == Status.SKIP),
        })
    return summaries


def _render_html(report: Dict[str, Any]) -> str:
    """Render a self-contained HTML report (no external deps)."""
    status_colors = {
        "PASS": "#22c55e",
        "FAIL": "#ef4444",
        "WARN": "#eab308",
        "ERROR": "#dc2626",
        "SKIP": "#9ca3af",
    }

    summary = report["summary"]
    results_html = ""
    current_cat = ""

    for r in report["results"]:
        if r["category"] != current_cat:
            current_cat = r["category"]
            results_html += f'<tr><td colspan="4" style="background:#1e293b;color:#38bdf8;font-weight:bold;padding:10px;text-transform:uppercase;letter-spacing:1px;">{current_cat}</td></tr>\n'

        color = status_colors.get(r["status"], "#9ca3af")
        results_html += f"""<tr>
            <td><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{r['status']}</span></td>
            <td style="color:#94a3b8;text-align:right;font-family:monospace;">{r['duration_ms']:.0f}ms</td>
            <td>{r['name']}</td>
            <td style="color:#cbd5e1;">{r['message']}</td>
        </tr>\n"""

    cat_rows = ""
    for cat in report["categories"]:
        cat_rows += f"""<tr>
            <td style="color:#38bdf8;font-weight:bold;">{cat['name'].upper()}</td>
            <td style="text-align:right;">{cat['total']}</td>
            <td style="text-align:right;color:#22c55e;">{cat['passed']}</td>
            <td style="text-align:right;color:#ef4444;">{cat['failed']}</td>
            <td style="text-align:right;color:#eab308;">{cat['warnings']}</td>
            <td style="text-align:right;color:#dc2626;">{cat['errors']}</td>
            <td style="text-align:right;color:#9ca3af;">{cat['skipped']}</td>
        </tr>\n"""

    verdict_color = "#22c55e" if summary["all_passed"] else "#ef4444"
    verdict_text = "ALL TESTS PASSED" if summary["all_passed"] else f"{summary['failed'] + summary['errors']} TEST(S) FAILED"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>nettest Report — {report['generated_at'][:19]}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1 {{ color: #38bdf8; margin-bottom: 4px; }}
  .meta {{ color: #64748b; margin-bottom: 24px; font-size: 14px; }}
  .verdict {{ padding: 16px; border-radius: 8px; font-size: 18px; font-weight: bold; text-align: center; margin-bottom: 24px; background: {verdict_color}22; color: {verdict_color}; border: 1px solid {verdict_color}44; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
  th {{ text-align: left; padding: 8px 12px; background: #1e293b; color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }}
  td {{ padding: 6px 12px; border-bottom: 1px solid #1e293b; font-size: 14px; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .stat {{ background: #1e293b; padding: 16px; border-radius: 8px; text-align: center; }}
  .stat .number {{ font-size: 28px; font-weight: bold; }}
  .stat .label {{ font-size: 12px; color: #64748b; text-transform: uppercase; margin-top: 4px; }}
</style>
</head>
<body>
<h1>nettest Report</h1>
<p class="meta">Generated {report['generated_at'][:19]} UTC &middot; nettest v{report['nettest_version']}</p>

<div class="verdict">{verdict_text}</div>

<div class="summary-grid">
  <div class="stat"><div class="number" style="color:#e2e8f0;">{summary['total']}</div><div class="label">Total</div></div>
  <div class="stat"><div class="number" style="color:#22c55e;">{summary['passed']}</div><div class="label">Passed</div></div>
  <div class="stat"><div class="number" style="color:#ef4444;">{summary['failed']}</div><div class="label">Failed</div></div>
  <div class="stat"><div class="number" style="color:#eab308;">{summary['warnings']}</div><div class="label">Warnings</div></div>
  <div class="stat"><div class="number" style="color:#dc2626;">{summary['errors']}</div><div class="label">Errors</div></div>
  <div class="stat"><div class="number" style="color:#9ca3af;">{summary['skipped']}</div><div class="label">Skipped</div></div>
</div>

<h2 style="color:#38bdf8;margin-bottom:12px;">By Category</h2>
<table>
<tr><th>Category</th><th style="text-align:right;">Total</th><th style="text-align:right;">Pass</th><th style="text-align:right;">Fail</th><th style="text-align:right;">Warn</th><th style="text-align:right;">Error</th><th style="text-align:right;">Skip</th></tr>
{cat_rows}
</table>

<h2 style="color:#38bdf8;margin-bottom:12px;">All Results</h2>
<table>
<tr><th style="width:60px;">Status</th><th style="width:80px;text-align:right;">Time</th><th>Test</th><th>Message</th></tr>
{results_html}
</table>

</body>
</html>"""
