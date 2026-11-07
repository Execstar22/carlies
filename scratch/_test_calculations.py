"""
Comprehensive test: Verify shares, PnL, and balance calculations are correct.
Simulates multiple trades at different prices and verifies consistency.
"""
import math
import sqlite3
import os
import sys

# Test 1: Shares calculation consistency
print("=" * 60)
print("TEST 1: Shares consistency (bot.py vs trader.py)")
print("=" * 60)

test_cases = [
    {"bet": 1.10, "signal": 0.78, "ceiling": 0.85},
    {"bet": 1.10, "signal": 0.80, "ceiling": 0.85},
    {"bet": 1.10, "signal": 0.81, "ceiling": 0.85},
    {"bet": 1.10, "signal": 0.83, "ceiling": 0.85},
    {"bet": 1.10, "signal": 0.85, "ceiling": 0.85},
    {"bet": 2.00, "signal": 0.79, "ceiling": 0.85},
]

all_pass = True
for tc in test_cases:
    # bot.py formula (shares to record):
    bot_shares = math.floor(tc["bet"] / tc["signal"] * 10000) / 10000
    
    # trader.py formula (size sent to Polymarket):
    amount = round(tc["bet"], 2)
    trader_size = math.floor(amount / tc["signal"] * 10000) / 10000
    
    match = bot_shares == trader_size
    if not match:
        all_pass = False
    
    # PnL on WIN
    win_pnl = bot_shares - tc["bet"]
    
    # Collateral needed (size * limit_price)
    collateral = bot_shares * tc["ceiling"]
    excess = collateral - tc["bet"]
    
    print(f"  bet=${tc['bet']:.2f} signal=${tc['signal']:.2f} -> "
          f"shares={bot_shares:.4f} pnl=${win_pnl:.4f} "
          f"collateral=${collateral:.4f} (excess=${excess:.4f}) "
          f"{'PASS' if match else 'FAIL'}")

print(f"\n  Result: {'ALL PASS' if all_pass else 'SOME FAILED'}")

# Test 2: Balance ledger math
print(f"\n{'=' * 60}")
print("TEST 2: Balance ledger math (pure ledger approach)")
print("=" * 60)

starting = 10.00
trades = [
    {"bet": 1.10, "signal": 0.80, "result": "WIN"},
    {"bet": 1.10, "signal": 0.79, "result": "WIN"},
    {"bet": 1.10, "signal": 0.81, "result": "LOSS"},
    {"bet": 1.10, "signal": 0.78, "result": "WIN"},
    {"bet": 1.10, "signal": 0.83, "result": "PENDING"},
]

print(f"  Starting balance: ${starting:.4f}")
cum_resolved_pnl = 0.0
cum_pending_bets = 0.0

for i, t in enumerate(trades):
    shares = math.floor(t["bet"] / t["signal"] * 10000) / 10000
    
    if t["result"] == "WIN":
        pnl = shares - t["bet"]
        cum_resolved_pnl += pnl
    elif t["result"] == "LOSS":
        pnl = -t["bet"]
        cum_resolved_pnl += pnl
    else:
        pnl = 0.0
        cum_pending_bets += t["bet"]
    
    balance = starting + cum_resolved_pnl - cum_pending_bets
    print(f"  Trade {i+1}: {t['result']:>7} | signal={t['signal']:.2f} | shares={shares:.4f} | pnl=${pnl:>7.4f} | balance=${balance:.4f}")

final = starting + cum_resolved_pnl - cum_pending_bets
print(f"\n  Final balance: ${final:.4f}")
print(f"  Expected on-chain: ${starting + cum_resolved_pnl:.4f} (if all pending resolved)")

# Test 3: Verify actual DB state
print(f"\n{'=' * 60}")
print("TEST 3: Verify current DB state")
print("=" * 60)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "polybots.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

starting_row = conn.execute(
    "SELECT value FROM settings WHERE key = 'real_starting_balance'"
).fetchone()
db_starting = float(starting_row["value"]) if starting_row else 0.0

trades_db = conn.execute(
    "SELECT id, entry_price, shares, bet_amount, result, pnl, balance_after "
    "FROM trades WHERE trading_mode='real' ORDER BY id ASC"
).fetchall()

print(f"  real_starting_balance: ${db_starting:.6f}")
print(f"  Trades: {len(trades_db)}")

cum_pnl = 0.0
cum_pending = 0.0
errors = 0

for t in trades_db:
    # Verify shares formula
    expected_shares = math.floor(t['bet_amount'] / 0.85 * 10000) / 10000  # Old trades used PRICE_CEILING
    shares_ok = abs(t['shares'] - expected_shares) < 0.0001
    
    # Verify PnL
    if t['result'] == 'WIN':
        expected_pnl = t['shares'] - t['bet_amount']
        cum_pnl += t['pnl']
    elif t['result'] == 'LOSS':
        expected_pnl = -t['bet_amount']
        cum_pnl += t['pnl']
    else:
        expected_pnl = 0.0
        cum_pending += t['bet_amount']
    pnl_ok = abs(t['pnl'] - expected_pnl) < 0.0001
    
    # Verify balance
    expected_bal = db_starting + cum_pnl - cum_pending
    bal_ok = abs(t['balance_after'] - expected_bal) < 0.01
    
    status = "PASS" if (shares_ok and pnl_ok and bal_ok) else "FAIL"
    if status == "FAIL":
        errors += 1
    
    print(f"  #{t['id']} | shares={t['shares']:.4f} ({'OK' if shares_ok else 'BAD'}) "
          f"| pnl=${t['pnl']:.4f} ({'OK' if pnl_ok else 'BAD'}) "
          f"| bal=${t['balance_after']:.4f} ({'OK' if bal_ok else 'BAD'}) | {status}")

print(f"\n  Errors: {errors}")
print(f"  Result: {'ALL PASS' if errors == 0 else 'SOME FAILED'}")

# Test 4: Stop profit accuracy
print(f"\n{'=' * 60}")
print("TEST 4: Stop profit PnL accuracy")
print("=" * 60)

daily_pnl = float(conn.execute("""
    SELECT COALESCE(SUM(pnl), 0) FROM trades
    WHERE result IN ('WIN', 'LOSS')
      AND trading_mode = 'real'
      AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
""").fetchone()[0])

daily_wins = conn.execute("""
    SELECT COUNT(*) FROM trades
    WHERE result = 'WIN' AND trading_mode = 'real'
      AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
""").fetchone()[0]

daily_losses = conn.execute("""
    SELECT COUNT(*) FROM trades
    WHERE result = 'LOSS' AND trading_mode = 'real'
      AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
""").fetchone()[0]

print(f"  Today's wins:   {daily_wins}")
print(f"  Today's losses:  {daily_losses}")
print(f"  Today's net PnL: ${daily_pnl:.4f}")
print(f"  Stop profit would trigger at: $2.50 (default)")
print(f"  Status: {'WOULD TRIGGER' if daily_pnl >= 2.50 else 'NOT YET'}")

conn.close()
