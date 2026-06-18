# Evaluator — Universal SQL Agent

Hasil evaluasi project pasca-pengembangan besar (Session 10–15 + migrasi OpenRouter & Supabase).
Dievaluasi 2026-06-12. Semua temuan diverifikasi langsung terhadap kode & test suite, bukan hanya
dari catatan devlog.

---

## Ringkasan verdict

Project sudah bertransformasi total dari review awal — semua blocker dan hampir semua item
"sinyal senior" beres. **Fondasi engineering selesai dan kuat**: testing, eval harness, CI,
security berlapis, dual-engine, sandbox. Yang tersisa hampir semuanya *presentasi*: demo yang
harus selalu hidup, README yang harus mengejar kodenya, dan angka akurasi yang harus milik model
yang benar-benar jalan. Untuk lamaran kerja, item presentasi inilah yang dilihat 30 detik pertama.

### Yang terverifikasi bagus ✅

- **Test suite: 165 passed, 0 failed** (`pytest tests/ -q`, dijalankan saat evaluasi)
- **Dual-engine SQLite/Postgres** terimplementasi benar — `information_schema` untuk Postgres,
  read-only di level engine kedua-duanya (`mode=ro` SQLite, `set_session(readonly=True)` Postgres)
- **Eval harness 38 cases** (21 battery + 17 ecommerce) dengan tag breakdown, `order_matters`,
  tolerance, dry-run
- **CI GitHub Actions** + badge di README
- **Fitur Arah A terimplementasi**: sandboxed pandas (`analysis.py`, builtins allowlist,
  block `import os`/`open`/`exec`/`eval`) + auto data-profiling (`profiler.py`, cached by mtime)
- **`requirements.txt` lengkap** termasuk `psycopg2-binary`
- **`_parse_connection_url`** menangani password dengan karakter spesial + strip bracket
  copy-paste Supabase — perhatian ke detail yang bagus

---

## Temuan (urut prioritas)

### 🔴 1. Live demo kemungkinan tidur / tidak bisa diakses

URL demo (`database-ai-agent-...streamlit.app`) merespons **HTTP 303 redirect loop** saat
diakses via curl — pola khas **Streamlit Community Cloud hibernasi** (app tidur setelah ~12 jam
tanpa traffic; pengunjung disambut "This app has gone to sleep").

Dampak: recruiter klik link di CV → layar tidur → close tab.

- [ ] Cek manual di browser; bangunkan kalau tidur
- [ ] Pertimbangkan keep-alive ping: GitHub Actions cron yang curl URL tiap beberapa jam
  (trik umum untuk Streamlit free tier)

(SUDAH AKU FIX SECARA MANDIRI, KAREN UJI COBA DI BROWSER AMAN KEMUNGKINAN AKSES BLOK DARI LLM)

### 🐛 2. Bug nyata: `_generate_error_hint` memberi hint salah di Postgres

`database.py:360` — cabang pertama menangkap `"does not exist"`. Error Postgres untuk **kolom**
hilang berbunyi `column "x" does not exist` — ikut ketangkap cabang pertama → dapat hint
**"Wrong table name"** padahal masalahnya kolom. Cabang kolom di bawahnya tidak pernah tercapai
untuk Postgres. Self-correction agent dapat petunjuk menyesatkan persis di engine yang baru
ditambahkan.

- [ ] Fix: cek kolom dulu sebelum tabel, ATAU persempit deteksi tabel ke
  `relation ... does not exist` (wording khas Postgres untuk tabel)
- [ ] Tambah test: error kolom Postgres → hint kolom, error tabel Postgres → hint tabel

### 📄 3. README stale — fitur baru belum tercermin

| Lokasi | Masalah |
|--------|---------|
| Tool Strategy (±line 192) | "The agent has three tools" — sekarang **empat**; `run_analysis` tidak ada di tabel padahal fitur paling impresif |
| Diagram arsitektur (±line 31–58) | Masih 3 tools; tidak ada `profiler.py`/`analysis.py`; kotak DB masih "SQLite" saja |
| Query Safety (±line 205–212) | Hanya whitelist/blacklist; tidak menyebut engine-level read-only — padahal itu lapisan terkuat |
| Folder Structure (±line 358–382) | Tidak ada `profiler.py`, `analysis.py`, `eval/`, `tests/`, `.github/` |

- [ ] Update keempat section di atas

### 📊 4. Angka akurasi headline milik model yang sudah tidak dipakai

Tabel eval README menampilkan **66.7% — diukur pada MiniMax-M2.7**, sedangkan model produksi
sekarang `google/gemma-4-31b-it:free` (barisnya masih *"pending re-run"*).
Satu-satunya angka kuantitatif di README tidak menggambarkan sistem yang dideploy.

- [ ] Re-run eval dengan nemotron → update tabel (skor bisa berubah, terutama tag
  `ranking`/`window-function` yang 0% di baseline)
- [ ] Laporkan **dua angka**: *strict accuracy* + *semantic accuracy* — 4 dari 7 kegagalan
  baseline adalah "extra context columns" (agent menambah kolom konteks seperti `battery_id`),
  yang untuk "data analyst" justru perilaku diinginkan. Strict 66.7% vs semantic ~85.7% —
  selisih besar di mata recruiter, dan menjelaskan trade-off desain eval adalah narasi
  interview yang kuat
- [ ] Opsional teknis: subset-aware matching (expected ⊆ actual columns) untuk case
  benar + konteks
- [ ] Fix bug ranking nyata (bat_008, bat_017 — missing LIMIT): tambah panduan prompt untuk
  pertanyaan "top N / paling X" → `ORDER BY ... LIMIT`

### 🧹 5. Housekeeping

- [ ] **plan.md masih `[ ]` semua** padahal mayoritas sudah beres — sinkronkan dengan devlog
- [ ] **License**: "Not yet specified. Learning project." → undersell untuk portfolio.
  Tambahkan MIT, hapus frasa "learning project"
- [ ] Koneksi Postgres dibuat per-query tanpa pooling — wajar untuk demo, tapi layak dicatat
  di Known Limitations

---

## Urutan eksekusi yang disarankan

| # | Item | Alasan |
|---|------|--------|
| 1 | Cek/bangunkan demo + keep-alive | First impression recruiter — paling kritis |
| 2 | Re-run eval dengan nemotron, update tabel | Angka headline harus milik sistem yang jalan |
| 3 | Fix bug hint Postgres | Bug nyata di self-correction path |
| 4 | Sinkronkan README + plan.md | Etalase harus mengejar kodenya |
| 5 | License MIT + housekeeping | Polish akhir |

---
---

# Evaluasi #2 — 2026-06-17 (Insight Report + Router + Writer)

Evaluasi pasca-implementasi Arah C (`insight_report.py`), Arah B1 (`router.py`), dan Arah B2
(`writer.py`). Semua diverifikasi langsung terhadap kode, wiring dashboard, dan test suite.

## Ringkasan verdict

**Genuinely production-grade.** Tiga fitur baru landed dengan kualitas tinggi dan aman. Write-path
mengeksekusi arsitektur aman persis seperti yang disepakati. Satu-satunya hal yang butuh
*keputusan* (bukan bug) adalah eksposur write di demo publik.

### Yang terverifikasi bagus ✅

- **Test suite: 248 passed** (167 → 248; +28 `test_writer.py`, +router, +report)
- **Write-path TIDAK diekspos ke LLM loop** — tidak ada `execute_insert` di `tools.py`/`agent.py`
- **Alur write aman:** upload → `classify_data` (router) → `preview_insert` → gate confidence ≥80%
  → tombol konfirmasi manusia → `execute_insert`
- **Injection-safe write:** tabel & kolom divalidasi vs schema asli; nilai ber-parameter; nama
  tabel dari LLM tetap dicek regex + existence; transaction + rollback + audit JSONL; row guard 500
- **Koneksi write terpisah** (`WRITE_DATABASE_URL`, tanpa `readonly`) — read path tetap read-only
- **Router B1 read-only** — heuristik (Jaccard nama + tipe + value-overlap) → LLM judge → fallback
- **Insight Report me-render chart** (`dashboard.py:1044` `auto_chart`) — visi Arah C terpenuhi penuh
- **Test writer menguji jalur Postgres** (16 ref `%s`/`set_session`/`WRITE_DATABASE_URL`)
- **README + devlog ter-update** lengkap (Session 25)

## Temuan (urut prioritas)

### 🔴 1. Demo publik + tanpa autentikasi + write-path = keputusan keamanan
`dashboard.py` tidak punya auth (grep `password/auth/login` → kosong). Kalau `WRITE_DATABASE_URL`
di-set di demo publik, **siapa pun pengunjung bisa append ke tabel Supabase asli** — gate confidence
& tombol konfirmasi tidak menghentikan pengunjung yang memang mau klik. Mitigasi sudah ada
(`GRANT INSERT` membatasi tabel; tanpa `WRITE_DATABASE_URL` write mati).
- [ ] **Keputusan:** biarkan `WRITE_DATABASE_URL` UNSET di demo publik (write mati di sana), ATAU
  password-gate section write (`st.secrets["write_password"]`). → user bilang akan dipikir ulang.

### 🟡 2. Tidak ada dedup/idempotency pada append
Upload CSV sama 2× → baris ganda. Catat di Known Limitations.

### 🟡 3. Insight Report tidak punya self-correction
`insight_report.py:204` — SQL hasil planning yang gagal hanya direkam sebagai error → finding hilang.
Agent utama punya `hint`-retry; report tidak. Pertimbangkan 1× retry dengan hint.

### 🧹 4. Minor
- `router.build_catalog` menaruh seluruh isi domain pack ke `description` setiap tabel (sama semua) —
  boros token, idealnya per-tabel.
- Write SQLite andalkan dynamic typing (tak ada validasi tipe sebelum insert); Postgres aman via
  constraint + rollback.

### 🔒 5. Belum ada guardrail AI (→ jadi Arah D di plan.md)
Belum ada penanganan prompt injection / scope / refusal. Data tak-tepercaya (web search, sample
rows, CSV, hasil query) mengalir mentah ke prompt. Ancaman dominan: **indirect prompt injection**
(4 vektor: `database.py:186`, `web_search.py`, `router.py:248`, `insight_report.py`). Rencana
lengkap → **Arah D** di `plan.md`. Prinsip: model + data = untrusted, enforce di kode.

## Urutan eksekusi yang disarankan

| # | Item | Catatan |
|---|------|---------|
| 1 | Keputusan write di demo publik (#1) | Butuh keputusan user — bukan bug |
| 2 | Guardrails Tier 1 (Arah D) | Menutup ancaman injeksi #1, ROI tertinggi |
| 3 | Self-correction di Insight Report (#3) | Robustness |
| 4 | Dedup append + Known Limitations (#2) | Polish |
