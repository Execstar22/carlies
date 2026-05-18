import sqlite3
import datetime

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

# the user asked for "hari ini" (today). Since we're in WIB (UTC+7), let's just get today's date in local time or just query the DB.
today = datetime.datetime.now().strftime('%Y-%m-%d')
print(f"Querying for date: {today}")

# Let's get all trades today. Wait, database stores ISO8601 timestamps which might be UTC. Let's just get the last 24 hours or just fetch all and filter in Python.
trades = conn.execute("SELECT * FROM trades").fetchall()

today_trades = []
for t in trades:
    # t['timestamp'] usually looks like '2026-05-12T05:00:00Z'
    ts = t['timestamp']
    if ts.startswith(today):
        today_trades.append(t)

if not today_trades:
    # If no trades exactly matching today string, maybe just get all trades from the last 1 day.
    print("No trades found for today's date string. Falling back to all trades for analysis.")
    today_trades = trades

completed_trades = [t for t in today_trades if t['result'] in ('WIN', 'LOSS')]

below_80 = [t for t in completed_trades if t['entry_price'] < 0.80]
above_80 = [t for t in completed_trades if t['entry_price'] >= 0.80]

total = len(completed_trades)
print(f'Total completed trades analyzed: {total}')

if total > 0:
    b80_win = len([t for t in below_80 if t['result'] == 'WIN'])
    b80_loss = len([t for t in below_80 if t['result'] == 'LOSS'])
    a80_win = len([t for t in above_80 if t['result'] == 'WIN'])
    a80_loss = len([t for t in above_80 if t['result'] == 'LOSS'])

    print(f'Below 80: {len(below_80)} ({len(below_80)/total*100:.2f}%)')
    if len(below_80) > 0:
        print(f'  - WIN:  {b80_win} ({b80_win/len(below_80)*100:.2f}%)')
        print(f'  - LOSS: {b80_loss} ({b80_loss/len(below_80)*100:.2f}%)')

    print(f'Above 80: {len(above_80)} ({len(above_80)/total*100:.2f}%)')
    if len(above_80) > 0:
        print(f'  - WIN:  {a80_win} ({a80_win/len(above_80)*100:.2f}%)')
        print(f'  - LOSS: {a80_loss} ({a80_loss/len(above_80)*100:.2f}%)')
else:
    print("No completed trades to analyze.")
