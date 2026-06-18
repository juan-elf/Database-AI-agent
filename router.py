"""
Router — catalog + classifier for matching new data to existing tables.

READ-ONLY by design (Arah B1): this module never writes to the database.
It only recommends which existing table a new dataset most likely belongs to.

Pipeline:
  1. build_catalog()     → table profiles (reuse profiler.py) + optional domain description
  2. classify_data(df)   → heuristic scoring on ALL tables (column names, types, value
                            overlap) → top-K candidates → LLM judge for final decision
                            + confidence + column mapping
"""
import json
import re
from typing import Any

import pandas as pd

from profiler import profile_database
from agent import client, MODEL_NAME, load_domain_pack
from guardrails import wrap_untrusted, check_input

TOP_K = 3

# ── Catalog ───────────────────────────────────────────────────────────────────

def build_catalog(domain: str | None = None) -> dict[str, Any]:
    """
    Build a catalog of all tables with their column profiles + optional domain
    description. Wraps profile_database() — does not re-derive table stats.

    profile_database() returns {"tables": {table: {"columns": {col_name: {...}}}}}
    (columns keyed by name); this normalizes columns into a list of dicts with
    "name" injected, which is what the rest of this module expects.

    Returns: {table_name: {row_count, columns: [{name, ...}], description}}
    """
    profile = profile_database()
    domain_desc = ""
    if domain:
        content = load_domain_pack(domain)
        if content:
            domain_desc = content

    catalog = {}
    for table, info in profile.get("tables", {}).items():
        columns = [
            {"name": col_name, **col_info}
            for col_name, col_info in info.get("columns", {}).items()
        ]
        catalog[table] = {
            "row_count": info.get("row_count", 0),
            "columns": columns,
            "description": domain_desc,
        }
    return catalog

# ── Heuristic scoring ─────────────────────────────────────────────────────────

def _normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _column_name_similarity(input_cols: list[str], table_cols: list[str]) -> float:
    """Jaccard similarity of normalized column name sets."""
    if not input_cols or not table_cols:
        return 0.0
    a = {_normalize_col(c) for c in input_cols}
    b = {_normalize_col(c) for c in table_cols}
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _type_compatibility(df: pd.DataFrame, table_columns: list[dict]) -> float:
    """Fraction of name-matching input columns whose dtype agrees with the
    table column's semantic_type (numeric vs categorical/text)."""
    if df.empty or not table_columns:
        return 0.0
    table_types = {_normalize_col(c["name"]): c.get("semantic_type", "") for c in table_columns}
    matched = total = 0
    for col in df.columns:
        norm = _normalize_col(col)
        if norm not in table_types:
            continue
        total += 1
        expected = table_types[norm]
        is_numeric = pd.api.types.is_numeric_dtype(df[col])
        if (is_numeric and expected == "numeric") or (not is_numeric and expected in ("categorical", "text")):
            matched += 1
    return matched / total if total else 0.0


def _value_overlap(df: pd.DataFrame, table_columns: list[dict]) -> float:
    """For name-matching categorical columns: sample-value set overlap.
    For name-matching numeric columns: fraction of input values within the
    table's observed [min, max] range."""
    if df.empty or not table_columns:
        return 0.0
    col_by_name = {_normalize_col(c["name"]): c for c in table_columns}
    scores = []
    for col in df.columns:
        norm = _normalize_col(col)
        if norm not in col_by_name:
            continue
        tcol = col_by_name[norm]
        if tcol.get("semantic_type") == "categorical":
            sample_vals = set(tcol.get("sample_values", []))
            input_vals = set(df[col].dropna().astype(str).unique())
            if not sample_vals or not input_vals:
                continue
            scores.append(len(input_vals & sample_vals) / len(input_vals))
        elif tcol.get("semantic_type") == "numeric":
            tmin, tmax = tcol.get("min"), tcol.get("max")
            if tmin is None or tmax is None:
                continue
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            if vals.empty:
                continue
            scores.append(((vals >= tmin) & (vals <= tmax)).mean())
    return sum(scores) / len(scores) if scores else 0.0


def _composite_score(name_sim: float, type_compat: float, value_overlap: float) -> float:
    """Column name similarity weighted highest — it's the most reliable signal."""
    return 0.5 * name_sim + 0.3 * type_compat + 0.2 * value_overlap


def _rank_candidates(df: pd.DataFrame, catalog: dict) -> list[dict]:
    """Score every table in the catalog against the input DataFrame, descending."""
    input_cols = list(df.columns)
    candidates = []
    for table, info in catalog.items():
        table_cols = [c["name"] for c in info["columns"]]
        name_sim      = _column_name_similarity(input_cols, table_cols)
        type_compat   = _type_compatibility(df, info["columns"])
        value_overlap = _value_overlap(df, info["columns"])
        candidates.append({
            "table":               table,
            "heuristic_score":     round(_composite_score(name_sim, type_compat, value_overlap), 4),
            "name_similarity":     round(name_sim, 4),
            "type_compatibility":  round(type_compat, 4),
            "value_overlap":       round(value_overlap, 4),
        })
    candidates.sort(key=lambda c: c["heuristic_score"], reverse=True)
    return candidates

# ── LLM judge ─────────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = (
    "You are a data engineer deciding which existing database table new data "
    "belongs to. Reply with valid JSON only — no prose, no markdown fences."
)

_JUDGE_USER = """\
New data sample (first 5 rows):
{sample}

Candidate tables (ranked by structural similarity heuristic):
{candidates_desc}

Decide:
1. Which candidate table (if any) is the best match for this new data
2. A confidence score 0-100
3. Brief reasoning
4. Column mapping: map each input column name to the matching table column name
   (only include columns you are confident map to an existing column)
5. Whether none of the candidates fit well enough (is_new_table_needed)

JSON format (no other text):
{{
  "best_match": "<table name or null>",
  "confidence": <0-100>,
  "reasoning": "...",
  "column_mapping": {{"input_col": "table_col", ...}},
  "is_new_table_needed": <true|false>
}}"""


def _format_candidates_for_prompt(candidates: list[dict], catalog: dict) -> str:
    parts = []
    for c in candidates:
        info = catalog[c["table"]]
        cols_desc = ", ".join(
            f"{col['name']} ({col.get('semantic_type', '?')})" for col in info["columns"]
        )
        parts.append(
            f"- {c['table']} (row_count={info['row_count']}, heuristic_score={c['heuristic_score']})\n"
            f"  columns: {cols_desc}"
        )
    return "\n".join(parts)


def _call_llm_judge(system: str, user: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)

# ── Public API ────────────────────────────────────────────────────────────────

def classify_data(df: pd.DataFrame, catalog: dict, top_k: int = TOP_K) -> dict[str, Any]:
    """
    Classify a new DataFrame against the catalog of existing tables.
    Never writes to the database — recommendation only.

    Returns:
        best_match:          str | None  — recommended table name
        confidence:           int        — 0-100, from LLM judge
        reasoning:            str        — LLM's explanation
        column_mapping:       dict       — input column -> table column
        is_new_table_needed:  bool       — True if no candidate fits well
        candidates:           list[dict] — all tables ranked by heuristic score
    """
    if df.empty:
        return {
            "best_match": None, "confidence": 0,
            "reasoning": "Input data kosong.", "column_mapping": {},
            "is_new_table_needed": True, "candidates": [],
        }

    if not catalog:
        return {
            "best_match": None, "confidence": 0,
            "reasoning": "Tidak ada tabel di katalog untuk dibandingkan.",
            "column_mapping": {}, "is_new_table_needed": True, "candidates": [],
        }

    ranked = _rank_candidates(df, catalog)
    top_candidates = ranked[:top_k]

    csv_sample = df.head(5).to_string(index=False)
    allow, reason = check_input(csv_sample, max_length=2000)
    if not allow:
        return {
            "best_match": None, "confidence": 0,
            "reasoning": f"Input CSV ditolak guardrail: {reason}",
            "column_mapping": {}, "is_new_table_needed": True, "candidates": ranked,
        }

    raw = _call_llm_judge(
        _JUDGE_SYSTEM,
        _JUDGE_USER.format(
            sample=wrap_untrusted(csv_sample, "csv_upload"),
            candidates_desc=_format_candidates_for_prompt(top_candidates, catalog),
        ),
    )

    try:
        judged = _extract_json(raw)
    except Exception as e:
        best = top_candidates[0] if top_candidates else None
        return {
            "best_match": best["table"] if best else None,
            "confidence": int(best["heuristic_score"] * 100) if best else 0,
            "reasoning": f"LLM judge gagal di-parse ({e}); fallback ke skor heuristik.",
            "column_mapping": {},
            "is_new_table_needed": best is None,
            "candidates": ranked,
        }

    return {
        "best_match":          judged.get("best_match"),
        "confidence":          judged.get("confidence", 0),
        "reasoning":           judged.get("reasoning", ""),
        "column_mapping":      judged.get("column_mapping", {}),
        "is_new_table_needed": judged.get("is_new_table_needed", False),
        "candidates":          ranked,
    }
