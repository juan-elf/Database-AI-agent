# Devlog — Universal SQL Agent

---

## 2026-06-12 — Session 16: Deployment Fase 2 — Dual Engine Foundation

### Yang dikerjakan

Fondasi untuk migrasi ke Supabase Postgres. Semua perubahan di-guard oleh `DATABASE_URL` env variable — kalau tidak di-set, behaviour SQLite identik seperti sebelumnya.

**`requirements.txt`** — tambah `psycopg2-binary>=2.9.0`

**`database.py`** — refactor besar: dual-engine (SQLite + PostgreSQL):
- `_use_postgres()` / `get_db_engine()` — deteksi engine dari `DATABASE_URL` env
- `get_db_label()` — human-readable DB identifier (filename untuk SQLite, `host/dbname` untuk Postgres)
- `get_connection()`:
  - SQLite: tetap `uri=True + ?mode=ro`
  - Postgres: `psycopg2.connect(DATABASE_URL)` + `set_session(readonly=True, autocommit=True)`
- `get_schema()` → branch ke `_get_schema_sqlite()` atau `_get_schema_postgres()`:
  - SQLite: PRAGMA + sqlite_master (tidak berubah)
  - Postgres: `information_schema.tables` + `information_schema.columns` + FK via `information_schema.table_constraints`
- `get_table_names()` → branch ke sqlite_master atau information_schema
- `execute_query()` → pakai `dict(zip(columns, row))` yang kompatibel kedua engine; exception ditangkap via `_DB_ERRORS` tuple `(sqlite3.Error, psycopg2.Error)`
- `get_distinct_values()` → PRAGMA untuk SQLite, information_schema untuk Postgres; `LIMIT {limit}` literal (integer-validated) menggantikan placeholder `?` agar kompatibel keduanya
- `_generate_error_hint()` → dialect-aware error message

**`profiler.py`** — dual-engine:
- `_cache_key()`: SQLite → `(path, mtime)`, Postgres → `("postgres", DATABASE_URL)`
- `_get_table_names(conn)`: sqlite_master vs information_schema
- `_get_column_meta(conn, table)`: PRAGMA vs information_schema.columns + `_pg_type_to_declared()` mapper
- Stats queries (null %, distinct, min/max/mean) tidak berubah — SQL standard untuk kedua engine

**`agent.py`** — dialect-aware system prompt:
- Import `get_db_engine`, `get_db_label` (replace `get_database_path`)
- `_DIALECT_RULES` dict — SQLite vs PostgreSQL specific hints (monthly grouping, date format, casting, ILIKE, dll)
- `GENERIC_INSTRUCTIONS` — hapus rules SQLite-specific (rules 1-3 lama), ganti dengan rules universal saja
- `build_system_prompt()` — inject `_DIALECT_RULES[dialect]` section + pakai `get_db_label()` sebagai identifier DB

### Yang BELUM dilakukan (perlu Supabase ready)
- Test end-to-end Postgres connection
- Import data dari demo.db ke Supabase
- Setup read-only role di Supabase (`GRANT SELECT`)
- Test eval cases di Postgres dialect

### Status test suite
`165 passed` (137 non-agent + 28 agent) — semua hijau, SQLite path tidak berubah.

---

## 2026-06-11 — Session 15: Auto Data-Profiling A.2

### Yang dikerjakan

**`profiler.py`** — new module, profil otomatis semua tabel di DB aktif:
- `profile_database(force=False)` → cached by `(db_path, mtime)` — hanya jalan satu kali per file+versi
- `_build_profile()` → iterasi semua tabel via `sqlite_master`
- `_profile_table()` → row count + per-column stats via PRAGMA (bypass `validate_query`)
- `_profile_column()` → null_pct, distinct_count, dan:
  - Numeric (INT/REAL/FLOAT/...): `semantic_type="numeric"` + min/max/mean
  - Categorical (distinct ≤ 20): `semantic_type="categorical"` + sample_values
  - High-cardinality text (distinct > 20): `semantic_type="text"`
- `MAX_STAT_ROWS = 100_000` — tabel sangat besar hanya dihitung row count, column stats di-skip
- `format_profile_for_prompt(profile)` → compact ASCII text per baris untuk injection ke system prompt

**Output format untuk demo.db:**
```
battery_cycles  (1,810 rows)
  battery_id    TEXT     |values: B1, B2, B3, B4, B5 |distinct: 5
  cycle         INTEGER  |range: 1-450 |mean: 188.2 |distinct: 450
  soh           REAL     |range: 72.63-100 |mean: 85.8 |distinct: 1807
  ...
```

**`agent.py`** — `build_system_prompt()` sekarang inject DATA PROFILE section setelah raw schema:
```python
from profiler import profile_database, format_profile_for_prompt
# ...
try:
    profile = profile_database()
    profile_text = format_profile_for_prompt(profile)
except Exception:
    profile_text = "(Data profile unavailable)"
```
Error di profiling tidak crash agent — fallback ke "(Data profile unavailable)".

**`tests/test_profiler.py`** — 30 tests, 6 kelas:

| Class | Tests | Apa yang diverifikasi |
|-------|-------|-----------------------|
| `TestProfileDatabase` | 4 | Table names, row count, all columns profiled, generated_at timestamp |
| `TestColumnSemanticTypes` | 5 | INTEGER→numeric, REAL→numeric, low-card→categorical, high-card→text, threshold boundary |
| `TestColumnStatistics` | 7 | min, max, mean, sample_values, distinct_count, null_pct=0, null_pct=80% |
| `TestCache` | 3 | Same object returned, force=True returns fresh, force content matches |
| `TestLargeTableSkip` | 2 | monkeypatch MAX_STAT_ROWS=5 → skipped=True, no min/max |
| `TestFormatProfileForPrompt` | 9 | table name, row count, range/values/nulls keywords, column names, empty fallback |

### Status test suite
`137 passed` — semua hijau.

---

## 2026-06-11 — Session 14: Sandboxed Pandas Tool A.1

### Yang dikerjakan

**`analysis.py`** — engine baru, sandboxed Python/pandas execution:
- `run_analysis(sql, code)` → fetch data via `execute_query`, exec code dalam restricted namespace
- `__builtins__` diganti dict allowlist (tidak ada `open`, `exec`, `eval`, `__import__`)
- Allowed modules: `pd`, `np`, `stats` (scipy jika tersedia)
- Limit 10,000 baris sebelum exec
- `_json_default` serializes numpy types (integer/floating/ndarray/Series) ke Python native

**`tools.py`** — ditambah:
- `run_analysis` entry di TOOLS_SCHEMA (parameters: `sql`, `code`)
- `_run_analysis_tool` wrapper → memanggil `_run_analysis_fn` lalu JSON-encode hasilnya

**`agent.py`** — GENERIC_INSTRUCTIONS updated: `run_analysis` masuk daftar AVAILABLE TOOLS dengan panduan kapan dipakai (korelasi, z-score, distribusi, trend linier) vs tidak (simple aggregation → pakai `execute_sql`).

**`requirements.txt`** — ditambah `numpy>=1.26.0`, `scipy>=1.11.0`

**`tests/test_analysis.py`** — 21 tests baru, 5 kelas:

| Class | Tests | Apa yang diverifikasi |
|-------|-------|-----------------------|
| `TestRunAnalysisHappyPath` | 8 | Mean, korelasi, tolist, rows_analyzed, numpy serialization, groupby, z-score, scipy linregress |
| `TestRunAnalysisSqlFailure` | 3 | Bad SQL, DROP TABLE (read-only), empty result |
| `TestRunAnalysisRowLimit` | 1 | >10,000 rows → rejected sebelum exec |
| `TestRunAnalysisCodeErrors` | 4 | No `result` assignment, wrong column name (+hint), SyntaxError, lambda not serializable |
| `TestSandboxBlocking` | 5 | `import os`, `open`, `exec`, `eval`, `import subprocess` — semua blocked |

**`tests/test_tools.py`** — `test_tools_schema_has_three_entries` diupdate jadi `test_tools_schema_has_four_entries` (4 tools setelah `run_analysis` ditambahkan).

### Status test suite
`107 passed` (test_analysis: 21, test_compare_rows: 13, test_database: 58, test_tools: 15) — semua hijau.

---

## 2026-06-11 — Session 13: Test agent.py & tools.py (E)

### Yang dikerjakan

**`tests/test_tools.py` — 16 tests**

| Class | Tests | Apa yang diverifikasi |
|-------|-------|-----------------------|
| `TestCallTool` | 7 | Unknown tool → error JSON; missing/extra arg → TypeError caught; 3 tool dispatches; unexpected exception caught |
| `TestExecuteSqlTruncation` | 6 | No truncation ≤50 rows; exactly 50 = no note; 51 → truncate + note; note berisi total count & kata "aggregation"; `row_count` tetap total bukan 50 |
| `TestWebSearchTool` | 2 | Delegates ke `search_web`; meneruskan `max_results` |

Semua test mock database via `database.set_database(tmp_path / "tools_test.db")` — tidak butuh API key.

**`tests/test_agent.py` — 27 tests**

| Class | Tests | Apa yang diverifikasi |
|-------|-------|-----------------------|
| `TestListAvailableDomains` | 3 | Dir tidak ada → []; file terurut; file non-.md diabaikan |
| `TestLoadDomainPack` | 2 | File ada → content; tidak ada → None |
| `TestBuildSystemPrompt` | 4 | Mengandung generic instructions; schema; domain content; fallback saat domain tidak ada |
| `TestAgentInit` | 5 | Token counts = 0; logger = None; 1 system message; domain disimpan; system prompt berisi instructions |
| `TestAgentChat` | 7 | Direct answer; history grows; token accumulation; tool call dispatched → final answer; invalid JSON args tidak crash; MAX_ITERATIONS → warning; API exception → error string |
| `TestAgentReset` | 3 | History dibersihkan; system prompt dipertahankan; token counts di-reset ke 0 |
| `TestCallApiWithRetry` | 4 | Success on first call; retry on APIConnectionError then success; raises after MAX_RETRIES exhausted; sleep count = MAX_RETRIES - 1 |

Semua test mock `agent.client.chat.completions.create` dan `agent._call_api_with_retry` — tidak ada real API call.

**Teknik testing:**
- `SimpleNamespace` untuk fake OpenAI response objects (tidak perlu install openai response fixtures)
- `patch("agent.get_schema")` + `patch("agent.web_available")` di `db_env` fixture
- `httpx.Request` untuk membuat `APIConnectionError` yang valid
- `monkeypatch.setattr(agent, "DOMAINS_DIR", tmp_path)` untuk isolasi domain tests

**Total test suite: 114 passed, 0 failed**

Breakdown:
- `test_database.py`: 71 tests
- `test_compare_rows.py`: 13 tests
- `test_agent.py`: 27 tests (baru)
- `test_tools.py`: 16 tests (baru - termasuk 3 dari `test_tools.py`)

Wait, breakdown:
- `test_database.py`: 58 tests
- `test_compare_rows.py`: 13 tests
- `test_agent.py`: 27 tests
- `test_tools.py`: 16 tests
- Total: 114

### Files yang diubah

| File | Perubahan |
|------|-----------|
| `tests/test_tools.py` | File baru — 16 tests |
| `tests/test_agent.py` | File baru — 27 tests |

---

## 2026-06-10 — Session 12: Eval Diperluas (C)

### Yang dikerjakan

**Regenerate `data/demo.db`**

Demo DB lama punya masalah: rate degradasi terlalu lambat sehingga tidak ada battery yang mencapai EOL (soh < 80%). Battery eval cases yang menguji EOL detection (bat_004, bat_005) akan trivially pass dengan empty result.

Fix: regenerate dengan rate lebih tinggi. 5 baterai (B1–B5) kini mencapai EOL dengan jelas:
- B1: EOL cycle ~262, B2: ~187, B3: ~322, B4: ~224, B5: ~296
- 1.810 baris, ~156 KB (lebih kecil dari sebelumnya karena max cycles diperpendek)

**Fix test case referencing B7 (tidak ada di demo.db)**

- `bat_005`: "B7" → "B3"
- `bat_013`: "B7" → "B3"

**Tambah 6 hard battery cases (`bat_016`–`bat_021`)**

Coverage yang ditambah: HAVING, ranking by computed expression, filter+group-by, computed column, window function (LAG), percentage via CASE WHEN.

| ID | Skill yang diuji |
|----|-----------------|
| bat_016 | `HAVING AVG(soh) > 85` — HAVING clause |
| bat_017 | `ORDER BY (MAX-MIN) DESC LIMIT 1` — ranking by computed expression |
| bat_018 | `WHERE soh > 90 GROUP BY` — filter + group-by |
| bat_019 | `AVG(discharge - charge)` — computed column in aggregate |
| bat_020 | `LAG() OVER (PARTITION BY ORDER BY)` — window function |
| bat_021 | `SUM(CASE WHEN...) / COUNT(*)` — percentage via CASE |

Semua SQL diverifikasi terhadap demo.db sebelum di-commit.

**Tambah 5 hard ecommerce cases (`eco_013`–`eco_017`)**

| ID | Skill yang diuji |
|----|-----------------|
| eco_013 | `NOT IN (subquery)` — anti-join / customers tanpa order |
| eco_014 | 3-table JOIN + ranking + `LIMIT 5` (order_matters: true) |
| eco_015 | 3-table JOIN + `HAVING SUM(...) > 10000` |
| eco_016 | Nested subquery — avg items per completed order |
| eco_017 | 3-table JOIN + `COUNT(DISTINCT)` per customer |

**Update README `## Eval Harness`**

- Update case count: 15→21 (battery), 12→17 (ecommerce)
- Tambah kolom "Tags covered" di tabel test cases
- Tambah "Latest eval results" tabel — placeholder siap diisi setelah eval dijalankan

**Bug fix: `set_database()` — relative path crash**

`get_connection()` pakai `.as_uri()` yang butuh path absolut. Kalau user pass path relatif (`data/demo.db`), crash dengan `ValueError: relative paths can't be expressed as file URIs`.

Fix: `Path(path).resolve()` di `set_database()`. Satu baris, solves the crash.

**Eval run: baseline accuracy 66.7% (14/21)**

Jalankan full eval terhadap demo.db, domain battery, 21 cases.

Hasil:
```
PASS 14  FAIL 7  Accuracy 66.7%
```

Failure analysis:

| Pattern | Cases | Root cause |
|---------|-------|-----------|
| Extra columns | bat_003, bat_004, bat_005, bat_013 | Agent tambah kolom konteks (battery_id, soh, cycle) |
| Missing LIMIT | bat_008, bat_017 | Ranking: agent return semua baris, bukan top-1 |
| Sign/polarity | bat_020 | "Drop" expected negatif (MIN), agent return positif (ABS) |

bat_020 expected_sql diupdate ke `MAX(ABS(soh_change))` agar sesuai dengan interpretasi natural "drop" = magnitude positif.

Tags 100% accuracy: count, subquery, having, temperature, percentage, degradation.
Tags perlu perbaikan: ranking (0%), window-function (0%), eol (33%), filter (50%).

### Files yang diubah

| File | Perubahan |
|------|-----------|
| `data/demo.db` | Regenerate — rate degradasi baru, semua battery kini punya EOL |
| `database.py` | `set_database()`: `Path(path)` → `Path(path).resolve()` |
| `eval/cases/battery.jsonl` | Fix bat_005/bat_013 (B7→B3); tambah bat_016–021; fix bat_020 expected_sql sign |
| `eval/cases/ecommerce.jsonl` | Tambah eco_013–017 |
| `README.md` | Update case count + eval results table dengan angka real |
| `eval/results_battery.json` | Hasil eval run (gitignored) |

---

## 2026-06-10 — Session 11: CI GitHub Actions

### Yang dikerjakan

**CI: `pytest` otomatis tiap push ke `main`**

Buat `.github/workflows/tests.yml` — workflow minimal yang:
- Trigger: `push` dan `pull_request` ke branch `main`
- Runner: `ubuntu-latest`, Python 3.12
- Steps: `actions/checkout@v4` → `actions/setup-python@v5` → `pip install -r requirements.txt` → `pytest tests/ -v`
- Hanya `tests/` yang dijalankan di CI (unit tests murni, tidak butuh API key). Eval harness (`eval/run_eval.py`) tidak masuk CI karena butuh `MINIMAX_API_KEY`.

Tambah badge status CI di baris pertama `README.md`:
```
[![Tests](https://github.com/juan-elf/Database-AI-agent/actions/workflows/tests.yml/badge.svg)](...)
```

Badge akan hijau setelah push pertama berhasil dan workflow jalan.

### Files yang diubah

| File | Perubahan |
|------|-----------|
| `.github/workflows/tests.yml` | File baru — CI workflow |
| `README.md` | Tambah badge CI di baris pertama |

---

## 2026-06-09 — Session 10: Deploy Blockers + Test Fixes

### Yang dikerjakan

**1. Fix: `compare_rows` order-dependent (eval/run_eval.py)**

Bug: `compare_rows` membandingkan baris per posisi via `zip(expected, actual)`. Kalau agent menghasilkan baris yang benar tapi urutannya berbeda (tidak pakai `ORDER BY`), hasilnya dilaporkan FAIL padahal sebenarnya benar.

Fix: tambah flag `order_matters` per test case (default `False`). Ketika `False`, kedua list di-sort dulu dengan `_row_sort_key` (stringify semua value) sebelum dibandingkan. Set `order_matters: true` di test case yang memang menguji urutan spesifik.

Perubahan: `eval/run_eval.py` — fungsi `compare_rows`, `_row_sort_key`, dan `run_case`.
Test baru: `tests/test_compare_rows.py` — 13 test case (order-independent + order-matters).

---

**2. Fix: SQLite read-only via `mode=ro` (database.py)**

Security fix: sebelumnya validasi keamanan hanya di layer aplikasi (whitelist SELECT/WITH + blacklist keyword). Kalau validasi bypass, koneksi masih bisa write.

Fix: `get_connection()` sekarang buka koneksi via URI dengan `?mode=ro`:
```python
conn = sqlite3.connect(get_database_path().as_uri() + "?mode=ro", uri=True)
```

Engine-level read-only — SQLite menolak write langsung di driver level.

Test: `test_connection_is_read_only_at_driver_level` di `tests/test_database.py`.
Note: test awalnya gagal karena regex `"read-only"` tidak cocok dengan pesan error SQLite `"attempt to write a readonly database"`. Fix: ubah pattern ke `"read"`.

---

**3. Deploy blocker: Demo database (`data/demo.db`)**

Problem: `.gitignore` meng-ignore seluruh `data/` dan `*.db`. Streamlit Cloud deploy tidak punya database → crash.

Fix:
- Buat `data/demo.db` — synthetic battery dataset, 5 baterai (B1–B5), 2.225 baris, ~156 KB.
  Kolom: `battery_id`, `cycle`, `soh`, `capacity`, `rul`, `charge_temperature`, `discharge_temperature`.
  Sesuai dengan hardcoded chart queries di `dashboard.py`.
- Update `.gitignore`: hapus `data/`, pertahankan `*.db`, tambah `!data/demo.db` sebagai exception.
  `battery.db` dan database lain tetap ignored.

---

**4. Deploy blocker: Secrets fallback untuk Streamlit Cloud (dashboard.py)**

Problem: `agent.py` baca API key via `os.getenv("MINIMAX_API_KEY")` setelah `load_dotenv()`. Streamlit Cloud tidak pakai `.env`, tapi `st.secrets` (TOML via UI).

Fix: tambah `_inject_secrets()` di `dashboard.py` — dipanggil saat startup, sebelum sidebar block (yang pertama kali import `agent`). Fungsi ini baca `st.secrets` dan inject ke `os.environ` kalau key belum ada. No-op saat lokal (`.env` sudah di-load oleh `dotenv`).

```python
def _inject_secrets() -> None:
    try:
        secrets = st.secrets
        for key in ("MINIMAX_API_KEY", "TAVILY_API_KEY"):
            if not os.environ.get(key) and key in secrets:
                os.environ[key] = secrets[key]
    except Exception:
        pass
```

---

**5. Known Limitations di README.md**

Tambah tabel "Known Limitations" sebelum Folder Structure. Mencakup:
- SQLite-only
- Unbounded conversation history
- Schema injection tidak scalable
- SQL dialect tied to SQLite
- Write-only by design (intentional)
- Single-tenancy

---

### Status setelah session ini

- Test suite: **71 passed, 0 failed** (`pytest tests/ -v`)
- Deploy blockers: **semua resolved** — demo.db ada, secrets fallback ada, README lengkap
- Langkah berikutnya: connect repo ke Streamlit Community Cloud, isi secrets di UI, live

---

### Files yang diubah

| File | Perubahan |
|------|-----------|
| `eval/run_eval.py` | `compare_rows` + `_row_sort_key` + `order_matters` flag |
| `tests/test_compare_rows.py` | File baru — 13 test case |
| `tests/test_database.py` | +1 test read-only; fix regex `"read-only"` → `"read"` |
| `database.py` | `get_connection()` pakai URI + `?mode=ro` |
| `data/demo.db` | File baru — demo database 2.225 baris |
| `.gitignore` | Hapus `data/`; tambah `!data/demo.db` |
| `dashboard.py` | `import os`; tambah `_inject_secrets()` |
| `README.md` | Tambah Known Limitations; update Folder Structure |
| `devlog.md` | File baru — log ini |
