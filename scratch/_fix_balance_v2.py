"""
Fix #2: Resync starting balance from on-chain and recalculate ALL balance_after.

Problem found:
- real_starting_balance = $10.1769 but on-chain says it should be $11.1299
- Old trades (#2875-2887) still have $0.07 offset from previous fix
- This causes the entire balance chain to be off by ~$0.95

Fix:
1. Back-calculate correct starting balance from on-chain
2. Recalculate balance_after for ALL real trades
3. Update real_starting_balance setting
"""
import sqlite3
import math
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "polybots.db")

# Step 1: Fetch on-chain balance
print("STEP 1: Fetching on-chain balance...")
rpc_url = os.environ.get("POLYGON_RPC_URL", "").strip()
proxy_addr = os.environ.get("POLY_PROXY_ADDRESS", "").strip()
pusd_contract = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
data_hex = "0x70a08231000000000000000000000000" + proxy_addr[2:].lower()
payload = {"jsonrpc": "2.0", "method": "eth_call",
           "params": [{"to": pusd_contract, "data": data_hex}, "latest"], "id": 1}
resp = requests.post(rpc_url, json=payload, timeout=10).json()
on_chain = int(resp["result"], 16) / 1e6
print(f"  On-chain pUSD: ${on_chain:.6f}")

# Step 2: Load all real trades
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
trades = conn.execute(
    "SELECT id, bet_amount, result, pnl, shares, balance_after "
    "FROM trades WHERE trading_mode = 'real' ORDER BY id ASC"
).fetchall()
print(f"\nSTEP 2: Found {len(trades)} real trades")

# Step 3: Calculate total resolved PnL and pending bets
total_resolved_pnl = 0.0
total_pending_bets = 0.0
for t in trades:
    if t['result'] in ('WIN', 'LOSS'):
        total_resolved_pnl += t['pnl']
    elif t['result'] == 'PENDING':
        total_pending_bets += t['bet_amount']

# Step 4: Back-calculate correct starting balance
correct_starting = on_chain - total_resolved_pnl + total_pending_bets
old_starting = float(conn.execute(
    "SELECT value FROM settings WHERE key = 'real_starting_balance'"
).fetchone()["value"])

print(f"\nSTEP 3: Starting balance calculation")
print(f"  Old starting:     ${old_starting:.6f}")
print(f"  Correct starting: ${correct_starting:.6f}")
print(f"  Drift:            ${old_starting - correct_starting:.6f}")

# Step 5: Recalculate all balance_after
print(f"\nSTEP 4: Recalculating all balance_after...")
cum_pnl = 0.0
cum_pending = 0.0
updates = []

for t in trades:
    if t['result'] in ('WIN', 'LOSS'):
        cum_pnl += t['pnl']
    else:
        cum_pending += t['bet_amount']
    
    new_bal = correct_starting + cum_pnl - cum_pending
    old_bal = t['balance_after']
    diff = abs(new_bal - old_bal)
    
    if diff > 0.001:
        updates.append((new_bal, t['id']))
        print(f"  #{t['id']}: ${old_bal:.4f} -> ${new_bal:.4f} (diff=${diff:.4f})")

print(f"\n  {len(updates)} trades need balance update")

# Step 6: Apply
print(f"\nSTEP 5: Applying fixes...")
conn.execute(
    "INSERT OR REPLACE INTO settings (key, value) VALUES ('real_starting_balance', ?)",
    (f"{correct_starting:.6f}",)
)
for new_bal, tid in updates:
    conn.execute("UPDATE trades SET balance_after = ? WHERE id = ?", (new_bal, tid))
conn.commit()
print(f"  Updated real_starting_balance: ${correct_starting:.6f}")
print(f"  Updated {len(updates)} trade balances")

# Step 7: Verify
print(f"\nSTEP 6: Verification...")
trades2 = conn.execute(
    "SELECT id, result, pnl, balance_after FROM trades WHERE trading_mode='real' ORDER BY id ASC"
).fetchall()
cum_pnl2 = 0.0
cum_pending2 = 0.0
errors = 0
for t in trades2:
    if t['result'] in ('WIN', 'LOSS'):
        cum_pnl2 += t['pnl']
    else:
        cum_pending2 += t['bet_amount']
    expected = correct_starting + cum_pnl2 - cum_pending2
    if abs(t['balance_after'] - expected) > 0.001:
        errors += 1

final_bal = correct_starting + cum_pnl2 - cum_pending2
print(f"  Final ledger balance: ${final_bal:.6f}")
print(f"  On-chain balance:     ${on_chain:.6f}")
print(f"  Match: {'YES' if abs(final_bal - on_chain) < 0.01 else 'NO'}")
print(f"  Balance errors: {errors}")

# Check stop profit
daily_pnl = float(conn.execute("""
    SELECT COALESCE(SUM(pnl), 0) FROM trades
    WHERE result IN ('WIN', 'LOSS')
      AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
""").fetchone()[0])
print(f"\n  Today's PnL: ${daily_pnl:.4f}")
print(f"  Stop profit target: $1.00")
print(f"  Would trigger: {'YES' if daily_pnl >= 1.0 else 'NO'}")

conn.close()
print(f"\nDONE.")
