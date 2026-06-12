# Plan — Universal SQL Agent

Roadmap pengembangan project menuju portfolio kuat (target: role **Data / ML / AI Engineer**)
dan jalur menuju production. Disusun 2026-06-09.

---

## Keputusan yang sudah diambil

- **Target role:** Data / ML / AI Engineer → fokus ke kualitas agent, SQL, eval, observability.
- **Deployment:** Streamlit Community Cloud + Supabase Postgres. **Vercel TIDAK dipakai**
  (Vercel tidak bisa host Streamlit yang stateful/long-running; rewrite ke Next.js tidak worth
  it untuk role Data/ML).
- **Prinsip identitas project:** read-only analytical agent adalah inti & selling point utama.
  Fitur write (kalau ada) harus jadi mode terpisah yang digated eksplisit, bukan digabung ke
  path analitik.

---

## Status saat ini (sudah beres)

- [x] `requirements.txt` ada
- [x] `dashboard.py` sudah di-commit
- [x] Eval harness (`eval/run_eval.py`) + test cases (`eval/cases/*.jsonl`)
- [x] Pytest untuk `database.py` (`tests/test_database.py`)

---

## Fixes tertunda dari review (kerjakan dulu — murah, dampak tinggi)

- [x] **🔴 Bug order-dependent di `compare_rows`** — `eval/run_eval.py:147` membandingkan baris
  per posisi (`zip`). Kalau urutan baris agent ≠ expected (mis. tanpa `ORDER BY`), hasil benar
  dilaporkan `FAIL`. Fix: sort kedua sisi sebelum compare, atau flag `order_matters` per-case
  (default false).
- [x] **Section "Known Limitations" di README** — sebutkan: SQLite-only (saat ini), history
  unbounded, schema injection belum scalable. Menunjukkan kesadaran batasan sistem = nilai plus.
- [x] **Read-only via engine** — saat masih SQLite: buka koneksi `mode=ro`. Saat sudah Postgres:
  role read-only (`GRANT SELECT` saja). Ganti narasi keamanan dari "blacklist keyword" jadi
  "engine-level read-only + app validation".
- [x] **Perluas coverage eval** — tambah 5–8 case "hard" (multi-JOIN, window function, pertanyaan
  ambigu). Coverage sekarang miring ke `count`/`basic`/`aggregation`.
- [x] **Test untuk `agent.py` & `tools.py`** — mock `client`, verifikasi MAX_ITERATIONS,
  handling tool_calls, retry. Logika paling agentic belum tertest.
- [x] **CI (GitHub Actions)** — jalankan `pytest` tiap push → badge hijau di README.
  (Eval butuh API key, tidak bisa di CI; pytest bisa.)

---

## DEPLOYMENT

### Fase 1 — Live dulu (minimal, hitungan jam) ⏳ prioritas

Deploy `dashboard.py` ke Streamlit Community Cloud.

- [x] **🔴 Sediakan demo DB di environment deploy.** `.gitignore` meng-ignore `data/` & `*.db`,
  jadi deploy tidak punya database → crash. Commit satu `.db` kecil (un-ignore khusus, mis.
  `!data/demo.db`).
- [x] **Secrets, bukan `.env`.** Streamlit Cloud pakai `st.secrets` (TOML di dashboard UI).
  Tambah pola fallback `os.getenv("X") or st.secrets.get("X")` supaya jalan di lokal + cloud.
  Masukkan `MINIMAX_API_KEY`, `TAVILY_API_KEY` ke secrets UI.
- [x] Connect repo di share.streamlit.io → pilih `dashboard.py` → isi secrets → live.
- [ ] Taruh URL demo di README + CV.

### Fase 2 — Supabase Postgres (upgrade, setelah live)

⚠️ **Bukan sekadar ganti connection string** — ini ganti dialek SQL:
- Prompt hardcode dialek SQLite (`agent.py:86` `strftime`). Postgres: `to_char(col,'YYYY-MM')`.
- Schema introspection pakai `PRAGMA`/`sqlite_master` (`database.py:67`) → `information_schema`.
- **Eval cases (`expected_sql`) ikut harus ditulis ulang** ke dialek Postgres.

- [x] Buat **abstraksi DB** — CLI tetap SQLite lokal, dashboard pakai Postgres.
- [x] Migrasi schema introspection ke `information_schema`.
- [x] Update prompt + domain packs ke dialek Postgres.
- [x] Buat **role read-only** di Supabase (`GRANT SELECT` saja) untuk koneksi agent.
- [x] Pakai **connection pooler** Supabase (Session pooler port 5432) — konek berhasil.
- [ ] ⚠️ `service_role` key hanya server-side; jangan pernah di client.

---

## FITUR — Arah A: "Spesialis Data Analyst" (prioritas portfolio)

Mengubah agent dari *text-to-SQL* jadi *analis*.

- [x] **🌟 #1 — Tool eksekusi Python/pandas (sandboxed).** Analisis yang sulit di SQL: korelasi,
  regresi, deteksi anomali (z-score), distribusi, forecasting. Jalankan pandas **di atas hasil
  SQL**. Kunci: **sandbox** — jangan `exec()` mentah; batasi ke operasi pada DataFrame hasil atau
  subprocess dengan resource limit. → Fitur paling impresif untuk portfolio Data/ML.
- [x] **🌟 #2 — Auto data-profiling / data dictionary.** Saat DB connect, profil tiap tabel sekali
  (row count, null %, distinct count, min/max, tipe semantik, relasi). Cache + suntik ke prompt.
  → Wujudkan visi "spesialis untuk data terpasang" + sekaligus fix masalah schema-injection yang
  tidak scalable (kirim profil ringkas, bukan dump mentah).
- [x] **#3 — Publish akurasi eval di README** (mis. "akurasi 87% pada 35 test case"). Bukti
  kuantitatif yang dipercaya recruiter Data/ML.

### Production signals (tier 2, opsional)
- [ ] Caching hasil query + schema (biaya & latensi).
- [ ] Tracking biaya/latensi per sesi (token sudah di-log → konversi ke $).
- [ ] Manajemen history (ringkas/truncate — fix unbounded growth).

---

## FITUR — Arah B: Insert/Write Data (visi jangka panjang, v2)

⚠️ **Fitur write menghancurkan selling point read-only kalau dilakukan naif.**
**JANGAN** hapus blacklist & biarkan LLM emit `INSERT`/`UPDATE` mentah.

Arsitektur write yang aman (kalau diimplementasikan):

1. **Pisahkan dua role/koneksi** — path analitik = role read-only; write lewat koneksi terpisah
   yang digated. Loop analitik tidak pernah menyentuh koneksi write.
2. **LLM tidak menulis SQL mentah** — definisikan **typed write tools** (`insert_row(table, values)`
   atau domain-spesifik `record_battery_test(...)`). App validasi kolom vs schema & bangun SQL
   ber-parameter. LLM hanya isi parameter.
3. **Human-in-the-loop (kontrol terpenting)** — tool tidak langsung eksekusi; kembalikan *usulan
   perubahan* (SQL + preview baris terdampak via dry-run SELECT). User klik "Konfirmasi" → baru jalan.
4. **Transaction + guard jumlah baris** — bungkus di transaction; kalau write > N baris, blokir &
   minta override eksplisit. Cegah bencana mass-update.
5. **Audit trail** — extend logger JSONL jadi audit immutable: siapa, kapan, SQL, berapa baris,
   snapshot before/after.

- [ ] **Minimal sekarang:** tulis arsitektur write aman ini di README sebagai roadmap
  (read path vs write path). Sudah menunjukkan production thinking = nilai interview, walau belum
  diimplementasikan.

---

## Urutan eksekusi yang disarankan

| # | Item | Alasan |
|---|------|--------|
| 1 | Fix bug order-dependent `compare_rows` | Bisa bikin eval false-negative saat di-review |
| 2 | Deployment Fase 1 (Streamlit Cloud) | Demo live = effort kecil, dampak besar untuk lamaran |
| 3 | Tool pandas/stats (Arah A.1) | Yang sebenarnya bikin "data analyst" |
| 4 | Auto data-profiling (Arah A.2) | Wujudkan visi "spesialis data terpasang" + fix schema scaling |
| 5 | Eval diperluas + akurasi di README | Bukti kuantitatif untuk Data/ML |
| 6 | Known Limitations + arsitektur write di README | Production thinking |
| 7 | Supabase Fase 2, lalu write-path (v2) | Effort & risiko besar — belakangan |
