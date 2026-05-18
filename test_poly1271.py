"""
FINAL FIX: Use signature_type=3 (POLY_1271) for deposit wallet trading.
"""
import os, sys, json, time
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    OrderArgs, OrderType, PartialCreateOrderOptions,
    BalanceAllowanceParams, AssetType,
)

# Try to import SignatureTypeV2
try:
    from py_clob_client_v2 import SignatureTypeV2
    print(f"SignatureTypeV2.POLY_1271 = {SignatureTypeV2.POLY_1271}")
    sig_type = SignatureTypeV2.POLY_1271
except ImportError:
    print("SignatureTypeV2 not available, using raw value 3")
    sig_type = 3

key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
funder = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()

print(f"\n{'='*60}")
print(f"  TESTING POLY_1271 (signature_type=3)")
print(f"{'='*60}")

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=key,
    funder=funder,
    signature_type=sig_type,  # 3 = POLY_1271!
)

print(f"  Signer:  {client.signer.address()}")
print(f"  Funder:  {client.builder.funder}")
print(f"  SigType: {client.builder.signature_type}")

# Get API creds
creds = client.create_or_derive_api_key()
client.set_api_creds(creds)
print(f"  API Key: {creds.api_key[:20]}...")

# Sync balance with signature_type=3
print(f"\n--- Balance Allowance (sig_type=3) ---")
try:
    ba = client.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print(f"  Balance: {ba.get('balance', 'N/A')}")
    print(f"  Allowances: {json.dumps(ba.get('allowances', {}), indent=4)}")
except Exception as e:
    print(f"  Error: {e}")

# Update balance
try:
    client.update_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print(f"  Updated!")
except Exception as e:
    print(f"  Update error: {e}")

# Check after update
print(f"\n--- After Update ---")
try:
    ba2 = client.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print(f"  Balance: {ba2.get('balance', 'N/A')}")
except Exception as e:
    print(f"  Error: {e}")

# Find live market
print(f"\n--- Finding live market ---")
import requests
now = time.time()
current_slot = int(now // 300) * 300
test_token = None

for ts in [current_slot, current_slot + 300]:
    slug = f"btc-updown-5m-{ts}"
    try:
        resp = requests.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                market = data[0].get("markets", [{}])[0]
                clob_ids = market.get("clobTokenIds", "[]")
                if isinstance(clob_ids, str):
                    clob_ids = json.loads(clob_ids)
                if clob_ids:
                    test_token = clob_ids[0]
                    print(f"  Market: {slug}")
                    print(f"  Token: {test_token[:30]}...")
                    break
    except:
        pass

if not test_token:
    print("  No live market!")
    sys.exit(1)

# Place test order with POLY_1271!
print(f"\n--- PLACING ORDER with signature_type=3 ---")
try:
    resp = client.create_and_post_order(
        order_args=OrderArgs(
            token_id=test_token,
            price=0.50,
            side="BUY",
            size=2.0,
        ),
        order_type=OrderType.FOK,
        options=PartialCreateOrderOptions(tick_size="0.01"),
    )
    print(f"  🎉 ORDER SUCCESS: {resp}")
except Exception as e:
    err = str(e)
    if "maker address not allowed" in err:
        print(f"  STILL BLOCKED: maker address not allowed")
    else:
        print(f"  DIFFERENT ERROR: {err[:200]}")

print(f"\n{'='*60}")
