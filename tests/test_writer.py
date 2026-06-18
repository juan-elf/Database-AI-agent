"""
Tests for writer.py — all mocked, no real DB or file writes required.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from writer import (
    MAX_ROWS_PER_INSERT,
    ColumnValidationError,
    WriteNotConfiguredError,
    execute_insert,
    get_writable_table_columns,
    get_write_connection,
    is_write_configured,
    preview_insert,
    validate_insert,
)

# ── Shared fixtures ────────────────────────────────────────────────────────────

SAMPLE_DF = pd.DataFrame({
    "battery_id": ["B1", "B2", "B3"],
    "soh": [98.5, 95.0, 92.1],
})

BATTERY_COLS = [
    {"name": "battery_id", "data_type": "text",    "is_nullable": "YES", "column_default": None},
    {"name": "soh",        "data_type": "numeric", "is_nullable": "YES", "column_default": None},
    {"name": "cycle",      "data_type": "integer", "is_nullable": "YES", "column_default": None},
]

GOOD_MAPPING = {"battery_id": "battery_id", "soh": "soh"}


def _patch_cols(monkeypatch, cols=None):
    monkeypatch.setattr("writer.get_writable_table_columns", lambda t: cols if cols is not None else BATTERY_COLS)


# ── is_write_configured ───────────────────────────────────────────────────────

class TestIsWriteConfigured:
    def test_sqlite_always_configured(self, monkeypatch):
        monkeypatch.setattr("writer.get_db_engine", lambda: "sqlite")
        assert is_write_configured() is True

    def test_postgres_without_write_url_not_configured(self, monkeypatch, monkeypatch_env):
        monkeypatch.setattr("writer.get_db_engine", lambda: "postgres")
        monkeypatch_env("WRITE_DATABASE_URL", None)
        assert is_write_configured() is False

    def test_postgres_with_write_url_configured(self, monkeypatch, monkeypatch_env):
        monkeypatch.setattr("writer.get_db_engine", lambda: "postgres")
        monkeypatch_env("WRITE_DATABASE_URL", "postgresql://user:pass@host:5432/db")
        assert is_write_configured() is True


# ── get_write_connection ───────────────────────────────────────────────────────

class TestGetWriteConnection:
    def test_postgres_without_write_url_raises(self, monkeypatch, monkeypatch_env):
        monkeypatch.setattr("writer.get_db_engine", lambda: "postgres")
        monkeypatch_env("WRITE_DATABASE_URL", None)
        with pytest.raises(WriteNotConfiguredError):
            get_write_connection()

    def test_sqlite_returns_connection(self, monkeypatch, tmp_path):
        import sqlite3
        db = tmp_path / "test.db"
        sqlite3.connect(str(db)).close()
        monkeypatch.setattr("writer.get_db_engine", lambda: "sqlite")
        monkeypatch.setattr("writer.get_database_path", lambda: db)
        conn = get_write_connection()
        assert conn is not None
        conn.close()


# ── get_writable_table_columns ────────────────────────────────────────────────

class TestGetWritableTableColumns:
    def test_invalid_table_name_raises(self):
        with pytest.raises(ColumnValidationError, match="Invalid table name"):
            get_writable_table_columns("'; DROP TABLE users; --")

    def test_mocked_postgres_returns_columns(self, monkeypatch):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("battery_id", "text", "YES", None)]
        mock_conn.cursor.return_value = mock_cursor
        monkeypatch.setattr("writer.get_connection", lambda: mock_conn)
        monkeypatch.setattr("writer._use_postgres", lambda: True)
        cols = get_writable_table_columns("battery_cycles")
        assert cols[0]["name"] == "battery_id"
        assert cols[0]["data_type"] == "text"

    def test_table_not_found_raises(self, monkeypatch):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        monkeypatch.setattr("writer.get_connection", lambda: mock_conn)
        monkeypatch.setattr("writer._use_postgres", lambda: True)
        with pytest.raises(ColumnValidationError, match="tidak ditemukan"):
            get_writable_table_columns("nonexistent_table")

    def test_mocked_sqlite_returns_columns(self, monkeypatch):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # PRAGMA table_info row: (cid, name, type, notnull, dflt_value, pk)
        mock_cursor.fetchall.return_value = [(0, "battery_id", "TEXT", 0, None, 0)]
        mock_conn.cursor.return_value = mock_cursor
        monkeypatch.setattr("writer.get_connection", lambda: mock_conn)
        monkeypatch.setattr("writer._use_postgres", lambda: False)
        cols = get_writable_table_columns("battery_cycles")
        assert cols[0]["name"] == "battery_id"


# ── validate_insert ───────────────────────────────────────────────────────────

class TestValidateInsert:
    def test_empty_df_is_invalid(self, monkeypatch):
        _patch_cols(monkeypatch)
        result = validate_insert(pd.DataFrame(), "battery_cycles", GOOD_MAPPING)
        assert not result["valid"]
        assert "kosong" in result["errors"][0]

    def test_empty_mapping_is_invalid(self, monkeypatch):
        _patch_cols(monkeypatch)
        result = validate_insert(SAMPLE_DF, "battery_cycles", {})
        assert not result["valid"]

    def test_row_count_guard_blocks_over_limit(self, monkeypatch):
        _patch_cols(monkeypatch)
        n = MAX_ROWS_PER_INSERT + 1
        big_df = pd.DataFrame({"battery_id": ["B"] * n, "soh": [99.0] * n})
        result = validate_insert(big_df, "battery_cycles", GOOD_MAPPING)
        assert not result["valid"]
        assert str(MAX_ROWS_PER_INSERT) in result["errors"][0]

    def test_row_count_override_passes(self, monkeypatch):
        _patch_cols(monkeypatch)
        n = MAX_ROWS_PER_INSERT + 1
        big_df = pd.DataFrame({"battery_id": ["B"] * n, "soh": [99.0] * n})
        result = validate_insert(big_df, "battery_cycles", GOOD_MAPPING, override_row_limit=True)
        assert result["valid"]

    def test_unknown_target_column_is_invalid(self, monkeypatch):
        _patch_cols(monkeypatch)
        bad_mapping = {"battery_id": "nonexistent_col"}
        result = validate_insert(SAMPLE_DF, "battery_cycles", bad_mapping)
        assert not result["valid"]
        assert "nonexistent_col" in result["errors"][0]

    def test_happy_path_returns_mapped_df(self, monkeypatch):
        _patch_cols(monkeypatch)
        result = validate_insert(SAMPLE_DF, "battery_cycles", GOOD_MAPPING)
        assert result["valid"]
        assert list(result["mapped_df"].columns) == ["battery_id", "soh"]
        assert len(result["mapped_df"]) == 3

    def test_unmapped_input_columns_produce_warning(self, monkeypatch):
        _patch_cols(monkeypatch)
        df = SAMPLE_DF.copy()
        df["extra_col"] = "x"
        result = validate_insert(df, "battery_cycles", GOOD_MAPPING)
        assert result["valid"]
        assert any("extra_col" in w for w in result["warnings"])

    def test_table_not_found_propagates_error(self, monkeypatch):
        def raise_err(t):
            raise ColumnValidationError(f"Tabel '{t}' tidak ditemukan.")
        monkeypatch.setattr("writer.get_writable_table_columns", raise_err)
        result = validate_insert(SAMPLE_DF, "missing_table", GOOD_MAPPING)
        assert not result["valid"]
        assert "tidak ditemukan" in result["errors"][0]

    def test_no_input_cols_in_mapping_is_invalid(self, monkeypatch):
        _patch_cols(monkeypatch)
        df = pd.DataFrame({"unrelated": [1, 2]})
        result = validate_insert(df, "battery_cycles", GOOD_MAPPING)
        assert not result["valid"]


# ── preview_insert ────────────────────────────────────────────────────────────

class TestPreviewInsert:
    def test_returns_row_count_and_preview_df(self, monkeypatch):
        _patch_cols(monkeypatch)
        result = preview_insert(SAMPLE_DF, "battery_cycles", GOOD_MAPPING)
        assert result["valid"]
        assert result["row_count"] == 3
        assert len(result["preview_df"]) <= 5
        assert result["table"] == "battery_cycles"
        assert "battery_id" in result["columns"]

    def test_invalid_input_propagates_errors(self, monkeypatch):
        _patch_cols(monkeypatch)
        result = preview_insert(pd.DataFrame(), "battery_cycles", GOOD_MAPPING)
        assert not result["valid"]
        assert result["errors"]

    def test_custom_max_preview_rows(self, monkeypatch):
        _patch_cols(monkeypatch)
        df = pd.DataFrame({"battery_id": [f"B{i}" for i in range(10)],
                           "soh": [90.0] * 10})
        result = preview_insert(df, "battery_cycles", GOOD_MAPPING, max_preview_rows=3)
        assert result["valid"]
        assert len(result["preview_df"]) == 3


# ── execute_insert ────────────────────────────────────────────────────────────

def _mock_conn(monkeypatch, side_effect=None):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    if side_effect:
        mock_cursor.executemany.side_effect = side_effect
    mock_conn.cursor.return_value = mock_cursor
    _patch_cols(monkeypatch)
    monkeypatch.setattr("writer.get_write_connection", lambda: mock_conn)
    monkeypatch.setattr("writer._write_uses_postgres", lambda: False)
    return mock_conn


class TestExecuteInsert:
    def test_validation_failure_skips_connection(self, monkeypatch):
        _patch_cols(monkeypatch)
        conn_called = []
        monkeypatch.setattr("writer.get_write_connection",
                            lambda: conn_called.append(True) or MagicMock())
        result = execute_insert(pd.DataFrame(), "battery_cycles", GOOD_MAPPING)
        assert not result["success"]
        assert result["rows_inserted"] == 0
        assert len(conn_called) == 0

    def test_write_not_configured_returns_error(self, monkeypatch):
        _patch_cols(monkeypatch)
        def raise_not_configured():
            raise WriteNotConfiguredError("WRITE_DATABASE_URL tidak di-set.")
        monkeypatch.setattr("writer.get_write_connection", raise_not_configured)
        result = execute_insert(SAMPLE_DF, "battery_cycles", GOOD_MAPPING)
        assert not result["success"]
        assert any("WRITE_DATABASE_URL" in e for e in result["errors"])

    def test_successful_insert_commits_and_returns_count(self, monkeypatch, tmp_path):
        monkeypatch.setattr("writer._AUDIT_DIR", tmp_path)
        mock_conn = _mock_conn(monkeypatch)
        result = execute_insert(SAMPLE_DF, "battery_cycles", GOOD_MAPPING, session_id="s-abc")
        assert result["success"]
        assert result["rows_inserted"] == 3
        assert result["table"] == "battery_cycles"
        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_not_called()

    def test_db_error_triggers_rollback(self, monkeypatch, tmp_path):
        monkeypatch.setattr("writer._AUDIT_DIR", tmp_path)
        mock_conn = _mock_conn(monkeypatch, side_effect=Exception("duplicate key"))
        result = execute_insert(SAMPLE_DF, "battery_cycles", GOOD_MAPPING)
        assert not result["success"]
        assert "duplicate key" in result["errors"][0]
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_audit_log_written_on_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("writer._AUDIT_DIR", tmp_path)
        _mock_conn(monkeypatch)
        execute_insert(SAMPLE_DF, "battery_cycles", GOOD_MAPPING, session_id="s123")
        audit_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(audit_files) == 1
        entry = json.loads(audit_files[0].read_text().splitlines()[0])
        assert entry["event"] == "insert"
        assert entry["session_id"] == "s123"
        assert entry["status"] == "success"
        assert entry["rows_inserted"] == 3

    def test_audit_log_written_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr("writer._AUDIT_DIR", tmp_path)
        _mock_conn(monkeypatch, side_effect=Exception("error!"))
        execute_insert(SAMPLE_DF, "battery_cycles", GOOD_MAPPING)
        audit_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(audit_files) == 1
        entry = json.loads(audit_files[0].read_text().splitlines()[0])
        assert entry["status"] == "failed"
        assert "error!" in entry["error"]

    def test_override_row_limit_flag_passed_through(self, monkeypatch, tmp_path):
        monkeypatch.setattr("writer._AUDIT_DIR", tmp_path)
        _mock_conn(monkeypatch)
        n = MAX_ROWS_PER_INSERT + 1
        big_df = pd.DataFrame({"battery_id": ["B"] * n, "soh": [99.0] * n})
        result = execute_insert(big_df, "battery_cycles", GOOD_MAPPING, override_row_limit=True)
        assert result["success"]
        assert result["rows_inserted"] == n


# ── Conftest helper ───────────────────────────────────────────────────────────

@pytest.fixture
def monkeypatch_env(monkeypatch):
    """Helper to set/unset env vars cleanly."""
    def _set(key, value):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return _set
