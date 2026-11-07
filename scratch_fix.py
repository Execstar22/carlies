import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timezone, timedelta

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

print("=== Current Settings ===")
keys = ['stop_profit_enabled', 'stop_profit_target', 'real_daily_start_balance', 
        'real_daily_start_date', 'trading_mode']
for key in keys:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    print(f"  {key} = {row['value'] if row else 'NOT SET'}")

now_utc = datetime.now(timezone.utc)
today_wib = (now_utc + timedelta(hours=7)).strftime("%Y-%m-%d")
wib_time = (now_utc + timedelta(hours=7)).strftime("%H:%M:%S")
print(f"\n=== Time Check ===")
print(f"  Current UTC:  {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  Current WIB:  {today_wib} {wib_time}")

anchor_row = conn.execute("SELECT value FROM settings WHERE key='real_daily_start_date'").fetchone()
anchor_date = anchor_row['value'] if anchor_row else 'NOT SET'
print(f"  Anchor date:  {anchor_date}")
print(f"  Date match?   {anchor_date == today_wib}")

# If anchor date != today, that means the bot thinks it's a NEW DAY
if anchor_date != today_wib:
    print(f"  >>> BUG: Anchor date '{anchor_date}' != today WIB '{today_wib}'")
    print(f"  >>> This means bot resets anchor = treats as NEW DAY = PnL resets to $0!")

print(f"\n=== On-Chain Balance ===")
from trader import get_real_balance
bal = get_real_balance()
print(f"  On-chain balance: ${bal}")

ds_row = conn.execute("SELECT value FROM settings WHERE key='real_daily_start_balance'").fetchone()
daily_start = float(ds_row['value']) if ds_row else 0
print(f"  Daily start anchor: ${daily_start:.6f}")
print(f"  Daily PnL (on-chain): ${bal - daily_start:+.6f}")

sp_row = conn.execute("SELECT value FROM settings WHERE key='stop_profit_target'").fetchone()
sp_target = float(sp_row['value']) if sp_row else 2.5
print(f"  Stop profit target: ${sp_target:.4f}")
print(f"  SHOULD STOP? {(bal - daily_start) >= sp_target}")

# Check latest trades
print(f"\n=== Latest Real Trades ===")
rows = conn.execute(
    "SELECT id, timestamp, side, result, pnl, balance_after FROM trades "
    "WHERE trading_mode='real' ORDER BY id DESC LIMIT 8"
).fetchall()
for r in rows:
    d = dict(r)
    ts_utc = d['timestamp'][:19]
    try:
        dt = datetime.fromisoformat(d['timestamp']).replace(tzinfo=timezone.utc)
        wib = (dt + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M')
    except:
        wib = '?'
    print(f"  #{d['id']} | UTC={ts_utc} | WIB={wib} | {d['result']:7s} | pnl=${d['pnl']:+.6f} | bal=${d['balance_after']:.6f}")

conn.close()
