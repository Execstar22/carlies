import sqlite3

def calculate_custom_balance():
    conn = sqlite3.connect('polybots.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, result, entry_price, shares, bet_amount, pnl FROM trades WHERE trading_mode='real' ORDER BY timestamp ASC")
    
    initial_balance = 10.0
    current_balance = initial_balance
    fixed_bet = 2.0
    
    trades = cursor.fetchall()
    
    print(f"Total trades found: {len(trades)}")
    
    for row in trades:
        t_id, result, entry_price, shares, bet_amount, pnl = row
        if result == 'WIN':
            # Payout in polymarket for winning is $1 per share.
            # Wait, how many shares does $2 buy?
            # Shares = fixed_bet / entry_price.
            # So payout = Shares * 1.0 = fixed_bet / entry_price.
            # Profit = payout - fixed_bet.
            shares_bought = fixed_bet / entry_price if entry_price else 0
            payout = shares_bought
            profit = payout - fixed_bet
            current_balance += profit
            print(f"Trade {t_id}: WIN  | Price: {entry_price:.3f} | Shares: {shares_bought:.2f} | Profit: +${profit:.2f} | Balance: ${current_balance:.2f}")
        elif result == 'LOSS':
            current_balance -= fixed_bet
            print(f"Trade {t_id}: LOSS | Price: {entry_price:.3f} | Profit: -${fixed_bet:.2f} | Balance: ${current_balance:.2f}")
        elif result == 'PENDING':
            print(f"Trade {t_id}: PENDING (Skipped in balance calculation)")
        else:
            print(f"Trade {t_id}: {result} | Unknown result")
            
    print(f"\nFinal Balance: ${current_balance:.2f}")
    conn.close()

if __name__ == '__main__':
    calculate_custom_balance()
