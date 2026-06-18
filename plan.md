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
- [x] Taruh URL demo di README. (CV — update manual)

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
- [x] ⚠️ `service_role` key hanya server-side — dikonfirmasi tidak dipakai di client. Hanya `DATABASE_URL` (agent_user read-only) yang di-set.

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

- [x] **Minimal sekarang:** tulis arsitektur write aman ini di README sebagai roadmap
  (read path vs write path). Section "Roadmap: Safe Write Architecture (v2)" ditambahkan ke README.

---

## FITUR — Arah B-lanjutan: Smart Data Ingestion + Routing (v2 lengkap)

> Visi user (2026-06-13): agent bisa **input data ke database**, dan **memilih sendiri tabel/DB
> yang sesuai** berdasarkan isi data (mis. laporan keuangan → tabel finance; data baterai →
> tabel battery). Dua pintu masuk: **upload file di dashboard** ATAU **lewat percakapan AI agent**,
> lalu data bisa langsung dianalisis.

### Prinsip framing

- ⚠️ **Ini PIVOT, bukan penambahan fitur.** Begitu agent bisa menulis, selling point
  "read-only, aman by design" runtuh kalau dilakukan naif. Wajib jalur write terpisah & digated.
- 🎯 **Angle terkuat bukan "INSERT", tapi "routing".** INSERT itu biasa. Yang impresif secara
  engineering adalah **klasifikasi/schema-matching**: menentukan data masuk ke tabel mana.
  Separuh solusinya **sudah ada di `profiler.py`** → reuse, jangan bangun dari nol.

### Komponen arsitektur

1. **Catalog layer** — profil semua tabel/DB yang dimiliki (reuse `profiler.py` + domain pack
   sebagai deskripsi) → katalog "tabel X berisi apa, kolomnya apa, tipe semantiknya apa".
2. **Router/classifier** — data masuk → bandingkan kolom & nilai dengan katalog → pilih tabel
   paling cocok + **confidence score** → **konfirmasi ke user** ("Data ini cocok untuk
   `finance.transactions` — benar?").
3. **SATU jalur write yang aman** — upload CSV **dan** input chat menyalurkan ke pipa yang sama.
   Jangan bangun dua mekanisme write terpisah.
4. **Dua pintu masuk (UI):** (a) upload file di dashboard, (b) percakapan ke agent. Keduanya
   bermuara ke jalur write yang sama.

### Security — tidak bisa ditawar

1. **Koneksi/role write terpisah** dari path analitik. Path baca tetap read-only (identitas inti).
   Write lewat role berbeda dengan grant `INSERT` terbatas, digated.
2. **LLM tidak pernah meng-emit SQL write mentah.** LLM hanya mengusulkan **data terstruktur**
   (tabel + mapping kolom→nilai). **App** yang bangun SQL ber-parameter. Mematikan SQL injection +
   menjaga write dalam schema yang dikenal.
3. **Human-in-the-loop** — untuk keputusan routing **dan** untuk write (preview baris sebelum commit).
4. **Transaction + audit log** — extend logger JSONL jadi audit immutable (siapa, kapan, ke tabel
   mana, berapa baris, snapshot).
5. **Andalkan constraint DB** (NOT NULL, FK, tipe) — biar Postgres menolak data kotor.

### Keputusan arsitektur (default tegas — konfirmasi saat mulai)

- **"Database yang sesuai" → banyak TABEL/schema dalam satu Postgres**, bukan banyak file DB
  terpisah. Karena sudah di Supabase, "pilih database sesuai" dipetakan jadi "pilih tabel/schema
  sesuai". Lebih mudah dikelola + memungkinkan analisis lintas-data.
- **Mulai append-only ke tabel yang sudah ada.** Tunda "bikin tabel baru otomatis dari upload yang
  tak cocok" (= schema evolution, paling berbahaya).
- **Routing default: human-confirms** (tampilkan tabel tujuan + confidence, user setujui).

### Pentahapan (urut risiko)

| Fase | Isi | Risiko |
|------|-----|--------|
| **B1** | Catalog + router classifier — **READ-ONLY**, hanya *menentukan & menampilkan* tabel tujuan, belum menulis | Nol — novel, impresif, langsung demoable |
| **B2** | Upload CSV → append ke tabel hasil match (jalur write aman + konfirmasi) | Sedang |
| **B3** | Input conversational lewat agent → jalur write yang sama | Sedang |
| **B4** | Bikin tabel baru / schema evolution | Tinggi — **defer** |

- [x] **B1 — Catalog + router classifier (READ-ONLY).** `router.py`: `build_catalog()`
  (reuse `profiler.py`) + `classify_data(df, catalog)` — heuristik (Jaccard nama kolom,
  type compatibility, value/range overlap) filter top-3, lalu LLM judge untuk confidence +
  column mapping final. 31 tests. Verified end-to-end: data cocok → 100% confidence +
  mapping benar; data tidak cocok → `is_new_table_needed=True`. UI dashboard ditangani
  terpisah (bukan scope sesi ini).
- [x] **B2 — Upload CSV → confirmed append.** `writer.py`: `is_write_configured()`,
  `get_write_connection()` (WRITE_DATABASE_URL untuk Postgres, read-write SQLite lokal),
  `validate_insert()` (row guard 500 baris, validasi kolom), `preview_insert()`, `execute_insert()`
  (transaksi + rollback on error + audit JSONL). Dashboard: write section muncul di halaman
  Klasifikasi Data setelah confidence ≥ 80% — preview baris → tombol "Konfirmasi & Simpan".
  Jika WRITE_DATABASE_URL belum di-set, tampilkan info instruksi alih-alih tombol. 28 tests.
- [ ] **B3 — Conversational insert** lewat jalur write yang sama.
- [ ] **B4 — New-table creation / schema evolution.** Defer; paling berisiko.

> Catatan timeline: **Fase B1 saja sudah fitur kuat & aman** untuk portfolio — router yang
> mengklasifikasi data ke tabel tepat, tanpa menulis. Tidak perlu langsung membangun write penuh.

---

## FITUR — Arah C: Insight Report Mode (rekomendasi #1 "saran terbaik", 2026-06-13)

> Saran terbaik untuk membedakan dari ribuan project text-to-SQL. **Deferred oleh user** ("nanti").

Ubah dari *penjawab query* (1 pertanyaan → 1 query) jadi **analis otonom**: satu tombol
"Generate Insight Report" yang menjalankan loop mandiri:
profil → rumuskan sendiri pertanyaan menarik → eksekusi SQL + pandas → deteksi anomali (z-score) →
render chart → sintesis laporan naratif (executive summary → temuan + grafik → rekomendasi).

- **Reuse 100% komponen existing** (profiler, pandas sandbox, auto-chart, agent loop) — orchestration
  layer, bukan dari nol.
- **Demo "wow" 2 menit**, showcase agentic engineering (multi-step planning + orchestration), bisa
  di-eval (tanam anomali di demo.db → apakah report menemukannya?), dan batu loncatan aman menuju
  Arah B (tetap read-only).

Alternatif yang dipertimbangkan: benchmark publik (BIRD/Spider) untuk angka yang dikenali;
LLM-as-judge di eval untuk menilai kebenaran semantik (menyelesaikan masalah exact-match yang
menghitung "extra column" sebagai FAIL).

- [x] Implementasi `insight_report.py` + tab "🧠 Insight Report" di dashboard. Pipeline: profil → LLM plan (JSON) → execute SQL → deteksi anomali (z-score) → LLM sintesis → laporan naratif + download .md. 22 tests.

---

## FITUR — Arah D: AI Guardrails (prioritas berikutnya, ditambahkan 2026-06-17)

> Status awal: **belum ada guardrail AI sama sekali.** Tidak ada penanganan injeksi, tidak ada
> aturan scope/refusal di system prompt, data tak-tepercaya (web search, sample rows, CSV upload,
> hasil query) mengalir mentah ke prompt.

### Prinsip (trust boundary)

Lapisan **tool** sudah aman secara deterministik (SQL `mode=ro`/`GRANT SELECT`, write butuh
konfirmasi manusia, sandbox pandas, truncation 50 baris, `MAX_ITERATIONS`). Itu aset terkuat:
model di-jailbreak pun tidak bisa menulis/DROP. **Prinsip guardrail: jangan andalkan model
"berkelakuan baik" — enforce di kode.** Model + semua data = tak tepercaya; hanya kode Python =
tepercaya. Guardrail level-model (aturan di prompt) = defense-in-depth, bukan pertahanan utama.

### Ancaman dominan: Indirect Prompt Injection (empat vektor terbuka)

| Vektor | Lokasi | Risiko |
|--------|--------|--------|
| Sample rows DB → system prompt | `database.py:186` | Cell berisi instruksi tersembunyi masuk prompt |
| Hasil web search → LLM | `web_search.py` | Konten web = injeksi klasik |
| **CSV upload → LLM judge** | `router.py:248` | **100% dikontrol penyerang**, memengaruhi keputusan routing (calon write) |
| Hasil query → sintesis report | `insight_report.py` | Data mengalir ke LLM kedua |

CSV→router paling kritis: upload sepenuhnya untrusted, langsung masuk judge penentu tabel tujuan.

### Rencana (urut ROI)

**🥇 Tier 1 — Pemisahan data/instruksi (paling murah, paling relevan)**
- [x] Blok guardrail di system prompt: `harden_system_prompt()` menambahkan blok
  "SECURITY GUARDRAILS / TRUST BOUNDARY" + aturan eksplisit di akhir system prompt.
- [x] **Delimit** semua konten untrusted dengan `wrap_untrusted(data, source)`:
  sample rows DB, web results (answer + content), CSV sample di router, query results di report.
- [x] Pertegas backstop deterministik: gate konfirmasi write tak boleh bisa di-trigger model
  (sudah begitu — tombol manusia ✅).

**🥈 Tier 2 — Input guardrail (pra-LLM)**
- [x] `guardrails.py`: `check_input(text, max_length) -> (allow, reason)` — 20 pola injeksi
  (case-insensitive) + cap panjang 5.000 karakter. Dipanggil di `router.classify_data()` (CSV,
  cap 2.000 karakter) dan sebagai Stage 1 di dalam `check_input_with_llm`.
- [x] Cap panjang input: 5.000 karakter untuk pesan user, 2.000 untuk CSV ke router judge.
- [x] `check_input_with_llm(text, client, model)` — LLM classifier dua-tahap: Stage 1 heuristik
  (gratis), Stage 2 LLM scope classifier (`max_tokens=5`, balas `ALLOW/BLOCK`). Menangkap compound
  injection ("pertanyaan data + tutorial Python") yang regex tidak bisa deteksi. Fail-open saat API
  error. Pakai `guardrail_client` terpisah (model bisa berbeda dari agent utama via `GUARDRAIL_MODEL`).
- [x] `_SCOPE_CHECK_SYSTEM` di-tune: prinsip "BLOCK hanya yang jelas salah, sisanya ALLOW" —
  greeting, capability questions, schema questions lolos; compound off-topic dan jailbreak tetap
  di-BLOCK.

**🥉 Tier 3 — Output guardrail (pasca-LLM)**
- [x] `check_output(answer)` — deteksi marker system prompt (`"SECURITY GUARDRAILS"`,
  `"TRUST BOUNDARY"`, `"cannot be overridden by data"`) di output LLM.
- [ ] Redaksi PII bila DB berisi data sensitif (cegah dump email massal). ← defer
- [x] Router/report sudah validasi JSON + tabel-harus-di-katalog — pertahankan.

**Tier 4 — Scope/refusal**
- [x] Instruksi scope/refusal sudah masuk dalam `_GUARDRAIL_BLOCK` via `harden_system_prompt()`.

### Status: ✅ Selesai (Session 26–27, 2026-06-17–18)
`guardrails.py` + `tests/test_guardrails.py` — Tier 1, 2, 3 selesai. 36 tests.
Dual-LLM: `AGENT_MODEL` + `GUARDRAIL_MODEL` terpisah. Scope classifier di-tune.
Upgrade ke guard-model (Llama Guard) defer ke depan.

---

## Urutan eksekusi yang disarankan

| # | Item | Status |
|---|------|--------|
| 1 | Fix bug order-dependent `compare_rows` | ✅ |
| 2 | Deployment Fase 1 (Streamlit Cloud) | ✅ live |
| 3 | Tool pandas/stats (Arah A.1) | ✅ |
| 4 | Auto data-profiling (Arah A.2) | ✅ |
| 5 | Eval diperluas + akurasi di README (85.7% Gemma) | ✅ |
| 6 | Known Limitations + arsitektur write di README | ✅ |
| 7 | Supabase Fase 2 (dual-engine + read-only role) | ✅ |
| 8 | Autonomous Insight Report (Arah C) | ✅ |
| 9 | Catalog + Router Classifier / CSV classify UI (Arah B1) | ✅ |
| 10 | Upload CSV → Confirmed Append (Arah B2) | ✅ |
| 11 | AI Guardrails — Tier 1/2/3 + LLM classifier (Arah D) | ✅ |
| 12 | Dual-LLM architecture (`AGENT_MODEL` + `GUARDRAIL_MODEL`) | ✅ |
| 13 | Caching query/schema, history management, cost tracking | ⏳ opsional |
| 14 | Conversational insert via chat (Arah B3) | ⏳ opsional |
| 15 | New table creation / schema evolution (Arah B4) | ⏳ defer — tertinggi risiko |
