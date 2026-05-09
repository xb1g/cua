"""AEGIS ablation runner — compare 5 configurations across held-out queries.

Configs (cumulative):
  no-aegis       — single attempt, no retry, no verification, no security
  +retry         — up to 5 attempts with self-critique, no verification gating
  +verification  — retry + LLM verifier judges each attempt
  +security      — retry + verification + dangerous-action policy
  full-aegis     — wide-scaling (parallel branches) + verification + security + marketplace scoring
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

from cua_loop.client import run_single_attempt
from cua_loop.marketplace import (
    MarketplaceListing,
    rank_marketplace_listings,
    score_marketplace_listing,
)
from cua_loop.runner import run_with_retry
from cua_loop.scaling import run_marketplace_scaling, run_wide_scaling
from cua_loop.types import AttemptResult, RunResult, Trajectory, VerifierResult
from cua_loop.verifier import verify

console = Console()

DEFAULT_QUERIES = Path(__file__).parent / "held_out_queries.jsonl"
DEFAULT_OUTPUT = Path(__file__).parent / "results"


@dataclass
class AblationConfig:
    name: str
    use_retry: bool = False
    use_verification: bool = False
    use_security: bool = False
    use_wide_scaling: bool = False
    use_marketplace_scoring: bool = False
    max_attempts: int = 5
    width: int = 3


ALL_CONFIGS: dict[str, AblationConfig] = {
    "no-aegis": AblationConfig(
        name="no-aegis",
    ),
    "+retry": AblationConfig(
        name="+retry",
        use_retry=True,
    ),
    "+verification": AblationConfig(
        name="+verification",
        use_retry=True,
        use_verification=True,
    ),
    "+security": AblationConfig(
        name="+security",
        use_retry=True,
        use_verification=True,
        use_security=True,
    ),
    "full-aegis": AblationConfig(
        name="full-aegis",
        use_retry=True,
        use_verification=True,
        use_security=True,
        use_wide_scaling=True,
        use_marketplace_scoring=True,
    ),
}


@dataclass
class QueryResult:
    query: dict[str, Any]
    config_name: str
    success: bool
    rows_extracted: int = 0
    schema_valid: bool = False
    num_attempts: int = 1
    duration_s: float = 0.0
    blocked_actions: int = 0
    marketplace_score: float | None = None
    verifier_reason: str = ""
    error: str | None = None


def _run_no_aegis(query: dict[str, Any]) -> QueryResult:
    """Single shot — no retry, no verification, no security."""
    started = time.time()
    env_backup = os.environ.get("AEGIS_ALLOW_DANGEROUS_ACTIONS")
    os.environ["AEGIS_ALLOW_DANGEROUS_ACTIONS"] = "1"
    try:
        traj = run_single_attempt(task=query["query"], url=query.get("url"))
        v = verify(traj)
        return QueryResult(
            query=query,
            config_name="no-aegis",
            success=v.success,
            rows_extracted=v.rows_extracted,
            schema_valid=v.schema_valid,
            num_attempts=1,
            duration_s=time.time() - started,
            blocked_actions=0,
            verifier_reason=v.reason,
        )
    except Exception as e:
        return QueryResult(
            query=query,
            config_name="no-aegis",
            success=False,
            duration_s=time.time() - started,
            error=str(e),
        )
    finally:
        if env_backup is None:
            os.environ.pop("AEGIS_ALLOW_DANGEROUS_ACTIONS", None)
        else:
            os.environ["AEGIS_ALLOW_DANGEROUS_ACTIONS"] = env_backup


def _run_with_config(query: dict[str, Any], config: AblationConfig) -> QueryResult:
    """Run a query under a specific ablation config."""
    started = time.time()

    env_backup_security = os.environ.get("AEGIS_ALLOW_DANGEROUS_ACTIONS")
    if not config.use_security:
        os.environ["AEGIS_ALLOW_DANGEROUS_ACTIONS"] = "1"

    try:
        if config.use_wide_scaling and config.use_marketplace_scoring:
            run = run_marketplace_scaling(
                task=query["query"],
                width_per_site=1,
            )
        elif config.use_wide_scaling:
            run = run_wide_scaling(
                task=query["query"],
                url=query.get("url"),
                width=config.width,
            )
        elif config.use_retry:
            run = run_with_retry(
                task=query["query"],
                url=query.get("url"),
                max_attempts=config.max_attempts,
            )
        else:
            return _run_no_aegis(query)

        blocked = sum(
            1
            for attempt in run.attempts
            for step in attempt.trajectory.steps
            if step.blocked
        )

        if not config.use_verification:
            v = verify(run.attempts[-1].trajectory)
        else:
            v = run.attempts[-1].verifier

        marketplace_score = None
        if config.use_marketplace_scoring and run.extracted:
            try:
                listings = _extract_marketplace_listings(run.extracted, query)
                if listings:
                    scores = rank_marketplace_listings(listings, query["query"])
                    marketplace_score = scores[0].score if scores else None
            except Exception:
                pass

        return QueryResult(
            query=query,
            config_name=config.name,
            success=v.success if config.use_verification else run.success,
            rows_extracted=v.rows_extracted,
            schema_valid=v.schema_valid,
            num_attempts=len(run.attempts),
            duration_s=time.time() - started,
            blocked_actions=blocked,
            marketplace_score=marketplace_score,
            verifier_reason=v.reason,
        )
    except Exception as e:
        return QueryResult(
            query=query,
            config_name=config.name,
            success=False,
            duration_s=time.time() - started,
            error=str(e),
        )
    finally:
        if env_backup_security is None:
            os.environ.pop("AEGIS_ALLOW_DANGEROUS_ACTIONS", None)
        else:
            os.environ["AEGIS_ALLOW_DANGEROUS_ACTIONS"] = env_backup_security


def _extract_marketplace_listings(
    extracted: Any, query: dict[str, Any]
) -> list[MarketplaceListing]:
    """Best-effort conversion of raw extracted data to MarketplaceListing objects."""
    if not isinstance(extracted, list):
        return []
    listings = []
    for item in extracted:
        if not isinstance(item, dict):
            continue
        price = item.get("price")
        if isinstance(price, str):
            import re
            m = re.search(r"[0-9][0-9,]*(?:\.\d+)?", price)
            price = float(m.group(0).replace(",", "")) if m else None
        listings.append(
            MarketplaceListing(
                title=str(item.get("title", item.get("name", "Unknown"))),
                price=price,
                condition=item.get("condition"),
                seller=item.get("seller"),
                marketplace=query.get("marketplace"),
                distance_mi=item.get("distance_mi"),
                posted_age_text=item.get("posted_age_text"),
                photo_count=item.get("photo_count"),
            )
        )
    return listings


def load_queries(path: Path) -> list[dict[str, Any]]:
    queries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    return queries


def run_ablation(
    configs: list[str] | None = None,
    queries_path: Path = DEFAULT_QUERIES,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, list[QueryResult]]:
    """Run the full ablation and persist results."""
    output_dir.mkdir(parents=True, exist_ok=True)
    queries = load_queries(queries_path)
    config_names = configs or list(ALL_CONFIGS.keys())
    results: dict[str, list[QueryResult]] = {}

    for cname in config_names:
        config = ALL_CONFIGS[cname]
        console.rule(f"[bold magenta]Config: {cname}")
        config_results: list[QueryResult] = []

        for i, query in enumerate(queries):
            console.print(
                f"  [{i + 1}/{len(queries)}] {query['query'][:60]} "
                f"({query['marketplace']})"
            )
            result = _run_with_config(query, config)
            config_results.append(result)
            status = "[green]OK[/green]" if result.success else "[red]FAIL[/red]"
            console.print(
                f"    {status} rows={result.rows_extracted} "
                f"attempts={result.num_attempts} "
                f"duration={result.duration_s:.1f}s"
            )

        results[cname] = config_results

        out_file = output_dir / f"{cname.replace('+', 'plus_')}.json"
        out_file.write_text(
            json.dumps(
                [_result_to_dict(r) for r in config_results],
                indent=2,
            )
        )
        console.print(f"  Saved -> {out_file}")

    _print_summary(results)
    return results


def _result_to_dict(r: QueryResult) -> dict[str, Any]:
    return {
        "query": r.query["query"],
        "marketplace": r.query.get("marketplace"),
        "category": r.query.get("category"),
        "config": r.config_name,
        "success": r.success,
        "rows_extracted": r.rows_extracted,
        "schema_valid": r.schema_valid,
        "num_attempts": r.num_attempts,
        "duration_s": round(r.duration_s, 2),
        "blocked_actions": r.blocked_actions,
        "marketplace_score": r.marketplace_score,
        "verifier_reason": r.verifier_reason,
        "error": r.error,
    }


def _print_summary(results: dict[str, list[QueryResult]]) -> None:
    table = Table(title="Ablation Summary")
    table.add_column("Config", style="bold")
    table.add_column("Success Rate", justify="right")
    table.add_column("Avg Rows", justify="right")
    table.add_column("Avg Attempts", justify="right")
    table.add_column("Blocked", justify="right")
    table.add_column("Avg Duration", justify="right")

    for cname, cresults in results.items():
        n = len(cresults)
        successes = sum(1 for r in cresults if r.success)
        avg_rows = sum(r.rows_extracted for r in cresults) / max(n, 1)
        avg_attempts = sum(r.num_attempts for r in cresults) / max(n, 1)
        total_blocked = sum(r.blocked_actions for r in cresults)
        avg_duration = sum(r.duration_s for r in cresults) / max(n, 1)

        table.add_row(
            cname,
            f"{successes}/{n} ({100 * successes / max(n, 1):.0f}%)",
            f"{avg_rows:.1f}",
            f"{avg_attempts:.1f}",
            str(total_blocked),
            f"{avg_duration:.1f}s",
        )

    console.print()
    console.print(table)

    if "no-aegis" in results and "full-aegis" in results:
        no_rate = sum(1 for r in results["no-aegis"] if r.success) / max(len(results["no-aegis"]), 1)
        full_rate = sum(1 for r in results["full-aegis"] if r.success) / max(len(results["full-aegis"]), 1)
        console.print()
        console.print(
            f"[bold]Without AEGIS: {100 * no_rate:.0f}%. "
            f"With AEGIS: {100 * full_rate:.0f}%.[/bold]"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="AEGIS ablation runner")
    parser.add_argument(
        "--configs",
        type=str,
        default=None,
        help="Comma-separated config names (default: all)",
    )
    parser.add_argument(
        "--queries",
        type=str,
        default=str(DEFAULT_QUERIES),
        help="Path to queries JSONL",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help="Output directory for results",
    )
    args = parser.parse_args()
    configs = args.configs.split(",") if args.configs else None
    run_ablation(
        configs=configs,
        queries_path=Path(args.queries),
        output_dir=Path(args.output),
    )


if __name__ == "__main__":
    main()
