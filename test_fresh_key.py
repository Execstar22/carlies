import sys, os
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()

from py_clob_client_v2.client import ClobClient

c = ClobClient(
    host='https://clob.polymarket.com',
    chain_id=137,
    key=os.environ['POLY_PRIVATE_KEY'].strip(),
    funder=os.environ['POLY_FUNDER_ADDRESS'].strip(),
    signature_type=1,
)

print('=== Existing API Keys ===')
try:
    keys = c.get_api_keys()
    print(keys)
except Exception as e:
    print(f'Error: {e}')

print('\n=== Derive API Key ===')
try:
    dk = c.derive_api_key()
    print(f'Derived: {dk}')
except Exception as e:
    print(f'Derive failed: {e}')

print('\n=== Create API Key ===')
try:
    ck = c.create_api_key()
    print(f'Created: {ck}')
except Exception as e:
    print(f'Create failed: {e}')

# Try deleting all keys then creating fresh
print('\n=== Delete all API keys and recreate ===')
try:
    c2 = ClobClient(
        host='https://clob.polymarket.com',
        chain_id=137,
        key=os.environ['POLY_PRIVATE_KEY'].strip(),
        funder=os.environ['POLY_FUNDER_ADDRESS'].strip(),
        signature_type=1,
    )
    dk2 = c2.derive_api_key()
    c2.set_api_creds(dk2)
    
    # Delete existing API key
    try:
        del_result = c2.delete_api_key()
        print(f'Deleted: {del_result}')
    except Exception as e:
        print(f'Delete failed: {e}')
    
    # Now create fresh
    c3 = ClobClient(
        host='https://clob.polymarket.com',
        chain_id=137,
        key=os.environ['POLY_PRIVATE_KEY'].strip(),
        funder=os.environ['POLY_FUNDER_ADDRESS'].strip(),
        signature_type=1,
    )
    try:
        fresh = c3.create_api_key()
        print(f'Fresh key created: {fresh}')
        c3.set_api_creds(fresh)
        
        # Test order with fresh key
        from py_clob_client_v2.clob_types import MarketOrderArgsV2, OrderType, PartialCreateOrderOptions
        import time, json, requests
        
        now = time.time()
        current_slot = int(now // 300) * 300
        for ts in [current_slot, current_slot + 300]:
            slug = f"btc-updown-5m-{ts}"
            resp = requests.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    market = data[0].get("markets", [{}])[0]
                    clob_ids = json.loads(market.get("clobTokenIds", "[]")) if isinstance(market.get("clobTokenIds"), str) else market.get("clobTokenIds", [])
                    if clob_ids:
                        token = clob_ids[0]
                        print(f'\nTest order with token: {token[:30]}...')
                        try:
                            r = c3.create_and_post_market_order(
                                order_args=MarketOrderArgsV2(token_id=token, amount=2.0, side="BUY"),
                                options=PartialCreateOrderOptions(tick_size="0.01"),
                                order_type=OrderType.FOK,
                            )
                            print(f'ORDER SUCCESS: {r}')
                        except Exception as oe:
                            print(f'ORDER FAILED: {str(oe)[:120]}')
                        break
    except Exception as e:
        print(f'Create fresh key failed: {e}')

except Exception as e:
    print(f'Error: {e}')
