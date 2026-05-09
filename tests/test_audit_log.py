"""Tests for the append-only hash-chained audit log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cua_loop.audit_log import AuditLog, GENESIS_HASH, _hash_line


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(path=tmp_path / "audit.jsonl")


class TestBasicRecording:
    def test_record_creates_file(self, log: AuditLog):
        log.record("action_proposed", {"step": 0, "type": "click"})
        assert log.path.exists()

    def test_record_writes_jsonl(self, log: AuditLog):
        log.record("action_proposed", {"step": 0})
        log.record("action_blocked", {"reason": "purchase"})
        lines = log.path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert "ts" in entry
            assert "kind" in entry
            assert "prev_hash" in entry

    def test_record_returns_hash(self, log: AuditLog):
        h = log.record("action_proposed", {"step": 0})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_entry_count(self, log: AuditLog):
        assert log.entry_count == 0
        log.record("action_proposed", {"step": 0})
        log.record("action_blocked", {"reason": "test"})
        assert log.entry_count == 2

    def test_extra_kwargs_merged(self, log: AuditLog):
        log.record("action_proposed", step=0, action_type="click")
        line = log.path.read_text().strip()
        entry = json.loads(line)
        assert entry["data"]["step"] == 0
        assert entry["data"]["action_type"] == "click"

    def test_data_and_extra_merged(self, log: AuditLog):
        log.record("action_proposed", {"step": 0}, action_type="click")
        line = log.path.read_text().strip()
        entry = json.loads(line)
        assert entry["data"]["step"] == 0
        assert entry["data"]["action_type"] == "click"


class TestHashChaining:
    def test_first_entry_has_genesis_hash(self, log: AuditLog):
        log.record("action_proposed", {"step": 0})
        line = log.path.read_text().strip()
        entry = json.loads(line)
        assert entry["prev_hash"] == GENESIS_HASH

    def test_second_entry_chains_to_first(self, log: AuditLog):
        log.record("action_proposed", {"step": 0})
        log.record("action_blocked", {"reason": "test"})
        lines = log.path.read_text().strip().split("\n")
        first_hash = _hash_line(lines[0])
        second_entry = json.loads(lines[1])
        assert second_entry["prev_hash"] == first_hash

    def test_chain_of_five(self, log: AuditLog):
        for i in range(5):
            log.record("action_proposed", {"step": i})
        lines = log.path.read_text().strip().split("\n")
        assert len(lines) == 5
        prev = GENESIS_HASH
        for line in lines:
            entry = json.loads(line)
            assert entry["prev_hash"] == prev
            prev = _hash_line(line)


class TestVerification:
    def test_empty_log_verifies(self, log: AuditLog):
        ok, err = log.verify()
        assert ok is True
        assert err is None

    def test_valid_chain_verifies(self, log: AuditLog):
        for i in range(10):
            log.record("action_proposed", {"step": i})
        ok, err = log.verify()
        assert ok is True
        assert err is None

    def test_tampered_entry_detected(self, log: AuditLog):
        for i in range(5):
            log.record("action_proposed", {"step": i})

        lines = log.path.read_text().strip().split("\n")
        entry = json.loads(lines[2])
        entry["data"]["step"] = 999
        lines[2] = json.dumps(entry, separators=(",", ":"))
        log.path.write_text("\n".join(lines) + "\n")

        ok, err = log.verify()
        assert ok is False
        assert "line 4" in err  # line after tampered one detects break

    def test_deleted_entry_detected(self, log: AuditLog):
        for i in range(5):
            log.record("action_proposed", {"step": i})

        lines = log.path.read_text().strip().split("\n")
        del lines[2]
        log.path.write_text("\n".join(lines) + "\n")

        ok, err = log.verify()
        assert ok is False
        assert "hash chain broken" in err

    def test_invalid_json_detected(self, log: AuditLog):
        log.record("action_proposed", {"step": 0})
        with open(log.path, "a") as f:
            f.write("not valid json\n")

        ok, err = log.verify()
        assert ok is False
        assert "invalid JSON" in err


class TestRecovery:
    def test_recovers_last_hash_on_restart(self, tmp_path: Path):
        path = tmp_path / "audit.jsonl"
        log1 = AuditLog(path=path)
        log1.record("action_proposed", {"step": 0})
        log1.record("action_blocked", {"reason": "test"})

        log2 = AuditLog(path=path)
        log2.record("action_proposed", {"step": 2})

        ok, err = log2.verify()
        assert ok is True
        assert err is None
        assert log2.entry_count == 3

    def test_new_log_on_missing_file(self, tmp_path: Path):
        log = AuditLog(path=tmp_path / "new.jsonl")
        assert log._prev_hash == GENESIS_HASH


class TestEventKinds:
    @pytest.mark.parametrize("kind", [
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
    ])
    def test_all_event_kinds_record(self, log: AuditLog, kind):
        log.record(kind, {"test": True})
        entry = json.loads(log.path.read_text().strip())
        assert entry["kind"] == kind
