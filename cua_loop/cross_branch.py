"""Cross-branch learning: successful branches teach failed ones their strategy.

When parallel marketplace branches run and some succeed, this module extracts
the successful action sequence and builds a demonstration hint for retrying
failed branches. Pure inference-time transfer — no training needed.
"""

from __future__ import annotations

from cua_loop.types import AttemptResult, Step, Trajectory


def extract_demonstration(trajectory: Trajectory, site_name: str = "") -> str:
    """Serialize a successful trajectory into a compact demonstration string."""
    steps = trajectory.steps
    if not steps:
        return ""

    action_lines: list[str] = []
    for i, step in enumerate(steps):
        action_type = step.action_type
        args = step.action_args

        if action_type == "navigate":
            action_lines.append(f"  {i+1}. Navigate to URL")
        elif action_type == "click":
            x, y = args.get("x", 0), args.get("y", 0)
            action_lines.append(f"  {i+1}. Click at ({x},{y})")
        elif action_type == "type":
            text = (args.get("text") or "")[:30]
            action_lines.append(f'  {i+1}. Type "{text}"')
        elif action_type in ("key", "keypress"):
            keys = args.get("keys", [])
            action_lines.append(f"  {i+1}. Press {'+'.join(keys) if keys else action_type}")
        elif action_type == "scroll":
            action_lines.append(f"  {i+1}. Scroll page")
        elif action_type == "wait":
            action_lines.append(f"  {i+1}. Wait for page load")
        else:
            action_lines.append(f"  {i+1}. {action_type}")

    if len(action_lines) > 12:
        action_lines = action_lines[:6] + ["  ..."] + action_lines[-4:]

    extracted_count = 0
    if trajectory.extracted and isinstance(trajectory.extracted, list):
        extracted_count = len(trajectory.extracted)

    header = f"Successful strategy"
    if site_name:
        header += f" ({site_name})"

    parts = [
        f"{header}:",
        *action_lines,
        f"  Result: {extracted_count} listings extracted in {len(steps)} steps.",
    ]

    if trajectory.final_message:
        msg_preview = trajectory.final_message[:100].replace("\n", " ")
        parts.append(f"  Final answer preview: {msg_preview}")

    return "\n".join(parts)


def build_cross_branch_hint(
    successful: list[tuple[str, AttemptResult]],
    target_site: str,
) -> str:
    """Build a hint string for a failing branch based on what worked elsewhere."""
    if not successful:
        return ""

    demos: list[str] = []
    for site_name, attempt in successful[:2]:
        demo = extract_demonstration(attempt.trajectory, site_name)
        if demo:
            demos.append(demo)

    if not demos:
        return ""

    hint_parts = [
        "CROSS-BRANCH LEARNING: Other marketplace searches succeeded. "
        "Adapt their approach to your site.",
        "",
        *demos,
        "",
        f"Adapt this strategy for {target_site}. Key principles:",
        "- Navigate directly to the search URL (already done for you)",
        "- Wait for results to fully load before extracting",
        "- If results aren't visible, scroll down to trigger lazy loading",
        "- Extract structured data (title, price, condition, URL) from what you see",
    ]

    return "\n".join(hint_parts)


def should_retry_with_hints(
    attempts: list[AttemptResult],
    min_successful: int = 1,
    min_failed: int = 1,
) -> bool:
    """Return True if we have enough successful branches to help failed ones."""
    successful_count = sum(1 for a in attempts if a.verifier.success)
    failed_count = sum(1 for a in attempts if not a.verifier.success)
    return successful_count >= min_successful and failed_count >= min_failed
