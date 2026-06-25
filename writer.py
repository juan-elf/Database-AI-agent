"""
Write module — safe CSV-to-table append for DataGen.

Design principles (Arah B architecture):
- Separate from the read path (database.py) — Postgres writes require WRITE_DATABASE_URL.
- LLM never generates SQL; app builds parameterized INSERT with bound parameters.
- Every write is wrapped in a transaction and logged to audit JSONL.
- Row count guard: INSERT > MAX_ROWS_PER_INSERT requires explicit override.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from database import (
    _parse_connection_url,
    _use_postgres,
    get_connection,
    get_database_path,
    get_db_engine,
)

MAX_ROWS_PER_INSERT = 500
_AUDIT_DIR = Path("logs")


# ── Exceptions ────────────────────────────────────────────────────────────────

class WriteNotConfiguredError(Exception):
    """Raised when WRITE_DATABASE_URL is not set (Postgres engine)."""

class ColumnValidationError(Exception):
    """Raised when column or table validation fails."""


# ── Write availability ────────────────────────────────────────────────────────

def is_write_configured() -> bool:
    """Return True if the write path is available.
    - Postgres: requires WRITE_DATABASE_URL env var.
    - SQLite:   always writable (local file).
    """
    if get_db_engine() == "postgres":
        return bool(os.environ.get("WRITE_DATABASE_URL"))
    return True


def _write_uses_postgres() -> bool:
    return get_db_engine() == "postgres"


# ── Connection ────────────────────────────────────────────────────────────────

def get_write_connection():
    """
    Return a writable DB connection (no readonly session).
    - Postgres: uses WRITE_DATABASE_URL, no set_session(readonly=True).
    - SQLite:   opens the database file in read-write mode.
    Raises WriteNotConfiguredError if Postgres engine and WRITE_DATABASE_URL not set.
    """
    if get_db_engine() == "postgres":
        url = os.environ.get("WRITE_DATABASE_URL")
        if not url:
            raise WriteNotConfiguredError(
                "WRITE_DATABASE_URL tidak di-set. "
                "Set env var ini untuk mengaktifkan write ke Postgres."
            )
        try:
            import psycopg2
        except ImportError:
            raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
        kwargs = _parse_connection_url(url)
        conn = psycopg2.connect(**kwargs)
        conn.autocommit = False
        return conn
    else:
        import sqlite3
        conn = sqlite3.connect(str(get_database_path()))
        conn.row_factory = sqlite3.Row
        return conn


# ── Schema helpers ────────────────────────────────────────────────────────────

def get_writable_table_columns(table: str) -> list[dict]:
    """
    Return column metadata for a table via the read connection.
    Uses the read path for schema introspection — no write access needed for this.
    Returns: [{name, data_type, is_nullable, column_default}, ...]
    """
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table):
        raise ColumnValidationError(f"Invalid table name: {table!r}")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if _use_postgres():
            cursor.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """, (table,))
            rows = cursor.fetchall()
            if not rows:
                raise ColumnValidationError(f"Tabel '{table}' tidak ditemukan.")
            return [
                {"name": r[0], "data_type": r[1], "is_nullable": r[2], "column_default": r[3]}
                for r in rows
            ]
        else:
            cursor.execute(f"PRAGMA table_info({table})")
            rows = cursor.fetchall()
            if not rows:
                raise ColumnValidationError(f"Tabel '{table}' tidak ditemukan.")
            return [
                {"name": r[1], "data_type": r[2],
                 "is_nullable": "YES" if not r[3] else "NO",
                 "column_default": r[4]}
                for r in rows
            ]
    finally:
        conn.close()


# ── Validation ────────────────────────────────────────────────────────────────

def validate_insert(
    df: pd.DataFrame,
    table: str,
    column_mapping: dict[str, str],
    override_row_limit: bool = False,
) -> dict[str, Any]:
    """
    Validate that df (remapped via column_mapping) can be inserted into table.

    column_mapping: {input_col -> table_col}
    override_row_limit: if True, skip MAX_ROWS_PER_INSERT guard

    Returns:
        valid:             bool
        errors:            list[str]   — blocking issues
        warnings:          list[str]   — non-blocking notes
        mapped_df:         pd.DataFrame | None
        effective_columns: list[str]
    """
    errors: list[str] = []
    warnings: list[str] = []

    if df.empty:
        return {"valid": False, "errors": ["Input data kosong."],
                "warnings": [], "mapped_df": None, "effective_columns": []}

    if not column_mapping:
        return {"valid": False, "errors": ["column_mapping kosong."],
                "warnings": [], "mapped_df": None, "effective_columns": []}

    if not override_row_limit and len(df) > MAX_ROWS_PER_INSERT:
        return {
            "valid": False,
            "errors": [
                f"Terlalu banyak baris ({len(df):,} > {MAX_ROWS_PER_INSERT:,} maks). "
                "Set override_row_limit=True untuk memaksa."
            ],
            "warnings": [], "mapped_df": None, "effective_columns": [],
        }

    try:
        table_cols_info = get_writable_table_columns(table)
    except ColumnValidationError as e:
        return {"valid": False, "errors": [str(e)], "warnings": [],
                "mapped_df": None, "effective_columns": []}

    table_col_names = {c["name"] for c in table_cols_info}

    for src_col, tgt_col in column_mapping.items():
        if tgt_col not in table_col_names:
            errors.append(f"Kolom tujuan '{tgt_col}' tidak ada di tabel '{table}'.")

    if errors:
        return {"valid": False, "errors": errors, "warnings": [],
                "mapped_df": None, "effective_columns": []}

    available_src = [c for c in column_mapping if c in df.columns]
    if not available_src:
        return {"valid": False,
                "errors": ["Tidak ada kolom input yang cocok dengan column_mapping."],
                "warnings": [], "mapped_df": None, "effective_columns": []}

    unmapped_input = [c for c in df.columns if c not in column_mapping]
    if unmapped_input:
        warnings.append(f"Kolom input tidak terpetakan (dilewati): {', '.join(unmapped_input)}")

    mapped_df = df[available_src].rename(columns=column_mapping)

    return {
        "valid": True,
        "errors": [],
        "warnings": warnings,
        "mapped_df": mapped_df,
        "effective_columns": list(mapped_df.columns),
    }


# ── Preview ───────────────────────────────────────────────────────────────────

def preview_insert(
    df: pd.DataFrame,
    table: str,
    column_mapping: dict[str, str],
    max_preview_rows: int = 5,
) -> dict[str, Any]:
    """
    Return a preview of the INSERT without executing it.
    Returns: {valid, table, row_count, preview_df, columns, warnings, errors}
    """
    val = validate_insert(df, table, column_mapping)
    if not val["valid"]:
        return {"valid": False, "table": table, "row_count": 0,
                "preview_df": None, "columns": [], "warnings": [], "errors": val["errors"]}
    mapped_df = val["mapped_df"]
    return {
        "valid": True,
        "table": table,
        "row_count": len(mapped_df),
        "preview_df": mapped_df.head(max_preview_rows),
        "columns": val["effective_columns"],
        "warnings": val["warnings"],
        "errors": [],
    }


# ── Audit ─────────────────────────────────────────────────────────────────────

def _write_audit(entry: dict) -> None:
    _AUDIT_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    audit_file = _AUDIT_DIR / f"audit_{date_str}.jsonl"
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    with audit_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ── Execute ───────────────────────────────────────────────────────────────────

def execute_insert(
    df: pd.DataFrame,
    table: str,
    column_mapping: dict[str, str],
    session_id: str = "unknown",
    override_row_limit: bool = False,
) -> dict[str, Any]:
    """
    Execute the confirmed INSERT in a transaction. Logs to audit JSONL.
    Returns: {success, rows_inserted, table, errors, audit_file}
    """
    val = validate_insert(df, table, column_mapping, override_row_limit=override_row_limit)
    if not val["valid"]:
        return {"success": False, "rows_inserted": 0, "table": table,
                "errors": val["errors"], "audit_file": None}

    mapped_df = val["mapped_df"]
    cols = val["effective_columns"]

    try:
        conn = get_write_connection()
    except WriteNotConfiguredError as e:
        return {"success": False, "rows_inserted": 0, "table": table,
                "errors": [str(e)], "audit_file": None}

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    audit_path = str(_AUDIT_DIR / f"audit_{date_str}.jsonl")
    audit_base = {
        "event": "insert",
        "session_id": session_id,
        "table": table,
        "columns": cols,
        "row_count_attempted": len(mapped_df),
        "column_mapping": column_mapping,
    }

    try:
        cursor = conn.cursor()
        col_str = ", ".join(f'"{c}"' for c in cols)
        placeholder = "%s" if _write_uses_postgres() else "?"
        placeholders = ", ".join([placeholder] * len(cols))
        sql = f'INSERT INTO "{table}" ({col_str}) VALUES ({placeholders})'
        records = [tuple(row) for row in mapped_df.itertuples(index=False, name=None)]
        cursor.executemany(sql, records)
        conn.commit()
        rows_inserted = len(records)

        _write_audit({**audit_base, "status": "success", "rows_inserted": rows_inserted})
        return {"success": True, "rows_inserted": rows_inserted, "table": table,
                "errors": [], "audit_file": audit_path}

    except Exception as e:
        conn.rollback()
        _write_audit({**audit_base, "status": "failed", "error": str(e)})
        return {"success": False, "rows_inserted": 0, "table": table,
                "errors": [f"Insert gagal: {e}"], "audit_file": audit_path}
    finally:
        conn.close()
