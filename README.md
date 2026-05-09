# AEGIS: E-Commerce Radar

Reliability and safety infrastructure for computer-use agents, demonstrated through an e-commerce price-comparison agent.

AEGIS wraps any CUA model with three inference-time layers:

- Wide scaling: run many browser attempts in parallel across Kernel browser instances, then select the best verified trajectory.
- Action verification: check after each action whether the screen changed as expected and whether the agent is still on track.
- Security guardrails: block dangerous actions like checkout, purchase, account changes, payment submission, and seller messaging unless a human approves.

The demo application is **E-Commerce Radar**: a user describes what they want, and AEGIS fans out across product sites like Best Buy, eBay, Walmart, Amazon, Newegg, and specialty stores to find, verify, deduplicate, and rank the best options.

Example query:

```text
Find the best 14-inch laptop under $1000 with 16GB RAM, 512GB SSD, good reviews, and delivery within 7 days.
```

## Why E-Commerce

E-commerce is the right demo for AEGIS because price comparison makes every AEGIS layer visible:

- Wide scaling becomes obvious: 6 stores times 4 search strategies means 24 parallel Kernel browsers per query.
- Verification is real: listings can be sponsored, out of stock, refurbished, wrong configuration, hidden shipping, or mismatched against the user request.
- Security matters: the agent must never click `buy now`, enter payment details, change account settings, or message a seller without approval.
- The value is concrete: the result is not an abstract benchmark score, it is money saved on a real product.
- Websites change constantly: product grids, filters, modals, and pagination are exactly where CUA models drift and need verification.

The headline claim:

```text
Same CUA model. Better shopping results through inference-time reliability engineering.
```

## Demo Flow

1. User enters a product query.
2. AEGIS launches multiple Kernel browser attempts in parallel.
3. Each branch searches one store with a different strategy: keywords, filters, sort order, price caps, review constraints, and delivery requirements.
4. Verification rejects products that are out of stock, refurbished when the user asked for new, above budget after shipping, or the wrong spec.
5. The final board ranks verified candidates by total price, availability, shipping time, review quality, and confidence.
6. If the user tries to buy, AEGIS blocks checkout until human approval.

Recommended live demo targets:

- Best Buy
- eBay
- Walmart
- Newegg
- B&H Photo
- Amazon as the optional stretch target

## Current Repo Status

This repo contains the AEGIS middleware skeleton and live viewer:

- `cua_loop/client.py` runs one Northstar-driven browser attempt against a pluggable browser backend.
- `cua_loop/backends/` supports Kernel cloud browsers and Lightcone-managed browsers.
- `cua_loop/runner.py` retries failed attempts with verifier feedback.
- `cua_loop/scaling.py` runs parallel wide-scaling branches and selects the best trajectory.
- `cua_loop/rl.py` trains a contextual bandit over Kernel-backed search strategies.
- `cua_loop/action_verifier.py` records simple per-action screen-change checks.
- `cua_loop/security.py` blocks dangerous action patterns and prompt-injection-like instructions.
- `cua_loop/verifier.py` uses an LLM judge to decide whether the trajectory satisfied the task.
- `cua_loop/ui_server.py` serves the live demo dashboard.
- `trajectories/` stores run logs for audit trails and future critic training.

## Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
cp .env.example .env
```

Fill in:

```bash
TZAFON_API_KEY=...
LIGHTCONE_API_KEY=...
KERNEL_API_KEY=...
MINIMAX_API_KEY=...
```

Useful links:

- Kernel browser dashboard: https://dashboard.onkernel.com/browsers
- Northstar credits: https://lightcone.ai/signup?campaign=HACKMAY9

## Browser Backends

Set `BROWSER_BACKEND` in `.env`:

- `kernel` uses Kernel cloud browsers and requires `KERNEL_API_KEY`.
- `lightcone` uses Lightcone-managed browsers/desktops and requires `LIGHTCONE_API_KEY` or `TZAFON_API_KEY`.

Northstar still runs through the Lightcone/Tzafon model API, so `TZAFON_API_KEY` or `LIGHTCONE_API_KEY` is always required.

## Run

Start the dashboard:

```bash
uv run cua-ui
```

Run one verified attempt loop:

```bash
uv run cua-loop \
  --url https://www.bestbuy.com/site/searchpage.jsp?st=laptop \
  --task "Find laptops under $1000 with 16GB RAM and 512GB SSD. Extract title, price, URL, availability, shipping estimate, and reject wrong configurations."
```

Run wide scaling with 4 parallel branches:

```bash
uv run cua-loop \
  --wide 4 \
  --url https://www.bestbuy.com/site/searchpage.jsp?st=laptop \
  --task "Find laptops under $1000 with 16GB RAM and 512GB SSD. Extract title, price, URL, availability, shipping estimate, and reject wrong configurations."
```

Run the local deterministic self-checks:

```bash
uv run aegis-check
```

Run Kernel-backed RL over search strategies:

```bash
uv run aegis-rl \
  --episodes 8 \
  --url https://www.bestbuy.com/site/searchpage.jsp?st=laptop \
  --task "Find laptops under $1000 with 16GB RAM and 512GB SSD. Extract title, price, URL, availability, shipping estimate, and reject wrong configurations."
```

This trains a contextual bandit over prompt/search strategies. It does not fine-tune model weights; it learns which strategy variants earn the best verifier reward when run through Kernel browser sessions.

## Architecture

```text
User product query
   |
   v
AEGIS wide scaling
   |-- branch 1: Kernel browser + store/search strategy A
   |-- branch 2: Kernel browser + store/search strategy B
   |-- branch 3: Kernel browser + store/search strategy C
   |-- branch N: Kernel browser + store/search strategy N
   |
   v
Per-action guardrail + verification loop
   |
   v
Trajectory verifier / judge
   |
   v
Ranked, audited product board
```

## Safety Policy

AEGIS is designed to browse, compare, and extract. It should not autonomously perform irreversible or user-representing actions.

Blocked without human approval:

- Buying or checking out
- Adding payment methods
- Sending money
- Messaging sellers
- Changing account settings
- Submitting passwords, payment data, secrets, or identity information

## Roadmap

- [x] Kernel browser backend
- [x] Lightcone browser fallback
- [x] LLM trajectory verifier
- [x] Retry loop with self-critique
- [x] Wide-scaling branch runner
- [x] Kernel-backed strategy RL harness
- [x] Basic action verification metadata
- [x] Basic dangerous-action guardrails
- [x] Live dashboard
- [ ] E-commerce product result schema
- [ ] Multi-store deduplication and ranking board
- [ ] Out-of-stock, refurbished, sponsored, and wrong-spec rejection heuristics
- [ ] Human approval flow for checkout/contact actions
- [ ] Split-screen raw CUA vs CUA+AEGIS demo
- [ ] Evaluation harness for before/after reliability metrics

## License

MIT.
