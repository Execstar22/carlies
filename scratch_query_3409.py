import sqlite3

def check_trade():
    conn = sqlite3.connect('c:/Users/aL/Pictures/RLM DEMO/polybots.db')
    c = conn.cursor()
    c.execute("SELECT id, pnl, result, timestamp, trading_mode FROM trades WHERE id = 3409")
    trade = c.fetchone()
    if trade:
        print(f"Trade 3409: PnL={trade[1]}, Result={trade[2]}, Time={trade[3]}, Mode={trade[4]}")
    else:
        print("Trade 3409 not found.")
    conn.close()

if __name__ == "__main__":
    check_trade()
