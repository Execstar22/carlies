"""Test: Use create_and_post_market_order (TAKER) instead of create_and_post_order (MAKER)"""
import os, sys, json, time
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    MarketOrderArgsV2, OrderType, PartialCreateOrderOptions,
    BalanceAllowanceParams, AssetType
)

key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
funder = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()
sig_type = int(os.environ.get("POLY_SIGNATURE_TYPE", "1"))

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=key,
    funder=funder,
    signature_type=sig_type,
)
creds = client.create_or_derive_api_key()
client.set_api_creds(creds)

# Update allowance
client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))

# Find a live market
import requests
now = time.time()
current_slot = int(now // 300) * 300

test_token = None
for ts in [current_slot, current_slot + 300]:
    slug = f"btc-updown-5m-{ts}"
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
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
                    test_token = clob_ids[0]  # "Down" token
                    print(f"Market: {slug}")
                    print(f"Token: {test_token[:30]}...")
                    break
    except:
        pass

if not test_token:
    print("No live market found!")
    sys.exit(1)

# Try MARKET ORDER (TAKER flow) - this is what the website uses!
print(f"\n=== TEST 1: create_and_post_market_order (TAKER) ===")
try:
    resp = client.create_and_post_market_order(
        order_args=MarketOrderArgsV2(
            token_id=test_token,
            amount=2.0,  # $2 USDC
            side="BUY",
            order_type=OrderType.FOK,
        ),
        options=PartialCreateOrderOptions(tick_size="0.01"),
        order_type=OrderType.FOK,
    )
    print(f"  SUCCESS! Response: {resp}")
except Exception as e:
    err = str(e)
    if "maker address not allowed" in err:
        print(f"  STILL BLOCKED: maker address not allowed")
    else:
        print(f"  DIFFERENT ERROR: {err[:150]}")

# Also try FAK (Fill and Kill) instead of FOK
print(f"\n=== TEST 2: Market order with FAK ===")
try:
    resp = client.create_and_post_market_order(
        order_args=MarketOrderArgsV2(
            token_id=test_token,
            amount=2.0,
            side="BUY",
            order_type=OrderType.FAK,
        ),
        options=PartialCreateOrderOptions(tick_size="0.01"),
        order_type=OrderType.FAK,
    )
    print(f"  SUCCESS! Response: {resp}")
except Exception as e:
    err = str(e)
    if "maker address not allowed" in err:
        print(f"  STILL BLOCKED: maker address not allowed")
    else:
        print(f"  DIFFERENT ERROR: {err[:150]}")

print(f"\nDONE")
