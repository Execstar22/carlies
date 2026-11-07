"""
Fix: Restore starting balance to the value calculated at today's startup.

The user's startup log showed:
  Balance sync OK: starting=$11.1299, on-chain=$13.9601, drift=$0.0000

This was CORRECT. The post-resolve sync and fix_balance_v2 changed it to $10.71,
which shifted all balance_after values down by ~$0.42.

This script restores starting to $11.1299 so that:
- Trade #2904 (yesterday's last) shows ~$13.96 (matching on-chain at that time)
- Trade #2905 (today's first WIN) shows ~$14.20 (correct: $13.96 + $0.24)
"""
import sqlite3, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "polybots.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# The correct starting balance from this morning's startup sync
CORRECT_STARTING = 11.129928

print(f"Restoring real_starting_balance to ${CORRECT_STARTING:.6f}")
print(f"(This was the value calculated at today's startup from on-chain $13.96)\n")

# Update setting
conn.execute(
    "INSERT OR REPLACE INTO settings (key, value) VALUES ('real_starting_balance', ?)",
    (f"{CORRECT_STARTING:.6f}",)
)

# Recalculate all balance_after
trades = conn.execute(
    "SELECT id, bet_amount, result, pnl FROM trades "
    "WHERE trading_mode='real' ORDER BY id ASC"
).fetchall()

cum_pnl = 0.0
cum_pending = 0.0
for t in trades:
    if t['result'] in ('WIN', 'LOSS'):
        cum_pnl += t['pnl']
    else:
        cum_pending += t['bet_amount']
    correct_bal = CORRECT_STARTING + cum_pnl - cum_pending
    conn.execute("UPDATE trades SET balance_after = ? WHERE id = ?", (correct_bal, t['id']))

conn.commit()

# Verify key trades
print("Key trade balances:")
for tid in [2904, 2905, 2906]:
    row = conn.execute("SELECT balance_after, result, pnl FROM trades WHERE id = ?", (tid,)).fetchone()
    if row:
        print(f"  #{tid}: ${row['balance_after']:.4f} ({row['result']}, pnl=${row['pnl']:.4f})")

# Show last trade
last = conn.execute(
    "SELECT id, balance_after FROM trades WHERE trading_mode='real' ORDER BY id DESC LIMIT 1"
).fetchone()
print(f"\n  Last trade #{last['id']}: ${last['balance_after']:.4f}")

conn.close()
print("\nDONE. Balance history now starts from yesterday's correct on-chain value.")
