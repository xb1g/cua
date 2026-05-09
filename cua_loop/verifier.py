"""Verifier using DOM extraction + optional LLM validation."""

from __future__ import annotations

import os

from cua_loop.types import Trajectory, VerifierResult
from cua_loop.validator import get_validator

VERIFY_MODE = os.getenv("VERIFY_MODE", "local")
MIN_LISTINGS = int(os.getenv("VERIFIER_MIN_LISTINGS", "3"))


def verify(traj: Trajectory) -> VerifierResult:
    """Verifier using DOM extraction results, with optional LLM validation.

    Defaults to local heuristics. When VALIDATOR_PROVIDER=kimi, also
    runs intelligent validation on extracted results.
    """

    extracted = traj.extracted
    if extracted is None:
        return VerifierResult(
            success=False,
            rows_extracted=0,
            schema_valid=False,
            reason="No DOM extraction performed",
        )

    if not isinstance(extracted, list):
        return VerifierResult(
            success=False,
            rows_extracted=0,
            schema_valid=False,
            reason=f"Invalid extracted type: {type(extracted).__name__}",
        )

    listings = extracted
    rows_extracted = len(listings)

    if rows_extracted == 0:
        return VerifierResult(
            success=False,
            rows_extracted=0,
            schema_valid=False,
            reason="No listings extracted",
        )

    valid_listings = 0
    for item in listings:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        price = item.get("price")
        if title and price:
            valid_listings += 1

    schema_valid = valid_listings >= MIN_LISTINGS
    success = schema_valid and valid_listings >= MIN_LISTINGS

    reason = f"{valid_listings}/{rows_extracted} listings with title+price"
    if not success:
        if rows_extracted < MIN_LISTINGS:
            reason = f"Only {rows_extracted} listings (min {MIN_LISTINGS})"
        else:
            reason = f"Only {valid_listings} valid (title+price)"

    if success and traj.extracted:
        validator = get_validator()
        llm_verification = validator.verify_results(
            task=traj.task,
            extracted=traj.extracted,
            screenshots=[s.screenshot_url for s in traj.steps if s.screenshot_url],
        )
        if not llm_verification.valid:
            success = False
            reason = f"{reason[:60]} | validator: {llm_verification.feedback[:60]}"
        elif llm_verification.score > 0:
            reason = f"{reason[:60]} | score={llm_verification.score:.2f}"

    return VerifierResult(
        success=success,
        rows_extracted=rows_extracted,
        schema_valid=schema_valid,
        reason=reason[:80],
    )