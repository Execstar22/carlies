import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polybots.db")

def calculate_pnl():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*), SUM(bet_amount) FROM trades WHERE result = 'LOSS'")
    loss_row = cursor.fetchone()
    loss_count = loss_row[0] if loss_row[0] else 0
    loss_amount = loss_row[1] if loss_row[1] else (loss_count * 2.0)

    cursor.execute("SELECT COUNT(*), SUM(pnl) FROM trades WHERE result = 'WIN'")
    win_row = cursor.fetchone()
    win_count = win_row[0] if win_row[0] else 0
    win_pnl = win_row[1] if win_row[1] else 0

    total_loss_naive = loss_count * 2.0
    total_win_naive = win_count * 2.0

    print(f'Total Loss: {loss_count} trades = ${total_loss_naive:.2f}')
    print(f'Total Win: {win_count} trades = ${total_win_naive:.2f}')
    print(f'Net Naive: ${(total_win_naive - total_loss_naive):.2f}')

    print('\n--- ACTUAL PNL DARI DATABASE ---')
    print(f'Total Modal Hilang (Loss): ${loss_amount:.2f}')
    print(f'Total Profit Bersih (Win): ${win_pnl:.2f}')
    print(f'Net PNL: ${(win_pnl - loss_amount):.2f}')

    conn.close()

if __name__ == '__main__':
    calculate_pnl()
