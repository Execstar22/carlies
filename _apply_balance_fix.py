"""Apply the balance fix: store initial balance + correct all existing real trades"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

INITIAL_BALANCE = 11.009238

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

# 1. Store real_starting_balance in settings
conn.execute(
    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
    ("real_starting_balance", str(INITIAL_BALANCE))
)

# 2. Fix all existing real trade balance_after values
trades = conn.execute(
    "SELECT id, pnl FROM trades WHERE trading_mode='real' AND result != 'PENDING' ORDER BY id"
).fetchall()

cum_pnl = 0
for t in trades:
    cum_pnl += t['pnl']
    correct_bal = INITIAL_BALANCE + cum_pnl
    conn.execute("UPDATE trades SET balance_after=? WHERE id=?", (correct_bal, t['id']))
    print(f"  #{t['id']} balance_after = ${correct_bal:.4f}")

conn.commit()
conn.close()
print(f"\nDone! Stored real_starting_balance = ${INITIAL_BALANCE:.6f}")
print(f"Fixed {len(trades)} trades.")
