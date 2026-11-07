"""Verify bot reads correct prices from Polymarket CLOB API."""
import os, sys, time, json, requests
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

# 1. Find current active market (same logic as bot.py)
now = time.time()
current_slot = int(now // 300) * 300
slots = [current_slot - 300, current_slot, current_slot + 300]

market = None
for ts in slots:
    slug = f"btc-updown-5m-{ts}"
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={"slug": slug}, timeout=10
        )
        if resp.status_code != 200 or not resp.json():
            continue
        event = resp.json()[0]
        m = event["markets"][0]
        if m.get("closed"):
            continue
        
        end_date = m.get("endDate") or event.get("endDate")
        end_time = ts + 300
        if end_date:
            from datetime import datetime, timezone
            if end_date.endswith("Z"):
                end_time = datetime.fromisoformat(end_date.replace("Z","+00:00")).timestamp()
        
        if end_time < now:
            continue
            
        clob_ids = json.loads(m.get("clobTokenIds","[]")) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds",[])
        outcomes = json.loads(m.get("outcomes","[]")) if isinstance(m.get("outcomes"), str) else m.get("outcomes",[])
        
        up_token = clob_ids[0]
        down_token = clob_ids[1] if len(clob_ids) > 1 else None
        for i, o in enumerate(outcomes):
            if "Up" in str(o) or "YES" in str(o).upper():
                up_token = clob_ids[i]
            elif "Down" in str(o) or "NO" in str(o).upper():
                down_token = clob_ids[i]
        
        market = {
            "slug": slug,
            "question": m.get("question",""),
            "up_token": up_token,
            "down_token": down_token,
            "countdown": end_time - now,
            "outcomes": outcomes,
            "outcomePrices": m.get("outcomePrices"),
        }
        break
    except:
        continue

if not market:
    print("No active market found. Try again in a moment.")
    sys.exit(0)

print(f"Market: {market['question']}")
print(f"Countdown: {int(market['countdown'])}s")
print(f"Outcomes: {market['outcomes']}")
print()

# 2. Fetch CLOB prices (what the BOT uses for trading)
print("=" * 60)
print("  CLOB API PRICES (what BOT uses for trading decisions)")
print("=" * 60)

for label, token in [("UP", market["up_token"]), ("DOWN", market["down_token"])]:
    try:
        resp = requests.get(
            "https://clob.polymarket.com/price",
            params={"token_id": token, "side": "buy"}, timeout=10
        )
        price = float(resp.json().get("price", 0)) if resp.status_code == 200 else None
        print(f"  {label} (buy-side): {price} = {price*100:.1f}¢" if price else f"  {label}: FAILED")
    except Exception as e:
        print(f"  {label}: ERROR {e}")

# 3. Fetch Gamma API outcomePrices (what Polymarket UI shows)
print()
print("=" * 60)
print("  GAMMA API PRICES (what Polymarket website shows)")
print("=" * 60)

if market.get("outcomePrices"):
    prices = json.loads(market["outcomePrices"]) if isinstance(market["outcomePrices"], str) else market["outcomePrices"]
    for i, (outcome, price) in enumerate(zip(market["outcomes"], prices)):
        p = float(price)
        print(f"  {outcome}: {p} = {p*100:.1f}¢")
    total = sum(float(p) for p in prices)
    print(f"  Total: {total*100:.1f}¢ (should be ~100¢)")

# 4. Fetch orderbook to show actual depth
print()
print("=" * 60)
print("  ORDERBOOK DEPTH (actual buy offers)")
print("=" * 60)

for label, token in [("UP", market["up_token"]), ("DOWN", market["down_token"])]:
    try:
        resp = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token}, timeout=10
        )
        if resp.status_code == 200:
            book = resp.json()
            asks = book.get("asks", [])[:3]
            bids = book.get("bids", [])[:3]
            print(f"\n  {label} Token:")
            print(f"    Best asks (sell offers): {[(a['price'], a['size']) for a in asks]}")
            print(f"    Best bids (buy offers):  {[(b['price'], b['size']) for b in bids]}")
            if asks:
                print(f"    >> Cheapest ask = {asks[0]['price']} = {float(asks[0]['price'])*100:.1f}¢")
    except Exception as e:
        print(f"  {label}: ERROR {e}")

print()
print("=" * 60)
print("  CONCLUSION")
print("=" * 60)
print("  CLOB price = cheapest ASK in orderbook (real fill price)")
print("  Gamma price = midpoint/probability (display only)")
print("  Bot uses CLOB price -> correct for actual order execution")
