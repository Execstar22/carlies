"""Fix real trade balance tracking - compute and store starting balance"""
import sqlite3, sys, os
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()

conn = sqlite3.connect('polybots.db')
conn.row_factory = sqlite3.Row

# Get all resolved real trades
trades = conn.execute(
    "SELECT id, result, pnl, bet_amount, shares FROM trades "
    "WHERE trading_mode='real' AND result != 'PENDING' ORDER BY id"
).fetchall()

# Get CLOB balance (most accurate)
try:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    client = ClobClient(
        host="https://clob.polymarket.com", chain_id=137,
        key=os.environ['POLY_PRIVATE_KEY'].strip(),
        funder=os.environ['POLY_FUNDER_ADDRESS'].strip(),
        signature_type=3,
    )
    creds = client.create_or_derive_api_key()
    client.set_api_creds(creds)
    ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    clob_balance = float(ba.get('balance', 0)) / 1e6
    print(f"CLOB balance now: ${clob_balance:.6f}")
except Exception as e:
    print(f"CLOB error: {e}")
    clob_balance = 7.236565  # fallback to on-chain

# Check pending trades (their bet amounts are already deducted from balance)
pending = conn.execute(
    "SELECT COUNT(*) as cnt, COALESCE(SUM(bet_amount),0) as total_bet "
    "FROM trades WHERE trading_mode='real' AND result='PENDING'"
).fetchone()
print(f"Pending trades: {pending['cnt']}, total bet: ${pending['total_bet']:.2f}")

# Cumulative PnL from resolved trades
cum_pnl = sum(t['pnl'] for t in trades)
print(f"Cumulative PnL: ${cum_pnl:.6f}")

# Back-calculate initial balance
# initial = clob_balance_now - cum_pnl + pending_bets
# (because pending bets reduced the CLOB balance but aren't in PnL yet)
initial = clob_balance - cum_pnl + pending['total_bet']
print(f"Computed initial balance: ${initial:.6f}")

# Now compute correct balance_after for each trade
print(f"\n--- Corrected balances ---")
running = initial
for t in trades:
    running_pnl = 0
    # Sum PnL up to and including this trade
    for t2 in trades:
        running_pnl += t2['pnl']
        if t2['id'] == t['id']:
            break
    correct_bal = initial + running_pnl
    print(f"  #{t['id']} {t['result']:4s} pnl={t['pnl']:+.4f} => balance_after=${correct_bal:.4f}")

conn.close()
print(f"\nInitial balance to store: ${initial:.6f}")
