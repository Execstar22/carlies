"""
Deep investigation of real trade balance issues.
Checks on-chain balance vs DB state to find the root cause.
"""
import sqlite3
import math
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "polybots.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 1. Check real_starting_balance setting
row = conn.execute("SELECT value FROM settings WHERE key = 'real_starting_balance'").fetchone()
starting = float(row["value"]) if row else 0.0
print(f"=== DB Settings ===")
print(f"real_starting_balance: ${starting:.6f}")

# 2. Fetch actual on-chain pUSD balance
print(f"\n=== On-Chain Balance ===")
try:
    import requests
    rpc_url = os.environ.get("POLYGON_RPC_URL", "").strip()
    proxy_addr = os.environ.get("POLY_PROXY_ADDRESS", "").strip()
    pusd_contract = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    data = "0x70a08231000000000000000000000000" + proxy_addr[2:].lower()
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pusd_contract, "data": data}, "latest"], "id": 1}
    resp = requests.post(rpc_url, json=payload, timeout=10).json()
    on_chain = int(resp["result"], 16) / 1e6
    print(f"On-chain pUSD: ${on_chain:.6f}")
except Exception as e:
    print(f"Failed to fetch on-chain: {e}")
    on_chain = None

# 3. Show ALL real trades 
rows = conn.execute("""
    SELECT id, timestamp, side, entry_price, shares, bet_amount, result, pnl, balance_after,
           order_id, resolved_at, claimed
    FROM trades WHERE trading_mode = 'real' ORDER BY id ASC
""").fetchall()

print(f"\n=== All Real Trades ({len(rows)}) ===")
total_bets = 0
total_resolved_pnl = 0
total_pending_bets = 0
total_win_payouts = 0

for r in rows:
    total_bets += r['bet_amount']
    if r['result'] == 'PENDING':
        total_pending_bets += r['bet_amount']
    else:
        total_resolved_pnl += r['pnl']
        if r['result'] == 'WIN':
            total_win_payouts += r['shares']  # payout = shares * $1
    
    print(f"  #{r['id']} | {r['timestamp'][:19]} | {r['side']:>4} @ {r['entry_price']:.4f} "
          f"| shrs={r['shares']:.4f} | bet=${r['bet_amount']:.2f} | {r['result']:>7} "
          f"| pnl=${r['pnl']:.4f} | bal=${r['balance_after']:.4f} "
          f"| resolved={r['resolved_at'] or 'N/A'}")

print(f"\n=== Summary ===")
print(f"Total bets placed:        ${total_bets:.4f}")
print(f"Total WIN payouts:        ${total_win_payouts:.4f}")  
print(f"Total resolved PnL:       ${total_resolved_pnl:.4f}")
print(f"Total pending bets:       ${total_pending_bets:.4f}")

if on_chain is not None:
    # Back-calculate what starting balance SHOULD be
    # Formula: on_chain = starting + resolved_pnl - pending_bets
    # Therefore: starting = on_chain - resolved_pnl + pending_bets
    correct_starting = on_chain - total_resolved_pnl + total_pending_bets
    print(f"\n=== Back-Calculated Starting Balance ===")
    print(f"Formula: on_chain - resolved_pnl + pending_bets")
    print(f"  = ${on_chain:.4f} - ${total_resolved_pnl:.4f} + ${total_pending_bets:.4f}")
    print(f"  = ${correct_starting:.6f}")
    print(f"\nCurrent DB setting:   ${starting:.6f}")
    print(f"Should be:            ${correct_starting:.6f}")
    print(f"Difference:           ${correct_starting - starting:.6f}")

    # Verify: with correct starting, what should each trade's balance_after be?
    print(f"\n=== Recalculated Balances (using correct starting) ===")
    cum_pnl = 0
    cum_pending = 0
    for r in rows:
        if r['result'] in ('WIN', 'LOSS'):
            cum_pnl += r['pnl']
        else:
            cum_pending += r['bet_amount']
        correct_bal = correct_starting + cum_pnl - cum_pending
        diff = correct_bal - r['balance_after']
        mark = " <-- WRONG" if abs(diff) > 0.01 else ""
        print(f"  #{r['id']} | current=${r['balance_after']:.4f} | correct=${correct_bal:.4f} | diff=${diff:.4f}{mark}")
    
    print(f"\nFinal ledger balance: ${correct_starting + cum_pnl - cum_pending:.4f}")
    print(f"On-chain balance:     ${on_chain:.4f}")
    print(f"Match: {'YES' if abs(correct_starting + cum_pnl - cum_pending - on_chain) < 0.01 else 'NO'}")

conn.close()
