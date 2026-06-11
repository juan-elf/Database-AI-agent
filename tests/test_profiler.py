"""
Tests for profiler.py — auto data-profiling module.

Run with:
    pytest tests/test_profiler.py -v
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database
import profiler
from profiler import (
    profile_database,
    format_profile_for_prompt,
    CATEGORICAL_THRESHOLD,
)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """
    DB with columns covering all semantic types and null scenarios:
      cycle     INTEGER NOT NULL  — numeric, no nulls, distinct=25
      soh       REAL    NOT NULL  — numeric, no nulls, distinct=25
      category  TEXT              — categorical (2 distinct: A/B), no nulls
      label     TEXT              — text/high-cardinality (25 distinct > threshold)
      note      TEXT              — categorical (1 non-null value), 80% nulls
    """
    db_file = tmp_path / "profile_test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE readings (
            id        INTEGER PRIMARY KEY,
            cycle     INTEGER NOT NULL,
            soh       REAL    NOT NULL,
            category  TEXT,
            label     TEXT,
            note      TEXT
        )
    """)
    rows = [
        (
            i,
            i * 10,                          # cycle: 10, 20, ..., 250
            round(100.0 - i * 4.0, 1),       # soh: 96.0, 92.0, ..., 0.0 (25 steps)
            "A" if i % 2 else "B",            # 2 distinct values
            f"id_{i:05d}",                    # 25 distinct values (> threshold)
            "note" if i <= 5 else None,       # 5 non-null, 20 null = 80% nulls
        )
        for i in range(1, 26)
    ]
    conn.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    database.set_database(db_file)
    profiler._cache.clear()  # ensure fresh profile per test
    return db_file


# ── profile_database structure ────────────────────────────────────────────────

class TestProfileDatabase:

    def test_table_present_in_profile(self, temp_db):
        p = profile_database()
        assert "readings" in p["tables"]

    def test_row_count_correct(self, temp_db):
        p = profile_database()
        assert p["tables"]["readings"]["row_count"] == 25

    def test_all_columns_profiled(self, temp_db):
        p = profile_database()
        cols = set(p["tables"]["readings"]["columns"])
        assert {"id", "cycle", "soh", "category", "label", "note"} == cols

    def test_profile_has_generated_at(self, temp_db):
        p = profile_database()
        assert "generated_at" in p
        assert isinstance(p["generated_at"], float)


# ── column semantic types ─────────────────────────────────────────────────────

class TestColumnSemanticTypes:

    def test_integer_column_is_numeric(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["cycle"]
        assert col["semantic_type"] == "numeric"

    def test_real_column_is_numeric(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["soh"]
        assert col["semantic_type"] == "numeric"

    def test_low_cardinality_text_is_categorical(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["category"]
        assert col["semantic_type"] == "categorical"

    def test_high_cardinality_text_is_text(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["label"]
        assert col["semantic_type"] == "text"

    def test_categorical_threshold_boundary(self, temp_db):
        # 'label' has 25 distinct values; threshold is 20 → should be "text"
        col = profile_database()["tables"]["readings"]["columns"]["label"]
        assert col["distinct_count"] > CATEGORICAL_THRESHOLD
        assert col["semantic_type"] == "text"


# ── column statistics ─────────────────────────────────────────────────────────

class TestColumnStatistics:

    def test_numeric_has_min(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["cycle"]
        assert col["min"] == 10

    def test_numeric_has_max(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["cycle"]
        assert col["max"] == 250

    def test_numeric_has_mean(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["cycle"]
        # mean of 10,20,...,250 = (10+250)/2 = 130
        assert col["mean"] == pytest.approx(130.0, abs=0.1)

    def test_categorical_has_sample_values(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["category"]
        vals = col["sample_values"]
        assert "A" in vals
        assert "B" in vals

    def test_categorical_distinct_count(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["category"]
        assert col["distinct_count"] == 2

    def test_no_nulls_zero_pct(self, temp_db):
        col = profile_database()["tables"]["readings"]["columns"]["cycle"]
        assert col["null_pct"] == 0.0

    def test_null_pct_correct(self, temp_db):
        # 20 out of 25 rows have null 'note'  → 80%
        col = profile_database()["tables"]["readings"]["columns"]["note"]
        assert col["null_pct"] == pytest.approx(80.0, abs=0.1)


# ── cache behaviour ───────────────────────────────────────────────────────────

class TestCache:

    def test_second_call_returns_same_object(self, temp_db):
        p1 = profile_database()
        p2 = profile_database()
        assert p1 is p2

    def test_force_returns_fresh_object(self, temp_db):
        p1 = profile_database()
        p2 = profile_database(force=True)
        assert p1 is not p2

    def test_force_result_matches_original(self, temp_db):
        p1 = profile_database()
        p2 = profile_database(force=True)
        assert p1["tables"]["readings"]["row_count"] == p2["tables"]["readings"]["row_count"]


# ── large-table stat skip ─────────────────────────────────────────────────────

class TestLargeTableSkip:

    def test_skips_column_stats_when_too_many_rows(self, monkeypatch, temp_db):
        monkeypatch.setattr(profiler, "MAX_STAT_ROWS", 5)
        p = profile_database(force=True)
        col = p["tables"]["readings"]["columns"]["cycle"]
        assert col.get("skipped") is True

    def test_skipped_column_has_no_min_max(self, monkeypatch, temp_db):
        monkeypatch.setattr(profiler, "MAX_STAT_ROWS", 5)
        p = profile_database(force=True)
        col = p["tables"]["readings"]["columns"]["soh"]
        assert "min" not in col
        assert "max" not in col


# ── format_profile_for_prompt ─────────────────────────────────────────────────

class TestFormatProfileForPrompt:

    def test_contains_table_name(self, temp_db):
        p = profile_database()
        text = format_profile_for_prompt(p)
        assert "readings" in text

    def test_contains_row_count(self, temp_db):
        p = profile_database()
        text = format_profile_for_prompt(p)
        assert "25" in text

    def test_contains_range_for_numeric(self, temp_db):
        p = profile_database()
        text = format_profile_for_prompt(p)
        assert "range:" in text

    def test_contains_values_for_categorical(self, temp_db):
        p = profile_database()
        text = format_profile_for_prompt(p)
        assert "values:" in text

    def test_contains_column_names(self, temp_db):
        p = profile_database()
        text = format_profile_for_prompt(p)
        assert "cycle" in text
        assert "category" in text

    def test_null_pct_shown_for_nullable_column(self, temp_db):
        p = profile_database()
        text = format_profile_for_prompt(p)
        assert "nulls:" in text

    def test_null_pct_not_shown_for_non_null_column(self, temp_db):
        p = profile_database()
        text = format_profile_for_prompt(p)
        # cycle has no nulls — its line should not contain "nulls:"
        cycle_line = next(
            (line for line in text.splitlines() if "cycle" in line and "INTEGER" in line),
            ""
        )
        assert "nulls:" not in cycle_line

    def test_empty_profile_returns_fallback(self, temp_db):
        text = format_profile_for_prompt({})
        assert "unavailable" in text.lower()

    def test_profile_with_empty_tables_returns_fallback(self, temp_db):
        text = format_profile_for_prompt({"tables": {}})
        assert "unavailable" in text.lower()
