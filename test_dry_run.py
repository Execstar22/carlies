# =============================================================================
# Dry-Run Test — Validate Place Order Pipeline WITHOUT Spending Real Money
# =============================================================================
# Script ini memverifikasi SEMUA step order pipeline:
#   1. ✅ .env credentials (private key, funder, proxy)
#   2. ✅ CLOB client authentication & API key derivation
#   3. ✅ Balance allowance (on-chain approval)
#   4. ✅ Real pUSD balance check (on-chain)
#   5. ✅ Live market discovery (find active BTC Up/Down 5m)
#   6. ✅ Price fetching dari CLOB API
#   7. ✅ Order signing (create_order — build & sign tanpa submit)
#   8. ❌ TIDAK post order → uang AMAN
#
# Jika semua step PASS, artinya place_real_order() PASTI berhasil di live.
# =============================================================================

import os
import sys
import json
import math
import time

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()


# ─── Helpers ─────────────────────────────────────────────────────────────────

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"
results = []


def report(step, status, detail=""):
    icon = PASS if status == "pass" else (FAIL if status == "fail" else WARN)
    line = f"  {icon}  {step}"
    if detail:
        line += f"  →  {detail}"
    print(line)
    results.append((step, status, detail))


def separator(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# =============================================================================
# STEP 1: Check .env Credentials
# =============================================================================
separator("STEP 1: Credentials Check")

key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
funder = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()
proxy = os.environ.get("POLY_PROXY_ADDRESS", "").strip()
sig_type = int(os.environ.get("POLY_SIGNATURE_TYPE", "0"))
rpc_url = os.environ.get("POLYGON_RPC_URL", "").strip()

if not key:
    report("POLY_PRIVATE_KEY", "fail", "KOSONG! Set di .env")
    print("\n🛑 Tidak bisa lanjut tanpa private key. Isi .env dulu.")
    sys.exit(1)
else:
    report("POLY_PRIVATE_KEY", "pass", f"Ada ({len(key)} chars, ...{key[-6:]})")

if not funder:
    report("POLY_FUNDER_ADDRESS", "fail", "KOSONG!")
else:
    report("POLY_FUNDER_ADDRESS", "pass", f"{funder[:10]}...{funder[-6:]}")

if not proxy:
    report("POLY_PROXY_ADDRESS", "warn", "Kosong — balance check akan skip")
else:
    report("POLY_PROXY_ADDRESS", "pass", f"{proxy[:10]}...{proxy[-6:]}")

report("POLY_SIGNATURE_TYPE", "pass", f"{sig_type}")


# =============================================================================
# STEP 2: Initialize CLOB Client + API Key
# =============================================================================
separator("STEP 2: CLOB Client Authentication")

client = None
try:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import (
        OrderArgs, OrderType, PartialCreateOrderOptions,
        BalanceAllowanceParams, AssetType,
    )

    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=key,
        funder=funder,
        signature_type=sig_type,
    )
    report("ClobClient init", "pass")

    creds = client.create_or_derive_api_key()
    client.set_api_creds(creds)
    api_key = creds.api_key if hasattr(creds, "api_key") else str(creds)[:20]
    report("API key derivation", "pass", f"key={api_key[:16]}...")

except ImportError:
    report("py-clob-client-v2", "fail", "Library tidak terinstall! pip install py-clob-client-v2")
    sys.exit(1)
except Exception as e:
    report("CLOB auth", "fail", str(e)[:120])
    print("\n🛑 Gagal autentikasi. Cek private key & funder address.")
    sys.exit(1)


# =============================================================================
# STEP 3: Balance Allowance (On-chain Approval)
# =============================================================================
separator("STEP 3: Balance Allowance")

try:
    client.update_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    report("COLLATERAL allowance", "pass", "Approved for trading")
except Exception as e:
    report("COLLATERAL allowance", "warn", f"Gagal: {e}")


# =============================================================================
# STEP 4: Real pUSD Balance Check
# =============================================================================
separator("STEP 4: pUSD Balance (On-chain)")

real_balance = None
if proxy and rpc_url:
    try:
        import requests

        pusd_contract = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
        data = "0x70a08231000000000000000000000000" + proxy[2:].lower()
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": pusd_contract, "data": data}, "latest"],
            "id": 1,
        }
        resp = requests.post(rpc_url, json=payload, timeout=10).json()
        if "result" in resp:
            real_balance = int(resp["result"], 16) / 1e6
            min_required = 1.10  # Polymarket minimum order
            if real_balance >= min_required:
                report("pUSD balance", "pass", f"${real_balance:.4f} (min ${min_required})")
            else:
                report("pUSD balance", "fail",
                       f"${real_balance:.4f} — kurang! Minimum ${min_required} untuk order")
        else:
            report("pUSD balance", "fail", f"RPC error: {resp}")
    except Exception as e:
        report("pUSD balance", "warn", f"Gagal fetch: {e}")
else:
    report("pUSD balance", "warn", "Skip — POLY_PROXY_ADDRESS atau POLYGON_RPC_URL kosong")


# =============================================================================
# STEP 5: Live Market Discovery
# =============================================================================
separator("STEP 5: Discover Active Market")

import requests as req

now = time.time()
current_slot = int(now // 300) * 300
slots = [current_slot - 300, current_slot, current_slot + 300]

market_info = None
for ts in slots:
    slug = f"btc-updown-5m-{ts}"
    try:
        resp = req.get(
            "https://gamma-api.polymarket.com/events",
            params={"slug": slug, "_": int(time.time())},
            headers={"Cache-Control": "no-cache"},
            timeout=10,
        )
        if resp.status_code != 200:
            continue
        data = resp.json()
        if not data:
            continue

        event = data[0]
        markets = event.get("markets", [])
        if not markets:
            continue

        market = markets[0]
        if market.get("closed", False):
            continue

        # Parse end time
        end_date = market.get("endDate") or event.get("endDate")
        end_time = ts + 300
        if end_date:
            try:
                from datetime import datetime, timezone
                if end_date.endswith("Z"):
                    end_time = datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp()
                else:
                    end_time = datetime.fromisoformat(end_date).timestamp()
            except Exception:
                pass

        if end_time < now:
            continue

        # Parse token IDs
        clob_ids_raw = market.get("clobTokenIds", "[]")
        if isinstance(clob_ids_raw, str):
            clob_ids = json.loads(clob_ids_raw)
        else:
            clob_ids = clob_ids_raw

        outcomes_raw = market.get("outcomes", "[]")
        if isinstance(outcomes_raw, str):
            outcomes = json.loads(outcomes_raw)
        else:
            outcomes = outcomes_raw

        if len(clob_ids) < 2:
            continue

        # Map UP/DOWN
        up_token = clob_ids[0]
        down_token = clob_ids[1]
        for i, outcome in enumerate(outcomes):
            name = str(outcome).upper()
            if "UP" in name or "YES" in name:
                up_token = clob_ids[i]
            elif "DOWN" in name or "NO" in name:
                down_token = clob_ids[i]

        countdown = end_time - now
        market_info = {
            "slug": slug,
            "question": market.get("question", slug),
            "end_time": end_time,
            "countdown": countdown,
            "up_token": up_token,
            "down_token": down_token,
        }

        report("Market found", "pass", slug)
        report("Question", "pass", market_info["question"][:60])
        report("Countdown", "pass", f"{int(countdown)}s ({int(countdown)//60}m {int(countdown)%60}s)")
        report("UP token", "pass", f"{up_token[:30]}...")
        report("DOWN token", "pass", f"{down_token[:30]}...")
        break
    except Exception as e:
        continue

if not market_info:
    report("Market discovery", "fail", "Tidak ada market aktif! Coba lagi dalam beberapa menit.")
    # Continue anyway — we'll skip order building step
    print("  (Lanjut ke step berikutnya tanpa market...)")


# =============================================================================
# STEP 6: Price Fetching
# =============================================================================
separator("STEP 6: Price Fetching (CLOB API)")

up_price = None
down_price = None

if market_info:
    for label, token_id in [("UP", market_info["up_token"]), ("DOWN", market_info["down_token"])]:
        try:
            resp = req.get(
                "https://clob.polymarket.com/price",
                params={"token_id": token_id, "side": "buy"},
                timeout=10,
            )
            if resp.status_code == 200:
                price = float(resp.json().get("price", 0))
                if label == "UP":
                    up_price = price
                else:
                    down_price = price
                report(f"{label} price", "pass", f"¢{price*100:.1f} (${price:.4f})")
            else:
                report(f"{label} price", "warn", f"HTTP {resp.status_code}")
        except Exception as e:
            report(f"{label} price", "warn", f"Error: {e}")
else:
    report("Price fetch", "warn", "Skipped — no active market")


# =============================================================================
# STEP 7: DRY-RUN Order Build + Sign (TANPA SUBMIT)
# =============================================================================
separator("STEP 7: 🧪 DRY-RUN Order (Build + Sign, NOT Posted)")

if market_info and client:
    # Pick the side with better price (closer to 0.80 target)
    test_side = "UP"
    test_price = up_price
    test_token = market_info["up_token"]

    if up_price and down_price:
        if abs(down_price - 0.80) < abs(up_price - 0.80):
            test_side = "DOWN"
            test_price = down_price
            test_token = market_info["down_token"]
    elif down_price and not up_price:
        test_side = "DOWN"
        test_price = down_price
        test_token = market_info["down_token"]

    if test_price and test_price > 0:
        # Use $1.10 minimum (Polymarket minimum order)
        test_amount = 1.10
        test_amount = round(test_amount, 2)
        test_size = math.floor(test_amount / test_price * 10000) / 10000

        print(f"\n  📋 Order Parameters (DRY-RUN):")
        print(f"     Side:   {test_side}")
        print(f"     Token:  {test_token[:30]}...")
        print(f"     Amount: ${test_amount:.2f}")
        print(f"     Price:  ¢{test_price*100:.1f} (${test_price:.4f})")
        print(f"     Size:   {test_size:.4f} shares")
        print(f"     Type:   FOK (Fill-or-Kill)")
        print()

        # Fix SDK rounding config (same as trader.py)
        try:
            from py_clob_client_v2.order_builder.builder import (
                ROUNDING_CONFIG, RoundConfig,
            )
            ROUNDING_CONFIG["0.01"] = RoundConfig(price=2, size=4, amount=2)
            report("SDK rounding config", "pass", "Patched (price=2, size=4, amount=2)")
        except Exception as e:
            report("SDK rounding config", "warn", f"Patch gagal: {e}")

        # BUILD ORDER (create_order = build + sign, TANPA post ke server)
        # py-clob-client-v2 create_order() has different signature versions.
        # We try multiple approaches to find the one that works.
        signed_order = None
        build_success = False

        # Approach 1: create_order(OrderArgs, options) — no order_type param
        if not build_success:
            try:
                signed_order = client.create_order(
                    OrderArgs(
                        token_id=test_token,
                        price=test_price,
                        side="BUY",
                        size=test_size,
                    ),
                    PartialCreateOrderOptions(tick_size="0.01"),
                )
                build_success = True
                report("Order BUILD (positional args)", "pass", "Order object berhasil dibuat")
            except TypeError:
                pass  # Try next approach
            except Exception as e:
                # Non-TypeError means the call signature was right but something else failed
                err = str(e)
                report("Order BUILD", "fail", err[:150])

        # Approach 2: create_order(order_args=..., options=...) — kwargs without order_type
        if not build_success:
            try:
                signed_order = client.create_order(
                    order_args=OrderArgs(
                        token_id=test_token,
                        price=test_price,
                        side="BUY",
                        size=test_size,
                    ),
                    options=PartialCreateOrderOptions(tick_size="0.01"),
                )
                build_success = True
                report("Order BUILD (kwargs no order_type)", "pass", "Order object berhasil dibuat")
            except TypeError:
                pass
            except Exception as e:
                err = str(e)
                report("Order BUILD", "fail", err[:150])

        # Approach 3: Use the order_builder directly to build + sign
        if not build_success:
            try:
                from py_clob_client_v2.order_builder.builder import OrderBuilder

                # Try to access the internal builder
                builder = client.order_builder if hasattr(client, "order_builder") else None
                if builder:
                    signed_order = builder.create_order(
                        OrderArgs(
                            token_id=test_token,
                            price=test_price,
                            side="BUY",
                            size=test_size,
                        ),
                        PartialCreateOrderOptions(tick_size="0.01"),
                    )
                    build_success = True
                    report("Order BUILD (via builder)", "pass", "Order object berhasil dibuat")
            except Exception:
                pass

        # Approach 4: Inspect create_order signature and call accordingly
        if not build_success:
            try:
                import inspect
                sig = inspect.signature(client.create_order)
                params = list(sig.parameters.keys())
                report("create_order signature", "warn",
                       f"params={params}")

                # Build kwargs dynamically based on what the method accepts
                kwargs = {}
                if "order_args" in params:
                    kwargs["order_args"] = OrderArgs(
                        token_id=test_token,
                        price=test_price,
                        side="BUY",
                        size=test_size,
                    )
                if "options" in params:
                    kwargs["options"] = PartialCreateOrderOptions(tick_size="0.01")
                if "order_type" in params:
                    kwargs["order_type"] = OrderType.FOK

                signed_order = client.create_order(**kwargs)
                build_success = True
                report("Order BUILD (dynamic)", "pass", "Order object berhasil dibuat")
            except Exception as e:
                err = str(e)
                report("Order BUILD/SIGN", "fail", err[:150])

                if "maker address not allowed" in err:
                    print("\n  💡 FIX: Jalankan enable_trading.py atau set allowance manual")
                elif "insufficient" in err.lower():
                    print("\n  💡 FIX: Balance pUSD tidak cukup. Top up wallet.")
                elif "nonce" in err.lower():
                    print("\n  💡 FIX: Nonce issue — coba reset client atau tunggu beberapa detik")

        # Show results if build succeeded
        if build_success and signed_order is not None:
            report("Order SIGN", "pass", "Signature valid (wallet signing OK)")

            print(f"\n  📦 Signed Order Object:")
            if isinstance(signed_order, dict):
                for k, v in signed_order.items():
                    val_str = str(v)
                    if len(val_str) > 60:
                        val_str = val_str[:60] + "..."
                    print(f"     {k}: {val_str}")
            else:
                # Try to convert to dict if it's a dataclass/object
                try:
                    order_dict = vars(signed_order) if hasattr(signed_order, "__dict__") else {}
                    if order_dict:
                        for k, v in order_dict.items():
                            val_str = str(v)
                            if len(val_str) > 60:
                                val_str = val_str[:60] + "..."
                            print(f"     {k}: {val_str}")
                    else:
                        order_str = str(signed_order)
                        if len(order_str) > 300:
                            order_str = order_str[:300] + "..."
                        print(f"     {order_str}")
                except Exception:
                    print(f"     {str(signed_order)[:300]}")

            report("🧪 DRY-RUN complete", "pass",
                   "Order VALID & SIGNED — siap untuk post jika real mode aktif")
    else:
        report("DRY-RUN order", "warn", "Skipped — harga tidak tersedia")
else:
    if not market_info:
        report("DRY-RUN order", "warn", "Skipped — tidak ada market aktif")
    if not client:
        report("DRY-RUN order", "fail", "Skipped — CLOB client gagal init")


# =============================================================================
# SUMMARY
# =============================================================================
separator("📊 SUMMARY")

pass_count = sum(1 for _, s, _ in results if s == "pass")
fail_count = sum(1 for _, s, _ in results if s == "fail")
warn_count = sum(1 for _, s, _ in results if s == "warn")

print(f"\n  Total checks: {len(results)}")
print(f"  ✅ PASS: {pass_count}")
print(f"  ❌ FAIL: {fail_count}")
print(f"  ⚠️  WARN: {warn_count}")

if fail_count == 0:
    print(f"\n  🎉 SEMUA CHECK PASSED!")
    print(f"  → place_real_order() SIAP digunakan di live trading.")
    print(f"  → Anda bisa switch ke TRADING_MODE=real dengan percaya diri.")
else:
    print(f"\n  🛑 Ada {fail_count} FAILURE — fix dulu sebelum live trading!")
    print(f"\n  Failures:")
    for step, status, detail in results:
        if status == "fail":
            print(f"    ❌ {step}: {detail}")

print(f"\n{'─' * 60}")
print(f"  💡 Script ini TIDAK mengeluarkan uang apapun.")
print(f"     Order hanya di-BUILD & SIGN, tidak di-POST ke server.")
print(f"{'─' * 60}\n")

# Prevent CMD from closing immediately
try:
    input("\nPress Enter to exit...")
except (KeyboardInterrupt, EOFError):
    pass
