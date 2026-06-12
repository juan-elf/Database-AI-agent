"""
Tests for database.py — validate_query, execute_query, get_distinct_values.

Run with:
    pytest tests/test_database.py -v
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database
from database import validate_query, execute_query, get_distinct_values, set_database


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """Temporary SQLite database with a simple items table."""
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE items (
            id       INTEGER PRIMARY KEY,
            name     TEXT    NOT NULL,
            value    REAL,
            category TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO items VALUES (?, ?, ?, ?)",
        [
            (1, "alpha", 10.5,  "A"),
            (2, "beta",  20.0,  "B"),
            (3, "gamma", 30.75, "A"),
            (4, "delta", None,  "B"),  # NULL value row
        ],
    )
    conn.commit()
    conn.close()
    set_database(db_file)
    return db_file


# ── validate_query ────────────────────────────────────────────────────────────

class TestValidateQuery:

    # --- Valid queries ---

    def test_simple_select(self):
        ok, err = validate_query("SELECT 1")
        assert ok is True
        assert err is None

    def test_select_from_table(self):
        ok, err = validate_query("SELECT * FROM items")
        assert ok is True
        assert err is None

    def test_with_cte(self):
        ok, err = validate_query("WITH cte AS (SELECT 1 AS n) SELECT n FROM cte")
        assert ok is True
        assert err is None

    def test_leading_trailing_whitespace_stripped(self):
        ok, err = validate_query("   SELECT 1   ")
        assert ok is True
        assert err is None

    def test_trailing_semicolon_allowed(self):
        # A lone trailing semicolon is stripped before the multi-statement check.
        ok, err = validate_query("SELECT 1;")
        assert ok is True
        assert err is None

    def test_semicolon_inside_string_literal_allowed(self):
        # String literals are stripped before the multi-statement check,
        # so a semicolon inside quotes must not trigger a rejection.
        ok, err = validate_query("SELECT 'hello; world' AS msg")
        assert ok is True
        assert err is None

    # --- Empty / blank ---

    def test_empty_string(self):
        ok, err = validate_query("")
        assert ok is False
        assert "empty" in err.lower()

    def test_whitespace_only(self):
        ok, err = validate_query("   \t\n   ")
        assert ok is False

    # --- Wrong starting keyword ---

    def test_starts_with_from(self):
        ok, err = validate_query("FROM items SELECT *")
        assert ok is False
        assert "SELECT" in err or "WITH" in err

    def test_random_text(self):
        ok, err = validate_query("give me all customers")
        assert ok is False

    def test_comment_before_select_rejected(self):
        # Current behavior: SQL starting with a comment (--) is rejected because
        # the stripped string begins with '-', not 'select' or 'with'.
        ok, err = validate_query("-- fetch all\nSELECT * FROM items")
        assert ok is False

    # --- Forbidden keywords ---

    @pytest.mark.parametrize("keyword", [
        "INSERT", "UPDATE", "DELETE", "DROP",
        "ALTER", "TRUNCATE", "CREATE", "REPLACE",
        "ATTACH", "DETACH", "PRAGMA", "VACUUM",
    ])
    def test_forbidden_keyword_blocked(self, keyword):
        # Keyword is embedded in an otherwise-valid SELECT.
        sql = f"SELECT {keyword} FROM t"
        ok, err = validate_query(sql)
        assert ok is False
        assert keyword in err

    def test_forbidden_keyword_lowercase_blocked(self):
        ok, err = validate_query("SELECT drop FROM t")
        assert ok is False
        assert "DROP" in err

    def test_forbidden_keyword_in_string_literal_still_blocked(self):
        # The forbidden-keyword regex is applied BEFORE string literals are
        # stripped, so 'drop' inside a quoted value is also rejected.
        # This documents current strict behavior.
        ok, err = validate_query("SELECT * FROM items WHERE name = 'drop'")
        assert ok is False

    def test_keyword_as_substring_not_blocked(self):
        # 'create' inside 'recreation' has no word boundary on the left
        # side — the \b regex must NOT match it.
        ok, err = validate_query("SELECT recreation FROM items")
        assert ok is True
        assert err is None

    # --- Multi-statement ---

    def test_two_statements_blocked(self):
        ok, err = validate_query("SELECT 1; SELECT 2")
        assert ok is False
        assert "multiple" in err.lower()

    def test_two_statements_both_with_trailing_semicolon(self):
        ok, err = validate_query("SELECT 1; SELECT 2;")
        assert ok is False

    def test_single_statement_with_trailing_semicolon_allowed(self):
        ok, err = validate_query("SELECT * FROM items;")
        assert ok is True


# ── execute_query ─────────────────────────────────────────────────────────────

class TestExecuteQuery:

    # --- Response shape ---

    def test_success_result_has_all_keys(self, temp_db):
        result = execute_query("SELECT 1 AS x")
        for key in ("success", "rows", "row_count", "columns", "error", "hint", "sql_executed"):
            assert key in result

    def test_sql_executed_field_preserved(self, temp_db):
        sql = "SELECT 1 AS x"
        assert execute_query(sql)["sql_executed"] == sql

    # --- Successful queries ---

    def test_simple_select(self, temp_db):
        result = execute_query("SELECT id, name FROM items WHERE id = 1")
        assert result["success"] is True
        assert result["row_count"] == 1
        assert result["rows"][0] == {"id": 1, "name": "alpha"}
        assert result["error"] is None
        assert result["hint"] is None

    def test_select_all_rows(self, temp_db):
        result = execute_query("SELECT * FROM items ORDER BY id")
        assert result["success"] is True
        assert result["row_count"] == 4
        assert result["columns"] == ["id", "name", "value", "category"]

    def test_aggregate_count(self, temp_db):
        result = execute_query("SELECT COUNT(*) AS n FROM items")
        assert result["success"] is True
        assert result["rows"][0]["n"] == 4

    def test_filter_returns_subset(self, temp_db):
        result = execute_query("SELECT id FROM items WHERE category = 'A' ORDER BY id")
        assert result["success"] is True
        assert result["row_count"] == 2
        assert [r["id"] for r in result["rows"]] == [1, 3]

    def test_empty_result_set(self, temp_db):
        result = execute_query("SELECT * FROM items WHERE id = 9999")
        assert result["success"] is True
        assert result["row_count"] == 0
        assert result["rows"] == []

    def test_null_value_in_result(self, temp_db):
        result = execute_query("SELECT value FROM items WHERE id = 4")
        assert result["success"] is True
        assert result["rows"][0]["value"] is None

    def test_with_cte(self, temp_db):
        result = execute_query(
            "WITH ranked AS (SELECT * FROM items) "
            "SELECT COUNT(*) AS n FROM ranked"
        )
        assert result["success"] is True
        assert result["rows"][0]["n"] == 4

    # --- Validation failures (no DB call should be made) ---

    def test_validation_fail_forbidden_keyword_no_hint(self, temp_db):
        # Validation errors return no hint — only runtime SQL errors get hints.
        result = execute_query("DROP TABLE items")
        assert result["success"] is False
        assert result["rows"] is None
        assert result["hint"] is None

    def test_validation_fail_empty_query(self, temp_db):
        result = execute_query("")
        assert result["success"] is False
        assert result["rows"] is None

    def test_validation_fail_multi_statement(self, temp_db):
        result = execute_query("SELECT 1; SELECT 2")
        assert result["success"] is False
        assert result["rows"] is None

    # --- SQL runtime errors with hints ---

    def test_no_such_table_returns_hint_with_table_list(self, temp_db):
        result = execute_query("SELECT * FROM nonexistent_table")
        assert result["success"] is False
        assert "SQL Error" in result["error"]
        assert result["hint"] is not None
        # Hint must tell the user which tables actually exist.
        assert "items" in result["hint"]

    def test_no_such_column_returns_hint(self, temp_db):
        result = execute_query("SELECT missing_column FROM items")
        assert result["success"] is False
        assert result["hint"] is not None
        assert "column" in result["hint"].lower()

    def test_syntax_error_returns_hint(self, temp_db):
        result = execute_query("SELECT FROM WHERE")
        assert result["success"] is False
        assert result["hint"] is not None
        assert "syntax" in result["hint"].lower()

    def test_connection_is_read_only_at_driver_level(self, temp_db):
        """SQLite must refuse writes even if validation is bypassed."""
        from database import get_connection
        conn = get_connection()
        with pytest.raises(sqlite3.OperationalError, match="read"):
            conn.execute("INSERT INTO items VALUES (99, 'x', 1.0, 'Z')")
        conn.close()

    def test_postgres_column_error_returns_column_hint_not_table_hint(self, temp_db):
        """Postgres column errors contain 'does not exist' — must not be
        misidentified as a table error and return 'wrong table name' hint."""
        from database import _generate_error_hint
        hint = _generate_error_hint('ERROR: column "xyz" does not exist')
        assert "column" in hint.lower()
        assert "table" not in hint.lower()

    def test_postgres_table_error_returns_table_hint(self, temp_db):
        from database import _generate_error_hint
        hint = _generate_error_hint('ERROR: relation "nonexistent" does not exist')
        assert "table" in hint.lower()
        assert "column" not in hint.lower()

    def test_ambiguous_column_returns_hint(self, temp_db):
        # Add a second table sharing a column name to trigger ambiguity.
        conn = sqlite3.connect(temp_db)
        conn.execute("CREATE TABLE other (id INTEGER, score REAL)")
        conn.commit()
        conn.close()
        result = execute_query("SELECT id FROM items, other")
        assert result["success"] is False
        assert result["hint"] is not None
        assert "ambiguous" in result["hint"].lower()


# ── get_distinct_values ───────────────────────────────────────────────────────

class TestGetDistinctValues:

    def test_basic_categorical_column(self, temp_db):
        result = get_distinct_values("items", "category")
        assert result["success"] is True
        assert set(result["distinct_values"]) == {"A", "B"}
        assert result["total_distinct"] == 2
        assert result["table"] == "items"
        assert result["column"] == "category"

    def test_showing_matches_distinct_values_length(self, temp_db):
        result = get_distinct_values("items", "category")
        assert result["showing"] == len(result["distinct_values"])

    def test_nulls_excluded_from_distinct(self, temp_db):
        # Row id=4 has value=NULL; NULL must not appear in distinct values.
        result = get_distinct_values("items", "value")
        assert result["success"] is True
        assert None not in result["distinct_values"]
        assert result["total_distinct"] == 3  # 10.5, 20.0, 30.75

    def test_limit_respected(self, temp_db):
        result = get_distinct_values("items", "id", limit=2)
        assert result["success"] is True
        assert result["showing"] == 2
        # total_distinct reports the true count regardless of limit
        assert result["total_distinct"] == 4

    def test_limit_zero_clamped_to_one(self, temp_db):
        result = get_distinct_values("items", "category", limit=0)
        assert result["success"] is True
        assert result["showing"] >= 1

    def test_limit_above_max_clamped(self, temp_db):
        result = get_distinct_values("items", "category", limit=9999)
        assert result["success"] is True  # must not raise

    def test_nonexistent_table(self, temp_db):
        result = get_distinct_values("no_such_table", "id")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_nonexistent_column(self, temp_db):
        result = get_distinct_values("items", "no_such_col")
        assert result["success"] is False
        assert "no_such_col" in result["error"]

    def test_table_name_with_semicolon_rejected(self, temp_db):
        result = get_distinct_values("items; DROP TABLE items", "category")
        assert result["success"] is False

    def test_table_name_with_space_rejected(self, temp_db):
        result = get_distinct_values("bad table", "category")
        assert result["success"] is False

    def test_column_name_with_special_chars_rejected(self, temp_db):
        result = get_distinct_values("items", "col--injection")
        assert result["success"] is False

    def test_column_name_with_dot_rejected(self, temp_db):
        result = get_distinct_values("items", "t.id")
        assert result["success"] is False
