# E-commerce Domain

Pengetahuan khusus untuk analisis data e-commerce / retail / marketplace.

## Glossary Istilah

| Istilah | Arti |
|---------|------|
| **Revenue** | Total pendapatan dari order yang completed |
| **GMV** (Gross Merchandise Value) | Total nilai transaksi termasuk cancelled/refund |
| **AOV** (Average Order Value) | Rata-rata nilai per order |
| **CAC** (Customer Acquisition Cost) | Biaya akuisisi per customer baru |
| **LTV** (Lifetime Value) | Total pembelian customer sepanjang waktu |
| **Conversion rate** | % visitor yang jadi buyer |
| **Cart abandonment** | Order yang dibuat tapi tidak completed |
| **SKU** (Stock Keeping Unit) | Identifier unik per produk varian |
| **Top seller** | Produk dengan total quantity/revenue tertinggi |
| **Repeat customer** | Customer dengan > 1 order completed |

## Status Order (Konvensi Umum)

Kolom `status` di tabel orders biasanya punya nilai-nilai:
- `completed` / `delivered` — order sukses, masuk revenue
- `shipped` — sedang dalam pengiriman, belum dikonfirmasi diterima
- `pending` / `processing` — belum diproses atau sedang diproses
- `cancelled` — dibatalkan (sebelum atau setelah pembayaran)
- `refunded` — sudah dikembalikan dana
- `failed` — gagal (umumnya gagal pembayaran)

**JANGAN asumsi value**. Selalu pakai `get_distinct_values` di kolom status
sebelum filter, karena setiap dataset bisa beda konvensi (misal Indonesian
marketplace pakai "selesai", US pakai "completed", dll).

## Konvensi & Default

1. **Revenue calculation default**: hanya order dengan status = 'completed'
   (atau setara — cek dulu via `get_distinct_values`).
2. **Time period default**: kalau user tidak specify, asumsi seluruh data.
   Kalau user bilang "bulan ini", clarify tanggal acuan dulu.
3. **Format mata uang Indonesia**: separator titik untuk ribuan
   (Rp 1.500.000, bukan 1500000 atau Rp 1,500,000).
4. **Top N default**: kalau user minta "top tanpa angka", pakai LIMIT 5.

## Common Patterns / Example Queries

### Top Selling Products
```sql
-- Top 5 produk terlaris (by quantity)
SELECT p.name, p.category, SUM(oi.quantity) AS total_sold
FROM order_items oi
JOIN products p ON p.id = oi.product_id
JOIN orders o ON o.id = oi.order_id
WHERE o.status = 'completed'
GROUP BY p.id
ORDER BY total_sold DESC
LIMIT 5;
```

### Revenue Per Time Period
```sql
-- Revenue per bulan
SELECT strftime('%Y-%m', order_date) AS month,
       SUM(total_amount) AS revenue
FROM orders
WHERE status = 'completed'
GROUP BY month
ORDER BY month;
```

### Customer Segmentation
```sql
-- Top 10 customer berdasarkan total pembelian
SELECT c.name, c.city,
       COUNT(DISTINCT o.id) AS num_orders,
       SUM(o.total_amount) AS lifetime_value
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE o.status = 'completed'
GROUP BY c.id
ORDER BY lifetime_value DESC
LIMIT 10;
```

### AOV (Average Order Value)
```sql
-- AOV per kategori
SELECT p.category, AVG(o.total_amount) AS aov
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
JOIN products p ON p.id = oi.product_id
WHERE o.status = 'completed'
GROUP BY p.category;
```

### Repeat Customer Rate
```sql
-- Persentase customer yang order > 1 kali
WITH customer_orders AS (
    SELECT customer_id, COUNT(*) AS num_orders
    FROM orders
    WHERE status = 'completed'
    GROUP BY customer_id
)
SELECT
    ROUND(100.0 * SUM(CASE WHEN num_orders > 1 THEN 1 ELSE 0 END)
          / COUNT(*), 2) AS repeat_rate_pct
FROM customer_orders;
```

### Geographic Analysis
```sql
-- Revenue per kota
SELECT c.city, SUM(o.total_amount) AS revenue,
       COUNT(DISTINCT o.id) AS num_orders
FROM orders o
JOIN customers c ON c.id = o.customer_id
WHERE o.status = 'completed'
GROUP BY c.city
ORDER BY revenue DESC;
```

## Common Pitfalls

❌ **JANGAN** include cancelled/refunded di revenue calculation kecuali
   user explicitly minta GMV.

❌ **JANGAN** asumsi nama kolom — sering bervariasi:
   - `total_amount` vs `grand_total` vs `total_price`
   - `order_date` vs `created_at` vs `purchase_date`
   - `customer_id` vs `user_id` vs `buyer_id`

   Selalu cek schema dulu.

❌ **JANGAN** lupa filter status saat hitung average — order cancelled
   bisa skew angka.

❌ **JANGAN** count `orders` tanpa DISTINCT kalau join dengan `order_items`
   (akan double-count).

✅ **DO** sertakan unit/currency (Rp untuk Indonesia, $ untuk US, dll).

✅ **DO** clarify time period kalau user tidak specify ("bulan ini" itu
   relatif terhadap apa?).

✅ **DO** gunakan window functions untuk perhitungan running total atau
   period-over-period comparison saat relevant.