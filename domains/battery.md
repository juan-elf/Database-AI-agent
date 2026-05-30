# Battery Research Domain

Pengetahuan khusus untuk analisis Li-ion battery degradation dataset.

## Glossary Istilah

| Istilah | Arti | Unit |
|---------|------|------|
| **Cycle** | Satu pasangan charge + discharge complete | count |
| **SOH** (State of Health) | Kondisi battery sebagai % dari nominal capacity (100% = baru) | % |
| **RUL** (Remaining Useful Life) | Sisa cycles sebelum End of Life | cycles |
| **EOL** (End of Life) | Konvensi: SOH = 80% | threshold |
| **Capacity** | Kapasitas energi battery saat ini | Ah (Ampere-hour) |
| **Degradation rate** | Kecepatan penurunan SOH/capacity per cycle | %/cycle atau Ah/cycle |
| **Knee point** | Cycle di mana degradasi mendadak akselerasi | cycle number |
| **C-rate** | Kecepatan charge/discharge dinormalisasi ke capacity | 1C = full charge in 1 hour |
| **Calendar aging** | Degradasi karena waktu (tanpa cycling) | per month/year |
| **Cyclic aging** | Degradasi karena pemakaian aktif | per cycle |

## Singkatan Kolom (Common Patterns)

Dataset battery sering pakai singkatan. Mapping umum:
- `chI`, `chV`, `chT` → charge current/voltage/temperature
- `disI`, `disV`, `disT` → discharge current/voltage/temperature
- `BCt`, `Cap` → battery capacity total
- `IR`, `Ri` → internal resistance

## Konvensi & Default

Saat menjawab pertanyaan tentang battery, gunakan konvensi ini kecuali user minta sebaliknya:

1. **EOL threshold default = SOH 80%**. Untuk pertanyaan "kapan EOL", cari cycle pertama di mana `soh < 80`.
2. **Degradation rate** dihitung sebagai `(SOH_awal - SOH_akhir) / (cycle_akhir - cycle_awal)` dalam %/cycle.
3. **Perbandingan antar battery**: SELALU pakai `GROUP BY battery_id`.
4. **Sertakan unit** pada setiap angka: Ah, °C, V, A, %, cycles.
5. **Format angka teknis**: 2-4 desimal untuk readability (misal 1.8523 Ah, bukan 1.85229876).

## Common Patterns / Example Queries

### EOL Detection
```sql
-- Cycle pertama saat SOH turun di bawah 80% (EOL)
SELECT battery_id, MIN(cycle) AS eol_cycle
FROM <table>
WHERE soh < 80
GROUP BY battery_id;
```

### Degradation Rate per Battery
```sql
-- Rata-rata penurunan SOH per cycle
SELECT battery_id,
       ROUND((MAX(soh) - MIN(soh)) / (MAX(cycle) - MIN(cycle)), 4)
       AS soh_drop_per_cycle
FROM <table>
GROUP BY battery_id;
```

### Comparison at Specific Cycles
```sql
-- Bandingkan capacity di cycle 1 vs cycle N
SELECT battery_id,
       MAX(CASE WHEN cycle = 1   THEN capacity END) AS cap_initial,
       MAX(CASE WHEN cycle = 100 THEN capacity END) AS cap_at_100
FROM <table>
GROUP BY battery_id;
```

### Threshold Crossing Detection
```sql
-- Cycle pertama saat SOH < threshold tertentu (misal 90%)
SELECT battery_id, MIN(cycle) AS first_below_90
FROM <table>
WHERE soh < 90
GROUP BY battery_id;
```

## Common Pitfalls

❌ **JANGAN** asumsi battery di-test pada kondisi sama — kondisi sering bervariasi
   (suhu, depth of discharge, dll). Selalu cek dulu sebelum compare.

❌ **JANGAN** hitung degradation rate cuma dari 2 cycle (awal & akhir) untuk
   battery yang punya knee point — hasilnya menyesatkan. Pakai segmentasi
   sebelum & sesudah knee kalau ada.

❌ **JANGAN** ambil SOH apa adanya tanpa cek anomaly — kadang ada outlier
   karena measurement error.

✅ **DO** sertakan satuan & konteks dataset di jawaban (misal: "berdasarkan
   data B5 di dataset NASA-style").

✅ **DO** gunakan web_search untuk benchmark eksternal kalau user nanya
   "apakah ini normal/typical" — bandingkan dengan industri.