"""
Auto data-profiling for the active SQLite database.

profile_database() runs once and caches results by (db_path, mtime).
Results are injected into the agent's system prompt so the model has
row counts, value ranges, and cardinality without using tool calls.

Design:
- Uses get_connection() directly for PRAGMA queries (bypasses validate_query)
- Numeric columns: min, max, mean
- Categorical columns (distinct <= CATEGORICAL_THRESHOLD): sample values
- Text columns (high cardinality): distinct count only
- Tables > MAX_STAT_ROWS: row count only (column stats skipped)
"""
import sqlite3
import time
from typing import Any

from database import get_connection, get_database_path

CATEGORICAL_THRESHOLD = 20
MAX_STAT_ROWS = 100_000

_cache: dict[tuple, dict] = {}


def profile_database(force: bool = False) -> dict[str, Any]:
    """Profile all tables in the active database. Cached by (path, mtime)."""
    db_path = get_database_path()
    mtime = db_path.stat().st_mtime
    key = (str(db_path), mtime)
    if not force and key in _cache:
        return _cache[key]
    profile = _build_profile()
    _cache[key] = profile
    return profile


def _build_profile() -> dict[str, Any]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        tables = [row[0] for row in cursor.fetchall()]

        profile: dict[str, Any] = {"tables": {}, "generated_at": time.time()}
        for table in tables:
            profile["tables"][table] = _profile_table(conn, table)
        return profile
    finally:
        conn.close()


def _profile_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    cursor = conn.cursor()

    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
    row_count: int = cursor.fetchone()[0]

    cursor.execute(f'PRAGMA table_info("{table}")')
    col_rows = cursor.fetchall()

    skip_stats = row_count > MAX_STAT_ROWS
    columns: dict[str, Any] = {}
    for col in col_rows:
        col_name: str = col[1]
        declared_type: str = (col[2] or "").upper()
        columns[col_name] = _profile_column(
            conn, table, col_name, declared_type, row_count, skip_stats
        )

    return {"row_count": row_count, "columns": columns}


def _profile_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declared_type: str,
    row_count: int,
    skip_stats: bool,
) -> dict[str, Any]:
    base: dict[str, Any] = {"declared_type": declared_type}

    if skip_stats or row_count == 0:
        base["skipped"] = True
        return base

    c = f'"{column}"'
    t = f'"{table}"'
    cursor = conn.cursor()

    cursor.execute(
        f'SELECT COUNT(*) - COUNT({c}) AS nulls, COUNT(DISTINCT {c}) AS dist FROM {t}'
    )
    row = cursor.fetchone()
    null_count: int = row[0]
    distinct_count: int = row[1]
    null_pct = round(100.0 * null_count / row_count, 1) if row_count > 0 else 0.0

    base["null_pct"] = null_pct
    base["distinct_count"] = distinct_count

    is_numeric = any(
        kw in declared_type
        for kw in ("INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DECIMAL")
    )

    if is_numeric:
        base["semantic_type"] = "numeric"
        cursor.execute(f'SELECT MIN({c}), MAX({c}), ROUND(AVG({c}), 4) FROM {t}')
        r = cursor.fetchone()
        if r:
            base["min"] = r[0]
            base["max"] = r[1]
            base["mean"] = r[2]
    elif distinct_count <= CATEGORICAL_THRESHOLD:
        base["semantic_type"] = "categorical"
        cursor.execute(
            f'SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL ORDER BY {c} LIMIT 20'
        )
        base["sample_values"] = [r[0] for r in cursor.fetchall()]
    else:
        base["semantic_type"] = "text"

    return base


def _fmt(v: Any) -> str:
    """Format a numeric value concisely."""
    if v is None:
        return "?"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e10:
        return str(int(v))
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def format_profile_for_prompt(profile: dict[str, Any]) -> str:
    """Compact single-line-per-column text for system prompt injection."""
    if not profile or not profile.get("tables"):
        return "(Data profile unavailable)"

    lines: list[str] = []
    for table, tinfo in profile["tables"].items():
        row_count = tinfo.get("row_count", "?")
        count_str = f"{row_count:,}" if isinstance(row_count, int) else str(row_count)
        lines.append(f"\n{table}  ({count_str} rows)")

        for col_name, cinfo in tinfo.get("columns", {}).items():
            dtype = cinfo.get("declared_type", "")
            prefix = f"  {col_name:<24} {dtype:<8}"

            if cinfo.get("skipped"):
                lines.append(prefix + " (large table — stats skipped)")
                continue

            stype = cinfo.get("semantic_type", "")
            distinct = cinfo.get("distinct_count", "?")
            null_pct = cinfo.get("null_pct", 0.0)

            line = prefix
            if stype == "numeric":
                mn, mx, mean = cinfo.get("min"), cinfo.get("max"), cinfo.get("mean")
                line += f" |range: {_fmt(mn)}-{_fmt(mx)} |mean: {_fmt(mean)}"
            elif stype == "categorical":
                vals = cinfo.get("sample_values", [])
                line += f" |values: {', '.join(str(v) for v in vals)}"
            line += f" |distinct: {distinct}"
            if null_pct > 0:
                line += f" |nulls: {null_pct}%"

            lines.append(line)

    return "\n".join(lines)
