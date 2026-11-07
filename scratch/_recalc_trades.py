"""
Recalculate shares, PnL, and balance_after for all real trades in the database.
Fixes historical data that was recorded with incorrect PRICE_CEILING-based shares.

This script:
1. Recalculates shares = floor(bet_amount / entry_price * 10000) / 10000
2. Recalculates PnL for resolved trades (WIN: shares - bet, LOSS: -bet)
3. Recalculates balance_after using pure ledger approach
"""

import sqlite3
import math

DB_PATH = "polybots.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Get starting balance
starting_row = conn.execute(
    "SELECT value FROM settings WHERE key = 'real_starting_balance'"
).fetchone()
starting_balance = float(starting_row["value"]) if starting_row else 0.0
print(f"Starting balance: ${starting_balance:.6f}\n")

# Get all real trades in chronological order
rows = conn.execute("""
    SELECT id, timestamp, side, entry_price, shares, bet_amount, result, pnl, balance_after
    FROM trades 
    WHERE trading_mode = 'real'
    ORDER BY id ASC
""").fetchall()

print(f"Found {len(rows)} real trades to recalculate.\n")

# Track ledger balance
resolved_pnl_sum = 0.0
pending_bets_sum = 0.0
updates = []

print(f"{'ID':>4} | {'Side':>4} | {'Entry':>7} | {'OldShrs':>9} | {'NewShrs':>9} | {'OldPnL':>8} | {'NewPnL':>8} | {'OldBal':>10} | {'NewBal':>10} | Changes")
print("-" * 120)

for r in rows:
    trade_id = r['id']
    entry_price = r['entry_price']
    bet_amount = r['bet_amount']
    result = r['result']
    
    old_shares = r['shares']
    old_pnl = r['pnl']
    old_balance = r['balance_after']
    
    # Recalculate shares from entry price
    new_shares = math.floor(bet_amount / entry_price * 10000) / 10000
    
    # Recalculate PnL
    if result == 'WIN':
        new_pnl = new_shares - bet_amount
    elif result == 'LOSS':
        new_pnl = -bet_amount
    else:  # PENDING
        new_pnl = 0.0
    
    # Calculate balance using pure ledger approach
    if result == 'PENDING':
        pending_bets_sum += bet_amount
    else:
        resolved_pnl_sum += new_pnl
    
    new_balance = starting_balance + resolved_pnl_sum - pending_bets_sum
    
    # Track changes
    changes = []
    if abs(new_shares - old_shares) > 0.0001:
        changes.append(f"shares")
    if abs(new_pnl - old_pnl) > 0.0001:
        changes.append(f"pnl")
    if abs(new_balance - old_balance) > 0.001:
        changes.append(f"balance")
    
    change_str = ", ".join(changes) if changes else "OK"
    
    print(f"{trade_id:>4} | {r['side']:>4} | {entry_price:>7.4f} | {old_shares:>9.4f} | {new_shares:>9.4f} | {old_pnl:>8.4f} | {new_pnl:>8.4f} | {old_balance:>10.4f} | {new_balance:>10.4f} | {change_str}")
    
    if changes:
        updates.append((new_shares, new_pnl, new_balance, trade_id))

print(f"\n{len(updates)} trades need updating.")

if updates:
    confirm = input("\nApply fixes? (yes/no): ").strip().lower()
    if confirm == "yes":
        for new_shares, new_pnl, new_balance, trade_id in updates:
            conn.execute(
                "UPDATE trades SET shares = ?, pnl = ?, balance_after = ? WHERE id = ?",
                (new_shares, new_pnl, new_balance, trade_id)
            )
        conn.commit()
        print(f"\n✅ {len(updates)} trades updated successfully!")
    else:
        print("\n❌ No changes applied.")
else:
    print("\n✅ All trades are already correct.")

conn.close()
