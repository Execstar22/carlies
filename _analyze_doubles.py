"""Analisis double order dari database trades."""
import sqlite3
from collections import Counter

conn = sqlite3.connect("polybots.db")
conn.row_factory = sqlite3.Row

# 1. Ambil semua trade terbaru
print("=" * 80)
print("ANALISIS DOUBLE ORDER — PAGIBOTS")
print("=" * 80)

# 2. Cari double order: market_slug yang muncul lebih dari 1x di trades
print("\n📊 [1] MARKET SLUG DENGAN MULTIPLE TRADES (POTENSI DOUBLE ORDER):")
print("-" * 80)
rows = conn.execute("""
    SELECT market_slug, COUNT(*) as cnt, 
           GROUP_CONCAT(id, ', ') as trade_ids,
           GROUP_CONCAT(side, ', ') as sides,
           GROUP_CONCAT(timestamp, ' | ') as timestamps,
           GROUP_CONCAT(trading_mode, ', ') as modes
    FROM trades 
    GROUP BY market_slug 
    HAVING cnt > 1
    ORDER BY MAX(id) DESC
    LIMIT 30
""").fetchall()

if rows:
    for r in rows:
        print(f"  Slug: {r['market_slug']}")
        print(f"  Count: {r['cnt']}x | IDs: {r['trade_ids']}")
        print(f"  Sides: {r['sides']}")
        print(f"  Timestamps: {r['timestamps']}")
        print(f"  Modes: {r['modes']}")
        print()
else:
    print("  Tidak ditemukan double order.")

# 3. Cek trade yang terjadi di sekitar midnight WIB (17:00 UTC)
print("\n📊 [2] TRADES SEKITAR MIDNIGHT WIB (16:30-17:30 UTC):")
print("-" * 80)
midnight_trades = conn.execute("""
    SELECT id, timestamp, market_slug, side, entry_price, bet_amount, result, trading_mode
    FROM trades 
    WHERE (
        substr(timestamp, 12, 2) IN ('16', '17')
    )
    ORDER BY id DESC
    LIMIT 30
""").fetchall()

for r in midnight_trades:
    print(f"  #{r['id']} | {r['timestamp']} | {r['market_slug'][-20:]} | {r['side']} | ${r['bet_amount']:.2f} | {r['result']} | {r['trading_mode']}")

# 4. Cek 60 trade terakhir
print("\n📊 [3] 60 TRADE TERAKHIR:")
print("-" * 80)
recent = conn.execute("""
    SELECT id, timestamp, market_slug, side, entry_price, bet_amount, result, trading_mode
    FROM trades 
    ORDER BY id DESC
    LIMIT 60
""").fetchall()

for r in recent:
    slug_short = r['market_slug'][-25:] if len(r['market_slug']) > 25 else r['market_slug']
    print(f"  #{r['id']:4d} | {r['timestamp'][:19]} | ...{slug_short} | {r['side']:4s} | ¢{r['entry_price']*100:.1f} | ${r['bet_amount']:.2f} | {r['result']:7s} | {r['trading_mode']}")

# 5. Total stats
total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
dupes = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT market_slug FROM trades GROUP BY market_slug HAVING COUNT(*) > 1
    )
""").fetchone()[0]

print(f"\n📊 [4] RINGKASAN:")
print(f"  Total trades: {total}")
print(f"  Slugs dengan >1 trade: {dupes}")
print(f"  Rasio duplikasi: {dupes/max(total,1)*100:.1f}%")

conn.close()
