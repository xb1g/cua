"""Central orchestrator — smart swarm coordinator for AEGIS.

Replaces dumb-parallel fan-out with strategy-diverse branches, real-time
monitoring, adaptive mode cascading (CUA → DOM → fallback), and early
termination when enough verified listings are collected.
"""

from __future__ import annotations

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console

from cua_loop.backends import make_backend
from cua_loop.client import run_single_attempt
from cua_loop.dom_extractor import extract_listings
from cua_loop.fallback_scripts import run_fallback_extraction
from cua_loop.marketplace import (
    MarketplaceScore,
    coerce_marketplace_listing,
    dedupe_across_marketplaces,
    score_marketplace_listing,
)
from cua_loop.query_parser import ParsedQuery, parse_query
from cua_loop.runner import _persist
from cua_loop.sites import MARKETPLACE_REGISTRY
from cua_loop.types import AttemptResult, RunResult, Trajectory, VerifierResult
from cua_loop.url_params import generate_all_filtered_urls
from cua_loop.verifier import verify

console = Console()

EARLY_STOP_THRESHOLD = int(os.getenv("AEGIS_EARLY_STOP", "10"))
CUA_FAIL_STEP_LIMIT = int(os.getenv("AEGIS_CUA_FAIL_STEPS", "3"))


SEARCH_STRATEGIES = [
    {
        "name": "keyword_default",
        "hint": "Search using the exact keywords from the query. Do not modify the search terms.",
    },
    {
        "name": "category_browse",
        "hint": "Instead of searching by keyword, browse the relevant category. "
                "Navigate to the category page (e.g. Furniture, Electronics, Musical Instruments) "
                "and scan listings visually.",
    },
    {
        "name": "price_sorted",
        "hint": "Search by keyword but sort results by price (lowest first). "
                "Focus on finding the cheapest options that match the criteria.",
    },
    {
        "name": "newest_first",
        "hint": "Search by keyword but sort by newest listings first. "
                "Recently posted items are more likely to still be available.",
    },
]


@dataclass
class BranchConfig:
    branch_index: int
    marketplace: str
    url: str
    strategy: dict[str, str]
    task: str


class OrchestratorResult(BaseModel):
    task: str
    parsed_query: ParsedQuery
    success: bool
    total_listings_found: int = 0
    total_branches: int = 0
    branches_succeeded: int = 0
    branches_failed: int = 0
    early_stopped: bool = False
    marketplace_scores: list[MarketplaceScore] | None = None
    raw_listings: list[dict[str, Any]] = Field(default_factory=list)
    total_duration_s: float = 0.0
    branch_details: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class _SharedState:
    """Thread-safe accumulator for cross-branch results."""

    def __init__(self, early_stop: int = EARLY_STOP_THRESHOLD):
        self._lock = threading.Lock()
        self._listings: list[dict[str, Any]] = []
        self._early_stop = early_stop
        self.should_stop = threading.Event()

    def add_listings(self, items: list[dict[str, Any]]) -> int:
        with self._lock:
            self._listings.extend(items)
            total = len(self._listings)
            if total >= self._early_stop:
                self.should_stop.set()
            return total

    @property
    def listings(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._listings)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._listings)


def _run_cua_branch(
    config: BranchConfig,
    shared: _SharedState,
) -> dict[str, Any]:
    """Run a single CUA branch with adaptive mode cascading."""
    result: dict[str, Any] = {
        "branch_index": config.branch_index,
        "marketplace": config.marketplace,
        "strategy": config.strategy["name"],
        "mode": "cua",
        "success": False,
        "listings_found": 0,
        "duration_s": 0.0,
    }
    started = time.time()

    if shared.should_stop.is_set():
        result["mode"] = "skipped"
        result["duration_s"] = time.time() - started
        return result

    extra_context = (
        f"Branch {config.branch_index} ({config.marketplace}). "
        f"Strategy: {config.strategy['hint']}"
    )

    # Phase 1: CUA attempt
    try:
        traj = run_single_attempt(
            task=config.task,
            url=config.url,
            extra_context=extra_context,
        )
        v = verify(traj)
        result["verifier_success"] = v.success
        result["rows_extracted"] = v.rows_extracted

        if v.success and isinstance(traj.extracted, list) and len(traj.extracted) > 0:
            shared.add_listings(traj.extracted)
            result["success"] = True
            result["listings_found"] = len(traj.extracted)
            result["mode"] = "cua"
            result["duration_s"] = time.time() - started
            return result
    except Exception as exc:
        result["error"] = str(exc)

    if shared.should_stop.is_set():
        result["duration_s"] = time.time() - started
        return result

    # Phase 2: DOM-only extraction on the same URL
    try:
        backend = make_backend()
        with backend as b:
            b.navigate(config.url)
            if hasattr(b, "wait_for_page_load"):
                b.wait_for_page_load()
            else:
                b.wait(2)
            dom_listings = extract_listings(b, marketplace=config.marketplace)
            if dom_listings:
                shared.add_listings(dom_listings)
                result["success"] = True
                result["listings_found"] = len(dom_listings)
                result["mode"] = "dom"
                result["duration_s"] = time.time() - started
                return result
    except Exception:
        pass

    if shared.should_stop.is_set():
        result["duration_s"] = time.time() - started
        return result

    # Phase 3: Fallback Playwright script
    try:
        fallback_listings = run_fallback_extraction(
            url=config.url,
            marketplace=config.marketplace,
        )
        if fallback_listings:
            shared.add_listings(fallback_listings)
            result["success"] = True
            result["listings_found"] = len(fallback_listings)
            result["mode"] = "fallback"
            result["duration_s"] = time.time() - started
            return result
    except Exception:
        pass

    result["duration_s"] = time.time() - started
    return result


def _assign_strategies(
    marketplaces: dict[str, str],
) -> list[BranchConfig]:
    """Assign diverse strategies across marketplace URLs."""
    branches: list[BranchConfig] = []
    idx = 0
    for mp_name, url in marketplaces.items():
        strategy = SEARCH_STRATEGIES[idx % len(SEARCH_STRATEGIES)]
        branches.append(BranchConfig(
            branch_index=idx,
            marketplace=mp_name,
            url=url,
            strategy=strategy,
            task="",
        ))
        idx += 1
    return branches


def orchestrate(
    query: str,
    max_browsers: int = 12,
    location: str | None = None,
    skip_login_required: bool = True,
    early_stop: int = EARLY_STOP_THRESHOLD,
) -> OrchestratorResult:
    """Single entry point for intelligent multi-marketplace search."""
    started = time.time()
    parsed = parse_query(query)

    urls = generate_all_filtered_urls(
        parsed_query=parsed,
        location=location,
        skip_login_required=skip_login_required,
    )

    branches = _assign_strategies(urls)
    for b in branches:
        b.task = query

    branches = branches[:max_browsers]
    shared = _SharedState(early_stop=early_stop)

    console.rule(f"[bold]AEGIS orchestrator: {len(branches)} branches across {len(urls)} marketplaces")
    for b in branches:
        console.print(f"  branch {b.branch_index}: {b.marketplace} / {b.strategy['name']}")

    branch_details: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(len(branches), max_browsers)) as pool:
        futures: dict[Future, BranchConfig] = {
            pool.submit(_run_cua_branch, branch, shared): branch
            for branch in branches
        }
        for future in as_completed(futures):
            branch_cfg = futures[future]
            try:
                detail = future.result()
            except Exception as exc:
                detail = {
                    "branch_index": branch_cfg.branch_index,
                    "marketplace": branch_cfg.marketplace,
                    "strategy": branch_cfg.strategy["name"],
                    "mode": "error",
                    "success": False,
                    "error": str(exc),
                    "duration_s": 0.0,
                }
            branch_details.append(detail)
            status = "[green]OK[/green]" if detail.get("success") else "[red]FAIL[/red]"
            console.print(
                f"  branch {detail['branch_index']} ({detail['marketplace']}): "
                f"{status} mode={detail.get('mode')} listings={detail.get('listings_found', 0)}"
            )

    all_listings = shared.listings
    succeeded = sum(1 for d in branch_details if d.get("success"))

    mp_scores = None
    if all_listings:
        scores = []
        for item in all_listings:
            if not isinstance(item, dict):
                continue
            try:
                listing = coerce_marketplace_listing(item)
                scores.append(score_marketplace_listing(listing, query))
            except Exception:
                continue
        if scores:
            mp_scores = dedupe_across_marketplaces(scores)

    result = OrchestratorResult(
        task=query,
        parsed_query=parsed,
        success=len(all_listings) > 0,
        total_listings_found=len(all_listings),
        total_branches=len(branches),
        branches_succeeded=succeeded,
        branches_failed=len(branches) - succeeded,
        early_stopped=shared.should_stop.is_set(),
        marketplace_scores=mp_scores,
        raw_listings=all_listings,
        total_duration_s=time.time() - started,
        branch_details=sorted(branch_details, key=lambda d: d["branch_index"]),
    )

    console.rule("[bold]Orchestrator Summary")
    console.print(
        f"  {succeeded}/{len(branches)} branches succeeded | "
        f"{len(all_listings)} total listings | "
        f"{len(mp_scores or [])} after dedup | "
        f"early_stop={result.early_stopped} | "
        f"{result.total_duration_s:.1f}s"
    )

    return result


def main() -> None:
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="AEGIS marketplace orchestrator")
    parser.add_argument("--query", required=True, help="Natural language search query")
    parser.add_argument("--max-browsers", type=int, default=12)
    parser.add_argument("--location", type=str, default=None)
    parser.add_argument("--early-stop", type=int, default=EARLY_STOP_THRESHOLD)
    args = parser.parse_args()

    result = orchestrate(
        query=args.query,
        max_browsers=args.max_browsers,
        location=args.location,
        early_stop=args.early_stop,
    )
    console.print(f"\nsuccess={result.success} listings={result.total_listings_found}")
    if result.marketplace_scores:
        console.print(f"top scores: {len(result.marketplace_scores)} scored+deduped listings")
        for s in result.marketplace_scores[:5]:
            console.print(f"  {s.listing.title}: ${s.listing.price} (score={s.score:.1f})")


if __name__ == "__main__":
    main()
