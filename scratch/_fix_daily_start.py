"""Fix A: Reset real_daily_start_balance to correct value and verify."""
from dotenv import load_dotenv
load_dotenv()
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trader import get_real_balance

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

# Show current values
r = conn.execute("SELECT value FROM settings WHERE key='real_daily_start_balance'").fetchone()
print(f"BEFORE - daily start: ${float(r['value']):.6f}" if r else "NOT SET")

# The user said starting balance today was ~9.1xx
# Original real_starting_balance before any sync was 9.193236
# This is the most accurate value we have for today's starting balance
NEW_DAILY_START = 9.193236

# Update
conn.execute(
    "INSERT OR REPLACE INTO settings (key, value) VALUES ('real_daily_start_balance', ?)",
    (f"{NEW_DAILY_START:.6f}",)
)
conn.commit()

# Verify
on_chain = get_real_balance()
print(f"AFTER  - daily start: ${NEW_DAILY_START:.6f}")
print(f"On-chain now:         ${on_chain:.6f}" if on_chain else "FAILED")
if on_chain:
    pnl = on_chain - NEW_DAILY_START
    print(f"Corrected daily PnL:  ${pnl:.6f}")
    sp_target = float(conn.execute("SELECT value FROM settings WHERE key='stop_profit_target'").fetchone()['value'])
    stop_at = NEW_DAILY_START + sp_target
    print(f"Stop profit at:       ${stop_at:.6f} (start ${NEW_DAILY_START:.4f} + target ${sp_target:.2f})")
    print(f"Distance to stop:     ${stop_at - on_chain:.6f}")

conn.close()
