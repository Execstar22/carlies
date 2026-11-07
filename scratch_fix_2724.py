import sqlite3

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

# Get all real trades in order
trades = conn.execute(
    "SELECT id, pnl, result FROM trades WHERE trading_mode='real' AND result != 'PENDING' ORDER BY id"
).fetchall()

starting_row = conn.execute("SELECT value FROM settings WHERE key = 'real_starting_balance'").fetchone()
balance = float(starting_row["value"])

for t in trades:
    trade_id = t["id"]
    pnl = t["pnl"]
    balance += pnl
    conn.execute("UPDATE trades SET balance_after=? WHERE id=?", (balance, trade_id))
    print(f"Trade #{trade_id} corrected: balance_after = ${balance:.6f}")

conn.commit()
conn.close()
print("All real trade balances corrected.")
