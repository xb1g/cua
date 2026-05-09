---
name: AEGIS: E-Commerce Radar
description: Reliability infrastructure for computer-use agents — live operator dashboard.
colors:
  canvas: "#0f172a"
  surface: "#1e293b"
  surface-border: "#ffffff14"
  text-primary: "#f8fafc"
  text-muted: "#94a3b8"
  blue-primary: "#3b82f6"
  blue-deep: "#2563eb"
  violet-accent: "#8b5cf6"
  state-success: "#10b981"
  state-danger: "#ef4444"
  state-warning: "#f59e0b"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.05em"
  mono:
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  pill: "99px"
  panel: "16px"
  control: "10px"
  badge: "99px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "20px"
  lg: "24px"
  xl: "40px"
components:
  btn-primary:
    backgroundColor: "{colors.blue-primary}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.control}"
    padding: "14px 24px"
  btn-primary-hover:
    backgroundColor: "{colors.blue-deep}"
  btn-primary-disabled:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-muted}"
  status-badge-idle:
    backgroundColor: "#94a3b81a"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
  status-badge-running:
    backgroundColor: "#3b82f61a"
    textColor: "#60a5fa"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
  status-badge-success:
    backgroundColor: "#10b9811a"
    textColor: "#34d399"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
  status-badge-failed:
    backgroundColor: "#ef44441a"
    textColor: "#f87171"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
  input-field:
    backgroundColor: "#0f172a99"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
  glass-panel:
    backgroundColor: "#1e293bb3"
    rounded: "{rounded.panel}"
    padding: "24px"
---

# Design System: AEGIS

## 1. Overview

**Creative North Star: "The Operator's War Room"**

AEGIS's dashboard is not a product to be admired — it is a surface to be read. The design language borrows from GitHub's dark-native aesthetic: structured information at high density, functional hierarchy enforced by type weight and color restraint, no decorative noise. The canvas is deep slate (`#0f172a`), not black. Panels float on it as darker-tinted surfaces with hairline borders. The only saturated colors that appear are status signals — blue for running, emerald for success, red for failure — and they carry meaning every time they fire.

This system explicitly rejects: the rainbow color soup of Grafana-style dashboards, consumer-warm rounded-corner friendliness, glassmorphism used as decoration, hackathon-demo clichés (hero gradients, glowing neon accents, large animated counters for vanity). The agent on screen is doing hard technical work; the interface should look like it agrees.

The visual register is GitHub's internal dark tooling crossed with a terminal-adjacent operator console. Information is dense because the operator needs it dense. Every whitespace decision is deliberate: `24px` gaps between panels for spatial grouping, `20px` internal section breaks for rhythm, `8px` micro-gaps for label-to-value pairs. Same padding everywhere is monotony; AEGIS varies it.

**Key Characteristics:**
- Deep slate canvas, no true black or white
- Status signals are the only saturated color moments
- Labels uppercase + tracked at 12px; values at 14px regular — always this pairing
- Monospace for all machine data: action payloads, JSON, step counts, URLs
- Panels at rest are subtle glass surfaces; interactions lift them slightly
- Motion is purposeful: the cursor overlay, screenshot fade, pulse dot — all carry state

## 2. Colors: The Operator's Palette

Restrained color strategy. Tinted neutrals carry the surface; state colors speak only when they have something to say.

### Primary
- **Blue Signal** (`#3b82f6`): Interactive primary only — the "Start Agent" button, focused input rings, step counter. Used on ≤10% of any screen surface. Its presence means "action" or "in progress".
- **Blue Deep** (`#2563eb`): Blue Signal on hover. Same role, darker.

### Secondary
- **Violet Accent** (`#8b5cf6`): Current action type label. Distinguishes the agent's last action from status and data — one step removed from the primary interactive blue.

### Tertiary (State Colors)
- **Emerald Success** (`#10b981` / glow `#34d399`): Verification passed. Agent stopped cleanly.
- **Red Failure** (`#ef4444` / glow `#f87171`): Attempt failed or agent blocked.
- **Amber Warning** (`#f59e0b`): Security guardrail triggered; human approval needed.

### Neutral
- **Deep Slate Canvas** (`#0f172a`): Page background. The floor everything sits on.
- **Panel Surface** (`#1e293b` at 70% opacity): Glass panel background. Slightly lighter than the canvas; the `backdrop-filter` blur reads the radial gradient behind it.
- **Hairline Border** (`#ffffff` at 8% / `#ffffff14`): Panel edges and dividers. Barely visible at rest; present enough to separate surfaces.
- **Off-White Text** (`#f8fafc`): Primary prose, values, interactive labels.
- **Slate Muted** (`#94a3b8`): Secondary labels, metadata, placeholder text, idle badge text.

### Named Rules
**The State-Color Purity Rule.** Blue, violet, emerald, red, and amber appear only as semantic signals — running, action, success, failure, warning. They are never used decoratively. If you reach for one of these colors to make something "look nicer," rewrite the element.

## 3. Typography

**Display / UI Font:** Inter (with system-ui, sans-serif fallback)
**Mono Font:** JetBrains Mono (with Fira Code, monospace fallback)

**Character:** Inter's geometric neutrality reads cleanly at small sizes on dark backgrounds — the right choice for dense operator tooling. JetBrains Mono's wide letterforms make JSON payloads scannable without horizontal scrolling. The two families never mix on the same line.

### Hierarchy
- **Display** (700, 20px, tracking -0.02em): Page title only ("AEGIS: E-Commerce Radar"). One instance per view.
- **Title** (600, 15px, 1.4): Button labels, section headings within panels.
- **Body** (400, 14px, 1.5): Data values, result text, general prose.
- **Label** (600, 12px, tracking 0.05em, ALL CAPS): Field labels, panel section names ("AGENT STATUS", "CURRENT ACTION", "ACTION PAYLOAD"). Always uppercase, always 12px, always muted slate color.
- **Mono** (400, 13px, 1.6): All machine data — JSON action payloads, URL bar, step counts, pre blocks. Never use a proportional font for data that the user will scan as structured output.

### Named Rules
**The Two-Register Rule.** Every text element is either Prose (Inter) or Machine (JetBrains Mono). There is no third register. A URL in a label uses mono. A button label uses Inter. Never mix them on the same line or within the same data value.

**The Label Protocol.** Every data field uses the same two-line pattern: `LABEL` at 12px uppercase muted, then `value` at 14px primary below it. The 2px of space between them is fixed. This pairing is the atomic unit of the dashboard's information display.

## 4. Elevation

This system uses tonal layering, not structural shadows. Depth is conveyed by surface lightness against the dark canvas, not by cast shadows.

Two active tiers:
1. **Canvas** (`#0f172a`): The floor. Nothing sits behind it.
2. **Panel Surface** (`#1e293b` at 70% opacity + `backdrop-filter: blur(16px)`): Glass panels float one tier above the canvas. The blur passes the radial gradient behind it, creating subtle depth without a shadow.

A `box-shadow` appears on panels (`0 20px 40px -10px rgba(0,0,0,0.3)`) but it functions as ambient grounding, not structural elevation. It is a subtle darkening of the canvas beneath the panel edge, not a cast-shadow that implies the panel is physically lifted.

**The Flat-by-Default Rule.** Shadows appear only on panels (ambient grounding) and on interactive elements at hover (the button's `box-shadow` lifts from `0 4px 15px` to `0 8px 25px`). No shadows on text, no inner shadows on inputs, no layered shadow stacks. One subtle ambient shadow per surface; nothing more.

## 5. Components

### Buttons

The primary action button ("Start Agent") is the highest-visibility interactive element on screen.

- **Shape:** Gently rounded (10px radius)
- **Primary:** Blue Signal background (`#3b82f6`) → Blue Deep (`#2563eb`) on hover. White text (600, 15px). Full-width inside its container. Gradient from blue to violet (`linear-gradient(135deg, #3b82f6, #8b5cf6)`) is the current implementation — acceptable here because it is the singular call-to-action, not a pattern applied everywhere.
- **Hover:** Lifts 2px (`translateY(-2px)`), shadow expands from `0 4px 15px rgba(59,130,246,0.3)` to `0 8px 25px rgba(59,130,246,0.4)`. Transition: `0.3s cubic-bezier(0.4,0,0.2,1)`.
- **Disabled:** Flat dark surface (`#334155`), no shadow, opacity 0.6, no transform on hover. Visually inert.
- **Loading state:** Inline spinner (16px, 2px border, `1s linear infinite` rotation) replaces the icon slot; label changes to context-appropriate copy ("Starting...", "Agent Running...").

### Status Badges

Pill-shaped state indicators. The only place all five state colors appear together.

- **Shape:** Full pill (`99px` radius)
- **Pattern:** Semi-transparent state-color background (10% opacity tint) + state-color text + hairline border at 20% opacity. Five variants: idle (muted), running (blue), success (emerald), failed (red), blocked (amber).
- **Running variant:** An 8px circle dot with pulse animation (`scale 0.95→1.2`, `1.5s infinite`) and a glow (`box-shadow: 0 0 10px #60a5fa`). This is the only ambient animation on the dashboard; it signals live agent activity.
- **Label:** 13px, 600 weight, uppercase, 0.05em tracking.

### Glass Panels (Containers)

The structural unit of the layout. Every data group lives in one.

- **Background:** `rgba(30, 41, 59, 0.7)` with `backdrop-filter: blur(16px)`
- **Border:** 1px solid `rgba(255,255,255,0.08)` — hairline only
- **Radius:** 16px
- **Shadow:** `0 20px 40px -10px rgba(0,0,0,0.3)` — ambient grounding
- **Internal padding:** 24px
- **Do not nest panels.** No glass card inside a glass card. The sidebar and browser view are both panels; their internal sections use `border-bottom: 1px solid var(--panel-border)` and spacing alone as dividers, never a nested panel.

### Inputs / Text Fields

- **Style:** Dark filled (`rgba(15,23,42,0.6)` background), hairline border, 10px radius
- **Focus:** Border lifts to Blue Signal (`#3b82f6`) + soft focus ring (`box-shadow: 0 0 0 3px rgba(59,130,246,0.15)`)
- **Typography:** 14px Inter regular, white text, system-placeholder color for placeholder
- **Textarea:** `resize: vertical` only; `min-height: 80px`
- **No error state yet.** When error states are added: red border + red focus ring + red helper text below. Never red background fill.

### Pre / Code Blocks (Action Payload)

The most frequently updated surface during a live agent run.

- **Background:** `rgba(15,23,42,0.8)` — darker than panel, visually recessed
- **Border:** 1px hairline
- **Radius:** 10px
- **Font:** JetBrains Mono, 13px, `#a5b4fc` (indigo-300) — slightly colored to distinguish from prose
- **Scrollable:** `max-height: 200px; overflow-y: auto`
- **`white-space: pre-wrap`** — no horizontal scroll; payload wraps.

### Browser Viewport (Signature Component)

The main content area: a simulated browser window showing the agent's current screen.

- **Outer container:** Glass panel with zero internal padding; the browser chrome is flush to the panel edges.
- **Browser chrome:** Dark bar (`rgba(15,23,42,0.8)`) with macOS-style traffic-light dots (decoration only) and a monospace URL bar at 12px muted slate.
- **Viewport area:** Pure black (`#000`) background; the screenshot image is `object-fit: contain` centered.
- **Screenshot transition:** `opacity: 0 → 1` over `0.5s ease` on load. The previous frame lingers at full opacity until the new one is ready — no flash between frames.
- **Cursor overlay:** Blue glowing dot (36px, radial gradient `rgba(59,130,246,0.8)→transparent`, `border: 2px solid #60a5fa`, `box-shadow: 0 0 20px rgba(59,130,246,0.6)`). Positioned absolutely over the screenshot with `transition: all 0.3s cubic-bezier(0.34,1.56,0.64,1)`. Click actions trigger a ripple (`scale 0.8→2, opacity 1→0`, `0.6s`).

### Custom Scrollbar

- **Track:** `rgba(0,0,0,0.1)`, 8px, 4px radius
- **Thumb:** `rgba(255,255,255,0.1)` at rest → `rgba(255,255,255,0.2)` on hover
- Applied globally via `::-webkit-scrollbar` rules.

## 6. Do's and Don'ts

### Do:
- **Do** use the Label Protocol for every data field: `12px uppercase muted label` above, `14px primary value` below. Always this structure.
- **Do** use JetBrains Mono for all machine data: JSON payloads, URLs, step counts, structured output. Prose uses Inter; data uses Mono. Never mix on the same line.
- **Do** keep state colors (blue, violet, emerald, red, amber) as semantic-only signals. Their meaning is their value. They appear only when they have something specific to communicate.
- **Do** use `ease-out` curves for transitions (`cubic-bezier(0.4,0,0.2,1)`). State changes should feel resolved, not bouncy.
- **Do** tint every neutral toward the slate hue. No pure `#000` or `#fff` anywhere in the system.
- **Do** make failure states legible and prominent. Failed attempts and blocked actions are not errors to hide — they are the story of the agent working. Red badges and result boxes should be as readable as success states.
- **Do** vary spacing for rhythm: `8px` micro, `12px` control-padding, `20px` section breaks, `24px` panel gaps, `40px` page padding. Same padding everywhere is monotony.

### Don't:
- **Don't** use rainbow color palettes or multi-hue overload (Grafana, Datadog). AEGIS uses two neutrals and five semantic colors. That is the entire palette.
- **Don't** use glassmorphism decoratively. The panel blur is structural (it separates tiers); blur on a tooltip, a dropdown, or a card inside a panel is decoration. Prohibited.
- **Don't** use `border-left` greater than 1px as a colored accent stripe on any element. Use background tints, full borders, or leading icons instead.
- **Don't** use `background-clip: text` gradient text. The header title currently uses this; it should be replaced with a solid off-white on the next design pass. Gradient text is decorative, never meaningful.
- **Don't** nest panels. A glass card inside a glass panel is always wrong. Use spacing, dividers, and label hierarchy to create internal structure.
- **Don't** add modal dialogs as a first solution. For human-approval flows (checkout, contact-seller guardrail), use an inline confirmation strip inside the panel, not a modal overlay.
- **Don't** add animations for decoration. The pulse dot signals live activity. The cursor ripple signals a click. The screenshot fade signals a new frame. Every other animation must pass the same test: what state does it communicate? If the answer is "nothing," remove it.
- **Don't** use consumer-warm aesthetic choices: bubbly radii, soft pastels, playful illustration, rounded-everything. This is an operator console, not a consumer app.
- **Don't** use the hero-metric template (big number, small label, gradient accent). Step count is data, not a vanity metric. Display it plainly.
