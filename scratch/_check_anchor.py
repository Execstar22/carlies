import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "polybots.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== ALL SETTINGS ===")
rows = conn.execute("SELECT * FROM settings").fetchall()
for r in rows:
    print(f"  {r['key']} = {r['value']}")

print("\n=== KEY ANCHOR SETTINGS ===")
for key in ["real_daily_start_balance", "real_daily_start_date", "real_starting_balance",
            "stop_profit_enabled", "stop_profit_target", "stop_profit_reached_date",
            "scheduler_enabled", "trading_mode"]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    val = row["value"] if row else "(not set)"
    print(f"  {key} = {val}")

print("\n=== TODAY'S TRADES (WIB) ===")
trades = conn.execute("""
    SELECT id, timestamp, market_slug, side, entry_price, shares, bet_amount, 
           result, pnl, balance_after, trading_mode
    FROM trades 
    WHERE date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
    ORDER BY id DESC
    LIMIT 20
""").fetchall()
for t in trades:
    print(f"  #{t['id']} {t['timestamp'][:19]} {t['side']:4s} @ {t['entry_price']:.2f} "
          f"bet=${t['bet_amount']:.2f} result={t['result']:7s} pnl=${t['pnl']:.4f} "
          f"bal_after=${t['balance_after']:.4f} mode={t['trading_mode']}")

print(f"\n  Total today trades: {len(trades)}")

# Count wins/losses today
wl = conn.execute("""
    SELECT result, COUNT(*) as cnt, COALESCE(SUM(pnl), 0) as total_pnl
    FROM trades
    WHERE date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
      AND trading_mode IN ('real', 'shadow')
    GROUP BY result
""").fetchall()
for r in wl:
    print(f"  {r['result']}: {r['cnt']} trades, PnL: ${r['total_pnl']:.4f}")

conn.close()
