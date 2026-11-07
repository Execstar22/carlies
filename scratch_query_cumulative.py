import sqlite3

def check_trade_history():
    conn = sqlite3.connect('c:/Users/aL/Pictures/RLM DEMO/polybots.db')
    c = conn.cursor()
    query = """
    SELECT id, timestamp, result, bet_amount, pnl, balance_after 
    FROM trades 
    WHERE id BETWEEN 3395 AND 3415
    AND trading_mode = 'shadow'
    ORDER BY id ASC
    """
    c.execute(query)
    rows = c.fetchall()
    print(f"{'id':<5} | {'result':<8} | {'bet_amount':<10} | {'pnl':<10} | {'balance_after':<15} | {'timestamp'}")
    print("-" * 80)
    for row in rows:
        print(f"{row[0]:<5} | {row[2]:<8} | {row[3]:<10} | {row[4]:<10.4f} | {row[5]:<15.4f} | {row[1]}")
    
    # Check cumulative PnL for today up to 3409
    query_cumulative = """
    SELECT SUM(pnl) as cumulative_pnl 
    FROM trades 
    WHERE id <= 3409 
    AND trading_mode = 'shadow'
    AND result IN ('WIN', 'LOSS')
    AND date(datetime(timestamp, '+7 hours')) = date(
        (SELECT datetime(timestamp, '+7 hours') FROM trades WHERE id = 3409)
    )
    """
    c.execute(query_cumulative)
    cum_row = c.fetchone()
    print(f"\nCumulative PnL today up to 3409: {cum_row[0]}")

    conn.close()

if __name__ == "__main__":
    check_trade_history()
