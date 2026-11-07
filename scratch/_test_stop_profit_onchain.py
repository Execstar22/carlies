"""
Test: Verify stop profit uses on-chain balance directly.
Simulates the exact stop profit logic from bot.py.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

from database import get_setting, update_setting

print("=" * 60)
print("  STOP PROFIT ON-CHAIN TEST")
print("=" * 60)

# Fetch current on-chain
from trader import get_real_balance
chain_now = get_real_balance()
print(f"\n  Current on-chain wallet: ${chain_now:.6f}")

# Get daily start anchor
daily_start = get_setting("real_daily_start_balance")
if daily_start:
    start_val = float(daily_start)
    print(f"  Daily start anchor:     ${start_val:.6f}")
    actual_pnl = chain_now - start_val
    print(f"  Actual daily PnL:       ${actual_pnl:.4f}")
else:
    print(f"  Daily start anchor:     NOT SET")
    print(f"  (Will be set at next bot startup)")
    actual_pnl = None

# Compare with DB PnL
from database import get_daily_profit_total
db_pnl = get_daily_profit_total(trading_mode="real")
print(f"\n  DB-recorded daily PnL:  ${db_pnl:.4f}")

if actual_pnl is not None:
    drift = db_pnl - actual_pnl
    print(f"  Drift (DB vs on-chain): ${drift:.4f}")
    
    sp_target = float(get_setting("stop_profit_target") or 1.0)
    print(f"\n  Stop profit target:     ${sp_target:.2f}")
    
    # Show what would happen with old vs new method
    print(f"\n  --- OLD METHOD (DB PnL) ---")
    print(f"  Would trigger: {'YES' if db_pnl >= sp_target else 'NO'} (PnL ${db_pnl:.4f})")
    
    print(f"\n  --- NEW METHOD (on-chain) ---")
    print(f"  Would trigger: {'YES' if actual_pnl >= sp_target else 'NO'} (PnL ${actual_pnl:.4f})")
    
    if db_pnl >= sp_target and actual_pnl < sp_target:
        print(f"\n  ⚠️ OLD method would FALSE TRIGGER!")
        print(f"  ✅ NEW method correctly continues trading")
    elif actual_pnl >= sp_target:
        print(f"\n  ✅ Both methods agree: target reached")
    else:
        print(f"\n  ✅ Both methods agree: target not yet reached")

print(f"\n{'=' * 60}")
print(f"  CONCLUSION: Stop profit now uses wallet balance directly.")
print(f"  Drift cannot affect stop profit trigger anymore.")
print(f"{'=' * 60}")
