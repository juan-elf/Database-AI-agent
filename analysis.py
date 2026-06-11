"""
Sandboxed Python/pandas analysis tool.

The agent provides SQL (to fetch data) and pandas/numpy code (to analyze it).
Code executes in a restricted namespace: only pd, np, scipy.stats, and a
safe subset of builtins.  File I/O, subprocess, and __import__ are blocked.

Security note: this is a best-effort sandbox suitable for single-user or
demo environments.  It is NOT production-grade multi-tenant isolation —
for that, use a subprocess with resource limits or a container.
"""
import builtins
import json
from typing import Any

import numpy as np
import pandas as pd

from database import execute_query

try:
    import scipy.stats as _scipy_stats
    _HAS_SCIPY = True
except ImportError:
    _scipy_stats = None
    _HAS_SCIPY = False


MAX_ROWS = 10_000

_SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name)
    for name in [
        "abs", "all", "any", "bool", "chr", "dict", "divmod",
        "enumerate", "filter", "float", "format", "frozenset",
        "hasattr", "hash", "hex", "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "min", "next", "object",
        "oct", "ord", "pow", "print", "range", "repr", "reversed",
        "round", "set", "slice", "sorted", "str", "sum", "tuple",
        "type", "zip",
        # exceptions — needed so code can raise/catch them
        "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "RuntimeError", "StopIteration",
    ]
}

_ALLOWED_MODULES: dict[str, Any] = {"pd": pd, "np": np}
if _HAS_SCIPY:
    _ALLOWED_MODULES["stats"] = _scipy_stats


def _json_default(obj: Any) -> Any:
    """Serialize numpy / pandas types that json.dumps can't handle natively."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.Series):
        return obj.tolist()
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"Not JSON-serializable: {type(obj).__name__}")


def run_analysis(sql: str, code: str) -> dict[str, Any]:
    """
    Execute SQL, load result as a pandas DataFrame, run analysis code in sandbox.

    The code operates on `df` (the SQL result DataFrame).
    Available names: df, pd, np, stats (if scipy installed).
    Code MUST assign output to `result`.

    Returns a dict with keys: success, result, rows_analyzed, columns_used, error.
    """
    # ── Step 1: fetch data ────────────────────────────────────────────────────
    sql_result = execute_query(sql)
    if not sql_result["success"]:
        return {
            "success": False,
            "error": f"SQL failed: {sql_result['error']}",
            "hint": sql_result.get("hint"),
        }

    rows = sql_result["rows"]
    if not rows:
        return {"success": False, "error": "SQL returned no rows — nothing to analyze."}

    if len(rows) > MAX_ROWS:
        return {
            "success": False,
            "error": (
                f"Too many rows ({len(rows):,}). "
                f"Aggregate or filter the SQL first (max {MAX_ROWS:,} rows for analysis)."
            ),
        }

    df = pd.DataFrame(rows)

    # ── Step 2: run code in sandbox ───────────────────────────────────────────
    namespace: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        "df": df,
        **_ALLOWED_MODULES,
    }

    try:
        exec(code, namespace)  # noqa: S102 — sandboxed, see module docstring
    except Exception as e:
        return {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
            "hint": (
                "Check that column names match df.columns. "
                "Assign output to `result`. "
                f"Available columns: {df.columns.tolist()}"
            ),
        }

    if "result" not in namespace:
        return {
            "success": False,
            "error": "Code did not assign to `result`. End with: result = {...}",
        }

    # ── Step 3: serialize result ──────────────────────────────────────────────
    try:
        serialized = json.loads(
            json.dumps(namespace["result"], default=_json_default)
        )
    except (TypeError, ValueError) as e:
        return {
            "success": False,
            "error": (
                f"result is not JSON-serializable: {e}. "
                "Use basic Python types: dict, list, float, int, str."
            ),
        }

    return {
        "success": True,
        "result": serialized,
        "rows_analyzed": len(df),
        "columns_used": df.columns.tolist(),
    }
