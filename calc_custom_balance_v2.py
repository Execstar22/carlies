
import sqlite3
import os

DB_PATH = "polybots.db"

def calculate_custom_balance(initial_balance=10.0, bet_amount=2.0):
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Get all resolved trades
    trades = conn.execute(
        "SELECT id, timestamp, result, entry_price, shares, bet_amount as orig_bet, trading_mode FROM trades WHERE result IN ('WIN', 'LOSS') ORDER BY id ASC"
    ).fetchall()
    
    if not trades:
        print("No resolved trades found in database.")
        conn.close()
        return

    current_balance = initial_balance
    wins = 0
    losses = 0
    
    print(f"{'ID':<5} | {'Timestamp':<20} | {'Result':<6} | {'Entry':<6} | {'PnL':<8} | {'Balance':<10}")
    print("-" * 70)
    
    for trade in trades:
        res = trade['result']
        entry = trade['entry_price']
        
        if res == 'WIN':
            # payout = shares * $1.00
            # shares = bet_amount / entry_price
            payout = bet_amount / entry
            pnl = payout - bet_amount
            wins += 1
        else:
            pnl = -bet_amount
            losses += 1
            
        current_balance += pnl
        
        print(f"{trade['id']:<5} | {trade['timestamp'][:19]:<20} | {res:<6} | {entry:<6.3f} | {pnl:<+8.2f} | {current_balance:<10.2f}")

    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0
    
    print("-" * 70)
    print(f"Starting Balance: ${initial_balance:.2f}")
    print(f"Fixed Bet Amount: ${bet_amount:.2f}")
    print(f"Total Trades:     {total}")
    print(f"Wins/Losses:      {wins}W / {losses}L")
    print(f"Win Rate:         {win_rate:.2f}%")
    print(f"Final Balance:    ${current_balance:.2f}")
    print(f"Net PnL:          ${(current_balance - initial_balance):.2f}")

    conn.close()

if __name__ == "__main__":
    calculate_custom_balance(10.0, 2.0)
