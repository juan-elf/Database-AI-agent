"""
Tests for tools.py — call_tool dispatcher and _execute_sql_tool truncation.

Run with:
    pytest tests/test_tools.py -v
"""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database
from tools import call_tool, TOOLS_SCHEMA


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """SQLite database with 59 rows — enough to trigger the 50-row truncation."""
    db_file = tmp_path / "tools_test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val REAL)")
    conn.executemany("INSERT INTO t VALUES (?,?)", [(i, i * 1.5) for i in range(1, 60)])
    conn.commit()
    conn.close()
    database.set_database(db_file)
    return db_file


# ── call_tool dispatcher ──────────────────────────────────────────────────────

class TestCallTool:

    def test_unknown_tool_returns_error_json(self):
        result = json.loads(call_tool("no_such_tool", {}))
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_missing_required_arg_returns_error(self, temp_db):
        # execute_sql requires 'sql'; passing empty dict triggers TypeError
        result = json.loads(call_tool("execute_sql", {}))
        assert result["success"] is False
        assert "Invalid arguments" in result["error"]

    def test_extra_unknown_kwarg_returns_error(self, temp_db):
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT 1", "bogus": True}))
        assert result["success"] is False
        assert "Invalid arguments" in result["error"]

    def test_execute_sql_dispatched(self, temp_db):
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT COUNT(*) AS n FROM t"}))
        assert result["success"] is True
        assert result["rows"][0]["n"] == 59

    def test_get_distinct_values_dispatched(self, temp_db):
        result = json.loads(call_tool("get_distinct_values", {"table": "t", "column": "id"}))
        assert result["success"] is True
        assert result["total_distinct"] == 59

    def test_unexpected_exception_caught_returns_error(self):
        boom = MagicMock(side_effect=RuntimeError("unexpected boom"))
        with patch.dict("tools.TOOL_FUNCTIONS", {"execute_sql": boom}):
            result = json.loads(call_tool("execute_sql", {"sql": "SELECT 1"}))
        assert result["success"] is False
        assert "unexpected boom" in result["error"]

    def test_tools_schema_has_four_entries(self):
        assert len(TOOLS_SCHEMA) == 4
        names = {t["function"]["name"] for t in TOOLS_SCHEMA}
        assert names == {"execute_sql", "get_distinct_values", "web_search", "run_analysis"}


# ── _execute_sql_tool row truncation ──────────────────────────────────────────

class TestExecuteSqlTruncation:

    def test_no_truncation_under_50_rows(self, temp_db):
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT * FROM t WHERE id <= 10"}))
        assert result["success"] is True
        assert result["row_count"] == 10
        assert len(result["rows"]) == 10
        assert "note" not in result

    def test_exactly_50_rows_not_truncated(self, temp_db):
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT * FROM t WHERE id <= 50"}))
        assert result["success"] is True
        assert len(result["rows"]) == 50
        assert "note" not in result

    def test_51_rows_truncated_to_50(self, temp_db):
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT * FROM t WHERE id <= 51"}))
        assert result["success"] is True
        assert result["row_count"] == 51
        assert len(result["rows"]) == 50

    def test_truncation_adds_note_with_total_count(self, temp_db):
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT * FROM t"}))
        assert result["success"] is True
        assert "note" in result
        assert "59" in result["note"]

    def test_truncation_note_mentions_aggregation(self, temp_db):
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT * FROM t"}))
        note = result["note"].upper()
        assert "AGGREGATION" in note or "AVG" in note or "COUNT" in note

    def test_row_count_field_reflects_actual_total(self, temp_db):
        # row_count must show real total, not the truncated 50
        result = json.loads(call_tool("execute_sql", {"sql": "SELECT * FROM t"}))
        assert result["row_count"] == 59


# ── web_search tool ───────────────────────────────────────────────────────────

class TestWebSearchTool:

    def test_web_search_delegates_to_search_web(self):
        fake_result = {"results": [{"title": "Test", "url": "https://example.com"}]}
        with patch("tools.search_web", return_value=fake_result) as mock_sw:
            result = json.loads(call_tool("web_search", {"query": "battery degradation"}))
        mock_sw.assert_called_once_with("battery degradation", 5)
        assert result["results"][0]["title"] == "Test"

    def test_web_search_passes_max_results(self):
        with patch("tools.search_web", return_value={}) as mock_sw:
            call_tool("web_search", {"query": "test", "max_results": 3})
        mock_sw.assert_called_once_with("test", 3)
