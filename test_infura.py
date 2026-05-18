"""Quick test script to verify Infura RPC connection for HPABOTS."""
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

rpc_url = os.environ.get("POLYGON_RPC_URL", "")
print(f"🔗 RPC URL: {rpc_url[:50]}...{rpc_url[-10:]}")
print()

# --- Test 1: Basic HTTP connectivity ---
print("=" * 50)
print("TEST 1: HTTP Connectivity")
print("=" * 50)
try:
    import requests
    start = time.time()
    resp = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
        timeout=10,
    )
    latency = (time.time() - start) * 1000
    data = resp.json()
    chain_id = int(data["result"], 16)
    print(f"  ✅ Connected! Chain ID: {chain_id} ({'Polygon Mainnet' if chain_id == 137 else 'UNKNOWN'})")
    print(f"  ⚡ Latency: {latency:.0f}ms")
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    sys.exit(1)

print()

# --- Test 2: Web3 connection (same lib used by trader.py) ---
print("=" * 50)
print("TEST 2: Web3 Library Connection")
print("=" * 50)
try:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    connected = w3.is_connected()
    print(f"  {'✅' if connected else '❌'} Web3 connected: {connected}")
    if connected:
        block = w3.eth.block_number
        print(f"  📦 Latest block: {block:,}")
        gas = w3.eth.gas_price
        print(f"  ⛽ Gas price: {gas / 1e9:.2f} Gwei")
except Exception as e:
    print(f"  ❌ FAILED: {e}")

print()

# --- Test 3: Can reach Polymarket CTF contract ---
print("=" * 50)
print("TEST 3: Polymarket CTF Contract Access")
print("=" * 50)
try:
    CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    code = w3.eth.get_code(Web3.to_checksum_address(CTF_ADDRESS))
    has_code = len(code) > 2  # '0x' = empty
    print(f"  {'✅' if has_code else '❌'} CTF contract found: {has_code}")
    print(f"  📄 Contract bytecode size: {len(code):,} bytes")
except Exception as e:
    print(f"  ❌ FAILED: {e}")

print()

# --- Test 4: Latency comparison ---
print("=" * 50)
print("TEST 4: Latency Benchmark (3 requests)")
print("=" * 50)
try:
    latencies = []
    for i in range(3):
        start = time.time()
        w3.eth.block_number
        lat = (time.time() - start) * 1000
        latencies.append(lat)
        print(f"  Request {i+1}: {lat:.0f}ms")
    avg = sum(latencies) / len(latencies)
    print(f"  📊 Average: {avg:.0f}ms")
    if avg < 500:
        print(f"  ✅ Excellent latency for live trading!")
    elif avg < 1000:
        print(f"  ⚠️ Acceptable latency, but could be better.")
    else:
        print(f"  ❌ High latency — consider a different RPC provider.")
except Exception as e:
    print(f"  ❌ FAILED: {e}")

print()
print("=" * 50)
print("🏁 ALL TESTS COMPLETE")
print("=" * 50)
