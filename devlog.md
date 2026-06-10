# Devlog — Universal SQL Agent

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
