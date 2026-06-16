"""
Tests for router.py — catalog builder + heuristic scoring + mocked LLM judge.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from router import (
    _column_name_similarity,
    _composite_score,
    _normalize_col,
    _rank_candidates,
    _type_compatibility,
    _value_overlap,
    build_catalog,
    classify_data,
)


# ── _normalize_col ────────────────────────────────────────────────────────────

class TestNormalizeCol:
    def test_lowercases(self):
        assert _normalize_col("Battery_ID") == "batteryid"

    def test_strips_special_chars(self):
        assert _normalize_col("charge temp (C)") == "chargetempc"

    def test_identical_after_normalization(self):
        assert _normalize_col("soh") == _normalize_col("SOH")


# ── _column_name_similarity ──────────────────────────────────────────────────

class TestColumnNameSimilarity:
    def test_identical_sets(self):
        cols = ["battery_id", "soh", "cycle"]
        assert _column_name_similarity(cols, cols) == 1.0

    def test_no_overlap(self):
        assert _column_name_similarity(["a", "b"], ["x", "y"]) == 0.0

    def test_partial_overlap(self):
        score = _column_name_similarity(["battery_id", "soh"], ["battery_id", "rul"])
        assert 0 < score < 1

    def test_empty_inputs(self):
        assert _column_name_similarity([], ["a"]) == 0.0
        assert _column_name_similarity(["a"], []) == 0.0

    def test_case_and_formatting_insensitive(self):
        score = _column_name_similarity(["Battery ID", "SOH"], ["battery_id", "soh"])
        assert score == 1.0


# ── _type_compatibility ───────────────────────────────────────────────────────

class TestTypeCompatibility:
    def test_numeric_matches_numeric(self):
        df = pd.DataFrame({"soh": [99.5, 88.2]})
        table_cols = [{"name": "soh", "semantic_type": "numeric"}]
        assert _type_compatibility(df, table_cols) == 1.0

    def test_text_matches_categorical(self):
        df = pd.DataFrame({"battery_id": ["B1", "B2"]})
        table_cols = [{"name": "battery_id", "semantic_type": "categorical"}]
        assert _type_compatibility(df, table_cols) == 1.0

    def test_type_mismatch(self):
        df = pd.DataFrame({"soh": ["high", "low"]})
        table_cols = [{"name": "soh", "semantic_type": "numeric"}]
        assert _type_compatibility(df, table_cols) == 0.0

    def test_no_matching_columns(self):
        df = pd.DataFrame({"x": [1, 2]})
        table_cols = [{"name": "y", "semantic_type": "numeric"}]
        assert _type_compatibility(df, table_cols) == 0.0

    def test_empty_df(self):
        assert _type_compatibility(pd.DataFrame(), [{"name": "x", "semantic_type": "numeric"}]) == 0.0


# ── _value_overlap ────────────────────────────────────────────────────────────

class TestValueOverlap:
    def test_categorical_full_overlap(self):
        df = pd.DataFrame({"battery_id": ["B1", "B2"]})
        table_cols = [{"name": "battery_id", "semantic_type": "categorical",
                        "sample_values": ["B1", "B2", "B3"]}]
        assert _value_overlap(df, table_cols) == 1.0

    def test_categorical_no_overlap(self):
        df = pd.DataFrame({"battery_id": ["X1", "X2"]})
        table_cols = [{"name": "battery_id", "semantic_type": "categorical",
                        "sample_values": ["B1", "B2"]}]
        assert _value_overlap(df, table_cols) == 0.0

    def test_numeric_within_range(self):
        df = pd.DataFrame({"soh": [85.0, 90.0]})
        table_cols = [{"name": "soh", "semantic_type": "numeric", "min": 70.0, "max": 100.0}]
        assert _value_overlap(df, table_cols) == 1.0

    def test_numeric_outside_range(self):
        df = pd.DataFrame({"soh": [500.0, 600.0]})
        table_cols = [{"name": "soh", "semantic_type": "numeric", "min": 70.0, "max": 100.0}]
        assert _value_overlap(df, table_cols) == 0.0

    def test_missing_range_skipped(self):
        df = pd.DataFrame({"soh": [85.0]})
        table_cols = [{"name": "soh", "semantic_type": "numeric", "min": None, "max": None}]
        assert _value_overlap(df, table_cols) == 0.0


# ── _composite_score ──────────────────────────────────────────────────────────

class TestCompositeScore:
    def test_perfect_score(self):
        assert _composite_score(1.0, 1.0, 1.0) == 1.0

    def test_zero_score(self):
        assert _composite_score(0.0, 0.0, 0.0) == 0.0

    def test_name_similarity_weighted_highest(self):
        score_name_only  = _composite_score(1.0, 0.0, 0.0)
        score_type_only  = _composite_score(0.0, 1.0, 0.0)
        score_value_only = _composite_score(0.0, 0.0, 1.0)
        assert score_name_only > score_type_only > score_value_only


# ── _rank_candidates ──────────────────────────────────────────────────────────

class TestRankCandidates:
    def test_sorted_descending(self):
        df = pd.DataFrame({"battery_id": ["B1"], "soh": [90.0]})
        catalog = {
            "battery_cycles": {
                "row_count": 100,
                "columns": [
                    {"name": "battery_id", "semantic_type": "categorical", "sample_values": ["B1", "B2"]},
                    {"name": "soh", "semantic_type": "numeric", "min": 70.0, "max": 100.0},
                ],
            },
            "unrelated_table": {
                "row_count": 50,
                "columns": [{"name": "x", "semantic_type": "text"}],
            },
        }
        ranked = _rank_candidates(df, catalog)
        assert ranked[0]["table"] == "battery_cycles"
        assert ranked[0]["heuristic_score"] >= ranked[1]["heuristic_score"]

    def test_returns_all_tables(self):
        df = pd.DataFrame({"a": [1]})
        catalog = {"t1": {"row_count": 1, "columns": []}, "t2": {"row_count": 1, "columns": []}}
        ranked = _rank_candidates(df, catalog)
        assert len(ranked) == 2


# ── build_catalog (mocked profiler) ──────────────────────────────────────────

class TestBuildCatalog:
    def test_wraps_profile_database(self, monkeypatch):
        fake_profile = {
            "tables": {
                "battery_cycles": {
                    "row_count": 1810,
                    "columns": {"soh": {"semantic_type": "numeric"}},
                }
            }
        }
        monkeypatch.setattr("router.profile_database", lambda: fake_profile)
        catalog = build_catalog()
        assert catalog["battery_cycles"]["row_count"] == 1810
        assert catalog["battery_cycles"]["description"] == ""

    def test_normalizes_columns_dict_to_list_with_name(self, monkeypatch):
        fake_profile = {
            "tables": {
                "battery_cycles": {
                    "row_count": 10,
                    "columns": {"soh": {"semantic_type": "numeric", "min": 70, "max": 100}},
                }
            }
        }
        monkeypatch.setattr("router.profile_database", lambda: fake_profile)
        catalog = build_catalog()
        cols = catalog["battery_cycles"]["columns"]
        assert cols == [{"name": "soh", "semantic_type": "numeric", "min": 70, "max": 100}]

    def test_includes_domain_description(self, monkeypatch):
        fake_profile = {"tables": {"battery_cycles": {"row_count": 10, "columns": {}}}}
        monkeypatch.setattr("router.profile_database", lambda: fake_profile)
        monkeypatch.setattr("router.load_domain_pack", lambda name: "Battery domain knowledge")
        catalog = build_catalog(domain="battery")
        assert catalog["battery_cycles"]["description"] == "Battery domain knowledge"


# ── classify_data (mocked LLM) ───────────────────────────────────────────────

_FAKE_CATALOG = {
    "battery_cycles": {
        "row_count": 1810,
        "columns": [
            {"name": "battery_id", "semantic_type": "categorical", "sample_values": ["B1", "B2"]},
            {"name": "soh", "semantic_type": "numeric", "min": 70.0, "max": 100.0},
        ],
    }
}


class TestClassifyData:
    def test_empty_dataframe_returns_no_match(self):
        result = classify_data(pd.DataFrame(), _FAKE_CATALOG)
        assert result["best_match"] is None
        assert result["is_new_table_needed"] is True

    def test_empty_catalog_returns_no_match(self):
        df = pd.DataFrame({"a": [1]})
        result = classify_data(df, {})
        assert result["best_match"] is None
        assert result["is_new_table_needed"] is True

    def test_successful_classification(self, monkeypatch):
        fake_response = (
            '{"best_match": "battery_cycles", "confidence": 92, '
            '"reasoning": "Column names and value ranges match closely.", '
            '"column_mapping": {"battery_id": "battery_id", "soh": "soh"}, '
            '"is_new_table_needed": false}'
        )
        monkeypatch.setattr("router._call_llm_judge", lambda s, u: fake_response)
        df = pd.DataFrame({"battery_id": ["B1"], "soh": [88.0]})
        result = classify_data(df, _FAKE_CATALOG)
        assert result["best_match"] == "battery_cycles"
        assert result["confidence"] == 92
        assert result["column_mapping"] == {"battery_id": "battery_id", "soh": "soh"}
        assert result["is_new_table_needed"] is False
        assert len(result["candidates"]) == 1

    def test_llm_judge_invalid_json_falls_back_to_heuristic(self, monkeypatch):
        monkeypatch.setattr("router._call_llm_judge", lambda s, u: "not json")
        df = pd.DataFrame({"battery_id": ["B1"], "soh": [88.0]})
        result = classify_data(df, _FAKE_CATALOG)
        assert result["best_match"] == "battery_cycles"
        assert "fallback" in result["reasoning"].lower()

    def test_llm_judge_response_in_code_fence(self, monkeypatch):
        fake_response = (
            '```json\n{"best_match": "battery_cycles", "confidence": 80, '
            '"reasoning": "ok", "column_mapping": {}, "is_new_table_needed": false}\n```'
        )
        monkeypatch.setattr("router._call_llm_judge", lambda s, u: fake_response)
        df = pd.DataFrame({"battery_id": ["B1"]})
        result = classify_data(df, _FAKE_CATALOG)
        assert result["best_match"] == "battery_cycles"
        assert result["confidence"] == 80
