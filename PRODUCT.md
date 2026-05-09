# Product

## Register

product

## Users

Developers and AI infrastructure engineers watching live CUA agent runs from the AEGIS dashboard. Primary context: a terminal-adjacent operator console, usually open in a second monitor or browser tab alongside a code editor. The user is technical, reads JSON payloads fluently, and wants dense information without hand-holding. Secondary users: hackathon judges evaluating the system on a demo machine — they need to read the dashboard quickly without needing a walkthrough.

## Product Purpose

AEGIS is reliability middleware for computer-use agents. It wraps any CUA model with inference-time verification, wide-scaling parallelism, and security guardrails. The dashboard exists to make the agent's reasoning legible: show what step the agent is on, what action it took, what the browser sees, and whether verification passed or failed. Success looks like: a judge watches the agent fail, self-correct, and succeed — and understands exactly why at each step without being told.

## Brand Personality

Precise, trustworthy, infrastructure-grade. Three words: **reliable, legible, serious**.

The reference is GitHub's UI: dark-native, information-dense, monospace in the right places, functional hierarchy. No decorative gradients. No marketing gloss. The tool should look like something engineers built for engineers.

## Anti-references

- Colorful SaaS dashboards: Grafana rainbow color soup, Datadog's multi-hue overload
- Consumer-warm tools: rounded bubbly UIs, playful illustrations, soft pastels
- Hackathon demo clichés: hero gradient buttons, glassmorphism for its own sake, glowing neon accents
- Marketing-mode dashboards that prioritize aesthetics over data density

## Design Principles

1. **Signal before decoration.** Every visual element must carry information. If it doesn't, remove it.
2. **Trust through legibility.** The agent's state should be readable at a glance — status, step, action, verification result — without scanning or parsing.
3. **Density without clutter.** GitHub-style: lots of information, clear hierarchy, generous but purposeful whitespace. Not sparse, not overwhelming.
4. **Monospace for machine data.** Action payloads, JSON, URLs, step counts: monospace. Prose and labels: sans-serif. Never mix them arbitrarily.
5. **Failure is a feature.** Failed attempts and verification rejections should be visually distinct and easy to read — they're not errors to hide, they're the story of the agent working.

## Accessibility & Inclusion

WCAG 2.1 AA minimum. Dark theme is the primary and only theme (the physical scene: a developer watching an agent run on a monitor, often in a dim room or alongside other terminal windows — dark is forced). All status colors must pass AA contrast against the dark background. Reduced-motion media query respected for animations.
