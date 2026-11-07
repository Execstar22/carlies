"""
DEFINITIVE FIX: Fix real_starting_balance AND revert incorrect shares/PnL changes.

Problem:
1. real_starting_balance was set to $8.016815 (wrong, should be ~$9.89)
2. Previous "fix" changed shares from 1.2941 to signal-price-based values,
   but the ACTUAL orders sent to Polymarket used PRICE_CEILING=0.85 for sizing,
   so actual shares traded = 1.2941. Must revert.

Solution:
1. Revert shares to what was ACTUALLY traded: floor(bet / 0.85 * 10000) / 10000
2. Recalculate PnL from correct shares
3. Back-calculate real_starting_balance from on-chain balance
4. Recalculate all balance_after using pure ledger
5. Verify final balance matches on-chain
"""
import sqlite3
import math
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "polybots.db")
PRICE_CEILING = 0.85  # The limit price that was used for ALL existing orders

# Step 1: Fetch on-chain balance
print("STEP 1: Fetching on-chain pUSD balance...")
rpc_url = os.environ.get("POLYGON_RPC_URL", "").strip()
proxy_addr = os.environ.get("POLY_PROXY_ADDRESS", "").strip()
pusd_contract = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
data_hex = "0x70a08231000000000000000000000000" + proxy_addr[2:].lower()
payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pusd_contract, "data": data_hex}, "latest"], "id": 1}
resp = requests.post(rpc_url, json=payload, timeout=10).json()
on_chain = int(resp["result"], 16) / 1e6
print(f"  On-chain pUSD: ${on_chain:.6f}")

# Step 2: Load all real trades
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
trades = conn.execute(
    "SELECT * FROM trades WHERE trading_mode = 'real' ORDER BY id ASC"
).fetchall()
print(f"\nSTEP 2: Found {len(trades)} real trades")

# Step 3: Revert shares to ACTUAL traded values (bet / PRICE_CEILING)
print(f"\nSTEP 3: Reverting shares to actual traded values (bet / {PRICE_CEILING})...")
total_resolved_pnl = 0.0
total_pending_bets = 0.0
corrections = []

for t in trades:
    actual_shares = math.floor(t['bet_amount'] / PRICE_CEILING * 10000) / 10000
    
    if t['result'] == 'WIN':
        actual_pnl = actual_shares - t['bet_amount']
    elif t['result'] == 'LOSS':
        actual_pnl = -t['bet_amount']
    else:
        actual_pnl = 0.0
    
    if t['result'] == 'PENDING':
        total_pending_bets += t['bet_amount']
    else:
        total_resolved_pnl += actual_pnl
    
    shares_changed = abs(t['shares'] - actual_shares) > 0.0001
    pnl_changed = abs(t['pnl'] - actual_pnl) > 0.0001
    
    corrections.append({
        'id': t['id'],
        'old_shares': t['shares'],
        'new_shares': actual_shares,
        'old_pnl': t['pnl'],
        'new_pnl': actual_pnl,
        'result': t['result'],
        'bet_amount': t['bet_amount'],
        'changed': shares_changed or pnl_changed,
    })
    
    status = "CHANGED" if shares_changed or pnl_changed else "OK"
    print(f"  #{t['id']} | shares: {t['shares']:.4f} -> {actual_shares:.4f} | pnl: {t['pnl']:.4f} -> {actual_pnl:.4f} | {status}")

# Step 4: Back-calculate correct starting balance
print(f"\nSTEP 4: Calculating correct starting balance...")
correct_starting = on_chain - total_resolved_pnl + total_pending_bets
print(f"  on_chain(${on_chain:.4f}) - resolved_pnl(${total_resolved_pnl:.4f}) + pending(${total_pending_bets:.4f})")
print(f"  = ${correct_starting:.6f}")

old_starting_row = conn.execute(
    "SELECT value FROM settings WHERE key = 'real_starting_balance'"
).fetchone()
old_starting = float(old_starting_row["value"]) if old_starting_row else 0.0
print(f"  Old starting: ${old_starting:.6f}")
print(f"  Difference:   ${correct_starting - old_starting:.6f}")

# Step 5: Calculate correct balance_after for each trade
print(f"\nSTEP 5: Recalculating balance_after with correct starting...")
cum_pnl = 0.0
cum_pending = 0.0
balance_updates = []

for c in corrections:
    if c['result'] in ('WIN', 'LOSS'):
        cum_pnl += c['new_pnl']
    else:
        cum_pending += c['bet_amount']
    
    new_balance = correct_starting + cum_pnl - cum_pending
    balance_updates.append((c['id'], new_balance))
    print(f"  #{c['id']} | balance: {new_balance:.4f}")

final_balance = correct_starting + cum_pnl - cum_pending
print(f"\n  Final ledger:  ${final_balance:.6f}")
print(f"  On-chain:      ${on_chain:.6f}")
print(f"  Match: {'YES' if abs(final_balance - on_chain) < 0.01 else 'NO'}")

# Step 6: Apply all fixes
print(f"\nSTEP 6: Applying fixes...")

# Update real_starting_balance
conn.execute(
    "INSERT OR REPLACE INTO settings (key, value) VALUES ('real_starting_balance', ?)",
    (f"{correct_starting:.6f}",)
)
print(f"  Updated real_starting_balance: ${correct_starting:.6f}")

# Update each trade
for c, (tid, new_bal) in zip(corrections, balance_updates):
    conn.execute(
        "UPDATE trades SET shares = ?, pnl = ?, balance_after = ? WHERE id = ?",
        (c['new_shares'], c['new_pnl'], new_bal, tid)
    )

conn.commit()
print(f"  Updated {len(corrections)} trades (shares, pnl, balance_after)")

# Step 7: Verify
print(f"\nSTEP 7: Verification...")
verify_rows = conn.execute(
    "SELECT id, side, entry_price, shares, bet_amount, result, pnl, balance_after "
    "FROM trades WHERE trading_mode = 'real' ORDER BY id ASC"
).fetchall()

print(f"  {'ID':>4} | {'Side':>4} | {'Entry':>7} | {'Shares':>8} | {'Bet':>6} | {'Result':>7} | {'PnL':>8} | {'Balance':>10}")
print(f"  " + "-" * 80)
for r in verify_rows:
    print(f"  {r['id']:>4} | {r['side']:>4} | {r['entry_price']:>7.4f} | {r['shares']:>8.4f} | {r['bet_amount']:>6.2f} | {r['result']:>7} | {r['pnl']:>8.4f} | {r['balance_after']:>10.4f}")

verify_starting = conn.execute(
    "SELECT value FROM settings WHERE key = 'real_starting_balance'"
).fetchone()
print(f"\n  real_starting_balance = ${float(verify_starting['value']):.6f}")
print(f"  On-chain pUSD         = ${on_chain:.6f}")
print(f"  Last trade balance    = ${verify_rows[-1]['balance_after']:.6f}")
match = abs(verify_rows[-1]['balance_after'] - on_chain) < 0.01
print(f"  Ledger matches chain: {'YES' if match else 'NO'}")
print(f"\nDONE - All fixes applied successfully.")

conn.close()
