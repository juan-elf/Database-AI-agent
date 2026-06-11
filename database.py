"""
SQLite database operations — generic, database-agnostic.

DB_PATH is not hardcoded; it is set at runtime via set_database().
get_schema() auto-detects foreign keys, enabling the agent to understand JOINs.
Error hints are generic (not domain-specific).
"""
import re
import sqlite3
from pathlib import Path
from typing import Any

FORBIDDEN_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "replace", "attach", "detach", "pragma", "vacuum"
]

_db_path: Path | None = None


def set_database(path: str | Path) -> None:
    """Set the active database. Called once at startup from main.py."""
    global _db_path
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Database not found: {p}")
    _db_path = p


def get_database_path() -> Path:
    """Return the active database path. Raises if not set yet."""
    if _db_path is None:
        raise RuntimeError("Database not set. Call set_database(path) first.")
    return _db_path


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_database_path().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema() -> str:
    """
    Full database schema: all tables, columns, types, primary keys,
    foreign keys (for JOIN guidance), and sample rows.
    """
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
                lines.append(f"    {table}.{fk[3]} → {fk[2]}.{fk[4]}")

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


def get_table_names() -> list[str]:
    """Return a list of table names in the active database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tables


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


def execute_query(sql: str) -> dict[str, Any]:
    """Execute a query with validation and generic error hints."""
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
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        return {
            **base,
            "success": True,
            "rows": [dict(row) for row in rows],
            "row_count": len(rows),
            "columns": columns,
        }
    except sqlite3.Error as e:
        return {
            **base,
            "error": f"SQL Error: {e}",
            "hint": _generate_error_hint(str(e)),
        }
    finally:
        conn.close()


def _generate_error_hint(error_msg: str) -> str:
    """Generate a helpful hint from a SQLite error message."""
    error_lower = error_msg.lower()

    if "no such table" in error_lower:
        try:
            tables = get_table_names()
            return f"Wrong table name. Available tables: {', '.join(tables)}."
        except Exception:
            return "Wrong table name. Check the schema in the system prompt."

    if "no such column" in error_lower:
        return ("Wrong column name. Check the schema in the system prompt. "
                "Use get_distinct_values to inspect column values.")

    if "syntax error" in error_lower:
        return ("Invalid SQL syntax. Remember: this is SQLite, not PostgreSQL/MySQL. "
                "For monthly grouping use strftime('%Y-%m', date_column).")

    if "ambiguous column" in error_lower:
        return ("Ambiguous column name (exists in multiple tables). "
                "Qualify with a table alias, e.g. t1.id instead of id.")

    return "Check the SQL query and try again with a correction."


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
            f"WHERE {column} IS NOT NULL ORDER BY {column} LIMIT ?",
            (limit,)
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
    except sqlite3.Error as e:
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
