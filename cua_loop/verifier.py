"""Local verifier using DOM extraction - no external API calls."""

from __future__ import annotations

import os

from cua_loop.types import Trajectory, VerifierResult

VERIFY_MODE = os.getenv("VERIFY_MODE", "local")
MIN_LISTINGS = int(os.getenv("VERIFIER_MIN_LISTINGS", "3"))


def verify(traj: Trajectory) -> VerifierResult:
    """Local verifier using DOM extraction results.

    No external API calls. Uses extracted listings from trajectory.
    """
    if VERIFY_MODE != "local":
        return VerifierResult(
            success=False,
            reason=f"unsupported VERIFY_MODE={VERIFY_MODE}, only 'local' supported",
        )

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

    return VerifierResult(
        success=success,
        rows_extracted=rows_extracted,
        schema_valid=schema_valid,
        reason=reason[:80],
    )