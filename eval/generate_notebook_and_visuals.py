from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"
RESULTS_DIR = EVAL_DIR / "results"
REPORT_PATH = EVAL_DIR / "report.json"
FIGURES_DIR = ROOT / "docs" / "figures"
NOTEBOOK_PATH = ROOT / "rl_analysis_generated.ipynb"

CONFIG_LABELS = {
    "no-aegis": "No AEGIS",
    "plus_retry": "+ Retry",
    "plus_verification": "+ Verification",
    "plus_security": "+ Security",
    "full-aegis": "Full AEGIS",
}
CONFIG_ORDER = ["no-aegis", "plus_retry", "plus_verification", "plus_security", "full-aegis"]


def load_results() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        rows.extend(json.loads(path.read_text()))
    if not rows:
        raise RuntimeError("No evaluation results found in eval/results")
    return rows


def load_report() -> dict:
    return json.loads(REPORT_PATH.read_text()) if REPORT_PATH.exists() else {}


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize_by_config(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["config"], []).append(row)

    summary = []
    for config in CONFIG_ORDER:
        items = grouped.get(config, [])
        if not items:
            continue
        summary.append(
            {
                "config": config,
                "label": CONFIG_LABELS.get(config, config),
                "success_rate": avg([1.0 if r.get("success") else 0.0 for r in items]),
                "avg_rows": avg([float(r.get("rows_extracted", 0)) for r in items]),
                "avg_attempts": avg([float(r.get("num_attempts", 0)) for r in items]),
                "avg_duration_s": avg([float(r.get("duration_s", 0)) for r in items]),
                "blocked_actions": sum(int(r.get("blocked_actions", 0)) for r in items),
                "total_queries": len(items),
            }
        )
    return summary


def summarize_marketplace(rows: list[dict]) -> tuple[list[str], dict[str, dict[str, float]]]:
    marketplaces = sorted({row.get("marketplace", "unknown") for row in rows})
    matrix: dict[str, dict[str, float]] = {m: {} for m in marketplaces}
    for marketplace in marketplaces:
        for config in CONFIG_ORDER:
            items = [
                row for row in rows
                if row.get("marketplace") == marketplace and row.get("config") == config
            ]
            if items:
                matrix[marketplace][CONFIG_LABELS.get(config, config)] = avg(
                    [1.0 if r.get("success") else 0.0 for r in items]
                )
    return marketplaces, matrix


def wrap_svg(title: str, body: str, width: int = 1000, height: int = 600) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#F8FAFC"/>'
        f'<text x="60" y="55" font-family="Segoe UI, Arial, sans-serif" font-size="28" '
        f'font-weight="700" fill="#0F172A">{title}</text>'
        f"{body}</svg>"
    )


def save_success_rate_svg(summary: list[dict]) -> None:
    width = 1000
    height = 600
    chart_left = 100
    chart_bottom = 500
    chart_top = 110
    chart_height = chart_bottom - chart_top
    bar_width = 110
    gap = 55
    colors = ["#CBD5E1", "#94A3B8", "#64748B", "#1D4ED8", "#0EA5E9"]
    parts = [
        '<line x1="90" y1="500" x2="930" y2="500" stroke="#475569" stroke-width="2"/>',
        '<line x1="100" y1="110" x2="100" y2="500" stroke="#475569" stroke-width="2"/>',
    ]
    for tick in range(5):
        value = tick * 0.25
        y = chart_bottom - chart_height * value
        parts.append(f'<line x1="100" y1="{y:.1f}" x2="930" y2="{y:.1f}" stroke="#E2E8F0" stroke-width="1"/>')
        parts.append(
            f'<text x="40" y="{y + 5:.1f}" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="16" fill="#475569">{int(value * 100)}%</text>'
        )
    for i, row in enumerate(summary):
        x = chart_left + i * (bar_width + gap)
        h = chart_height * row["success_rate"]
        y = chart_bottom - h
        parts.append(f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{h:.1f}" rx="10" fill="{colors[i % len(colors)]}"/>')
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 12:.1f}" text-anchor="middle" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="18" font-weight="700" fill="#0F172A">{row["success_rate"]:.0%}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="535" text-anchor="middle" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="16" fill="#334155">{row["label"]}</text>'
        )
    (FIGURES_DIR / "success_rate_by_config.svg").write_text(
        wrap_svg("Task Success Rate by Configuration", "".join(parts), width, height),
        encoding="utf-8",
    )


def save_rows_svg(summary: list[dict]) -> None:
    width = 1000
    height = 600
    chart_left = 100
    chart_bottom = 500
    chart_top = 110
    chart_height = chart_bottom - chart_top
    bar_width = 110
    gap = 55
    max_rows = max((row["avg_rows"] for row in summary), default=1.0) or 1.0
    parts = [
        '<line x1="90" y1="500" x2="930" y2="500" stroke="#475569" stroke-width="2"/>',
        '<line x1="100" y1="110" x2="100" y2="500" stroke="#475569" stroke-width="2"/>',
    ]
    for tick in range(5):
        value = max_rows * tick / 4
        y = chart_bottom - chart_height * (value / max_rows)
        parts.append(f'<line x1="100" y1="{y:.1f}" x2="930" y2="{y:.1f}" stroke="#E2E8F0" stroke-width="1"/>')
        parts.append(
            f'<text x="45" y="{y + 5:.1f}" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="16" fill="#475569">{value:.1f}</text>'
        )
    for i, row in enumerate(summary):
        x = chart_left + i * (bar_width + gap)
        h = chart_height * (row["avg_rows"] / max_rows)
        y = chart_bottom - h
        parts.append(f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{h:.1f}" rx="10" fill="#14B8A6"/>')
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 12:.1f}" text-anchor="middle" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="18" font-weight="700" fill="#0F172A">{row["avg_rows"]:.1f}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="535" text-anchor="middle" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="16" fill="#334155">{row["label"]}</text>'
        )
    (FIGURES_DIR / "avg_rows_by_config.svg").write_text(
        wrap_svg("Average Rows Extracted", "".join(parts), width, height),
        encoding="utf-8",
    )


def heat_color(value: float) -> str:
    light = (240, 249, 255)
    dark = (14, 165, 233)
    rgb = tuple(int(light[i] + (dark[i] - light[i]) * value) for i in range(3))
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def save_marketplace_heatmap_svg(marketplaces: list[str], matrix: dict[str, dict[str, float]]) -> None:
    width = 1000
    height = 520
    start_x = 220
    start_y = 130
    cell_w = 140
    cell_h = 80
    labels = [CONFIG_LABELS[c] for c in CONFIG_ORDER]
    parts = []
    for col, label in enumerate(labels):
        x = start_x + col * cell_w + cell_w / 2
        parts.append(
            f'<text x="{x:.1f}" y="110" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="16" font-weight="600" fill="#334155">{label}</text>'
        )
    for row_idx, marketplace in enumerate(marketplaces):
        y = start_y + row_idx * cell_h
        parts.append(
            f'<text x="200" y="{y + 48:.1f}" text-anchor="end" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="18" fill="#334155">{marketplace}</text>'
        )
        for col_idx, label in enumerate(labels):
            x = start_x + col_idx * cell_w
            value = matrix.get(marketplace, {}).get(label, 0.0)
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 4}" height="{cell_h - 4}" '
                f'rx="10" fill="{heat_color(value)}" stroke="#E2E8F0"/>'
            )
            parts.append(
                f'<text x="{x + (cell_w - 4) / 2:.1f}" y="{y + 46:.1f}" text-anchor="middle" '
                f'font-family="Segoe UI, Arial, sans-serif" font-size="20" font-weight="700" fill="#0F172A">{value:.0%}</text>'
            )
    (FIGURES_DIR / "marketplace_success_heatmap.svg").write_text(
        wrap_svg("Marketplace Success Rate Heatmap", "".join(parts), width, height),
        encoding="utf-8",
    )


def markdown_cell(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code_cell(code: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": code.splitlines(keepends=True),
    }


def build_notebook(rows: list[dict], summary: list[dict], report: dict) -> dict:
    headline = report.get("headline", {})
    without_rate = headline.get("without_aegis_rate", 0.0)
    with_rate = headline.get("with_aegis_rate", 0.0)
    improvement = headline.get("improvement_pp", with_rate - without_rate)
    summary_json = json.dumps(summary, indent=2)
    failed_rows = [row for row in rows if not row.get("success")]
    failed_json = json.dumps(failed_rows, indent=2)

    cells = [
        markdown_cell(
            "# AEGIS RL Analysis\n\n"
            f"- Without AEGIS success rate: **{without_rate:.0%}**\n"
            f"- Full AEGIS success rate: **{with_rate:.0%}**\n"
            f"- Improvement: **{improvement:.0%}**\n\n"
            "This notebook is generated directly from the existing evaluation artifacts in `eval/results`."
        ),
        code_cell(
            "from pathlib import Path\n"
            "from IPython.display import SVG, display\n"
            "import json\n\n"
            "ROOT = Path.cwd()\n"
            "FIGURES_DIR = ROOT / 'docs' / 'figures'\n"
            "summary = json.loads('''" + summary_json.replace("\\", "\\\\").replace("'''", "\\'\\'\\'") + "''')\n"
            "failures = json.loads('''" + failed_json.replace("\\", "\\\\").replace("'''", "\\'\\'\\'") + "''')"
        ),
        markdown_cell("## Summary Metrics"),
        code_cell("summary"),
        markdown_cell("## Generated Visuals"),
        code_cell(
            "for name in [\n"
            "    'success_rate_by_config.svg',\n"
            "    'avg_rows_by_config.svg',\n"
            "    'marketplace_success_heatmap.svg',\n"
            "]:\n"
            "    display(SVG(filename=str(FIGURES_DIR / name)))"
        ),
        markdown_cell("## Failure Cases"),
        code_cell("failures"),
    ]
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_results()
    report = load_report()
    summary = summarize_by_config(rows)
    marketplaces, matrix = summarize_marketplace(rows)
    save_success_rate_svg(summary)
    save_rows_svg(summary)
    save_marketplace_heatmap_svg(marketplaces, matrix)
    NOTEBOOK_PATH.write_text(json.dumps(build_notebook(rows, summary, report), indent=2), encoding="utf-8")
    print(f"Wrote notebook: {NOTEBOOK_PATH}")
    print(f"Wrote figures to: {FIGURES_DIR}")


if __name__ == "__main__":
    main()
