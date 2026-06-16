"""
Tests for insight_report.py — unit tests for helper functions + mocked pipeline.
"""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from insight_report import (
    _detect_anomaly,
    _extract_json,
    _summarize_rows,
    generate_report,
)


# ── _extract_json ─────────────────────────────────────────────────────────────

class TestExtractJson:
    def test_plain_json(self):
        raw = '{"questions": [{"question": "Q1", "sql": "SELECT 1", "type": "summary"}]}'
        result = _extract_json(raw)
        assert result["questions"][0]["question"] == "Q1"

    def test_json_with_code_fence(self):
        raw = '```json\n{"questions": []}\n```'
        result = _extract_json(raw)
        assert result == {"questions": []}

    def test_json_with_plain_fence(self):
        raw = "```\n{\"questions\": []}\n```"
        result = _extract_json(raw)
        assert result == {"questions": []}

    def test_json_with_leading_prose(self):
        raw = 'Here is the output:\n{"questions": [{"question": "Q", "sql": "SELECT 1", "type": "summary"}]}'
        result = _extract_json(raw)
        assert len(result["questions"]) == 1

    def test_invalid_json_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _extract_json("not json at all")


# ── _summarize_rows ───────────────────────────────────────────────────────────

class TestSummarizeRows:
    def test_empty_rows(self):
        assert _summarize_rows([]) == "(no data returned)"

    def test_single_row(self):
        rows = [{"battery_id": "B1", "soh": 99.5}]
        result = _summarize_rows(rows)
        assert "battery_id" in result
        assert "B1" in result
        assert "99.5" in result

    def test_truncates_at_max_rows(self):
        rows = [{"n": i} for i in range(20)]
        result = _summarize_rows(rows, max_rows=5)
        assert "15 more rows" in result

    def test_no_truncation_within_limit(self):
        rows = [{"n": i} for i in range(3)]
        result = _summarize_rows(rows, max_rows=5)
        assert "more rows" not in result


# ── _detect_anomaly ───────────────────────────────────────────────────────────

class TestDetectAnomaly:
    def test_no_anomaly_uniform_values(self):
        rows = [{"soh": 90 + i * 0.1} for i in range(10)]
        assert _detect_anomaly(rows) is False

    def test_detects_outlier(self):
        rows = [{"soh": 90.0}] * 9 + [{"soh": 10.0}]  # extreme outlier
        assert _detect_anomaly(rows) is True

    def test_too_few_rows_returns_false(self):
        rows = [{"soh": 90.0}, {"soh": 10.0}]
        assert _detect_anomaly(rows) is False

    def test_non_numeric_column_ignored(self):
        rows = [{"label": "A"}, {"label": "B"}, {"label": "C"}, {"label": "D"}]
        assert _detect_anomaly(rows) is False

    def test_constant_column_no_anomaly(self):
        rows = [{"soh": 90.0}] * 6
        assert _detect_anomaly(rows) is False


# ── generate_report (mocked LLM + DB) ────────────────────────────────────────

_MOCK_PLAN_JSON = json.dumps({
    "questions": [
        {"question": "How many records?", "sql": "SELECT COUNT(*) AS n FROM battery_cycles", "type": "summary"},
        {"question": "Average SOH?",      "sql": "SELECT AVG(soh) AS avg_soh FROM battery_cycles", "type": "summary"},
    ]
})

_MOCK_NARRATIVE = """\
## Ringkasan Eksekutif
Database berisi data baterai yang lengkap.

## Temuan Utama
### 1. Jumlah record
Terdapat 1810 record.

### 2. Rata-rata SOH
SOH rata-rata 85.8%.

## Rekomendasi
- Monitor B4 secara berkala.
- Perhatikan suhu pengisian.
- Lakukan analisis RUL lebih lanjut.
"""


@pytest.fixture()
def mock_llm_and_db(tmp_path, monkeypatch):
    """Patch LLM calls + database functions to avoid real I/O."""
    # Patch _call_llm to return plan JSON first, then narrative
    call_count = {"n": 0}
    def fake_call_llm(system, user):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _MOCK_PLAN_JSON
        return _MOCK_NARRATIVE

    monkeypatch.setattr("insight_report._call_llm", fake_call_llm)

    # Patch database functions
    monkeypatch.setattr("insight_report.get_db_label", lambda: "test.db")
    monkeypatch.setattr("insight_report.get_db_engine", lambda: "sqlite")
    monkeypatch.setattr("insight_report.get_schema", lambda: "TABLE battery_cycles ...")
    monkeypatch.setattr(
        "insight_report.profile_database", lambda: {}
    )
    monkeypatch.setattr(
        "insight_report.format_profile_for_prompt", lambda _: "profile text"
    )
    monkeypatch.setattr(
        "insight_report.execute_query",
        lambda sql: {"rows": [{"n": 1810}], "success": True},
    )


class TestGenerateReport:
    def test_returns_expected_keys(self, mock_llm_and_db):
        report = generate_report(n_questions=2)
        for key in ("title", "db_label", "generated_at", "narrative",
                    "executive_summary", "findings", "recommendations", "errors"):
            assert key in report

    def test_correct_number_of_findings(self, mock_llm_and_db):
        report = generate_report(n_questions=2)
        assert len(report["findings"]) == 2

    def test_executive_summary_extracted(self, mock_llm_and_db):
        report = generate_report(n_questions=2)
        assert "baterai" in report["executive_summary"].lower()

    def test_recommendations_extracted(self, mock_llm_and_db):
        report = generate_report(n_questions=2)
        assert "Monitor" in report["recommendations"]

    def test_no_errors_on_success(self, mock_llm_and_db):
        report = generate_report(n_questions=2)
        assert report["errors"] == []

    def test_progress_callback_called(self, mock_llm_and_db):
        steps = []
        generate_report(n_questions=2, on_progress=lambda s, d: steps.append(s))
        assert "profiling"    in steps
        assert "planning"     in steps
        assert "executing"    in steps
        assert "synthesizing" in steps
        assert "done"         in steps

    def test_bad_plan_json_returns_error(self, monkeypatch):
        monkeypatch.setattr("insight_report._call_llm", lambda s, u: "NOT JSON")
        monkeypatch.setattr("insight_report.get_db_label",  lambda: "test.db")
        monkeypatch.setattr("insight_report.get_db_engine", lambda: "sqlite")
        monkeypatch.setattr("insight_report.get_schema",    lambda: "")
        monkeypatch.setattr("insight_report.profile_database",         lambda: {})
        monkeypatch.setattr("insight_report.format_profile_for_prompt", lambda _: "")
        report = generate_report(n_questions=2)
        assert len(report["errors"]) > 0
        assert report["findings"] == []

    def test_sql_error_captured_in_finding(self, monkeypatch):
        call_count = {"n": 0}
        def fake_llm(s, u):
            call_count["n"] += 1
            return _MOCK_PLAN_JSON if call_count["n"] == 1 else _MOCK_NARRATIVE

        monkeypatch.setattr("insight_report._call_llm",    fake_llm)
        monkeypatch.setattr("insight_report.get_db_label",  lambda: "test.db")
        monkeypatch.setattr("insight_report.get_db_engine", lambda: "sqlite")
        monkeypatch.setattr("insight_report.get_schema",    lambda: "")
        monkeypatch.setattr("insight_report.profile_database",         lambda: {})
        monkeypatch.setattr("insight_report.format_profile_for_prompt", lambda _: "")
        # Simulate SQL error
        monkeypatch.setattr(
            "insight_report.execute_query",
            lambda sql: {"rows": [], "success": False, "error": "table not found"},
        )
        report = generate_report(n_questions=2)
        assert report["findings"][0]["error"] == "table not found"
        assert report["errors"] == []  # pipeline-level error is empty; finding-level error is in finding
