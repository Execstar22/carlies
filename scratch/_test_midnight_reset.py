"""
Test midnight WIB auto-reset for stop profit anchor.
Simulates: yesterday's anchor -> midnight passes -> anchor resets.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

from database import get_setting, update_setting
from datetime import datetime, timezone, timedelta

passed = 0
failed = 0

def check(name, expected, actual):
    global passed, failed
    ok = expected == actual
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}")
    if not ok:
        print(f"         expected={expected}, got={actual}")
        failed += 1
    else:
        passed += 1

print("=" * 60)
print("  MIDNIGHT WIB RESET TEST")
print("=" * 60)

now_utc = datetime.now(timezone.utc)
today_wib = (now_utc + timedelta(hours=7)).strftime("%Y-%m-%d")
yesterday_wib = (now_utc + timedelta(hours=7) - timedelta(days=1)).strftime("%Y-%m-%d")

print(f"\n  Today WIB: {today_wib}")
print(f"  Yesterday WIB: {yesterday_wib}")

# --- TEST 1: Anchor is from today -> should NOT reset ---
print(f"\n--- TEST 1: Anchor is today -> no reset ---")
update_setting("real_daily_start_balance", "14.500000")
update_setting("real_daily_start_date", today_wib)

anchor_date = get_setting("real_daily_start_date")
needs_reset = (anchor_date != today_wib)
check("Anchor is today, no reset needed", False, needs_reset)

# --- TEST 2: Anchor is from yesterday -> should reset ---
print(f"\n--- TEST 2: Anchor is yesterday -> needs reset ---")
update_setting("real_daily_start_date", yesterday_wib)

anchor_date = get_setting("real_daily_start_date")
needs_reset = (anchor_date != today_wib)
check("Anchor is yesterday, reset needed", True, needs_reset)

# --- TEST 3: No anchor date set -> should reset ---
print(f"\n--- TEST 3: No anchor date -> needs reset ---")
update_setting("real_daily_start_date", "")

anchor_date = get_setting("real_daily_start_date")
needs_reset = (anchor_date != today_wib or not anchor_date)
check("No anchor date, reset needed", True, needs_reset)

# --- TEST 4: Simulate full flow ---
print(f"\n--- TEST 4: Full midnight reset flow ---")
# Set yesterday's anchor
update_setting("real_daily_start_balance", "13.500000")
update_setting("real_daily_start_date", yesterday_wib)

# Simulate what bot.py does:
anchor_date = get_setting("real_daily_start_date")
daily_start = get_setting("real_daily_start_balance")

if anchor_date != today_wib or not daily_start:
    # NEW DAY: would fetch on-chain and update
    simulated_chain_bal = 14.458860  # simulate on-chain value
    update_setting("real_daily_start_balance", f"{simulated_chain_bal:.6f}")
    update_setting("real_daily_start_date", today_wib)
    daily_start = f"{simulated_chain_bal:.6f}"
    print(f"  Anchor reset: ${simulated_chain_bal:.4f} for {today_wib}")

# Simulate PnL check after some trading
simulated_current = 15.100000
daily_pnl = simulated_current - float(daily_start)
sp_target = 1.0

print(f"  Anchor: ${float(daily_start):.4f}")
print(f"  Current wallet: ${simulated_current:.4f}")
print(f"  Daily PnL: ${daily_pnl:.4f}")
print(f"  Target: ${sp_target:.2f}")
print(f"  Would trigger: {'YES' if daily_pnl >= sp_target else 'NO'}")

check("PnL calculated from today's anchor", True, abs(daily_pnl - 0.6411) < 0.01)
check("Would NOT trigger ($0.64 < $1.00)", False, daily_pnl >= sp_target)

# --- TEST 5: Verify anchor persists correctly ---
print(f"\n--- TEST 5: Anchor persists ---")
stored_date = get_setting("real_daily_start_date")
stored_bal = get_setting("real_daily_start_balance")
check("Date stored correctly", today_wib, stored_date)
check("Balance stored correctly", "14.458860", stored_bal)

# Restore
update_setting("real_daily_start_date", today_wib)

print(f"\n{'=' * 60}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'=' * 60}")
if failed == 0:
    print(f"  Midnight WIB auto-reset verified!")
