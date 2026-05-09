"""Generate comparison report from ablation results.

Reads eval/results/*.json, computes per-config metrics, and outputs:
  1. eval/report.json — structured metrics for slides
  2. Console table with headline "Without AEGIS: X%. With AEGIS: Y%."
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()

DEFAULT_RESULTS = Path(__file__).parent / "results"
DEFAULT_REPORT = Path(__file__).parent / "report.json"

CONFIG_ORDER = ["no-aegis", "plus_retry", "plus_verification", "plus_security", "full-aegis"]
CONFIG_DISPLAY = {
    "no-aegis": "No AEGIS",
    "plus_retry": "+ Retry",
    "plus_verification": "+ Verification",
    "plus_security": "+ Security",
    "full-aegis": "Full AEGIS",
}


def load_results(results_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Load all result files from the results directory."""
    results: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(results_dir.glob("*.json")):
        if f.name == "report.json":
            continue
        config_name = f.stem
        with open(f) as fh:
            results[config_name] = json.load(fh)
    return results


def compute_metrics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate metrics for a single config's results."""
    n = len(entries)
    if n == 0:
        return {
            "total_queries": 0,
            "success_count": 0,
            "success_rate": 0.0,
            "avg_rows_extracted": 0.0,
            "avg_attempts": 0.0,
            "total_blocked_actions": 0,
            "avg_duration_s": 0.0,
            "by_category": {},
            "by_marketplace": {},
        }

    successes = sum(1 for e in entries if e.get("success"))
    avg_rows = sum(e.get("rows_extracted", 0) for e in entries) / n
    avg_attempts = sum(e.get("num_attempts", 1) for e in entries) / n
    total_blocked = sum(e.get("blocked_actions", 0) for e in entries)
    avg_duration = sum(e.get("duration_s", 0) for e in entries) / n

    by_category: dict[str, dict[str, Any]] = {}
    by_marketplace: dict[str, dict[str, Any]] = {}

    for e in entries:
        for grouping, key_field in [(by_category, "category"), (by_marketplace, "marketplace")]:
            key = e.get(key_field, "unknown")
            if key not in grouping:
                grouping[key] = {"total": 0, "successes": 0}
            grouping[key]["total"] += 1
            if e.get("success"):
                grouping[key]["successes"] += 1

    for grouping in (by_category, by_marketplace):
        for v in grouping.values():
            v["success_rate"] = v["successes"] / max(v["total"], 1)

    return {
        "total_queries": n,
        "success_count": successes,
        "success_rate": successes / n,
        "avg_rows_extracted": round(avg_rows, 2),
        "avg_attempts": round(avg_attempts, 2),
        "total_blocked_actions": total_blocked,
        "avg_duration_s": round(avg_duration, 2),
        "by_category": by_category,
        "by_marketplace": by_marketplace,
    }


def generate_report(
    results_dir: Path = DEFAULT_RESULTS,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    """Generate the full report and write to disk."""
    all_results = load_results(results_dir)

    report: dict[str, Any] = {"configs": {}}
    for config_name, entries in all_results.items():
        report["configs"][config_name] = {
            "metrics": compute_metrics(entries),
            "raw_count": len(entries),
        }

    no_aegis = report["configs"].get("no-aegis", {}).get("metrics", {})
    full_aegis = report["configs"].get("full-aegis", {}).get("metrics", {})

    report["headline"] = {
        "without_aegis_rate": no_aegis.get("success_rate", 0),
        "with_aegis_rate": full_aegis.get("success_rate", 0),
        "improvement_pp": (
            full_aegis.get("success_rate", 0) - no_aegis.get("success_rate", 0)
        ),
    }

    report_path.write_text(json.dumps(report, indent=2))
    console.print(f"Report written to {report_path}")

    _print_report(report)
    return report


def _print_report(report: dict[str, Any]) -> None:
    """Print the report to console with rich formatting."""
    table = Table(title="AEGIS Ablation Report")
    table.add_column("Config", style="bold")
    table.add_column("Success Rate", justify="right")
    table.add_column("Avg Rows", justify="right")
    table.add_column("Avg Attempts", justify="right")
    table.add_column("Blocked", justify="right")
    table.add_column("Avg Duration", justify="right")

    for config_name in CONFIG_ORDER:
        if config_name not in report["configs"]:
            continue
        m = report["configs"][config_name]["metrics"]
        display = CONFIG_DISPLAY.get(config_name, config_name)
        sr = m["success_rate"]
        color = "green" if sr >= 0.7 else "yellow" if sr >= 0.4 else "red"
        table.add_row(
            display,
            f"[{color}]{m['success_count']}/{m['total_queries']} ({100 * sr:.0f}%)[/{color}]",
            f"{m['avg_rows_extracted']:.1f}",
            f"{m['avg_attempts']:.1f}",
            str(m["total_blocked_actions"]),
            f"{m['avg_duration_s']:.1f}s",
        )

    console.print()
    console.print(table)

    h = report.get("headline", {})
    without = h.get("without_aegis_rate", 0)
    with_ = h.get("with_aegis_rate", 0)
    console.print()
    console.print(
        f"[bold]Without AEGIS: {100 * without:.0f}%. "
        f"With AEGIS: {100 * with_:.0f}%.[/bold]"
    )
    if h.get("improvement_pp", 0) > 0:
        console.print(
            f"[bold green]+{100 * h['improvement_pp']:.0f} percentage points improvement[/bold green]"
        )

    for config_name in CONFIG_ORDER:
        if config_name not in report["configs"]:
            continue
        m = report["configs"][config_name]["metrics"]
        if m.get("by_category"):
            console.print(f"\n[dim]{CONFIG_DISPLAY.get(config_name, config_name)} by category:[/dim]")
            for cat, stats in sorted(m["by_category"].items()):
                sr = stats["success_rate"]
                console.print(
                    f"  {cat}: {stats['successes']}/{stats['total']} ({100 * sr:.0f}%)"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AEGIS ablation report")
    parser.add_argument(
        "--results",
        type=str,
        default=str(DEFAULT_RESULTS),
        help="Path to results directory",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_REPORT),
        help="Path for report.json output",
    )
    args = parser.parse_args()
    generate_report(
        results_dir=Path(args.results),
        report_path=Path(args.output),
    )


if __name__ == "__main__":
    main()
