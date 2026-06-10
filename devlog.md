# Devlog — Universal SQL Agent

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

### Files yang diubah

| File | Perubahan |
|------|-----------|
| `data/demo.db` | Regenerate — rate degradasi baru, semua battery kini punya EOL |
| `eval/cases/battery.jsonl` | Fix bat_005/bat_013 (B7→B3); tambah bat_016–021 |
| `eval/cases/ecommerce.jsonl` | Tambah eco_013–017 |
| `README.md` | Update case count + eval results table |

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
