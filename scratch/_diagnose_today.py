"""
Diagnose today's trade history vs real balance mismatch.
Check: shares, PnL, balance_after, and stop profit trigger logic.
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

# 1. Settings
print("=== SETTINGS ===")
for key in ['real_starting_balance', 'stop_profit_enabled', 'stop_profit_target', 'real_bet_amount']:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    val = row["value"] if row else "NOT SET"
    print(f"  {key}: {val}")

starting = float(conn.execute(
    "SELECT value FROM settings WHERE key = 'real_starting_balance'"
).fetchone()["value"])

# 2. On-chain balance
print(f"\n=== ON-CHAIN BALANCE ===")
try:
    import requests
    rpc_url = os.environ.get("POLYGON_RPC_URL", "").strip()
    proxy_addr = os.environ.get("POLY_PROXY_ADDRESS", "").strip()
    pusd_contract = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    data_hex = "0x70a08231000000000000000000000000" + proxy_addr[2:].lower()
    payload = {"jsonrpc": "2.0", "method": "eth_call",
               "params": [{"to": pusd_contract, "data": data_hex}, "latest"], "id": 1}
    resp = requests.post(rpc_url, json=payload, timeout=10).json()
    on_chain = int(resp["result"], 16) / 1e6
    print(f"  On-chain pUSD: ${on_chain:.6f}")
except Exception as e:
    print(f"  Failed: {e}")
    on_chain = None

# 3. ALL real trades today (WIB = UTC+7)
print(f"\n=== TODAY'S REAL TRADES (WIB) ===")
today_trades = conn.execute("""
    SELECT id, timestamp, side, entry_price, shares, bet_amount, result, pnl, balance_after, trading_mode
    FROM trades
    WHERE trading_mode = 'real'
      AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
    ORDER BY id ASC
""").fetchall()

print(f"  Found {len(today_trades)} real trades today\n")
print(f"  {'ID':>4} | {'Time':>8} | {'Side':>4} | {'Entry':>6} | {'Shares':>8} | {'Bet':>6} | {'Result':>7} | {'PnL':>8} | {'BalAfter':>10}")
print(f"  " + "-" * 90)

total_pnl_today = 0.0
total_pending_today = 0.0
issues = []

for t in today_trades:
    ts_short = t['timestamp'][11:19] if t['timestamp'] else "?"
    
    # Check shares calculation
    expected_shares = math.floor(t['bet_amount'] / t['entry_price'] * 10000) / 10000
    shares_ok = abs(t['shares'] - expected_shares) < 0.0001
    
    # Check PnL
    if t['result'] == 'WIN':
        expected_pnl = t['shares'] - t['bet_amount']
        total_pnl_today += t['pnl']
    elif t['result'] == 'LOSS':
        expected_pnl = -t['bet_amount']
        total_pnl_today += t['pnl']
    else:
        expected_pnl = 0.0
        total_pending_today += t['bet_amount']
    
    pnl_ok = abs(t['pnl'] - expected_pnl) < 0.0001
    
    flag = ""
    if not shares_ok:
        flag += " [SHARES!]"
        issues.append(f"#{t['id']}: shares={t['shares']:.4f} expected={expected_shares:.4f} (entry={t['entry_price']})")
    if not pnl_ok:
        flag += " [PNL!]"
        issues.append(f"#{t['id']}: pnl={t['pnl']:.4f} expected={expected_pnl:.4f}")
    
    print(f"  {t['id']:>4} | {ts_short:>8} | {t['side']:>4} | {t['entry_price']:>6.4f} | {t['shares']:>8.4f} | {t['bet_amount']:>6.2f} | {t['result']:>7} | {t['pnl']:>8.4f} | {t['balance_after']:>10.4f}{flag}")

# 4. Balance chain verification
print(f"\n=== BALANCE VERIFICATION ===")
all_real = conn.execute("""
    SELECT id, bet_amount, result, pnl, balance_after
    FROM trades WHERE trading_mode = 'real' ORDER BY id ASC
""").fetchall()

cum_pnl = 0.0
cum_pending = 0.0
for t in all_real:
    if t['result'] in ('WIN', 'LOSS'):
        cum_pnl += t['pnl']
    else:
        cum_pending += t['bet_amount']

expected_final = starting + cum_pnl - cum_pending
print(f"  real_starting_balance:  ${starting:.6f}")
print(f"  Total resolved PnL:    ${cum_pnl:.6f}")
print(f"  Total pending bets:    ${cum_pending:.6f}")
print(f"  Expected final balance: ${expected_final:.6f}")
if all_real:
    last_bal = all_real[-1]['balance_after']
    print(f"  Last trade balance_after: ${last_bal:.6f}")
    print(f"  Ledger drift:          ${last_bal - expected_final:.6f}")
if on_chain:
    print(f"  On-chain balance:      ${on_chain:.6f}")
    correct_starting = on_chain - cum_pnl + cum_pending
    print(f"  Correct starting (from on-chain): ${correct_starting:.6f}")
    print(f"  Starting balance drift: ${starting - correct_starting:.6f}")

# 5. Stop profit check
print(f"\n=== STOP PROFIT STATUS ===")
daily_pnl_db = float(conn.execute("""
    SELECT COALESCE(SUM(pnl), 0) FROM trades
    WHERE result IN ('WIN', 'LOSS')
      AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
""").fetchone()[0])

sp_target = float(conn.execute(
    "SELECT value FROM settings WHERE key = 'stop_profit_target'"
).fetchone()["value"])

print(f"  Daily PnL (DB query):  ${daily_pnl_db:.6f}")
print(f"  Stop profit target:    ${sp_target:.4f}")
print(f"  Would trigger:         {'YES' if daily_pnl_db >= sp_target else 'NO'}")
print(f"  Margin:                ${daily_pnl_db - sp_target:.4f}")

# 6. Issues summary
if issues:
    print(f"\n=== ISSUES FOUND ===")
    for iss in issues:
        print(f"  - {iss}")
else:
    print(f"\n=== NO CALCULATION ISSUES FOUND ===")

# 7. Per-trade balance recalculation
print(f"\n=== BALANCE RECALCULATION (all real trades) ===")
cum_pnl2 = 0.0
cum_pending2 = 0.0
balance_issues = 0
for t in all_real:
    if t['result'] in ('WIN', 'LOSS'):
        cum_pnl2 += t['pnl']
    else:
        cum_pending2 += t['bet_amount']
    correct_bal = starting + cum_pnl2 - cum_pending2
    diff = t['balance_after'] - correct_bal
    flag = " <-- MISMATCH" if abs(diff) > 0.01 else ""
    if flag:
        balance_issues += 1
    print(f"  #{t['id']:>4} | recorded=${t['balance_after']:>10.4f} | correct={correct_bal:>10.4f} | diff={diff:>8.4f}{flag}")

print(f"\n  Balance mismatches: {balance_issues}")

conn.close()
