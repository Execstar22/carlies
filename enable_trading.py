"""
Enable trading: Set COLLATERAL and CONDITIONAL balance allowances.
"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
funder = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()
sig_type = int(os.environ.get("POLY_SIGNATURE_TYPE", "1"))
chain_id = int(os.environ.get("POLY_CHAIN_ID", "137"))

print("=" * 60)
print("  ENABLE TRADING")
print("=" * 60)

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=chain_id,
    key=key,
    funder=funder,
    signature_type=sig_type,
)
creds = client.create_or_derive_api_key()
client.set_api_creds(creds)

print(f"  Signer:  {client.signer.address()}")
print(f"  Funder:  {client.builder.funder}")
print(f"  SigType: {client.builder.signature_type}")

# Check and update for COLLATERAL (USDC)
print(f"\n--- COLLATERAL (USDC) ---")
try:
    ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  Current: {ba}")
except Exception as e:
    print(f"  Get error: {e}")

try:
    r = client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  Update:  {r}")
except Exception as e:
    print(f"  Update error: {e}")

# Check and update for CONDITIONAL (CTF tokens)
print(f"\n--- CONDITIONAL (CTF tokens) ---")
try:
    ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL))
    print(f"  Current: {ba}")
except Exception as e:
    print(f"  Get error: {e}")

try:
    r = client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL))
    print(f"  Update:  {r}")
except Exception as e:
    print(f"  Update error: {e}")

# Now try placing a test order
print(f"\n--- TEST ORDER ---")
from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions

test_token = "96343711943910618279316813988543988646539419677592327089076563743396498973137"
try:
    resp = client.create_and_post_order(
        order_args=OrderArgs(
            token_id=test_token,
            price=0.50,
            side="BUY",
            size=1.0,
        ),
        order_type=OrderType.FOK,
        options=PartialCreateOrderOptions(tick_size="0.01"),
    )
    print(f"  SUCCESS: {resp}")
except Exception as e:
    err = str(e)
    if "maker address not allowed" in err:
        print(f"  STILL BLOCKED: maker address not allowed")
    else:
        print(f"  Different error (may be progress!): {err[:120]}")

print(f"\n{'=' * 60}")
