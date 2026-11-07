import sqlite3

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

# Get today's trades (WIB = UTC+7)
rows = conn.execute("""
    SELECT id, timestamp, side, entry_price, shares, bet_amount, trading_mode, result
    FROM trades 
    WHERE date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
    ORDER BY id DESC
    LIMIT 30
""").fetchall()

print(f"{'ID':>4} | {'Time':>19} | {'Side':>4} | {'Entry':>7} | {'Shares':>9} | {'Bet':>7} | {'Mode':>7} | {'Result':>7}")
print("-" * 90)
for r in rows:
    print(f"{r['id']:>4} | {r['timestamp'][:19]:>19} | {r['side']:>4} | {r['entry_price']:>7.4f} | {r['shares']:>9.4f} | {r['bet_amount']:>7.2f} | {r['trading_mode']:>7} | {r['result']:>7}")

print(f"\nTotal trades today: {len(rows)}")

# Check if shares are all the same
if rows:
    shares_set = set(r['shares'] for r in rows)
    print(f"Unique share amounts: {shares_set}")
    
    prices_set = set(r['entry_price'] for r in rows)
    print(f"Unique entry prices: {prices_set}")

conn.close()
