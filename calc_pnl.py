import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polybots.db")

def calculate_pnl():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get total loss
    cursor.execute("SELECT COUNT(*), SUM(bet_amount) FROM trades WHERE status = 'LOSS'")
    loss_row = cursor.fetchone()
    loss_count = loss_row[0] if loss_row[0] else 0
    loss_amount = loss_row[1] if loss_row[1] else (loss_count * 2.0)

    # Get total wins (Assuming a win returns the payout minus the bet amount. Or maybe just count wins * bet_amount)
    # Usually in Polymarket, if you win you get $1 per share. 
    # But the user example said "loss 9 = 18". So let's just multiply the count.
    cursor.execute("SELECT COUNT(*), SUM(pnl) FROM trades WHERE status = 'WIN'")
    win_row = cursor.fetchone()
    win_count = win_row[0] if win_row[0] else 0
    # In Polymarket, PNL is recorded. Let's see if the user just wants the bet amount representation.
    
    # Calculate the naive way based on the user's example
    total_loss_naive = loss_count * 2.0
    total_win_naive = win_count * 2.0 # Wait, does a win yield $2 net profit? Usually it yields $1 per share so if you bet $2 at $0.80 you get $2.5, net profit $0.5.
    
    print(f"Total Loss: {loss_count} trades = ${total_loss_naive:.2f} lost")
    print(f"Total Win: {win_count} trades")
    
    # Let's also get the actual DB sum
    cursor.execute("SELECT status, bet_amount, pnl FROM trades WHERE status IN ('WIN', 'LOSS')")
    trades = cursor.fetchall()
    
    actual_loss = 0.0
    actual_profit = 0.0
    
    for status, bet_amount, pnl in trades:
        if status == 'LOSS':
            actual_loss += bet_amount
        elif status == 'WIN':
            actual_profit += pnl if pnl is not None else 0.0

    print(f"\n--- ACTUAL PNL DARI DATABASE ---")
    print(f"Total Modal Hilang (Loss): ${actual_loss:.2f}")
    print(f"Total Profit Bersih (Win): ${actual_profit:.2f}")
    print(f"Net Profit/Loss: ${(actual_profit - actual_loss):.2f}")

    conn.close()

if __name__ == '__main__':
    calculate_pnl()
