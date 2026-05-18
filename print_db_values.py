import sqlite3

def print_db_values():
    conn = sqlite3.connect('polybots.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, result, entry_price, shares, bet_amount, pnl FROM trades WHERE trading_mode='real' ORDER BY timestamp ASC")
    
    trades = cursor.fetchall()
    for row in trades:
        t_id, result, entry_price, shares, bet_amount, pnl = row
        print(f"ID: {t_id}, Result: {result}, Price: {entry_price}, Shares_DB: {shares}, Bet_DB: {bet_amount}, PnL_DB: {pnl}")
        
    conn.close()

if __name__ == '__main__':
    print_db_values()
