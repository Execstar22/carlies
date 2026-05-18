import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(
    'SELECT id, result, pnl, balance_after, bet_amount, shares, trading_mode '
    'FROM trades WHERE trading_mode=? ORDER BY id', ('real',)
).fetchall()
for r in rows:
    print(f'#{r["id"]} {r["result"]:7s} pnl={r["pnl"]:+.4f} bal_after={r["balance_after"]:.4f} bet={r["bet_amount"]}')
conn.close()
