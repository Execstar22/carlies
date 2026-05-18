"""
Debug: Inspect exactly what the SDK sends vs what the API expects.
"""
import os, sys, json, time
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    OrderArgs, OrderType, PartialCreateOrderOptions,
    BalanceAllowanceParams, AssetType
)

key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
funder = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()
sig_type = int(os.environ.get("POLY_SIGNATURE_TYPE", "1"))
chain_id = int(os.environ.get("POLY_CHAIN_ID", "137"))

print("=" * 60)
print("  ORDER DEBUG")
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

print(f"  Signer:    {client.signer.address()}")
print(f"  Funder:    {client.builder.funder}")
print(f"  SigType:   {client.builder.signature_type}")
print(f"  API Key:   {creds.api_key[:20]}...")

# Check SDK contract config
print(f"\n--- SDK Contract Config ---")
from py_clob_client_v2.config import get_contract_config
cc = get_contract_config(chain_id)
print(f"  exchange:           {cc.exchange}")
print(f"  neg_risk_exchange:  {cc.neg_risk_exchange}")
print(f"  exchange_v2:        {cc.exchange_v2}")
print(f"  neg_risk_exchange_v2: {cc.neg_risk_exchange_v2}")

# Check allowance again (after user traded on website)
print(f"\n--- Balance Allowance (COLLATERAL) ---")
try:
    ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  {json.dumps(ba, indent=2)}")
except Exception as e:
    print(f"  Error: {e}")

# Update allowance
try:
    client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print("  Allowance updated")
except Exception as e:
    print(f"  Update error: {e}")

# Check allowance after update
print(f"\n--- Balance Allowance AFTER update ---")
try:
    ba2 = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  {json.dumps(ba2, indent=2)}")
except Exception as e:
    print(f"  Error: {e}")

# Find a LIVE active market token to test with
print(f"\n--- Finding live market token ---")
import requests

now = time.time()
current_slot = int(now // 300) * 300
test_token = None
test_neg_risk = None

for ts in [current_slot, current_slot + 300]:
    slug = f"btc-updown-5m-{ts}"
    try:
        resp = requests.get(
            f"https://gamma-api.polymarket.com/events",
            params={"slug": slug},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                market = data[0].get("markets", [{}])[0]
                clob_ids = market.get("clobTokenIds", "[]")
                if isinstance(clob_ids, str):
                    clob_ids = json.loads(clob_ids)
                if clob_ids:
                    test_token = clob_ids[0]
                    print(f"  Slug: {slug}")
                    print(f"  Token: {test_token[:30]}...")
                    
                    # Check neg_risk and tick_size for this token
                    neg_risk = client.get_neg_risk(test_token)
                    tick_size = client.get_tick_size(test_token)
                    print(f"  neg_risk: {neg_risk}")
                    print(f"  tick_size: {tick_size}")
                    test_neg_risk = neg_risk
                    break
    except Exception as e:
        print(f"  {slug}: {e}")

if not test_token:
    print("  No live market found!")
    sys.exit(1)

# Create order WITHOUT posting to see the details
print(f"\n--- Order Details (dry-run) ---")
try:
    order = client.create_order(
        OrderArgs(
            token_id=test_token,
            price=0.50,
            side="BUY",
            size=1.0,
        ),
        options=PartialCreateOrderOptions(tick_size=str(tick_size)),
    )
    print(f"  Order object type: {type(order).__name__}")
    if hasattr(order, '__dict__'):
        for k, v in order.__dict__.items():
            val = str(v)
            if len(val) > 80:
                val = val[:80] + "..."
            print(f"  {k}: {val}")
    else:
        print(f"  {order}")
except Exception as e:
    print(f"  Create order error: {e}")

# Now try to post the order
print(f"\n--- Posting Order ---")
try:
    resp = client.create_and_post_order(
        order_args=OrderArgs(
            token_id=test_token,
            price=0.50,
            side="BUY",
            size=1.0,
        ),
        order_type=OrderType.FOK,
        options=PartialCreateOrderOptions(tick_size=str(tick_size)),
    )
    print(f"  SUCCESS: {resp}")
except Exception as e:
    err = str(e)
    print(f"  FAILED: {err[:200]}")
    
    # If maker error, try with tick_size from API
    if "maker address" in err:
        print(f"\n  Trying with different tick_size...")
        for ts_val in ["0.01", "0.001", "0.0001"]:
            try:
                resp = client.create_and_post_order(
                    order_args=OrderArgs(
                        token_id=test_token,
                        price=0.50,
                        side="BUY",
                        size=1.0,
                    ),
                    order_type=OrderType.FOK,
                    options=PartialCreateOrderOptions(tick_size=ts_val),
                )
                print(f"  tick_size={ts_val}: SUCCESS: {resp}")
                break
            except Exception as e2:
                print(f"  tick_size={ts_val}: {str(e2)[:80]}")

print(f"\n{'=' * 60}")
