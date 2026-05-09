"""Append-only JSONL audit log with SHA-256 hash chaining.

Every security-relevant event — action proposed, verdict issued, action
blocked, injection detected — is recorded as a single JSON line. Each
entry includes the SHA-256 hash of the previous entry, creating a
tamper-evident chain: modifying or deleting any record breaks the chain
from that point forward.

Usage:
    from cua_loop.audit_log import audit_log

    audit_log.record("action_proposed", {"step": 3, "type": "click", "x": 430, "y": 520})
    audit_log.record("action_blocked", {"reason": "purchase detected"})

    # Verify integrity
    ok, err = audit_log.verify()
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


EventKind = Literal[
    "action_proposed",
    "action_allowed",
    "action_blocked",
    "action_needs_approval",
    "loop_detected",
    "injection_detected",
    "verification_passed",
    "verification_failed",
    "verifier_verdict",
    "scan_result",
    "attempt_start",
    "attempt_end",
]

GENESIS_HASH = "0" * 64


def _hash_line(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            log_dir = Path(os.getenv("CUA_TRAJ_DIR", "trajectories"))
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / "audit.jsonl"
        self._path = Path(path)
        self._lock = threading.Lock()
        self._prev_hash = self._recover_last_hash()

    def _recover_last_hash(self) -> str:
        if not self._path.exists():
            return GENESIS_HASH
        last_line = ""
        try:
            with open(self._path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        last_line = line
        except Exception:
            return GENESIS_HASH
        if not last_line:
            return GENESIS_HASH
        return _hash_line(last_line)

    def record(self, kind: EventKind, data: dict[str, Any] | None = None, **extra: Any) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "prev_hash": self._prev_hash,
        }
        if data:
            entry["data"] = data
        if extra:
            entry["data"] = {**(entry.get("data") or {}), **extra}

        line = json.dumps(entry, separators=(",", ":"), default=str)

        with self._lock:
            with open(self._path, "a") as f:
                f.write(line + "\n")
            self._prev_hash = _hash_line(line)

        return self._prev_hash

    def verify(self) -> tuple[bool, str | None]:
        if not self._path.exists():
            return True, None

        prev_hash = GENESIS_HASH
        line_num = 0

        with open(self._path, "r") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                line_num += 1
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    return False, f"line {line_num}: invalid JSON"

                recorded_prev = entry.get("prev_hash")
                if recorded_prev != prev_hash:
                    return False, (
                        f"line {line_num}: hash chain broken — "
                        f"expected prev_hash={prev_hash[:16]}..., "
                        f"got {str(recorded_prev)[:16]}..."
                    )

                prev_hash = _hash_line(raw_line)

        return True, None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def entry_count(self) -> int:
        if not self._path.exists():
            return 0
        count = 0
        with open(self._path, "r") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count


audit_log = AuditLog()
