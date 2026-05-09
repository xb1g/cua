"""Generate a comprehensive, presentation-ready visualization dashboard."""

import json
from pathlib import Path
from datetime import datetime
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

OUTPUT_DIR = Path("trajectories")
OUTPUT_DIR.mkdir(exist_ok=True)


def load_data():
    """Load experiment data from CSV and policy JSON."""
    csv_path = OUTPUT_DIR / "episodes.csv"
    policy_path = OUTPUT_DIR / "aegis-rl-policy.json"

    if not csv_path.exists():
        print(f"No data found at {csv_path}")
        return None, None

    import csv
    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = {}
            for k, v in row.items():
                if v == "True":
                    parsed[k] = True
                elif v == "False":
                    parsed[k] = False
                else:
                    try:
                        parsed[k] = float(v)
                    except ValueError:
                        parsed[k] = v
            rows.append(parsed)

    with open(policy_path) as f:
        policy = json.load(f).get("stats", {})

    return rows, policy


def plot_dashboard(rows, policy):
    """Generate the full presentation dashboard."""

    episodes = [r["episode"] for r in rows]
    rewards = [r["reward"] for r in rows]
    strategies = [r["strategy"] for r in rows]
    successes = [r["success"] for r in rows]
    rows_count = [r["rows"] for r in rows]

    fig = plt.figure(figsize=(24, 16))
    fig.patch.set_facecolor("#0a0a14")
    gs = gridspec.GridSpec(4, 8, figure=fig, hspace=0.3, wspace=0.3)

    # Color scheme
    BG = "#0a0a14"
    CARD_BG = "#14142a"
    ACCENT1 = "#00d4ff"   # cyan
    ACCENT2 = "#ff6b6b"   # red
    ACCENT3 = "#4ecdc4"   # teal
    ACCENT4 = "#ffe66d"   # yellow
    TEXT = "#e0e0e0"
    GRID = "#2a2a4a"

    # ===== TITLE SECTION =====
    ax_title = fig.add_subplot(gs[0, :4])
    ax_title.set_facecolor(BG)
    ax_title.axis("off")
    ax_title.text(0.5, 0.7, "AEGIS RL EXPERIMENT", fontsize=36, fontweight="bold",
                  color=ACCENT1, ha="center", va="center", fontfamily="monospace")
    ax_title.text(0.5, 0.35, "Autonomous Web Scraping Agent", fontsize=18,
                  color=TEXT, ha="center", va="center", fontfamily="sans-serif")
    ax_title.text(0.5, 0.1, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                  fontsize=12, color="#888888", ha="center", va="center")

    # ===== KPI CARDS =====
    total_eps = len(rows)
    total_success = sum(1 for r in successes if r)
    success_rate = total_success / total_eps * 100 if total_eps > 0 else 0
    avg_reward = np.mean(rewards)
    avg_runtime = np.mean([r["runtime_seconds"] for r in rows])

    kpis = [
        ("EPISODES", str(total_eps), ACCENT1),
        ("SUCCESS RATE", f"{success_rate:.1f}%", ACCENT3),
        ("AVG REWARD", f"{avg_reward:.3f}", ACCENT4),
        ("AVG RUNTIME", f"{avg_runtime:.1f}s", ACCENT2),
    ]

    for i, (label, value, color) in enumerate(kpis):
        ax = fig.add_subplot(gs[0, 4 + i])
        ax.set_facecolor(BG)
        ax.axis("off")
        rect = FancyBboxPatch((0.05, 0.1), 0.9, 0.8, boxstyle="round,pad=0.02",
                               facecolor=CARD_BG, edgecolor=color, linewidth=2)
        ax.add_patch(rect)
        ax.text(0.5, 0.65, value, fontsize=32, fontweight="bold",
                color=color, ha="center", va="center")
        ax.text(0.5, 0.25, label, fontsize=12, color=TEXT,
                ha="center", va="center")

    # ===== REWARD CURVE (Large) =====
    ax_curve = fig.add_subplot(gs[1, :5])
    ax_pie = fig.add_subplot(gs[1, 5])
    ax_bar = fig.add_subplot(gs[1, 6:])
    ax_bar.set_facecolor(BG)
    strat_success = {}
    strat_counts = Counter(strategies)
    for r in rows:
        s = r["strategy"]
        if s not in strat_success:
            strat_success[s] = {"success": 0, "total": 0}
        strat_success[s]["total"] += 1
        if r["success"]:
            strat_success[s]["success"] += 1

    strat_names = list(strat_success.keys())
    success_rates = [strat_success[s]["success"] / strat_success[s]["total"] * 100 for s in strat_names]
    bars = ax_bar.bar(strat_names, success_rates, color=[ACCENT1, ACCENT2, ACCENT3, ACCENT4][:len(strat_names)])
    ax_bar.set_ylabel("Success Rate (%)", color=TEXT, fontsize=10)
    ax_bar.set_title("Success Rate by Strategy", color=TEXT, fontsize=14, fontweight="bold", pad=10)
    ax_bar.tick_params(colors=TEXT, axis="x", rotation=20)
    ax_bar.set_facecolor(CARD_BG)
    ax_bar.grid(True, alpha=0.2, axis="y", color=GRID)
    for bar, rate in zip(bars, success_rates):
        ax_bar.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                    f"{rate:.0f}%", ha="center", color=TEXT, fontsize=10)

    # ===== HEATMAP: SUCCESS x STRATEGY =====
    ax_heat = fig.add_subplot(gs[2, :3])
    ax_rows = fig.add_subplot(gs[2, 3:6])
    ax_eps = fig.add_subplot(gs[2, 6:])
    ax_eps.set_facecolor(CARD_BG)
    epsilons = [r["epsilon"] for r in rows]
    ax_eps.plot(episodes, epsilons, "o-", color=ACCENT4, linewidth=2, markersize=5)
    ax_eps.fill_between(episodes, epsilons, alpha=0.2, color=ACCENT4)
    ax_eps.set_xlabel("Episode", color=TEXT)
    ax_eps.set_ylabel("Epsilon", color=TEXT)
    ax_eps.set_title("Exploration Rate (Epsilon) Decay", color=TEXT, fontsize=14, fontweight="bold", pad=10)
    ax_eps.tick_params(colors=TEXT)
    ax_eps.set_facecolor(CARD_BG)
    ax_eps.grid(True, alpha=0.2, color=GRID)

    # ===== POLICY STATS TABLE =====
    ax_table = fig.add_subplot(gs[3, :4])
    ax_table.set_facecolor(CARD_BG)
    ax_table.axis("off")
    ax_table.set_title("Bandit Policy Statistics", color=TEXT, fontsize=14, fontweight="bold", pad=10)

    table_data = []
    headers = ["Strategy", "Pulls", "Mean Reward", "Success Rate", "Beta α", "Beta β"]
    for name, stats in sorted(policy.items(), key=lambda x: x[1].get("mean_reward", 0), reverse=True):
        pulls = int(stats.get("pulls", 0))
        mean_r = stats.get("mean_reward", 0)
        succ = stats.get("successes", 0)
        fail = stats.get("failures", 0)
        succ_rate = succ / pulls * 100 if pulls > 0 else 0
        table_data.append([
            name,
            str(pulls),
            f"{mean_r:.3f}",
            f"{succ_rate:.1f}%",
            f"{succ:.2f}",
            f"{fail:.2f}",
        ])

    table = ax_table.table(
        cellText=table_data,
        colLabels=headers,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 2)

    for (row, col), cell in table.get_celld().items():
        cell.set_facecolor(CARD_BG)
        cell.set_text_props(color=TEXT)
        cell.set_edgecolor(GRID)
        if row == 0:
            cell.set_facecolor("#1a1a3a")
            cell.set_text_props(color=ACCENT1, fontweight="bold")

    # ===== FINAL SUMMARY BOX =====
    ax_summary = fig.add_subplot(gs[3, 4:])
    ax_summary.set_facecolor(CARD_BG)
    ax_summary.axis("off")
    ax_summary.set_title("Key Findings", color=TEXT, fontsize=14, fontweight="bold", pad=10)

    best_strat = max(strat_success.items(), key=lambda x: x[1]["success"] / x[1]["total"] if x[1]["total"] > 0 else 0)
    most_used = max(strat_counts.items(), key=lambda x: x[1])
    z = np.polyfit(episodes, rewards, 1)
    trend = "Improving" if z[0] > 0.01 else "Declining" if z[0] < -0.01 else "Stable"

    findings = [
        f"Best performing strategy: {best_strat[0]} ({best_strat[1]['success'] / best_strat[1]['total'] * 100:.0f}% success)",
        f"Most frequently selected: {most_used[0]} ({most_used[1]} times)",
        f"Overall trend: {trend} (slope: {z[0]:.4f})",
        f"Total episodes: {total_eps} | Successful: {total_success} | Failed: {total_eps - total_success}",
        f"Average reward: {avg_reward:.3f} | Average runtime: {avg_runtime:.1f}s",
    ]

    for i, finding in enumerate(findings):
        ax_summary.text(0.05, 0.85 - i * 0.18, f"• {finding}",
                       fontsize=12, color=TEXT, va="top", fontfamily="sans-serif")

    # ===== FINALIZE =====
    plt.tight_layout(pad=2)

    # Add border
    for ax in fig.axes:
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID)
            spine.set_linewidth(0.5)

    output_path = OUTPUT_DIR / "dashboard.png"
    plt.savefig(str(output_path), dpi=150, facecolor=BG, bbox_inches="tight", pad_inches=0.5)
    plt.close()

    print(f"Dashboard saved to {output_path}")
    return output_path


def main():
    rows, policy = load_data()
    if rows is None:
        print("No data to visualize. Run experiment first.")
        return
    plot_dashboard(rows, policy)


if __name__ == "__main__":
    main()