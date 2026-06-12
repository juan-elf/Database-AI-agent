"""
SQLite / PostgreSQL database operations — generic, database-agnostic.

Engine is chosen at runtime:
  - DATABASE_URL env variable set  → PostgreSQL (Supabase / any Postgres)
  - DATABASE_URL not set           → SQLite (local file, path via set_database())

Read-only is enforced at the driver level:
  - SQLite:   uri=True + ?mode=ro
  - Postgres: conn.set_session(readonly=True)
"""
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

FORBIDDEN_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "replace", "attach", "detach", "pragma", "vacuum"
]

_db_path: Path | None = None

# Tuple of DB exception types for both engines
_DB_ERRORS: tuple = (sqlite3.Error,)
if _HAS_PSYCOPG2:
    _DB_ERRORS = (sqlite3.Error, psycopg2.Error)


# ── Engine detection ──────────────────────────────────────────────────────────

def _use_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def get_db_engine() -> str:
    """Returns 'postgres' or 'sqlite'."""
    return "postgres" if _use_postgres() else "sqlite"


# ── SQLite-only path management ───────────────────────────────────────────────

def set_database(path: str | Path) -> None:
    """Set the active SQLite database. Called once at startup from main.py."""
    global _db_path
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Database not found: {p}")
    _db_path = p


def get_database_path() -> Path:
    """Return the active SQLite database path. Raises if not set or using Postgres."""
    if _use_postgres():
        raise RuntimeError(
            "Using PostgreSQL engine — no local file path. "
            "Check DATABASE_URL env variable."
        )
    if _db_path is None:
        raise RuntimeError("Database not set. Call set_database(path) first.")
    return _db_path


# ── Connection ────────────────────────────────────────────────────────────────

def get_connection():
    """Return a read-only DB connection for the active engine."""
    if _use_postgres():
        if not _HAS_PSYCOPG2:
            raise RuntimeError(
                "psycopg2 not installed. Run: pip install psycopg2-binary"
            )
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        conn.set_session(readonly=True, autocommit=True)
        return conn
    conn = sqlite3.connect(get_database_path().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_db_label() -> str:
    """Human-readable DB identifier for the system prompt."""
    if _use_postgres():
        url = os.environ.get("DATABASE_URL", "")
        # extract host/dbname without credentials
        try:
            import urllib.parse
            p = urllib.parse.urlparse(url)
            return f"{p.hostname}/{p.path.lstrip('/')}"
        except Exception:
            return "postgres"
    return get_database_path().name


# ── Schema introspection ──────────────────────────────────────────────────────

def get_schema() -> str:
    """Full schema: tables, columns, types, PKs, FKs, sample rows."""
    return _get_schema_postgres() if _use_postgres() else _get_schema_sqlite()


def _get_schema_sqlite() -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        conn.close()
        return "(Database has no tables)"

    lines = [f"Database has {len(tables)} table(s): {', '.join(tables)}\n"]

    for table in tables:
        lines.append(f"\nTABLE: {table}")

        cursor.execute(f"PRAGMA table_info({table})")
        for col in cursor.fetchall():
            pk_marker = " [PK]" if col[5] else ""
            notnull = " NOT NULL" if col[3] else ""
            lines.append(f"  - {col[1]}: {col[2] or '?'}{notnull}{pk_marker}")

        cursor.execute(f"PRAGMA foreign_key_list({table})")
        fks = cursor.fetchall()
        if fks:
            lines.append("  Foreign keys:")
            for fk in fks:
                lines.append(f"    {table}.{fk[3]} -> {fk[2]}.{fk[4]}")

        cursor.execute(f"SELECT * FROM {table} LIMIT 2")
        sample_rows = cursor.fetchall()
        if sample_rows:
            lines.append("  Sample rows:")
            for row in sample_rows:
                row_dict = {
                    k: (str(v)[:60] + "..." if len(str(v)) > 60 else v)
                    for k, v in dict(row).items()
                }
                lines.append(f"    {row_dict}")

    conn.close()
    return "\n".join(lines)


def _get_schema_postgres() -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            return "(Database has no tables)"

        lines = [f"Database has {len(tables)} table(s): {', '.join(tables)}\n"]

        for table in tables:
            lines.append(f"\nTABLE: {table}")

            cursor.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """, (table,))
            for col in cursor.fetchall():
                notnull = " NOT NULL" if col[2] == "NO" else ""
                lines.append(f"  - {col[0]}: {col[1].upper()}{notnull}")

            cursor.execute("""
                SELECT kcu.column_name, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = 'public'
                    AND tc.table_name = %s
            """, (table,))
            fks = cursor.fetchall()
            if fks:
                lines.append("  Foreign keys:")
                for fk in fks:
                    lines.append(f"    {table}.{fk[0]} -> {fk[1]}.{fk[2]}")

            cursor.execute(f'SELECT * FROM "{table}" LIMIT 2')
            sample_rows = cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description]
            if sample_rows:
                lines.append("  Sample rows:")
                for row in sample_rows:
                    row_dict = dict(zip(col_names, row))
                    row_dict = {
                        k: (str(v)[:60] + "..." if len(str(v)) > 60 else v)
                        for k, v in row_dict.items()
                    }
                    lines.append(f"    {row_dict}")

        return "\n".join(lines)
    finally:
        conn.close()


# ── Table names ───────────────────────────────────────────────────────────────

def get_table_names() -> list[str]:
    """Return a list of user table names in the active database."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
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
    finally:
        conn.close()


# ── Query validation ──────────────────────────────────────────────────────────

def validate_query(sql: str) -> tuple[bool, str | None]:
    """Defense-in-depth validation: whitelist + blacklist + no multi-statement."""
    sql_clean = sql.strip()
    if not sql_clean:
        return False, "Empty query."

    sql_lower = sql_clean.lower()
    if not (sql_lower.startswith("select") or sql_lower.startswith("with")):
        return False, "Query must start with SELECT or WITH. This tool is read-only."

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(r'\b' + keyword + r'\b', sql_lower):
            return False, f"Keyword '{keyword.upper()}' is not allowed."

    sql_no_strings = re.sub(r"'[^']*'", "''", sql_clean)
    sql_no_strings = re.sub(r'"[^"]*"', '""', sql_no_strings)
    if ";" in sql_no_strings.rstrip(";").rstrip():
        return False, "Multiple statements are not allowed."

    return True, None


# ── Query execution ───────────────────────────────────────────────────────────

def execute_query(sql: str) -> dict[str, Any]:
    """Execute a validated SELECT query and return rows as list of dicts."""
    base = {
        "success": False,
        "rows": None,
        "row_count": 0,
        "columns": None,
        "error": None,
        "hint": None,
        "sql_executed": sql,
    }

    is_valid, validation_error = validate_query(sql)
    if not is_valid:
        return {**base, "error": validation_error}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        rows_raw = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = [dict(zip(columns, row)) for row in rows_raw]
        return {
            **base,
            "success": True,
            "rows": rows,
            "row_count": len(rows),
            "columns": columns,
        }
    except _DB_ERRORS as e:
        return {
            **base,
            "error": f"SQL Error: {e}",
            "hint": _generate_error_hint(str(e)),
        }
    finally:
        conn.close()


def _generate_error_hint(error_msg: str) -> str:
    """Generate a helpful hint from a DB error message."""
    error_lower = error_msg.lower()

    if "no such table" in error_lower or "does not exist" in error_lower:
        try:
            tables = get_table_names()
            return f"Wrong table name. Available tables: {', '.join(tables)}."
        except Exception:
            return "Wrong table name. Check the schema in the system prompt."

    if "no such column" in error_lower or "column" in error_lower and "does not exist" in error_lower:
        return ("Wrong column name. Check the schema in the system prompt. "
                "Use get_distinct_values to inspect column values.")

    if "syntax error" in error_lower:
        engine = get_db_engine()
        if engine == "sqlite":
            return ("Invalid SQL syntax. Remember: this is SQLite, not PostgreSQL/MySQL. "
                    "For monthly grouping use strftime('%Y-%m', date_column).")
        return ("Invalid SQL syntax. Remember: this is PostgreSQL. "
                "For monthly grouping use to_char(date_col, 'YYYY-MM').")

    if "ambiguous column" in error_lower or "ambiguous" in error_lower:
        return ("Ambiguous column name (exists in multiple tables). "
                "Qualify with a table alias, e.g. t1.id instead of id.")

    return "Check the SQL query and try again with a correction."


# ── Distinct values ───────────────────────────────────────────────────────────

def get_distinct_values(table: str, column: str, limit: int = 20) -> dict[str, Any]:
    """Return unique values from a column (safe, identifier-validated)."""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table):
        return {"success": False, "error": f"Invalid table name: '{table}'"}
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column):
        return {"success": False, "error": f"Invalid column name: '{column}'"}

    limit = max(1, min(limit, 100))
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Validate table/column existence
        if _use_postgres():
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """, (table,))
            cols_info = cursor.fetchall()
            if not cols_info:
                return {"success": False, "error": f"Table '{table}' not found."}
            col_names = [c[0] for c in cols_info]
        else:
            cursor.execute(f"PRAGMA table_info({table})")
            cols_info = cursor.fetchall()
            if not cols_info:
                return {"success": False, "error": f"Table '{table}' not found."}
            col_names = [c[1] for c in cols_info]

        if column not in col_names:
            return {
                "success": False,
                "error": f"Column '{column}' not found in '{table}'. Columns: {col_names}"
            }

        cursor.execute(
            f"SELECT DISTINCT {column} FROM {table} "
            f"WHERE {column} IS NOT NULL ORDER BY {column} LIMIT {limit}"
        )
        values = [row[0] for row in cursor.fetchall()]

        cursor.execute(f"SELECT COUNT(DISTINCT {column}) FROM {table}")
        total_distinct = cursor.fetchone()[0]

        return {
            "success": True,
            "table": table,
            "column": column,
            "distinct_values": values,
            "total_distinct": total_distinct,
            "showing": len(values),
        }
    except _DB_ERRORS as e:
        return {"success": False, "error": f"SQL Error: {e}"}
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python database.py <path_to_db>")
        sys.exit(1)

    set_database(sys.argv[1])
    print("=" * 60)
    print(f"SCHEMA: {get_database_path()}")
    print("=" * 60)
    print(get_schema())
