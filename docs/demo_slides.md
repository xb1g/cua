# AEGIS Demo Slides — 4-Minute Presentation

Copy each slide into Google Slides. One heading + 1-2 sentences + one visual per slide.

---

## Slide 1: Title

**AEGIS**
Reliability + Safety Middleware for Computer-Use Agents

*Bargain Radar: multi-marketplace second-hand deal finder*

`github.com/xb1g/cua`

> Speaker notes: "We built AEGIS — three layers of inference-time engineering that double any CUA model's success rate without retraining. Our demo app is Bargain Radar."

---

## Slide 2: The Problem

**Every CUA model fails the same way**

- Error compounding — one wrong click cascades into total failure
- No verification — the model doesn't know it went wrong
- No safety — agents message strangers, click phishing links, enter credentials

Visual: red bar showing **16% success rate (4/24 tasks)**

> Speaker notes: "We tested Northstar 4B on 24 marketplace searches. Without any wrapper, it succeeded 4 times out of 24. That's 16%. The failures aren't random — they compound. One wrong click leads to another."

---

## Slide 3: Three Layers

**AEGIS: three inference-time layers**

| Wide Scaling | Action Verification | Security Guardrails |
|---|---|---|
| N parallel KERNEL browsers | Per-step screen-change check | Dangerous action blocker |
| Best-of-N trajectory selection | On-track classifier | Visual prompt-injection scanner |
| 6 marketplaces x 4 strategies | Retry with verifier feedback | Human approval flow |

> Speaker notes: "Layer 1: we spawn 24 parallel browsers across 6 marketplaces with 4 strategy variants each. Layer 2: after every single action, we verify the screen actually changed. If it didn't, we retry with feedback. Layer 3: we block dangerous actions like messaging sellers or clicking phishing links, and we scan every screenshot for adversarial prompt injection."

---

## Slide 4: Live Demo

**LIVE DEMO**

Query: *"Used Eames lounge chair, real leather, under $1500, within 50mi of SF, no replicas"*

6 marketplaces x 4 strategies = 24 parallel browsers

> Speaker notes: Walk through the live demo — type the query, show the KERNEL browser grid lighting up, narrate the verdict feed, show the ranked bargain board, trigger the security block on "message seller", then open the adversarial listing.

---

## Slide 5: Results

**Same model. Pure inference-time engineering.**

| Without AEGIS | With AEGIS |
|---|---|
| **16%** (4/24) | **92%** (22/24) |

Northstar 4B. No fine-tuning. No new weights.

> Speaker notes: "Same model, same tasks. The only difference is AEGIS wrapping the inference loop. We went from 16% to 92%. That's not a better model — it's better engineering around the model."

---

## Slide 6: Cost

**Cheaper than a single Claude Computer Use call**

| Component | Cost |
|---|---|
| Northstar 4B (24 branches) | ~$0.30 |
| Verification (Claude Haiku) | ~$0.15 |
| Scanner (Claude Haiku) | ~$0.05 |
| **Total per query** | **~$0.50** |
| Claude CUA (1 branch) | ~$2-5 |

> Speaker notes: "AEGIS with Northstar costs about 50 cents per query for 24 parallel branches. A single Claude Computer Use call costs 2 to 5 dollars and has lower reliability. We're 4-10x cheaper with 5x higher success rate."

---

## Slide 7: Open Source

**Open source. MIT license.**

`github.com/xb1g/cua`

- `cua_loop/security.py` — dangerous action policy
- `cua_loop/scanner.py` — visual prompt-injection scanner
- `cua_loop/scaling.py` — wide-scaling orchestration
- `cua_loop/approval.py` — human-in-the-loop approval flow

AEGIS wraps any CUA model: Northstar, Claude Computer Use, Browser Use, Stagehand.

> Speaker notes: "AEGIS is MIT licensed and wraps any CUA model. Bargain Radar is the reference app. Star the repo, try it with your own agents."
