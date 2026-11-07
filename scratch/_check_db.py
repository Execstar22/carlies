import sqlite3

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

print("=== TRADES TABLE ===")
cnt = conn.execute("SELECT COUNT(*) as cnt FROM trades").fetchone()["cnt"]
print(f"Total trades: {cnt}")

if cnt > 0:
    rows = conn.execute(
        "SELECT trading_mode, result, COUNT(*) as cnt, COALESCE(SUM(pnl),0) as total_pnl "
        "FROM trades GROUP BY trading_mode, result"
    ).fetchall()
    for r in rows:
        print(f"  Mode={r['trading_mode']}, Result={r['result']}, Count={r['cnt']}, PnL={r['total_pnl']:.4f}")

    print("\n=== SAMPLE TRADES (last 5) ===")
    samples = conn.execute("SELECT id, timestamp, market_slug, side, result, pnl, trading_mode FROM trades ORDER BY id DESC LIMIT 5").fetchall()
    for s in samples:
        print(f"  #{s['id']} | {s['timestamp']} | {s['trading_mode']} | {s['side']} | {s['result']} | PnL=${s['pnl']:.4f}")

print("\n=== TODAY PnL (get_daily_profit_total equivalent) ===")
row = conn.execute(
    "SELECT COALESCE(SUM(pnl), 0) as net_pnl FROM trades "
    "WHERE result IN ('WIN', 'LOSS') "
    "AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')"
).fetchone()
print(f"Daily net PnL (all modes): ${row['net_pnl']:.4f}")

print("\n=== TODAY PnL BY MODE ===")
rows2 = conn.execute(
    "SELECT trading_mode, COALESCE(SUM(pnl), 0) as net_pnl FROM trades "
    "WHERE result IN ('WIN', 'LOSS') "
    "AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours') "
    "GROUP BY trading_mode"
).fetchall()
if rows2:
    for r in rows2:
        print(f"  Mode={r['trading_mode']}: ${r['net_pnl']:.4f}")
else:
    print("  (no resolved trades today)")

print("\n=== TODAY LOSS (get_daily_loss_total equivalent) ===")
row2 = conn.execute(
    "SELECT COALESCE(SUM(ABS(pnl)), 0) as total_loss FROM trades "
    "WHERE result = 'LOSS' "
    "AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')"
).fetchone()
print(f"Daily total loss (all modes): ${row2['total_loss']:.4f}")

print("\n=== SKIPPED EVENTS ===")
skip_cnt = conn.execute("SELECT COUNT(*) as cnt FROM skipped_events").fetchone()["cnt"]
print(f"Total skipped events: {skip_cnt}")

print("\n=== KEY SETTINGS ===")
for key in ['stop_profit_enabled', 'stop_profit_target', 'current_balance', 
            'shadow_balance', 'capital', 'shadow_capital', 'trading_mode',
            'bot_running', 'real_bet_amount']:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    print(f"  {key} = {row['value'] if row else '(not set)'}")

conn.close()
