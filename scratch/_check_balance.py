"""
Diagnose balance drift between on-chain wallet and DB ledger.

Key insight: The bot records `shares = bet_amount / signal_price` using the
SIGNAL price (e.g. 0.80), but the actual FOK order is sent at PRICE_CEILING
(e.g. 0.85). The real fill price could differ from the signal price.

If the real fill uses MORE money than bet_amount (due to slippage/fees),
or if the shares received are FEWER than calculated, the ledger will
overestimate the balance.
"""
import sqlite3

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

# All real trades
trades = conn.execute(
    "SELECT id, side, entry_price, shares, bet_amount, result, pnl, balance_after "
    "FROM trades WHERE trading_mode='real' ORDER BY id ASC"
).fetchall()

starting_row = conn.execute("SELECT value FROM settings WHERE key='real_starting_balance'").fetchone()
starting = float(starting_row['value']) if starting_row else 0.0

print(f"real_starting_balance = {starting:.6f}")
print(f"Total real trades: {len(trades)}")
print()

# Recalculate expected balance step by step
running_balance = starting
print(f"{'ID':>5} {'Side':>4} {'Result':>6} {'EntryP':>7} {'Shares':>8} {'Bet':>7} {'PnL':>8} {'DB_Bal':>10} {'Calc_Bal':>10} {'Drift':>8}")
print("-" * 95)

total_drift = 0
for t in trades:
    if t['result'] == 'WIN':
        # Payout = shares * $1 (binary option)
        payout = t['shares']
        expected_pnl = payout - t['bet_amount']
        running_balance += expected_pnl
    elif t['result'] == 'LOSS':
        running_balance -= t['bet_amount']
    else:  # PENDING
        running_balance -= t['bet_amount']

    drift = t['balance_after'] - running_balance
    
    print(
        f"{t['id']:>5} {t['side']:>4} {t['result']:>6} "
        f"{t['entry_price']:>7.4f} {t['shares']:>8.4f} {t['bet_amount']:>7.4f} "
        f"{t['pnl']:>8.4f} {t['balance_after']:>10.4f} {running_balance:>10.4f} {drift:>8.4f}"
    )

print()
print(f"Final calculated balance: {running_balance:.6f}")

# Now check: what if shares were actually different on-chain?
# The key formula: shares = bet_amount / entry_price
# But in the bot code (line 696-698), it does:
#   shares = (bet_amount / signal_price), rounded down to 4 decimals
# The actual fill might be at a different price
print()
print("=== CHECKING SHARES CALCULATION ===")
for t in trades:
    expected_shares = round(t['bet_amount'] / t['entry_price'], 4)
    actual_shares = t['shares']
    if abs(expected_shares - actual_shares) > 0.0001:
        print(f"  Trade {t['id']}: expected_shares={expected_shares:.4f}, db_shares={actual_shares:.4f}, DIFF={expected_shares-actual_shares:.4f}")

# Check if there's a Polygon gas fee issue
# Real trades on Polymarket use USDC.e (pUSD) on Polygon
# Orders go through the CLOB - no gas from balance, but there could be
# protocol fees that aren't tracked

print()
print("=== SUMMARY ===")
total_bets = sum(t['bet_amount'] for t in trades)
total_pnl = sum(t['pnl'] for t in trades if t['result'] != 'PENDING')
wins = [t for t in trades if t['result'] == 'WIN']
losses = [t for t in trades if t['result'] == 'LOSS']

print(f"Total trades: {len(trades)} (W:{len(wins)} L:{len(losses)})")
print(f"Total bet amount: {total_bets:.6f}")
print(f"Total PnL: {total_pnl:.6f}")
print(f"Starting + PnL = {starting + total_pnl:.6f} (should = on-chain wallet)")
print()

# The REAL issue: protocol fees
# Polymarket charges a ~2% fee on winnings that the bot doesn't account for
for t in wins:
    payout = t['shares']  # This is what the bot ASSUMES it gets back
    gross_profit = payout - t['bet_amount']
    # Polymarket takes ~2% of profit (not of payout)
    fee_estimate = gross_profit * 0.02
    print(f"  Trade {t['id']}: WIN payout=${payout:.4f}, profit=${gross_profit:.4f}, est_fee=${fee_estimate:.4f}")

total_fees = sum((t['shares'] - t['bet_amount']) * 0.02 for t in wins)
print(f"\nEstimated total Polymarket fees (2% of profit): ${total_fees:.6f}")
print(f"Ledger balance: {starting + total_pnl:.6f}")
print(f"Ledger - fees = {starting + total_pnl - total_fees:.6f}")

conn.close()
