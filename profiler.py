"""
Auto data-profiling for the active database (SQLite or PostgreSQL).

profile_database() runs once and caches results.
Results are injected into the agent's system prompt so the model has
row counts, value ranges, and cardinality without using tool calls.

Cache key:
  - SQLite:   (file_path, mtime)
  - Postgres: (DATABASE_URL, ) — invalidated only on engine restart

Design:
  - Uses get_connection() directly for metadata queries
  - Numeric columns: min, max, mean
  - Categorical columns (distinct <= CATEGORICAL_THRESHOLD): sample values
  - Text columns (high cardinality): distinct count only
  - Tables > MAX_STAT_ROWS: row count only (column stats skipped)
"""
import os
import time
from typing import Any

from database import get_connection, get_db_engine, _use_postgres

CATEGORICAL_THRESHOLD = 20
MAX_STAT_ROWS = 100_000

_cache: dict[tuple, dict] = {}


# ── Cache key ─────────────────────────────────────────────────────────────────

def _cache_key() -> tuple:
    if _use_postgres():
        return ("postgres", os.environ.get("DATABASE_URL", ""))
    from database import get_database_path
    db_path = get_database_path()
    return (str(db_path), db_path.stat().st_mtime)


# ── Public API ────────────────────────────────────────────────────────────────

def profile_database(force: bool = False) -> dict[str, Any]:
    """Profile all tables. Cached; use force=True to regenerate."""
    key = _cache_key()
    if not force and key in _cache:
        return _cache[key]
    profile = _build_profile()
    _cache[key] = profile
    return profile


# ── Build ─────────────────────────────────────────────────────────────────────

def _build_profile() -> dict[str, Any]:
    conn = get_connection()
    try:
        tables = _get_table_names(conn)
        profile: dict[str, Any] = {"tables": {}, "generated_at": time.time()}
        for table in tables:
            profile["tables"][table] = _profile_table(conn, table)
        return profile
    finally:
        conn.close()


def _get_table_names(conn) -> list[str]:
    cursor = conn.cursor()
    if _use_postgres():
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
    else:
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
    return [row[0] for row in cursor.fetchall()]


def _get_column_meta(conn, table: str) -> list[dict]:
    """Return [{"name": ..., "declared_type": ...}, ...] for both engines."""
    cursor = conn.cursor()
    if _use_postgres():
        cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        return [
            {"name": row[0], "declared_type": _pg_type_to_declared(row[1])}
            for row in cursor.fetchall()
        ]
    cursor.execute(f'PRAGMA table_info("{table}")')
    return [
        {"name": col[1], "declared_type": (col[2] or "").upper()}
        for col in cursor.fetchall()
    ]


def _pg_type_to_declared(pg_type: str) -> str:
    """Normalize a Postgres data_type string to the declared-type used by is_numeric check."""
    pg = pg_type.upper()
    if any(t in pg for t in ("INT", "SERIAL")):
        return "INTEGER"
    if any(t in pg for t in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL", "PRECISION")):
        return "REAL"
    return "TEXT"


def _profile_table(conn, table: str) -> dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
    row_count: int = cursor.fetchone()[0]

    col_meta = _get_column_meta(conn, table)
    skip_stats = row_count > MAX_STAT_ROWS

    columns: dict[str, Any] = {}
    for col_info in col_meta:
        col_name = col_info["name"]
        declared_type = col_info["declared_type"]
        columns[col_name] = _profile_column(
            conn, table, col_name, declared_type, row_count, skip_stats
        )

    return {"row_count": row_count, "columns": columns}


def _profile_column(
    conn,
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


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt(v: Any) -> str:
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
                lines.append(prefix + " (large table -- stats skipped)")
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
