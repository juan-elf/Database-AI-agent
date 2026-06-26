# Evaluator — DataGen

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

---
---

# Evaluasi #3 — 2026-06-18 (Review dokumentasi: devlog + plan.md, Arah D Guardrails)

Evaluasi **akurasi update devlog & plan.md** pasca Session 26 (AI Guardrails) + Session 27
(Dual-LLM architecture). Setiap klaim di-cross-check langsung ke kode.

## Verdict

**Update akurat, kualitas dokumentasi tinggi.** Pekerjaan nyata terverifikasi penuh; devlog jujur
(tandai item defer) dan menjelaskan "why" (alasan dual-LLM, fail-open, scope tuning) — materi
portfolio kuat. Yang tersisa hanya drift kecil doc-vs-kode.

### Terverifikasi akurat ✅

- **284 tests passing** (sesuai devlog Session 27)
- `guardrails.py`: `harden_system_prompt`, `wrap_untrusted`, `check_input`, `check_input_with_llm`,
  `check_output` — semua ada
- **Dual-LLM**: `client` + `guardrail_client` (`agent.py:38,43`); env `AGENT_MODEL` /
  `GUARDRAIL_MODEL` / `GUARDRAIL_API_KEY`
- **Integrasi `wrap_untrusted` di 5 file**: database (×2), web_search (answer+content), router,
  insight_report — semua terpasang
- `agent.chat` panggil `check_input_with_llm` (`agent.py:252`)
- Tier 3 PII di-defer — jujur ditandai `[ ]`

## Drift doc-vs-kode (urut dampak)

### 🟡 1. Tabel "Urutan eksekusi" di ujung plan.md basi
`plan.md:292` masih bilang "semua prioritas tinggi selesai per 2026-06-12, sisanya tier 2 opsional"
dan tabelnya (item 1–8) **hanya Arah A**. B1, B2, C, D selesai setelah itu → ringkasan eksekutif
paling bawah **menjual murah** kerja besar. Tambahkan B/C/D ke tabel.

### 🟡 2. plan.md Tier 2 keliru sebut fungsi
`plan.md:263-264` bilang `check_input` (heuristik) "dipanggil di `Agent.chat()`". Faktanya
`agent.chat` panggil `check_input_with_llm` (heuristik hanya Stage 1 di dalamnya). Hanya
`router.py:247` yang panggil `check_input` langsung. Fungsi benar, penamaan menyesatkan.

### 🟡 3. `.env.example` salah sebut default
Komentar di bawah `GUARDRAIL_MODEL` bilang "Default: google/gemma-4-31b-it:free", padahal default
kode `openai/gpt-oss-120b:free` (`agent.py:29`) — dan nilai contohnya pun gpt-oss-120b. Kontradiktif.

### 🟢 4. Devlog: kata "skip test_guardrails.py" kurang tepat
`devlog.md:37` — conftest tidak men-*skip*; ia mengecualikan file itu dari bypass
`check_input_with_llm` (test guardrail tetap jalan, ikut terhitung di 284). "Skip" bisa menyesatkan.

## Belum diverifikasi
- **README** — devlog klaim sudah update untuk guardrails + tabel config. README = etalase, rawan
  drift; layak dicek terpisah.

## Urutan rapikan
| # | Item | Catatan |
|---|------|---------|
| 1 | Update tabel "Urutan eksekusi" plan.md (B/C/D) | Hindari menjual murah; reviewer baca bagian akhir |
| 2 | Fix `.env.example` komentar default GUARDRAIL_MODEL | Kontradiksi langsung kelihatan |
| 3 | Koreksi wording Tier 2 plan.md + "skip" devlog | Presisi |
| 4 | Cek drift README | Etalase |

---
---

# Evaluasi #4 — 2026-06-23 (Telegram Bot, Arah E1–E4)

Evaluasi `telegram_bot.py` (E1 read-only, E2 rate-limit, E3 CSV write, E4 report+chart). Klaim
plan.md & devlog di-cross-check ke kode. Branch saat ini: `DataGen` (ada commit "Rebrand to DataGen").

## Verdict

**Implementasi rapi; dokumentasi jujur (tidak overclaim).** plan.md:334 & devlog:91 dua-duanya
tulis eksplisit "Allowlist chat_id di-skip (bot terbuka untuk semua)". Justru kejujuran itu
mengungkap 1 gap keamanan nyata: write dirilis tanpa access control, melanggar aturan sequencing
project sendiri.

### Terverifikasi bagus ✅
- **Reuse `Agent.chat()`** — multi-channel terbukti, bot hanya adapter
- **Async hygiene benar** — semua call blocking (chat, report, classify, insert, PNG) via
  `asyncio.to_thread`; event loop tak beku
- **Human-in-the-loop dipertahankan** — inline keyboard ✅/❌ untuk write (E3)
- **Guardrail ikut** — `classify_data` bawa `check_input` (CSV), `agent.chat` bawa
  `check_input_with_llm`
- Token dari env (tak hardcoded), rate-limit, HTML formatting + auto-split pesan
- `.env.example` + README terdokumentasi; E1–E4 fungsional; 284 test tetap hijau

## Temuan (urut prioritas)

### 🔴 1. Write di bot terbuka tanpa access control — melanggar aturan project sendiri
plan.md:338 mengatur eksplisit "Write di bot (E3) hanya setelah access-control (E2) beres".
Kenyataan: **E2 allowlist di-skip, E3 write tetap dirilis.** Jika `WRITE_DATABASE_URL` di-set,
siapa pun yang menemukan bot bisa upload CSV → tap "✅ Simpan" → menulis ke DB asli. Confidence
≥80% + tombol konfirmasi TIDAK menahan user tak-berwenang (Telegram bot discoverable, konfirmasi
1 tap). Pola sama dengan eksposur write demo publik — lebih terbuka.
- [ ] **Fix:** `TELEGRAM_ALLOWED_CHAT_IDS` (env, comma-separated) dicek di `handle_document` +
  `handle_callback` (minimal jalur write). ATAU biarkan `WRITE_DATABASE_URL` unset di bot publik.

### 🟡 2. `telegram_bot.py` tidak punya test sama sekali
Modul lain tes ketat (284 test), bot nol. Helper murni mudah dites tanpa API Telegram: `_md_to_html`,
`_split`, `_is_rate_limited`, `_rows_to_png`. Untuk portfolio, satu modul tanpa test mencolok.
- [ ] Tulis `tests/test_telegram_bot.py` (~15 test untuk helper murni).

### 🟡 3. Minor
- **Memory leak bot always-on:** `_sessions` & `_pending_insert` tumbuh tanpa eviction; tiap
  `chat_id` → satu `Agent` history unbounded. Perlu TTL/eviction untuk 24/7.
- **Bot asumsikan Postgres:** tak ada `set_database()`; butuh `DATABASE_URL` (SQLite lokal tanpa
  env → `get_schema()` error). Wajar untuk deploy, catat saja.
- **Chart PNG butuh `kaleido`** (tak di requirements, di-skip diam-diam kalau absen) — report tetap
  jalan tanpa chart. Sudah didokumentasikan.
- Commit "Rebrand to DataGen" — pastikan judul README/branding konsisten (belum diverifikasi).

## Urutan rapikan
| # | Item | Catatan |
|---|------|---------|
| 1 | `TELEGRAM_ALLOWED_CHAT_IDS` allowlist (#1) | Tutup lubang write; wajib sebelum bot+write publik |
| 2 | `tests/test_telegram_bot.py` (#2) | Konsistensi coverage |
| 3 | TTL/eviction `_sessions` & `_pending_insert` (#3) | Stabilitas bot 24/7 |
| 4 | Verifikasi branding README (DataGen) | Konsistensi etalase |
