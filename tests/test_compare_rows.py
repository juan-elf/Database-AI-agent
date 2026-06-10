"""
Tests for eval/run_eval.py — compare_rows function.

Run with:
    pytest tests/test_compare_rows.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.run_eval import compare_rows


class TestCompareRowsOrderIndependent:
    """Default: order_matters=False — row order must not affect the result."""

    def test_identical_rows_same_order(self):
        rows = [{"id": 1}, {"id": 2}]
        ok, _ = compare_rows(rows, rows, tolerance=0.0)
        assert ok is True

    def test_same_rows_different_order_passes(self):
        expected = [{"battery_id": "B5", "max_cycle": 220},
                    {"battery_id": "B6", "max_cycle": 210},
                    {"battery_id": "B7", "max_cycle": 250}]
        actual   = [{"battery_id": "B7", "max_cycle": 250},
                    {"battery_id": "B5", "max_cycle": 220},
                    {"battery_id": "B6", "max_cycle": 210}]
        ok, reason = compare_rows(expected, actual, tolerance=0.0)
        assert ok is True, reason

    def test_reversed_single_column_passes(self):
        expected = [{"n": 1}, {"n": 2}, {"n": 3}]
        actual   = [{"n": 3}, {"n": 2}, {"n": 1}]
        ok, reason = compare_rows(expected, actual, tolerance=0.0)
        assert ok is True, reason

    def test_both_empty(self):
        ok, _ = compare_rows([], [], tolerance=0.0)
        assert ok is True

    def test_row_count_mismatch_fails(self):
        ok, reason = compare_rows([{"id": 1}], [{"id": 1}, {"id": 2}], tolerance=0.0)
        assert ok is False
        assert "row count" in reason

    def test_wrong_value_fails_after_sort(self):
        expected = [{"id": 1}, {"id": 2}]
        actual   = [{"id": 2}, {"id": 9}]   # 9 != 1
        ok, _ = compare_rows(expected, actual, tolerance=0.0)
        assert ok is False

    def test_numeric_tolerance_respected(self):
        expected = [{"soh": 71.39}]
        actual   = [{"soh": 71.40}]          # within 1% of 71.39
        ok, reason = compare_rows(expected, actual, tolerance=0.01)
        assert ok is True, reason

    def test_numeric_tolerance_exceeded_fails(self):
        expected = [{"soh": 71.0}]
        actual   = [{"soh": 80.0}]           # >1% off
        ok, _ = compare_rows(expected, actual, tolerance=0.01)
        assert ok is False

    def test_string_values_case_insensitive(self):
        expected = [{"name": "Alpha"}]
        actual   = [{"name": "alpha"}]
        ok, reason = compare_rows(expected, actual, tolerance=0.0)
        assert ok is True, reason

    def test_null_values_match(self):
        expected = [{"value": None}]
        actual   = [{"value": None}]
        ok, reason = compare_rows(expected, actual, tolerance=0.0)
        assert ok is True, reason


class TestCompareRowsOrderMatters:
    """When order_matters=True, row position must match exactly."""

    def test_same_order_passes(self):
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        ok, _ = compare_rows(rows, rows, tolerance=0.0, order_matters=True)
        assert ok is True

    def test_different_order_fails(self):
        expected = [{"id": 1}, {"id": 2}]
        actual   = [{"id": 2}, {"id": 1}]
        ok, _ = compare_rows(expected, actual, tolerance=0.0, order_matters=True)
        assert ok is False

    def test_reversed_list_fails(self):
        expected = [{"rank": 1, "name": "B6"},
                    {"rank": 2, "name": "B7"},
                    {"rank": 3, "name": "B5"}]
        actual   = [{"rank": 3, "name": "B5"},
                    {"rank": 2, "name": "B7"},
                    {"rank": 1, "name": "B6"}]
        ok, _ = compare_rows(expected, actual, tolerance=0.0, order_matters=True)
        assert ok is False
