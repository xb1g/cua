"""Seed the AEGIS dashboard with demo data for the hackathon presentation.

Usage:
    python scripts/demo_seed.py          # seed all views
    python scripts/demo_seed.py --only browsers
    python scripts/demo_seed.py --only verdicts
    python scripts/demo_seed.py --only bargains

Assumes the UI server is running on localhost:8555.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

BASE = "http://localhost:8555"


def seed_browsers(client: httpx.Client) -> None:
    marketplaces = [
        "craigslist", "craigslist", "craigslist", "craigslist",
        "fb_marketplace", "fb_marketplace", "fb_marketplace", "fb_marketplace",
        "offerup", "offerup", "offerup", "offerup",
        "mercari", "mercari", "mercari", "mercari",
        "ebay", "ebay", "ebay", "ebay",
        "reverb", "reverb", "reverb", "reverb",
    ]
    for i, mp in enumerate(marketplaces):
        client.post(f"{BASE}/api/browsers", json={
            "id": f"kernel-{i:02d}",
            "live_view_url": f"about:blank#{mp}-{i}",
            "marketplace": mp,
        })
    print(f"  seeded {len(marketplaces)} browser sessions")


DEMO_VERDICTS = [
    {"type": "SECURITY", "result": "BLOCKED", "reason": "prompt injection pattern matched: ignore all previous instructions"},
    {"type": "MARKETPLACE", "result": "REJECTED", "reason": "rejected: replica/knockoff and user requested authentic — 'MCM Style Lounge Chair - Inspired by Eames'"},
    {"type": "MARKETPLACE", "result": "REJECTED", "reason": "rejected: scam-pattern phrasing matched — 'MUST SHIP zelle only'"},
    {"type": "VERIFIER", "result": "PASSED", "reason": "extracted 8 structured rows matching task schema"},
    {"type": "SECURITY", "result": "PASSED", "reason": "action policy check passed — navigate to craigslist.org"},
    {"type": "MARKETPLACE", "result": "WARNING", "reason": "seller account <2 weeks old — elevated caution"},
    {"type": "VERIFIER", "result": "PASSED", "reason": "extracted 6 structured rows matching task schema"},
    {"type": "SECURITY", "result": "BLOCKED", "reason": "marketplace dangerous action matched: message seller"},
    {"type": "MARKETPLACE", "result": "REJECTED", "reason": "rejected: 45mi > 15mi requested — too far from search area"},
    {"type": "SECURITY", "result": "PASSED", "reason": "action policy check passed — scroll down page"},
    {"type": "VERIFIER", "result": "PASSED", "reason": "extracted 10 structured rows matching task schema"},
    {"type": "MARKETPLACE", "result": "REJECTED", "reason": "zero photos (high scam risk) — listing from quicksale_now"},
    {"type": "SECURITY", "result": "BLOCKED", "reason": "marketplace dangerous action matched: make an offer"},
    {"type": "MARKETPLACE", "result": "WARNING", "reason": "listing >1 month old — stale listing, may no longer be available"},
    {"type": "VERIFIER", "result": "PASSED", "reason": "success — 12 rows extracted in 34.2s across 3 marketplaces"},
    {"type": "SECURITY", "result": "PASSED", "reason": "action policy check passed — type search query"},
    {"type": "MARKETPLACE", "result": "REJECTED", "reason": "rejected: scam-pattern phrasing matched — 'non-refundable deposit to hold'"},
    {"type": "SCANNER", "result": "PASSED", "reason": "visual injection scan clean — no adversarial overlays detected"},
    {"type": "SCANNER", "result": "BLOCKED", "reason": "visual injection detected: white-on-white hidden text in listing image"},
    {"type": "VERIFIER", "result": "PASSED", "reason": "extracted 5 structured rows — all prices verified against budget"},
]


def seed_verdicts(client: httpx.Client) -> None:
    for v in DEMO_VERDICTS:
        client.post(f"{BASE}/api/verdicts", json=v)
        time.sleep(0.05)
    print(f"  seeded {len(DEMO_VERDICTS)} verdicts")


def seed_bargains(client: httpx.Client) -> None:
    r = client.get(f"{BASE}/api/bargains")
    data = r.json()
    print(f"  bargain board has {len(data)} listings (mock data auto-loaded)")


def check_server(client: httpx.Client) -> bool:
    try:
        r = client.get(f"{BASE}/")
        return r.status_code == 200
    except httpx.ConnectError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed AEGIS demo dashboard")
    parser.add_argument("--only", choices=["browsers", "verdicts", "bargains"],
                        help="Seed only one view")
    args = parser.parse_args()

    client = httpx.Client(timeout=10.0)

    if not check_server(client):
        print("ERROR: UI server not running on localhost:8555")
        print("Start it with: python -m cua_loop.ui_server")
        return 1

    print("AEGIS Demo Seeder")
    print("=" * 40)

    if args.only is None or args.only == "browsers":
        print("Seeding browser grid...")
        seed_browsers(client)

    if args.only is None or args.only == "verdicts":
        print("Seeding verdict feed...")
        seed_verdicts(client)

    if args.only is None or args.only == "bargains":
        print("Checking bargain board...")
        seed_bargains(client)

    print("=" * 40)
    print("Dashboard ready at:")
    print(f"  Main:       {BASE}/")
    print(f"  Split:      {BASE}/split")
    print(f"  Bargains:   {BASE}/bargains")
    print(f"  Verdicts:   {BASE}/verdicts")
    print(f"  Browsers:   {BASE}/browsers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
