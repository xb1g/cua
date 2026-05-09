# cua-verified-loop

A verified self-correcting outer loop around any CUA (computer-use agent) model.
Wraps Lightcone Northstar by default. Built at the Computer Agents Hackathon
(Tzafon / KERNEL / Anthropic / SF Compute, 2026-05-09).

The thesis: **scraping has cheap verification signals.** Row counts, schema
validity, "did we paginate?" — all of these are easy to check. Hand a mediocre
CUA model that reward signal and it becomes a reliable scraper through retry
plus self-critique. Optionally train a small reranker on the trajectories you
generate, and you get a measurable win at inference time.

## Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
cp .env.example .env  # then fill in TZAFON_API_KEY, KERNEL_API_KEY, MINIMAX_API_KEY
```

- Northstar credits: https://lightcone.ai/signup?campaign=HACKMAY9
- Kernel API key: https://kernel.sh

## Browser backends

Set `BROWSER_BACKEND` in `.env`:

- `kernel` (default) — uses Kernel cloud browsers. Requires `KERNEL_API_KEY`.
- `lightcone` — uses Lightcone-managed browsers/desktops. Requires `LIGHTCONE_API_KEY` (or `TZAFON_API_KEY`).

The Northstar model API runs through Lightcone in either case, so
`TZAFON_API_KEY` / `LIGHTCONE_API_KEY` is always required.

## Run

```bash
cua-loop --url https://news.ycombinator.com --task "extract a table of the top 10 stories with title, url, points, comment count"
```

## How it works

```
                      ┌─────────────────────────┐
                      │  outer retry (N=5)      │
                      │  + self-critique        │
                      └────────────┬────────────┘
                                   │
                  ┌────────────────▼────────────────┐
                  │  inner loop (Northstar Responses │
                  │  API + Lightcone computer)       │
                  └────────────────┬────────────────┘
                                   │
                                   ▼
                          ┌────────────────┐
                          │  LLM-as-judge  │  Haiku 4.5
                          │  verifier      │
                          └────────┬───────┘
                                   │
                              success / fail
```

- `cua_loop/client.py` — single attempt: drives the Lightcone computer using
  Northstar via the Responses API.
- `cua_loop/verifier.py` — Haiku 4.5 judges whether the agent actually produced
  what the task asked for.
- `cua_loop/runner.py` — outer loop. On failure, feeds the failure reason back
  into the next attempt as additional context.
- `cua_loop/critic.py` — stub for the lunchtime Brev training task (Phase 2).
- `trajectories/` — every run is logged here for offline critic training.

## Roadmap (hackathon timeline)

- [x] Phase 0: scaffold
- [ ] Phase 1: get inner loop working on one URL
- [ ] Phase 1.5: outer retry + verifier
- [ ] Phase 2: train critic on Brev (`tzafon-hack` coupon)
- [ ] Phase 3: K-sample reranking with critic
- [ ] Phase 4: live demo UI showing pure-loop vs loop+critic side-by-side

See `DESIGN.md` for the full design doc.

## License

MIT.
