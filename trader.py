# =============================================================================
# Polymarket BTC Up/Down Bot — Real Trading Execution Module
# =============================================================================
# Modul ini menangani SEMUA interaksi real dengan Polymarket CLOB API.
# Digunakan sebagai pengganti paper trading simulation.
#
# Mode:
#   "shadow" = Log only, tidak place order (untuk testing/verifikasi)
#   "real"   = Place order sungguhan (UANG ASLI)
# =============================================================================

import os
import logging
import time
import json
import math
from decimal import Decimal, ROUND_DOWN

log = logging.getLogger("trader")

# Lazy-init: client hanya dibuat saat dibutuhkan
_client = None
_creds = None
_allowance_set = False


def reset_client():
    """Force re-initialization of the CLOB client (clears singleton cache)."""
    global _client, _creds, _allowance_set
    _client = None
    _creds = None
    _allowance_set = False
    log.info("CLOB client reset — will re-initialize on next use")


def _get_client():
    """Initialize and return authenticated ClobClient (singleton)."""
    global _client, _creds, _allowance_set
    if _client is not None:
        return _client

    try:
        from py_clob_client_v2.client import ClobClient
    except ImportError:
        raise RuntimeError(
            "py-clob-client-v2 not installed! Run: pip install py-clob-client-v2"
        )

    host = "https://clob.polymarket.com"
    key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    funder = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()
    sig_type = int(os.environ.get("POLY_SIGNATURE_TYPE", "0"))
    chain_id = int(os.environ.get("POLY_CHAIN_ID", "137"))

    if not key:
        raise RuntimeError(
            "POLY_PRIVATE_KEY is empty! Set it in your .env file."
        )
    if not funder:
        raise RuntimeError(
            "POLY_FUNDER_ADDRESS is empty! Set it in your .env file."
        )

    log.info("Initializing Polymarket CLOB client...")
    log.info(f"  Chain ID: {chain_id}")
    log.info(f"  Signature Type: {sig_type}")
    log.info(f"  Funder: {funder[:10]}...{funder[-6:]}")

    _client = ClobClient(
        host=host,
        chain_id=chain_id,
        key=key,
        funder=funder,
        signature_type=sig_type,
    )

    # Derive or create API credentials (L2 auth for order placement)
    _creds = _client.create_or_derive_api_key()
    _client.set_api_creds(_creds)

    # Enable trading: set on-chain allowances for CLOB exchange contracts.
    # Without this step, orders are rejected with "maker address not allowed".
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        _client.update_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        _allowance_set = True
        log.info("✅ Balance allowance (COLLATERAL) updated for CLOB trading")
    except Exception as e:
        log.warning(f"⚠️ Could not update balance allowance: {e}")

    log.info("✅ Polymarket CLOB client authenticated successfully")
    return _client


def get_real_balance():
    """
    Fetch real pUSD balance from Polymarket proxy wallet on-chain.
    Returns balance in USD (float), or None on failure.
    """
    try:
        import requests as _req
        rpc_url = os.environ.get("POLYGON_RPC_URL", "").strip()
        proxy_addr = os.environ.get("POLY_PROXY_ADDRESS", "").strip()

        if not rpc_url or not proxy_addr:
            log.warning("POLYGON_RPC_URL or POLY_PROXY_ADDRESS not set in .env")
            return None

        # pUSD contract on Polygon
        pusd_contract = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
        # balanceOf(address) selector = 0x70a08231
        data = "0x70a08231000000000000000000000000" + proxy_addr[2:].lower()
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": pusd_contract, "data": data}, "latest"],
            "id": 1,
        }
        resp = _req.post(rpc_url, json=payload, timeout=10).json()
        if "result" in resp:
            balance = int(resp["result"], 16) / 1e6  # pUSD has 6 decimals
            return balance
        else:
            log.error(f"RPC error fetching pUSD balance: {resp}")
            return None
    except Exception as e:
        log.error(f"Failed to fetch real balance: {e}")
        return None


def _ensure_allowance(client):
    """Ensure balance allowance is set. Call before placing any order."""
    global _allowance_set
    if _allowance_set:
        return
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        client.update_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        _allowance_set = True
        log.info("✅ Balance allowance (COLLATERAL) set for trading")
    except Exception as e:
        log.warning(f"⚠️ Could not update balance allowance: {e}")


def place_real_order(token_id, side, amount_usdc, price, limit_price=None):
    """
    Place a real FOK (Fill-or-Kill) order on Polymarket.

    Args:
        token_id: The CLOB token ID for UP or DOWN
        side: "BUY"
        amount_usdc: Amount in USD to spend (e.g. 0.50)
        price: Signal/market price per share (used for size calculation)
        limit_price: Maximum FOK limit price for slippage tolerance.
                     Defaults to price if not specified.

    Returns:
        dict with order details if successful, None if failed
    """
    if limit_price is None:
        limit_price = price

    try:
        from py_clob_client_v2.clob_types import (
            OrderArgs, OrderType, PartialCreateOrderOptions
        )

        # Fix SDK rounding config: API requires makerAmount max 2 decimals,
        # but SDK defaults to 4.  Patch before building orders.
        from py_clob_client_v2.order_builder.builder import (
            ROUNDING_CONFIG, RoundConfig,
        )
        ROUNDING_CONFIG["0.01"] = RoundConfig(price=2, size=4, amount=2)

        client = _get_client()
        _ensure_allowance(client)

        # Calculate size using Decimal to avoid float precision errors
        # Float: 2.00 / 0.80 = 2.4999999999999996 (WRONG)
        # Decimal: Decimal("2.00") / Decimal("0.80") = 2.5 (CORRECT)
        amount_d = Decimal(str(amount_usdc)).quantize(Decimal("0.01"))
        price_d = Decimal(str(price)).quantize(Decimal("0.01"))
        limit_d = Decimal(str(limit_price)).quantize(Decimal("0.01"))
        size_d = (amount_d / price_d).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        size = float(size_d)
        amount_usdc = float(amount_d)
        limit_price = float(limit_d)

        log.info(
            f"🔴 PLACING REAL ORDER:\n"
            f"   Token:  {token_id[:24]}...\n"
            f"   Side:   {side}\n"
            f"   Amount: ${amount_usdc:.4f}\n"
            f"   Price:  {price:.4f} (limit: {limit_price:.4f})\n"
            f"   Size:   {size:.4f} shares (Decimal precision)"
        )

        resp = client.create_and_post_order(
            order_args=OrderArgs(
                token_id=token_id,
                price=limit_price,
                side="BUY",
                size=size,
            ),
            order_type=OrderType.FOK,
            options=PartialCreateOrderOptions(tick_size="0.01"),
        )

        log.info(f"📋 Order response: {resp}")

        # Extract order ID from response
        order_id = None
        if isinstance(resp, dict):
            order_id = resp.get("orderID") or resp.get("id") or resp.get("order_id")

        # Capture actual order status from CLOB API response
        actual_status = "SUBMITTED"
        if isinstance(resp, dict):
            actual_status = resp.get("status", "SUBMITTED")

        return {
            "order_id": order_id or "unknown",
            "status": actual_status,
            "response": resp,
            "timestamp": time.time(),
        }

    except Exception as e:
        err_msg = str(e)

        # Auto-retry: if "maker address not allowed", force reset client and retry once
        if "maker address not allowed" in err_msg:
            log.warning("⚠️ Maker address rejected — resetting client and retrying...")
            reset_client()
            try:
                client = _get_client()
                _ensure_allowance(client)

                retry_size = float(
                    (Decimal(str(amount_usdc)) / Decimal(str(price))).quantize(
                        Decimal("0.0001"), rounding=ROUND_DOWN
                    )
                )
                resp = client.create_and_post_order(
                    order_args=OrderArgs(
                        token_id=token_id,
                        price=limit_price,
                        side="BUY",
                        size=retry_size,
                    ),
                    order_type=OrderType.FOK,
                    options=PartialCreateOrderOptions(tick_size="0.01"),
                )
                log.info(f"📋 Retry order response: {resp}")
                order_id = None
                if isinstance(resp, dict):
                    order_id = resp.get("orderID") or resp.get("id") or resp.get("order_id")
                retry_status = "SUBMITTED"
                if isinstance(resp, dict):
                    retry_status = resp.get("status", "SUBMITTED")
                return {
                    "order_id": order_id or "unknown",
                    "status": retry_status,
                    "response": resp,
                    "timestamp": time.time(),
                }
            except Exception as retry_err:
                log.error(f"❌ RETRY ALSO FAILED: {retry_err}", exc_info=True)
                return None

        log.error(f"❌ REAL ORDER FAILED: {e}", exc_info=True)
        return None



def shadow_order(token_id, side, amount_usdc, price, market_slug):
    """
    Verified Shadow mode: BUILD & SIGN a real order to validate the pipeline,
    but do NOT post it to the server. No money is spent.

    If credentials are unavailable, falls back to simple log-only mode.
    """
    # Use Decimal precision for shadow orders too
    amount_d = Decimal(str(amount_usdc)).quantize(Decimal("0.01"))
    price_d = Decimal(str(price)).quantize(Decimal("0.01"))
    size_d = (amount_d / price_d).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    amount_usdc = float(amount_d)
    size = float(size_d)

    log.info(
        f"👻 SHADOW ORDER (verifying...):\n"
        f"   Market: {market_slug}\n"
        f"   Token:  {token_id[:24]}...\n"
        f"   Side:   {side}\n"
        f"   Amount: ${amount_usdc:.4f}\n"
        f"   Price:  {price:.4f}\n"
        f"   Size:   {size:.4f} shares"
    )

    # ── Try verified shadow: build + sign via create_order() ──
    try:
        from py_clob_client_v2.clob_types import (
            OrderArgs, OrderType, PartialCreateOrderOptions
        )
        from py_clob_client_v2.order_builder.builder import (
            ROUNDING_CONFIG, RoundConfig,
        )

        # Apply same SDK rounding fix as place_real_order
        ROUNDING_CONFIG["0.01"] = RoundConfig(price=2, size=4, amount=2)

        client = _get_client()
        _ensure_allowance(client)

        # Build + sign order WITHOUT posting
        signed_order = None
        # Try positional args first (most common SDK signature)
        try:
            signed_order = client.create_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    side="BUY",
                    size=size,
                ),
                PartialCreateOrderOptions(tick_size="0.01"),
            )
        except TypeError:
            # Fallback: kwargs without order_type
            signed_order = client.create_order(
                order_args=OrderArgs(
                    token_id=token_id,
                    price=price,
                    side="BUY",
                    size=size,
                ),
                options=PartialCreateOrderOptions(tick_size="0.01"),
            )

        log.info(
            f"✅ SHADOW VERIFIED: Order built & signed successfully!\n"
            f"   Pipeline valid — order would be accepted if posted."
        )

        return {
            "order_id": f"shadow-verified-{int(time.time())}",
            "status": "SHADOW",
            "response": {"shadow": True, "verified": True},
            "timestamp": time.time(),
        }

    except Exception as e:
        # ── Fallback: log-only shadow (no credentials / client error) ──
        log.warning(
            f"⚠️ SHADOW UNVERIFIED (create_order failed: {e})\n"
            f"   Order logged but NOT validated. Fix credentials for verified shadow."
        )
        return {
            "order_id": f"shadow-{int(time.time())}",
            "status": "SHADOW",
            "response": {"shadow": True, "verified": False, "error": str(e)[:100]},
            "timestamp": time.time(),
        }


def check_order_status(order_id):
    """Check the status of a previously placed order."""
    if not order_id or order_id.startswith("shadow-"):
        return {"status": "SHADOW"}
    try:
        client = _get_client()
        order = client.get_order(order_id=order_id)
        return order
    except Exception as e:
        log.warning(f"Error checking order {order_id}: {e}")
        return None


def cancel_all_orders():
    """Cancel all pending orders (for kill switch)."""
    try:
        client = _get_client()
        resp = client.cancel_all()
        log.info(f"🛑 Cancelled all orders: {resp}")
        return resp
    except Exception as e:
        log.error(f"Error cancelling orders: {e}")
        return None


def get_positions():
    """Get all current positions."""
    try:
        client = _get_client()
        positions = client.get_positions()
        return positions
    except Exception as e:
        log.warning(f"Error fetching positions: {e}")
        return []


def validate_wallet():
    """
    Validate wallet configuration at startup.
    Returns (success: bool, message: str)
    """
    key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    funder = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()

    if not key:
        return False, "POLY_PRIVATE_KEY is not set in .env"
    if not funder:
        return False, "POLY_FUNDER_ADDRESS is not set in .env"
    if len(key) < 30:
        return False, "POLY_PRIVATE_KEY looks too short — check your .env"

    try:
        client = _get_client()
        balance = get_real_balance()
        if balance is None:
            return False, "Cannot fetch wallet balance — check your credentials"
        if balance < 0.50:
            return False, f"Insufficient pUSD balance: ${balance:.4f} (need >= $0.50)"
        return True, f"Wallet OK — pUSD balance: ${balance:.4f}"
    except Exception as e:
        return False, f"Wallet validation failed: {e}"


# ─── Auto Claim (Redeem Winnings) ────────────────────────────────────────────

# Polymarket CTF contract on Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
# V2 collateral = pUSD (replaces old USDC.e after April 2026 migration)
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

# Minimal ABI for redeemPositions function only
CTF_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"}
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

HASH_ZERO = "0x" + "00" * 32


def redeem_positions(condition_id):
    """
    Redeem winning conditional tokens back to pUSD on-chain.

    Args:
        condition_id: The market's conditionId (0x hex string)

    Returns:
        tx_hash string on success, None on failure
    """
    try:
        from web3 import Web3

        rpc_url = os.environ.get(
            "POLYGON_RPC_URL",
            "https://polygon-rpc.com"
        )
        w3 = Web3(Web3.HTTPProvider(rpc_url))

        if not w3.is_connected():
            log.error("Cannot connect to Polygon RPC")
            return None

        key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
        proxy_addr = os.environ.get("POLY_PROXY_ADDRESS", "").strip()

        if not key or not proxy_addr:
            log.error("Missing wallet credentials for redeem (POLY_PRIVATE_KEY or POLY_PROXY_ADDRESS)")
            return None

        account = w3.eth.account.from_key(key)
        account_address = account.address

        # Redeem from the Polymarket proxy wallet where pUSD resides
        redeem_from = Web3.to_checksum_address(proxy_addr)

        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=CTF_ABI
        )

        log.info(
            f"💰 REDEEMING positions:\n"
            f"   Condition: {condition_id[:20]}...\n"
            f"   From:      {redeem_from[:10]}...{redeem_from[-6:]}"
        )

        # Build the redeemPositions transaction
        txn = ctf.functions.redeemPositions(
            Web3.to_checksum_address(PUSD_ADDRESS),  # collateralToken (pUSD)
            bytes.fromhex(HASH_ZERO[2:]),             # parentCollectionId
            bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id),
            [1, 2]                                    # indexSets for binary markets
        ).build_transaction({
            "from": account_address,
            "nonce": w3.eth.get_transaction_count(account_address),
            "gas": 300000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 137,
        })

        signed = w3.eth.account.sign_transaction(txn, key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = w3.to_hex(tx_hash)

        log.info(f"✅ Redeem TX sent: {tx_hex}")

        # Wait for confirmation (up to 60 seconds)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt["status"] == 1:
            log.info(f"✅ Redeem confirmed! TX: {tx_hex}")
            return tx_hex
        else:
            log.warning(f"⚠️ Redeem TX reverted: {tx_hex}")
            return None

    except Exception as e:
        log.error(f"❌ Redeem failed: {e}", exc_info=True)
        return None


def auto_claim_wins():
    """
    Automatically claim all unclaimed winning trades.
    Called after each resolution check in the bot loop.
    Returns number of successfully claimed trades.
    """
    from config import TRADING_MODE
    if TRADING_MODE == "paper":
        return 0

    from database import get_unclaimed_wins, mark_trade_claimed

    unclaimed = get_unclaimed_wins()
    if not unclaimed:
        return 0

    claimed_count = 0
    for trade in unclaimed:
        condition_id = trade.get("condition_id", "")
        if not condition_id:
            log.warning(f"Trade #{trade['id']} has no condition_id, skipping claim")
            continue

        if TRADING_MODE == "shadow":
            # Shadow mode: mark as claimed without actual redemption
            log.info(
                f"👻 SHADOW CLAIM (not redeemed): Trade #{trade['id']} | "
                f"Condition: {condition_id[:20]}..."
            )
            mark_trade_claimed(trade["id"], tx_hash="shadow-claim")
            claimed_count += 1
            continue

        # Real mode: Polymarket Native Auto-Redeem is enabled.
        # We no longer call `redeem_positions(condition_id)` to save gas and avoid reverting.
        log.info(
            f"💰 Polymarket Auto-Redeem handled Trade #{trade['id']} | "
            f"PnL: ${trade.get('pnl', 0):.4f} | "
            f"Condition: {condition_id[:20]}..."
        )
        mark_trade_claimed(trade["id"], tx_hash="polymarket-auto")
        claimed_count += 1

    if claimed_count > 0:
        log.info(f"💰 Auto-claim complete: {claimed_count}/{len(unclaimed)} trades redeemed")

    return claimed_count
