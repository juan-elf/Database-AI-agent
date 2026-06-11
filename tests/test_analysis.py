"""
Tests for analysis.py — sandboxed run_analysis engine.

Run with:
    pytest tests/test_analysis.py -v
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database
from analysis import run_analysis, MAX_ROWS


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """Small SQLite DB with numeric + categorical columns."""
    db_file = tmp_path / "analysis_test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE readings (
            id       INTEGER PRIMARY KEY,
            cycle    INTEGER NOT NULL,
            soh      REAL    NOT NULL,
            category TEXT
        )
    """)
    conn.executemany("INSERT INTO readings VALUES (?,?,?,?)", [
        (1,  10, 95.0, "A"),
        (2,  20, 90.0, "A"),
        (3,  30, 85.0, "B"),
        (4,  40, 80.0, "B"),
        (5,  50, 75.0, "A"),
        (6,  60, 70.0, "B"),
        (7,  70, 65.0, "A"),
        (8,  80, 60.0, "A"),
        (9,  90, 55.0, "B"),
        (10, 100, 50.0, "B"),
    ])
    conn.commit()
    conn.close()
    database.set_database(db_file)
    return db_file


# ── Happy path ────────────────────────────────────────────────────────────────

class TestRunAnalysisHappyPath:

    def test_basic_mean_returns_success(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="result = {'mean_soh': float(df['soh'].mean())}",
        )
        assert r["success"] is True
        assert abs(r["result"]["mean_soh"] - 72.5) < 0.01

    def test_correlation_between_two_columns(self, temp_db):
        r = run_analysis(
            sql="SELECT cycle, soh FROM readings",
            code="result = {'corr': float(df['cycle'].corr(df['soh']))}",
        )
        assert r["success"] is True
        assert r["result"]["corr"] == pytest.approx(-1.0, abs=1e-6)

    def test_result_is_list(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings ORDER BY soh",
            code="result = df['soh'].tolist()",
        )
        assert r["success"] is True
        assert isinstance(r["result"], list)
        assert r["result"][0] == 50.0

    def test_rows_analyzed_and_columns_returned(self, temp_db):
        r = run_analysis(
            sql="SELECT cycle, soh FROM readings",
            code="result = {}",
        )
        assert r["rows_analyzed"] == 10
        assert set(r["columns_used"]) == {"cycle", "soh"}

    def test_numpy_types_serialized_to_python(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="result = {'std': np.std(df['soh'])}",
        )
        assert r["success"] is True
        # numpy.float64 must be converted to plain Python float
        assert isinstance(r["result"]["std"], float)

    def test_pandas_groupby_in_code(self, temp_db):
        r = run_analysis(
            sql="SELECT category, soh FROM readings",
            code="result = df.groupby('category')['soh'].mean().to_dict()",
        )
        assert r["success"] is True
        assert "A" in r["result"]
        assert "B" in r["result"]

    def test_z_score_anomaly_detection(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code=(
                "z = (df['soh'] - df['soh'].mean()) / df['soh'].std();"
                "result = {'anomalies': int((z.abs() > 1.5).sum())}"
            ),
        )
        assert r["success"] is True
        assert isinstance(r["result"]["anomalies"], int)

    def test_scipy_stats_available(self, temp_db):
        r = run_analysis(
            sql="SELECT cycle, soh FROM readings",
            code=(
                "slope, intercept, r, p, se = stats.linregress(df['cycle'], df['soh']);"
                "result = {'slope': float(slope), 'r_squared': float(r**2)}"
            ),
        )
        # Only assert success if scipy is installed
        if r["success"]:
            assert r["result"]["slope"] == pytest.approx(-0.5, abs=0.01)
        else:
            assert "stats" in r["error"] or "not found" in r["error"].lower()


# ── SQL failure ───────────────────────────────────────────────────────────────

class TestRunAnalysisSqlFailure:

    def test_bad_sql_returns_error(self, temp_db):
        r = run_analysis(
            sql="SELECT * FROM no_such_table",
            code="result = {}",
        )
        assert r["success"] is False
        assert "SQL failed" in r["error"]

    def test_forbidden_sql_returns_error(self, temp_db):
        r = run_analysis(
            sql="DROP TABLE readings",
            code="result = {}",
        )
        assert r["success"] is False

    def test_empty_sql_result_returns_error(self, temp_db):
        r = run_analysis(
            sql="SELECT * FROM readings WHERE id = 9999",
            code="result = {}",
        )
        assert r["success"] is False
        assert "no rows" in r["error"].lower()


# ── Row limit ─────────────────────────────────────────────────────────────────

class TestRunAnalysisRowLimit:

    def test_row_limit_exceeded_returns_error(self, tmp_path):
        """Rows > MAX_ROWS must be rejected before exec."""
        import sqlite3, database as db_mod
        db_file = tmp_path / "big.db"
        conn = sqlite3.connect(db_file)
        conn.execute("CREATE TABLE big (v REAL)")
        conn.executemany("INSERT INTO big VALUES (?)", [(i,) for i in range(MAX_ROWS + 1)])
        conn.commit()
        conn.close()
        db_mod.set_database(db_file)

        r = run_analysis(sql="SELECT * FROM big", code="result = {}")
        assert r["success"] is False
        assert "Too many rows" in r["error"]


# ── Code errors ───────────────────────────────────────────────────────────────

class TestRunAnalysisCodeErrors:

    def test_missing_result_assignment_returns_error(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="x = df['soh'].mean()",    # no `result = ...`
        )
        assert r["success"] is False
        assert "result" in r["error"]

    def test_wrong_column_name_returns_error_with_hint(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="result = df['nonexistent_col'].mean()",
        )
        assert r["success"] is False
        assert r.get("hint") is not None
        assert "soh" in r["hint"]           # hint shows actual columns

    def test_syntax_error_in_code_returns_error(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="result = df['soh'].mean( :::invalid",
        )
        assert r["success"] is False

    def test_non_serializable_result_returns_error(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="result = lambda x: x",    # lambda is not JSON-serializable
        )
        assert r["success"] is False
        assert "JSON" in r["error"] or "serializable" in r["error"].lower()


# ── Sandbox blocking ──────────────────────────────────────────────────────────

class TestSandboxBlocking:

    def test_import_os_blocked(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="import os; result = os.getcwd()",
        )
        assert r["success"] is False

    def test_open_builtin_blocked(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="result = open('secrets.txt').read()",
        )
        assert r["success"] is False

    def test_exec_builtin_blocked(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="exec('import os'); result = {}",
        )
        assert r["success"] is False

    def test_eval_builtin_blocked(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="result = eval('1+1')",
        )
        assert r["success"] is False

    def test_subprocess_not_accessible(self, temp_db):
        r = run_analysis(
            sql="SELECT soh FROM readings",
            code="import subprocess; result = subprocess.run(['ls'])",
        )
        assert r["success"] is False
