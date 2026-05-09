"""Critic / reranker stub.

Phase 2 of the design doc. Trains a small head on top of a frozen image encoder
to predict P(success | screenshot, candidate_action). Run on Brev.

This file is a stub — fill in once the loop in runner.py is producing trajectories
and you have data to train on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_pairs(traj_dir: Path) -> list[dict[str, Any]]:
    """Load (state, action, outcome) pairs from saved trajectories.

    Each pair: { 'screenshot_url': str, 'action': dict, 'success': bool }
    Outcome is the verifier result of the WHOLE attempt — coarse but a fine
    starting reward. Refine to step-level credit assignment if you have time.
    """
    pairs: list[dict[str, Any]] = []
    for fp in sorted(traj_dir.glob("run-*.json")):
        import json

        run = json.loads(fp.read_text())
        for attempt in run["attempts"]:
            success = attempt["verifier"]["success"]
            for step in attempt["trajectory"]["steps"]:
                pairs.append(
                    {
                        "screenshot_url": step.get("screenshot_url"),
                        "action": {"type": step["action_type"], **step["action_args"]},
                        "success": success,
                    }
                )
    return pairs


def train(traj_dir: Path, out_path: Path) -> None:
    """Train the critic. Implement on Brev with the [critic] extras installed.

    Suggested setup:
      - Frozen open_clip ViT-B/32 image encoder.
      - Embed each screenshot once (cache by hash).
      - Action featurizer: one-hot type + normalized x/y + text length bucket.
      - MLP head: [img_emb; action_feat] -> sigmoid (success prob).
      - Loss: BCE. Train ~3 epochs on collected pairs.
    """
    raise NotImplementedError("Critic training is the lunch task. See cua_loop/critic.py docstring.")


def score(critic_path: Path, screenshot_url: str, candidate_action: dict[str, Any]) -> float:
    """Inference-time score for one candidate action."""
    raise NotImplementedError(
        "Stub. Wire this into runner.py to enable K-sample reranking once trained."
    )
