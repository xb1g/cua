[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)

# AEGIS

**AEGIS takes a 4B CUA model from 0% to 100% success rate through pure inference-time engineering — no retraining, no new weights.**

Open-source reliability and safety middleware for computer-use agents. Model-agnostic: works with Northstar, Kimi K2.6, or any OpenAI-compatible CUA.

## The Problem

CUA models fail in predictable, compounding ways. One wrong click cascades into total failure. The model doesn't know it went wrong. It messages strangers, clicks phishing links, enters credentials on spoofed pages. On real marketplace search tasks, **Northstar 4B succeeds 0% of the time without a wrapper.**

## The Solution

AEGIS wraps the CUA loop with four inference-time layers:

```
Query → Orchestrator → Wide Scaling → Action Verification → Security Guardrails → Results
```

| Layer | What it does | Why it matters |
|---|---|---|
| **Orchestrator** | Coordinates the swarm: assigns strategies per branch, monitors progress, stops early, cascades CUA → DOM → fallback | Turns a single fragile attempt into an intelligent multi-strategy search |
| **Wide Scaling** | N parallel KERNEL browsers across marketplaces and strategy variants, cross-branch learning | One branch failing doesn't matter when 5 others succeed |
| **Action Verification** | Per-step screen-change check, stuck detection, mid-loop DOM extraction, loop breaker | Catches failures at step 3 instead of step 40 |
| **Security Guardrails** | Dangerous action blocking, visual injection scanner, human approval flow, scam/replica detection | The agent literally cannot message anyone or click a phishing link without you |

## Results

| | Without AEGIS | With AEGIS |
|---|---|---|
| **Success rate** | **0%** (0/5) | **100%** (5/5) |
| **Listings extracted** | 0 | 16.2 avg |
| **Dangerous actions blocked** | 0 | 20 |

Same Northstar 4B model. Same queries. Same marketplaces. Pure inference-time engineering.

## Demo App: Bargain Radar

A multi-marketplace second-hand deal finder. Describe what you want in natural language, and AEGIS fans out across 6 marketplaces in parallel to find, verify, deduplicate, and rank the best deals.

**Supported marketplaces:** Craigslist, eBay, Mercari, OfferUp, Reverb, Facebook Marketplace

**Example query:**
```
Used Eames lounge chair, real leather, under $1500, within 50 miles of SF, no replicas.
```

**Example output** (real data from a live run):

| Listing | Price | Source |
|---|---|---|
| Loveseat Couch Green Velvet | $100 | Craigslist |
| Reclining Couch | $350 | Craigslist |
| Couch 3 Seater with Pillows | $499 | Craigslist |

Scam listings filtered. Replicas rejected. Distance-verified. Seller trust signals checked.

## How It Works

```
1. Parse query         "couch under $200" → budget=$200, keywords=[couch]
2. Generate URLs       Maximally-filtered search URLs for each marketplace
3. Launch swarm        N parallel KERNEL browsers, each with a different strategy
4. CUA navigates       Northstar drives each browser: search, scroll, extract
5. Mid-loop DOM        At step 5, DOM extraction detects listings → early termination
6. Cascade             CUA fails? → DOM rescue → Fallback Playwright scripts
7. Cross-branch        Successful branch teaches failing ones its strategy
8. Score & dedup       Marketplace scorer filters scams, replicas, stale listings
9. Rank                Final board: verified candidates sorted by score
```

## Quick Start

```bash
git clone https://github.com/xb1g/cua.git && cd cua
uv venv && source .venv/bin/activate && uv pip install -e .
cp .env.example .env   # fill in TZAFON_API_KEY + KERNEL_API_KEY
uv run aegis-orchestrate --query "Used couch under $200"
```

Start the live dashboard:
```bash
uv run cua-ui   # open http://localhost:8555
```

Run the ablation evaluation:
```bash
uv run python eval/run_ablation.py --configs no-aegis,full-aegis
```

## Architecture

```
cua_loop/
  orchestrator.py      Central swarm coordinator + adaptive cascade
  strategies.py        Per-marketplace search strategy definitions
  cascade.py           CUA → DOM → Fallback cascade logic
  models.py            Model provider abstraction (Northstar, Kimi, OpenAI)
  validator.py         LLM-based action validator + strategic guider

  client.py            Single-attempt CUA inner loop
  runner.py            Retry loop with self-critique + fallback
  scaling.py           Parallel wide-scaling + cross-branch learning
  backends/            KERNEL cloud browsers + Lightcone browsers

  security.py          Dangerous action policy engine (tri-state: allow/approve/block)
  scanner.py           Visual prompt-injection scanner (pixel + VLM)
  approval.py          Human-in-the-loop WebSocket approval flow
  action_verifier.py   Per-step screen-change verification + loop breaker

  marketplace.py       Replica detection, scam flags, distance + freshness scoring
  ecommerce.py         Generic listing schema + scoring
  query_parser.py      NL query → structured ParsedQuery
  sites.py             URL generators for 6 marketplaces
  dom_extractor.py     Playwright DOM extraction (per-marketplace + generic)
  url_params.py        URL parameter optimization per marketplace
  cross_branch.py      Successful branches teach failing ones
  fallback_scripts.py  Deterministic Playwright fallback extraction
  pagination.py        JS-driven scroll-and-accumulate pagination

  ui_server.py         Live dashboard: browser grid, verdict feed, bargain board
  rl.py                Contextual bandit over search strategies
  types.py             Pydantic data models
```

## Safety

AEGIS blocks dangerous actions before they execute:

| Action | Policy |
|---|---|
| Message / contact seller | Requires human approval |
| Buy / place order / bid | Blocked |
| Send money (Zelle, Venmo, crypto) | Blocked |
| Click external links in listings | Blocked |
| Submit credentials on untrusted origins | Requires human approval |

**Visual prompt injection defense:** Two-layer scanner detects adversarial text hidden in listing screenshots — pixel-level contrast analysis catches steganographic text (white-on-white), and a VLM scan catches instruction overrides. Unicode homoglyph and zero-width character obfuscation are normalized before pattern matching.

**Human approval flow:** When AEGIS blocks an action, the dashboard shows a real-time approval prompt. The CUA loop pauses, the human reviews, and the agent proceeds or stops. WebSocket-based, sub-second latency.

## Tech Stack

| Component | Technology |
|---|---|
| Cloud browsers | [KERNEL](https://onkernel.com) browser pools |
| CUA model | Northstar 4B via [Lightcone](https://lightcone.ai) API |
| Validator (optional) | Kimi K2.6 Turbo via [Fireworks AI](https://fireworks.ai) |
| Verifier | MiniMax via OpenAI-compatible API |
| Dashboard | FastAPI + SSE + WebSocket |
| DOM extraction | Playwright (via KERNEL browser) |
| Language | Python 3.11+ |

## Built At

Open Source Computer Use Hackathon, San Francisco, May 9 2026.

MIT License.
