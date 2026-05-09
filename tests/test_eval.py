"""Tests for the eval harness — queries, ablation configs, and report generation.

All tests use mocks; no API keys or live services needed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cua_loop.types import AttemptResult, RunResult, Step, Trajectory, VerifierResult


# ── Held-out queries ─────────────────────────────────────────────────────────

QUERIES_PATH = Path(__file__).parent.parent / "eval" / "held_out_queries.jsonl"

REQUIRED_QUERY_FIELDS = {"query", "marketplace", "url", "category", "expected_fields"}
EXPECTED_CATEGORIES = {"furniture", "electronics", "instruments", "cameras", "bikes", "vintage"}
EXPECTED_MARKETPLACES = {"craigslist", "facebook", "offerup", "mercari", "ebay", "reverb"}


def _load_queries() -> list[dict]:
    queries = []
    with open(QUERIES_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    return queries


class TestHeldOutQueries:
    def test_file_exists(self):
        assert QUERIES_PATH.exists()

    def test_has_at_least_20_queries(self):
        queries = _load_queries()
        assert len(queries) >= 20

    def test_required_fields_present(self):
        for q in _load_queries():
            missing = REQUIRED_QUERY_FIELDS - set(q.keys())
            assert not missing, f"Query {q.get('query', '?')} missing fields: {missing}"

    def test_category_coverage(self):
        categories = {q["category"] for q in _load_queries()}
        assert categories == EXPECTED_CATEGORIES

    def test_marketplace_coverage(self):
        marketplaces = {q["marketplace"] for q in _load_queries()}
        assert marketplaces == EXPECTED_MARKETPLACES

    def test_each_category_has_at_least_2(self):
        counts: dict[str, int] = {}
        for q in _load_queries():
            counts[q["category"]] = counts.get(q["category"], 0) + 1
        for cat, count in counts.items():
            assert count >= 2, f"Category {cat} has only {count} queries"

    def test_each_marketplace_has_at_least_2(self):
        counts: dict[str, int] = {}
        for q in _load_queries():
            counts[q["marketplace"]] = counts.get(q["marketplace"], 0) + 1
        for mkt, count in counts.items():
            assert count >= 2, f"Marketplace {mkt} has only {count} queries"

    def test_expected_fields_are_lists(self):
        for q in _load_queries():
            assert isinstance(q["expected_fields"], list)
            assert len(q["expected_fields"]) >= 3

    def test_urls_are_strings(self):
        for q in _load_queries():
            assert isinstance(q["url"], str)
            assert q["url"].startswith("http")


# ── Ablation config definitions ──────────────────────────────────────────────

class TestAblationConfigs:
    @pytest.fixture(autouse=True)
    def _patch_imports(self):
        """Patch cua_loop imports so we can import eval.run_ablation without tzafon."""
        mock_client = MagicMock()
        mock_runner = MagicMock()
        mock_scaling = MagicMock()
        mock_verifier = MagicMock()

        import sys
        modules_to_mock = {
            "tzafon": MagicMock(),
            "kernel": MagicMock(),
            "kernel_sdk": MagicMock(),
            "cua_loop.client": mock_client,
            "cua_loop.backends": MagicMock(),
            "cua_loop.action_verifier": MagicMock(),
        }
        with patch.dict(sys.modules, modules_to_mock):
            mock_client.run_single_attempt = MagicMock()
            from eval.run_ablation import ALL_CONFIGS, AblationConfig
            self.ALL_CONFIGS = ALL_CONFIGS
            self.AblationConfig = AblationConfig
            yield

    def test_has_5_configs(self):
        assert len(self.ALL_CONFIGS) == 5

    def test_config_names(self):
        expected = {"no-aegis", "+retry", "+verification", "+security", "full-aegis"}
        assert set(self.ALL_CONFIGS.keys()) == expected

    def test_no_aegis_is_baseline(self):
        cfg = self.ALL_CONFIGS["no-aegis"]
        assert not cfg.use_retry
        assert not cfg.use_verification
        assert not cfg.use_security
        assert not cfg.use_wide_scaling
        assert not cfg.use_marketplace_scoring

    def test_full_aegis_enables_everything(self):
        cfg = self.ALL_CONFIGS["full-aegis"]
        assert cfg.use_retry
        assert cfg.use_verification
        assert cfg.use_security
        assert cfg.use_wide_scaling
        assert cfg.use_marketplace_scoring

    def test_configs_are_cumulative(self):
        retry = self.ALL_CONFIGS["+retry"]
        assert retry.use_retry
        assert not retry.use_verification

        verif = self.ALL_CONFIGS["+verification"]
        assert verif.use_retry
        assert verif.use_verification
        assert not verif.use_security

        sec = self.ALL_CONFIGS["+security"]
        assert sec.use_retry
        assert sec.use_verification
        assert sec.use_security
        assert not sec.use_wide_scaling


# ── Report generation ────────────────────────────────────────────────────────

class TestReportGeneration:
    def _write_mock_results(self, tmp: Path, config_name: str, entries: list[dict]):
        results_dir = tmp / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / f"{config_name}.json").write_text(json.dumps(entries))

    def _make_entry(self, success: bool, rows: int = 0, attempts: int = 1,
                    category: str = "electronics", marketplace: str = "ebay") -> dict:
        return {
            "query": "test query",
            "marketplace": marketplace,
            "category": category,
            "config": "test",
            "success": success,
            "rows_extracted": rows,
            "schema_valid": success,
            "num_attempts": attempts,
            "duration_s": 10.0,
            "blocked_actions": 0,
            "marketplace_score": None,
            "verifier_reason": "ok" if success else "fail",
            "error": None,
        }

    def test_compute_metrics_empty(self):
        from eval.generate_report import compute_metrics
        m = compute_metrics([])
        assert m["total_queries"] == 0
        assert m["success_rate"] == 0.0

    def test_compute_metrics_all_success(self):
        from eval.generate_report import compute_metrics
        entries = [self._make_entry(True, rows=5) for _ in range(10)]
        m = compute_metrics(entries)
        assert m["total_queries"] == 10
        assert m["success_count"] == 10
        assert m["success_rate"] == 1.0
        assert m["avg_rows_extracted"] == 5.0

    def test_compute_metrics_mixed(self):
        from eval.generate_report import compute_metrics
        entries = [
            self._make_entry(True, rows=3),
            self._make_entry(False, rows=0),
            self._make_entry(True, rows=7),
        ]
        m = compute_metrics(entries)
        assert m["success_count"] == 2
        assert abs(m["success_rate"] - 2 / 3) < 0.01
        assert abs(m["avg_rows_extracted"] - 10 / 3) < 0.1

    def test_compute_metrics_by_category(self):
        from eval.generate_report import compute_metrics
        entries = [
            self._make_entry(True, category="furniture"),
            self._make_entry(False, category="furniture"),
            self._make_entry(True, category="bikes"),
        ]
        m = compute_metrics(entries)
        assert m["by_category"]["furniture"]["successes"] == 1
        assert m["by_category"]["furniture"]["total"] == 2
        assert m["by_category"]["bikes"]["successes"] == 1

    def test_generate_report_writes_json(self):
        from eval.generate_report import generate_report

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_mock_results(tmp_path, "no-aegis", [
                self._make_entry(True, rows=3),
                self._make_entry(False),
            ])
            self._write_mock_results(tmp_path, "full-aegis", [
                self._make_entry(True, rows=5),
                self._make_entry(True, rows=4),
            ])

            report_path = tmp_path / "report.json"
            report = generate_report(
                results_dir=tmp_path / "results",
                report_path=report_path,
            )

            assert report_path.exists()
            saved = json.loads(report_path.read_text())
            assert "headline" in saved
            assert saved["headline"]["without_aegis_rate"] == 0.5
            assert saved["headline"]["with_aegis_rate"] == 1.0
            assert saved["headline"]["improvement_pp"] == 0.5

    def test_headline_missing_configs(self):
        from eval.generate_report import generate_report

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_mock_results(tmp_path, "plus_retry", [
                self._make_entry(True, rows=3),
            ])

            report = generate_report(
                results_dir=tmp_path / "results",
                report_path=tmp_path / "report.json",
            )
            assert report["headline"]["without_aegis_rate"] == 0
            assert report["headline"]["with_aegis_rate"] == 0


# ── QueryResult and ablation runner logic ────────────────────────────────────

class TestAblationRunner:
    @pytest.fixture(autouse=True)
    def _patch_imports(self):
        import sys
        modules_to_mock = {
            "tzafon": MagicMock(),
            "kernel": MagicMock(),
            "kernel_sdk": MagicMock(),
            "cua_loop.backends": MagicMock(),
            "cua_loop.action_verifier": MagicMock(),
        }

        mock_client_mod = MagicMock()
        mock_single = MagicMock()
        mock_client_mod.run_single_attempt = mock_single
        modules_to_mock["cua_loop.client"] = mock_client_mod

        with patch.dict(sys.modules, modules_to_mock):
            from eval.run_ablation import (
                QueryResult,
                _result_to_dict,
                load_queries,
            )
            self.QueryResult = QueryResult
            self._result_to_dict = _result_to_dict
            self.load_queries = load_queries
            yield

    def test_query_result_defaults(self):
        qr = self.QueryResult(
            query={"query": "test", "marketplace": "ebay"},
            config_name="no-aegis",
            success=False,
        )
        assert qr.rows_extracted == 0
        assert qr.num_attempts == 1
        assert qr.marketplace_score is None

    def test_result_to_dict(self):
        qr = self.QueryResult(
            query={"query": "test", "marketplace": "ebay", "category": "electronics"},
            config_name="full-aegis",
            success=True,
            rows_extracted=5,
            schema_valid=True,
            num_attempts=3,
            duration_s=15.5,
        )
        d = self._result_to_dict(qr)
        assert d["config"] == "full-aegis"
        assert d["success"] is True
        assert d["rows_extracted"] == 5
        assert d["duration_s"] == 15.5
        assert d["marketplace"] == "ebay"
        assert d["category"] == "electronics"

    def test_load_queries(self):
        queries = self.load_queries(QUERIES_PATH)
        assert len(queries) == 20
        assert all("query" in q for q in queries)
