# AEGIS — Hackathon Submission

## Team Name

AEGIS

## Project Description

AEGIS is open-source reliability and safety middleware for computer-use agents. It wraps any CUA model with three inference-time layers: wide-scaling parallel sampling across KERNEL browser pools, per-step action verification with LLM-based trajectory judging, and security guardrails including dangerous-action blocking, visual prompt-injection scanning, and human-in-the-loop approval flows.

Demo app: Bargain Radar — a multi-marketplace second-hand deal finder that searches Craigslist, Reverb, OfferUp, Mercari, eBay, and Facebook Marketplace in parallel, verifies and deduplicates listings, and blocks unsafe agent actions. Same Northstar 4B model: without AEGIS 0% verified success, with AEGIS 80% verified success (4/5 queries, avg 13.2 extracted rows, 7 dangerous actions blocked). Pure inference-time engineering.

## One-Line Pitch

AEGIS takes Northstar CUA from 0% to 80% success rate through inference-time reliability engineering — no retraining, no new weights, demonstrated live via Bargain Radar: a multi-marketplace second-hand deal finder.

## Repository

https://github.com/xb1g/cua

## Screen Recording

[Link to recording — to be added after task #20 completes]

## Google Slides

[Link to Google Slides — to be added after slides are uploaded]

## Tracks

- [x] Nvidia NemoClaw Track — OpenShell sandbox policy enforcement at the runtime boundary for defense-in-depth alongside the application-level policy engine
- [x] OpenShell Track — sandbox-level action gating complements AEGIS application-layer security

## Tech Stack

- **KERNEL** — cloud browser pools for wide-scaling parallel agent execution
- **Northstar 4B** — CUA model via Lightcone/Tzafon API
- **MiniMax M2.7** — action verification, trajectory judging
- **Northstar vision** — visual prompt-injection scanning
- **FastAPI** — dashboard server with SSE + WebSocket for real-time agent monitoring
- **Python 3.11+** — MIT licensed, installable via `uv` or `pip`

## Team Members

[Add team member names]

## Key Differentiators

1. **Inference-time only** — no fine-tuning, no new weights, wraps any CUA model
2. **Three-layer defense** — scaling, verification, and security compose independently
3. **Real-world demo** — second-hand marketplaces with adversarial listings, scam detection, and replica filtering
4. **Open source** — MIT license, designed as reusable middleware for any CUA application
