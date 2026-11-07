# =============================================================================
# Polymarket BTC Up/Down Bot — Core Engine
# =============================================================================

import time
import math
import json
import logging
import requests
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from concurrent.futures import ThreadPoolExecutor

# Shared HTTP session for API requests
_session = requests.Session()

from config import (
    GAMMA_API_BASE, CLOB_API_BASE, MARKET_SLUG_PREFIX,
    MARKET_INTERVAL_SECONDS, COUNTDOWN_BUY_START, COUNTDOWN_BUY_END,
    PRICE_FLOOR, PRICE_TARGET, PRICE_CEILING,
    BET_AMOUNT, POLL_INTERVAL, MARKET_SCAN_INTERVAL,
    TRADING_MODE, REAL_BET_AMOUNT, MAX_BET_AMOUNT, MAX_DAILY_LOSS,
    STOP_LOSS_BALANCE,
    get_trading_mode,
    WS_CLOB_URL, WS_RECONNECT_DELAY, WS_MAX_RECONNECT, WS_PING_INTERVAL,
    MIN_BOOK_DEPTH_MULTIPLIER,
)
from database import (
    get_current_balance, record_trade, resolve_trade,
    get_pending_trades, get_setting, update_setting,
    record_skipped_event, reset_paper_trading,
    record_real_trade, get_daily_loss_total, get_daily_profit_total,
    is_slug_already_traded, get_recent_traded_slugs
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BOT] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("polybot")

# #region agent log
def _agent_log(hypothesisId, message, data=None, runId="pre-fix"):
    try:
        payload = {
            "sessionId": "1aece9",
            "timestamp": int(time.time() * 1000),
            "location": "bot.py",
            "runId": runId,
            "hypothesisId": hypothesisId,
            "message": message,
            "data": data or {},
        }
        with open("debug-1aece9.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# #endregion agent log

# ─── Cached On-Chain Balance (for stop profit without RPC spam) ────────────
_cached_chain_balance = None
_cached_chain_balance_ts = 0
_CHAIN_BALANCE_CACHE_TTL = 10  # seconds — refresh on-chain balance every 10s


def get_cached_chain_balance():
    """Return on-chain balance from cache, refreshing every 30 seconds."""
    global _cached_chain_balance, _cached_chain_balance_ts
    now = time.time()
    if _cached_chain_balance is None or (now - _cached_chain_balance_ts) >= _CHAIN_BALANCE_CACHE_TTL:
        try:
            from trader import get_real_balance
            bal = get_real_balance()
            if bal is not None:
                _cached_chain_balance = bal
                _cached_chain_balance_ts = now
        except Exception as e:
            log.debug(f"Cached balance refresh failed: {e}")
    return _cached_chain_balance


class BotState:
    """Shared bot state accessible from dashboard."""
    def __init__(self):
        self.running = True
        self.current_market = None        # dict with slug, question, end_time, tokens
        self.up_price = None
        self.down_price = None
        self.countdown = None             # seconds remaining
        self.last_error = None
        self.last_update = None
        self.traded_slugs = OrderedDict()    # slug → timestamp (insertion-ordered for FIFO cleanup)
        self.skipped_slugs = OrderedDict()    # Track slugs already logged as skipped
        self.disqualified_slugs = OrderedDict()  # Track slugs disqualified before buy window
        self.notifications = []           # Store recent notifications
        self.lock = threading.Lock()
        # Display-only prices: fetched via REST midpoint (stable, no WS flicker).
        # Completely independent from up_price/down_price used by trading logic.
        self.display_up_price = None
        self.display_down_price = None

    def to_dict(self):
        with self.lock:
            return {
                "running": self.running,
                "current_market": self.current_market,
                "up_price": self.up_price,
                "down_price": self.down_price,
                "display_up_price": self.display_up_price,
                "display_down_price": self.display_down_price,
                "countdown": self.countdown,
                "countdown_display": self._fmt_countdown(),
                "last_error": self.last_error,
                "last_update": self.last_update,
                "notifications": self.notifications[-20:],
            }

    def _fmt_countdown(self):
        if self.countdown is None:
            return "--:--"
        m = int(self.countdown) // 60
        s = int(self.countdown) % 60
        return f"{m}:{s:02d}"


# Global bot state
bot_state = BotState()

# Global WebSocket price feed instance
_ws_feed = None

# Thread pool for parallel order execution
_order_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="order")

def add_notification(msg):
    with bot_state.lock:
        now_str = datetime.now().strftime("%H:%M:%S")
        bot_state.notifications.append({"msg": msg, "time": now_str})
        if len(bot_state.notifications) > 50:
            bot_state.notifications.pop(0)


# ─── Market Discovery ────────────────────────────────────────────────────────

def get_current_market_timestamps():
    """Generate timestamps for the current and next 5-minute market windows."""
    now = time.time()
    # Align to 5-minute intervals
    current_slot = int(now // MARKET_INTERVAL_SECONDS) * MARKET_INTERVAL_SECONDS
    # Generate a few slots around current time
    slots = [
        current_slot - MARKET_INTERVAL_SECONDS,
        current_slot,
        current_slot + MARKET_INTERVAL_SECONDS,
    ]
    return slots


def fetch_market_by_slug(slug):
    """Fetch market data from Gamma API by event slug."""
    try:
        url = f"{GAMMA_API_BASE}/events"
        # Add cache-busting query + no-cache headers to reduce stale resolution reads.
        params = {"slug": slug, "_": int(time.time())}
        headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
        resp = _session.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            # Guard against empty or non-JSON response body before parsing
            raw = resp.text.strip()
            if not raw:
                log.debug(f"Empty response body for market {slug}, skipping")
                return None
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as je:
                log.debug(f"Non-JSON response for market {slug} ({je}), skipping")
                return None
            if data and len(data) > 0:
                return data[0]
        elif resp.status_code == 429:
            msg = f"Rate limited (429) when fetching market {slug}. Backing off."
            log.warning(msg)
            add_notification(msg)
            time.sleep(2)
        return None
    except Exception as e:
        log.warning(f"Error fetching market {slug}: {e}")
        return None


def discover_active_market():
    """Find the currently active (not yet resolved) BTC Up/Down 5-min market."""
    timestamps = get_current_market_timestamps()

    for ts in timestamps:
        slug = f"{MARKET_SLUG_PREFIX}-{ts}"
        event = fetch_market_by_slug(slug)
        if not event:
            log.debug(f"No event found for slug: {slug}")
            continue

        markets = event.get("markets", [])
        if not markets:
            log.debug(f"Event {slug} has no markets")
            continue

        market = markets[0]
        end_date = market.get("endDate") or event.get("endDate")
        closed = market.get("closed", False)

        if closed:
            log.debug(f"Market {slug} is closed, skipping")
            continue

        # Parse end time
        end_time = None
        if end_date:
            try:
                if end_date.endswith("Z"):
                    end_time = datetime.fromisoformat(
                        end_date.replace("Z", "+00:00")
                    ).timestamp()
                else:
                    end_time = datetime.fromisoformat(end_date).timestamp()
            except Exception:
                end_time = ts + MARKET_INTERVAL_SECONDS

        if end_time is None:
            end_time = ts + MARKET_INTERVAL_SECONDS

        now = time.time()
        if end_time < now:
            log.debug(f"Market {slug} already ended (end_time={end_time}, now={now})")
            continue  # Already ended

        # Extract token IDs — API returns these as JSON strings, must parse
        clob_ids_raw = market.get("clobTokenIds", [])
        outcomes_raw = market.get("outcomes", [])

        # Parse JSON strings if needed
        if isinstance(clob_ids_raw, str):
            try:
                clob_ids = json.loads(clob_ids_raw)
            except (json.JSONDecodeError, TypeError):
                log.warning(f"Failed to parse clobTokenIds for {slug}: {clob_ids_raw[:100]}")
                continue
        else:
            clob_ids = clob_ids_raw

        if isinstance(outcomes_raw, str):
            try:
                outcomes = json.loads(outcomes_raw)
            except (json.JSONDecodeError, TypeError):
                log.warning(f"Failed to parse outcomes for {slug}: {outcomes_raw[:100]}")
                outcomes = []
        else:
            outcomes = outcomes_raw

        if len(clob_ids) < 2:
            log.warning(f"Market {slug} has less than 2 clobTokenIds: {clob_ids}")
            continue

        # Map outcomes to token IDs
        up_token = None
        down_token = None
        for i, outcome in enumerate(outcomes):
            name = outcome.upper() if isinstance(outcome, str) else str(outcome).upper()
            if "UP" in name or "YES" in name:
                up_token = clob_ids[i] if i < len(clob_ids) else None
            elif "DOWN" in name or "NO" in name:
                down_token = clob_ids[i] if i < len(clob_ids) else None

        if not up_token:
            up_token = clob_ids[0]
        if not down_token:
            down_token = clob_ids[1] if len(clob_ids) > 1 else None

        question = market.get("question", event.get("title", slug))

        log.info(f"✅ Found active market: {slug} | {question}")
        log.info(f"   UP token: {up_token[:20]}... | DOWN token: {down_token[:20]}...")
        log.info(f"   End time: {datetime.fromtimestamp(end_time, tz=timezone.utc).strftime('%H:%M:%S UTC')} | Countdown: {end_time - now:.0f}s")

        return {
            "slug": slug,
            "question": question,
            "end_time": end_time,
            "up_token": up_token,
            "down_token": down_token,
            "market_id": market.get("id"),
            "condition_id": market.get("conditionId", ""),
        }

    log.debug(f"No active market found for timestamps: {timestamps}")
    return None


# ─── Price Fetching ──────────────────────────────────────────────────────────

def fetch_price(token_id, side="buy"):
    """Fetch current price from CLOB API."""
    try:
        url = f"{CLOB_API_BASE}/price"
        params = {"token_id": token_id, "side": side}
        resp = _session.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            price = float(data.get("price", 0))
            return price
        elif resp.status_code == 429:
            msg = f"Rate limited (429) when fetching price. Backing off."
            log.warning(msg)
            add_notification(msg)
            time.sleep(2)
        return None
    except Exception as e:
        log.warning(f"Error fetching price for {token_id}: {e}")
        return None


def fetch_midpoint(token_id):
    """Fetch midpoint price from CLOB API."""
    try:
        url = f"{CLOB_API_BASE}/midpoint"
        params = {"token_id": token_id}
        resp = _session.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            mid = float(data.get("mid", 0))
            return mid
        elif resp.status_code == 429:
            msg = f"Rate limited (429) when fetching midpoint. Backing off."
            log.warning(msg)
            add_notification(msg)
            time.sleep(2)
        return None
    except Exception as e:
        log.warning(f"Error fetching midpoint for {token_id}: {e}")
        return None


# ─── Resolution Check ────────────────────────────────────────────────────────

# --- Order Book Depth Check -----------------------------------------------

def check_book_depth(token_id, bet_amount, side="buy"):
    """Check if order book has enough liquidity for our order.
    
    Returns:
        (has_enough: bool, total_liquidity: float)
    """
    min_liquidity = bet_amount * MIN_BOOK_DEPTH_MULTIPLIER
    try:
        url = f"{CLOB_API_BASE}/book"
        resp = _session.get(url, params={"token_id": token_id}, timeout=5)
        if resp.status_code != 200:
            return True, -1  # Optimistic: proceed if can't check
        
        book = resp.json()
        entries = book.get("asks", []) if side == "buy" else book.get("bids", [])
        
        total_liquidity = 0.0
        for entry in entries:
            try:
                entry_price = float(entry.get("price", 0))
                entry_size = float(entry.get("size", 0))
                total_liquidity += entry_price * entry_size
            except (ValueError, TypeError):
                continue
        
        has_enough = total_liquidity >= min_liquidity
        if not has_enough:
            log.info(
                f"\ud83d\udcca Book depth LOW: ${total_liquidity:.2f} available "
                f"(need ${min_liquidity:.2f} = {MIN_BOOK_DEPTH_MULTIPLIER}\u00d7 ${bet_amount:.2f})"
            )
        else:
            log.debug(
                f"\ud83d\udcca Book depth OK: ${total_liquidity:.2f} \u2265 ${min_liquidity:.2f}"
            )
        
        return has_enough, total_liquidity
    except Exception as e:
        log.warning(f"Book depth check failed: {e}")
        return True, -1  # Optimistic fallback


def check_and_resolve_pending():
    """Check if any pending trades' markets have resolved."""
    pending = get_pending_trades()
    for trade in pending:
        slug = trade["market_slug"]
        event = fetch_market_by_slug(slug)
        if not event:
            # If market end_time passed and we can't fetch, check by time
            # NOTE: Do not mark LOSS just because the API is temporarily unavailable.
            # Polymarket resolution can lag, and marking early LOSS makes results incorrect.
            # We'll keep the trade pending and re-check in the next cycles.
            if trade.get("end_time") and time.time() > trade["end_time"] + 1800:
                log.info(
                    f"Trade #{trade['id']} market expired, API unavailable for 30m+ past end; keeping PENDING"
                )
            continue

        markets = event.get("markets", [])
        if not markets:
            continue

        market = markets[0]

        # Detect resolution using both status flags and payout finality.
        is_closed = market.get("closed", False)
        uma_status = market.get("umaResolutionStatus", "")
        auto_resolved = market.get("automaticallyResolved", False)
        flag_resolved = is_closed and (uma_status == "resolved" or auto_resolved)

        # Parse outcomePrices early so we can use it as an additional resolution signal.
        outcome_prices_raw = market.get("outcomePrices", "[]")
        if isinstance(outcome_prices_raw, str):
            try:
                outcome_prices = json.loads(outcome_prices_raw)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = []
        else:
            outcome_prices = outcome_prices_raw

        payout_resolved = False
        if outcome_prices and len(outcome_prices) >= 2:
            try:
                p0 = float(outcome_prices[0])
                p1 = float(outcome_prices[1])
                # Treat as resolved when payouts are effectively final (0/1 style).
                # This catches cases where payout arrives before resolution flags update.
                payout_final = (
                    (p0 >= 0.99 and p1 <= 0.01) or
                    (p1 >= 0.99 and p0 <= 0.01)
                )
                # Only trust payout signal AFTER market end_time has passed.
                # Gamma API often returns near-final prices (e.g. 0.995/0.005)
                # while the market is still running, which is NOT actual resolution.
                if payout_final and trade.get("end_time") and trade["end_time"] > 0:
                    payout_resolved = time.time() > trade["end_time"]
                    if payout_final and not payout_resolved:
                        log.info(
                            f"Trade #{trade['id']} payout looks final "
                            f"({outcome_prices}) but market still running "
                            f"({trade['end_time'] - time.time():.0f}s left); "
                            f"deferring resolution"
                        )
                else:
                    payout_resolved = payout_final
            except (ValueError, TypeError):
                payout_resolved = False

        is_resolved = flag_resolved or payout_resolved

        if not is_resolved:
            # Market not resolved yet; keep PENDING and retry later.
            # DO NOT auto-mark LOSS based on a time threshold, because resolution can be delayed.
            end_date = market.get("endDate") or event.get("endDate")
            if end_date:
                try:
                    if end_date.endswith("Z"):
                        end_ts = datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp()
                    else:
                        end_ts = datetime.fromisoformat(end_date).timestamp()
                    if time.time() > end_ts + 1800:
                        log.info(
                            f"Trade #{trade['id']} market past end+30min, still not resolved; keeping PENDING"
                        )
                except Exception:
                    pass
            continue

        trade_side = trade["side"].upper()
        won = False

        if outcome_prices and len(outcome_prices) >= 2:
            try:
                up_payout = float(outcome_prices[0])
                down_payout = float(outcome_prices[1])
                # #region agent log
                _agent_log(
                    "H1",
                    "resolve_mapping_inputs",
                    {
                        "trade_id": trade.get("id"),
                        "slug": slug,
                        "trade_side": trade_side,
                        "market_outcomes_raw": market.get("outcomes"),
                        "market_clobTokenIds_raw": market.get("clobTokenIds"),
                        "market_outcomePrices_raw": market.get("outcomePrices"),
                        "parsed_outcome_prices": outcome_prices,
                        "assumed_up_payout": up_payout,
                        "assumed_down_payout": down_payout,
                        "is_closed": market.get("closed", False),
                        "umaResolutionStatus": market.get("umaResolutionStatus", ""),
                        "automaticallyResolved": market.get("automaticallyResolved", False),
                    },
                )
                # #endregion agent log
                if trade_side == "UP":
                    won = up_payout > 0.5
                else:
                    won = down_payout > 0.5
            except (ValueError, TypeError):
                # Fallback: check if closed past end_time, assume loss
                won = False

        log.info(f"Trade #{trade['id']} resolved: {'WIN' if won else 'LOSS'} "
                 f"(side={trade_side}, outcomePrices={outcome_prices})")
        resolve_trade(trade["id"], won=won)

        # ── Cap Scale Up: auto-raise bet on LOSS (shadow/real only) ──
        trade_mode = trade.get("trading_mode", "paper")
        if not won and trade_mode in ("real", "shadow"):
            cap_enabled = get_setting("cap_scale_up_enabled") != "false"
            cap_already_active = get_setting("cap_scale_up_active") == "true"
            if cap_enabled and not cap_already_active:
                cap_amount = float(get_setting("cap_scale_up_amount") or 5.0)
                current_bet = get_setting("real_bet_amount")
                # Save original bet so it can be restored later
                update_setting("cap_scale_up_original_bet", current_bet or "1.00")
                update_setting("real_bet_amount", f"{cap_amount:.4f}")
                update_setting("cap_scale_up_active", "true")
                msg = (
                    f"📈 CAP SCALE UP: Loss detected on trade #{trade['id']} → "
                    f"Bet raised from ${float(current_bet or 1):.2f} to ${cap_amount:.2f}"
                )
                log.info(msg)
                add_notification(msg)


# ─── Strategy ────────────────────────────────────────────────────────────────

def should_trade(countdown, up_price, down_price):
    """
    Strategy: buy when countdown is between 2:30 and 0:30
    AND price hits exactly $0.80-$0.82.
    Returns: ('UP', price) or ('DOWN', price) or (None, reason_string)
    """
    if countdown is None:
        return None, "no_countdown"

    if not (COUNTDOWN_BUY_END <= countdown <= COUNTDOWN_BUY_START):
        if countdown > COUNTDOWN_BUY_START:
            return None, f"countdown_too_early ({countdown:.0f}s > {COUNTDOWN_BUY_START}s)"
        else:
            return None, f"countdown_too_late ({countdown:.0f}s < {COUNTDOWN_BUY_END}s)"

    candidates = []
    
    # Deteksi apakah harga masuk dalam range aman (PRICE_FLOOR hingga PRICE_CEILING)
    if up_price is not None and PRICE_FLOOR <= up_price <= PRICE_CEILING:
        candidates.append(("UP", up_price))

    if down_price is not None and PRICE_FLOOR <= down_price <= PRICE_CEILING:
        candidates.append(("DOWN", down_price))

    if not candidates:
        reason_parts = []
        if up_price is not None:
            reason_parts.append(f"UP=${up_price:.2f}")
        if down_price is not None:
            reason_parts.append(f"DOWN=${down_price:.2f}")
        price_info = ", ".join(reason_parts) if reason_parts else "no prices"
        return None, f"price_out_of_range ({price_info} not in ${PRICE_FLOOR:.2f}-${PRICE_CEILING:.2f})"

    # Jika kedua opsi tersedia, pilih harga yang paling mendekati PRICE_TARGET
    candidates.sort(key=lambda x: abs(x[1] - PRICE_TARGET))
    return candidates[0][0], candidates[0][1]


def execute_paper_trade(market, side, price):
    """Execute a paper trade with fixed $2 bet."""
    balance = get_current_balance()
    bet_amount = BET_AMOUNT  # Fixed $2 per trade

    if balance < bet_amount:
        log.warning(f"Balance too low (${balance:.4f} < ${bet_amount}), skipping trade")
        return False

    shares = bet_amount / price  # shares = amount / price_per_share

    new_balance = record_trade(
        market_slug=market["slug"],
        market_question=market["question"],
        side=side,
        entry_price=price,
        shares=shares,
        bet_amount=bet_amount,
        token_id=market.get(f"{side.lower()}_token", ""),
        end_time=market.get("end_time", 0)
    )

    log.info(
        f"📊 PAPER TRADE: {side} @ ¢{price*100:.1f} | "
        f"Bet: ${bet_amount:.4f} | Shares: {shares:.4f} | "
        f"Balance: ${new_balance:.4f} | Market: {market['slug']}"
    )
    return True


def execute_real_trade(market, side, price):
    """Execute a REAL or SHADOW trade on Polymarket."""
    from trader import place_real_order, shadow_order, get_real_balance

    # Read bet amount from DB setting (changeable from dashboard)
    db_bet = get_setting("real_bet_amount")
    current_bet = float(db_bet) if db_bet else REAL_BET_AMOUNT
    bet_amount = min(current_bet, MAX_BET_AMOUNT)

    mode = get_trading_mode()
    
    # Daily loss safety check
    if MAX_DAILY_LOSS > 0:
        daily_loss = None
        
        # If real mode, try to use on-chain anchor first
        if mode == "real":
            try:
                daily_start = get_setting("real_daily_start_balance")
                chain_bal = get_real_balance()
                if chain_bal is not None and daily_start:
                    daily_loss = float(daily_start) - chain_bal
                    if daily_loss < 0:
                        daily_loss = 0  # wallet went UP, no loss
            except Exception:
                pass
        
        # Fallback for real mode, and primary for shadow mode: use DB method
        if daily_loss is None:
            daily_loss = get_daily_loss_total(mode)
        
        if daily_loss >= MAX_DAILY_LOSS:
            msg = (
                f"STOP LOSS REACHED (${daily_loss:.4f} >= ${MAX_DAILY_LOSS:.2f}), "
                f"skipping trade"
            )
            log.warning(msg)
            add_notification(msg)
            return False

    token_id = market.get(f"{side.lower()}_token", "")
    if not token_id:
        log.error(f"No token ID for side {side}")
        return False


    if mode == "shadow":
        result = shadow_order(token_id, "BUY", bet_amount, price, market["slug"])
        mode_label = "shadow"
    else:
        # Real mode — check real balance first
        real_balance = get_real_balance()
        if real_balance is None:
            log.error("Cannot fetch real balance, skipping trade")
            return False
        if real_balance < bet_amount:
            log.warning(f"Real balance too low (${real_balance:.4f} < ${bet_amount})")
            return False
        mode_label = "real"
        # FOK orders need headroom: use PRICE_CEILING as limit price so the
        # order tolerates small price moves.  Actual fill price will still be
        # the best available (often near `price`), NOT the ceiling.
        from config import PRICE_CEILING
        log.info(
            f"📤 FOK slippage: signal ¢{price*100:.1f} → "
            f"order limit ¢{PRICE_CEILING*100:.1f}"
        )
        result = place_real_order(token_id, "BUY", bet_amount, price, limit_price=PRICE_CEILING)


    if result is None:
        log.error("Order placement failed")
        return False

    # ── Check if FOK order was killed (not filled) ──
    resp_data = result.get("response", {})
    resp_status = ""
    if isinstance(resp_data, dict):
        resp_status = str(resp_data.get("status", "")).upper()

    killed_statuses = ("KILLED", "UNMATCHED", "NOT_MATCHED", "REJECTED")
    if resp_status in killed_statuses:
        order_id = result.get("order_id", "unknown")
        reason = (
            f"order_killed (FOK {resp_status}, "
            f"{side} @ ¢{price*100:.1f}, ${bet_amount:.2f})"
        )
        log.warning(
            f"💀 Order KILLED: {side} @ ¢{price*100:.1f} | "
            f"Bet: ${bet_amount:.4f} | Status: {resp_status} | "
            f"Order: {order_id[:16]}... | Market: {market['slug']}"
        )
        record_skipped_event(
            market_slug=market["slug"],
            market_question=market.get("question", ""),
            reason=reason,
            up_price=bot_state.up_price,
            down_price=bot_state.down_price,
            countdown=bot_state.countdown
        )
        add_notification(
            f"💀 Order KILLED: {side} @ ¢{price*100:.1f} | "
            f"${bet_amount:.2f} | {resp_status}"
        )
        return False

    # Calculate shares from signal price using Decimal precision.
    # Both real and shadow use the same formula for consistency.
    amount_d = Decimal(str(bet_amount)).quantize(Decimal("0.01"))
    price_d = Decimal(str(price)).quantize(Decimal("0.01"))
    shares = float((amount_d / price_d).quantize(Decimal("0.0001"), rounding=ROUND_DOWN))
    record_real_trade(
        market_slug=market["slug"],
        market_question=market["question"],
        side=side,
        entry_price=price,
        shares=shares,
        bet_amount=bet_amount,
        token_id=token_id,
        end_time=market.get("end_time", 0),
        order_id=result.get("order_id", ""),
        order_status=result.get("status", ""),
        trading_mode=mode_label,
        condition_id=market.get("condition_id", ""),
    )

    icon = "👻" if mode_label == "shadow" else "🔴"
    log.info(
        f"{icon} {mode_label.upper()} TRADE: {side} @ ¢{price*100:.1f} | "
        f"Bet: ${bet_amount:.4f} | Shares: {shares:.4f} | "
        f"Order: {result.get('order_id', 'N/A')[:16]}... | "
        f"Market: {market['slug']}"
    )
    return True


def kill_switch():
    """Emergency stop: pause bot and cancel all pending orders."""
    log.warning("🛑 KILL SWITCH ACTIVATED")
    update_setting("bot_running", "false")
    bot_state.running = False
    if get_trading_mode() == "real":
        try:
            from trader import cancel_all_orders
            cancel_all_orders()
        except Exception as e:
            log.error(f"Error during kill switch cancel: {e}")


# ─── Main Bot Loop ───────────────────────────────────────────────────────────

def _bootstrap_traded_slugs():
    """Load recently traded slugs from database to survive container restarts.

    Without this, a Docker restart wipes the in-memory traded_slugs set,
    allowing the bot to re-trade markets that are still active.
    """
    try:
        recent = get_recent_traded_slugs(hours=1)
        for slug in recent:
            bot_state.traded_slugs[slug] = time.time()
        if recent:
            log.info(f"🔄 Bootstrapped {len(recent)} traded slugs from DB (anti-double-order)")
    except Exception as e:
        log.warning(f"Failed to bootstrap traded slugs: {e}")


def bot_loop():
    """Main bot loop — runs in a background thread."""
    global _ws_feed
    log.info("🤖 Bot started!")

    # Initialize WebSocket price feed
    try:
        from ws_price_feed import WebSocketPriceFeed
        _ws_feed = WebSocketPriceFeed(
            ws_url=WS_CLOB_URL,
            reconnect_delay=WS_RECONNECT_DELAY,
            max_reconnect=WS_MAX_RECONNECT,
            ping_interval=WS_PING_INTERVAL,
        )
        _ws_feed.start()
        log.info("📡 WebSocket price feed initialized")
    except Exception as e:
        log.warning(f"⚠️ WebSocket init failed ({e}), using REST polling only")
        _ws_feed = None

    # Bootstrap traded slugs from DB to prevent double orders after restart
    _bootstrap_traded_slugs()

    last_scan = 0
    last_resolve_check = 0
    last_display_fetch = 0       # Throttle REST midpoint calls for display prices

    while True:
        try:
            # Check if bot should be running
            running_flag = get_setting("bot_running")
            
            # --- Scheduled Balance Reset ---
            target_time = datetime(2026, 4, 26, 17, 0, 0, tzinfo=timezone.utc).timestamp()
            has_reset = get_setting("has_reset_april27")
            if time.time() >= target_time and has_reset != "true":
                log.info("⏰ Scheduled event: Resetting balance to $10.0 (April 27, 2026 00:00 WIB reached)!")
                reset_paper_trading(10.0)
                update_setting("has_reset_april27", "true")
            # -------------------------------

            # --- Cap Scale Up: Midnight WIB Reset ---
            from datetime import timedelta as _td_cap
            _now_utc_cap = datetime.now(timezone.utc)
            _now_wib_cap = _now_utc_cap + _td_cap(hours=7)
            _today_wib_cap = _now_wib_cap.strftime("%Y-%m-%d")
            _cap_reset_date = get_setting("cap_scale_up_reset_date")
            if _cap_reset_date != _today_wib_cap and _now_wib_cap.hour == 0:
                if get_setting("cap_scale_up_active") == "true":
                    original_bet = get_setting("cap_scale_up_original_bet")
                    if original_bet:
                        update_setting("real_bet_amount", original_bet)
                    update_setting("cap_scale_up_active", "false")
                    update_setting("cap_scale_up_original_bet", "")
                    msg = f"🔄 CAP SCALE UP reset (midnight WIB). Bet restored to ${float(original_bet or 1):.2f}"
                    log.info(msg)
                    add_notification(msg)
                update_setting("cap_scale_up_reset_date", _today_wib_cap)
            # -----------------------------------------
            
            bot_state.running = running_flag != "false"

            if not bot_state.running:
                time.sleep(POLL_INTERVAL)
                continue

            now = time.time()

            # ── Scan for new markets periodically ──
            if now - last_scan > MARKET_SCAN_INTERVAL or bot_state.current_market is None:
                market = discover_active_market()
                if market:
                    with bot_state.lock:
                        if (bot_state.current_market is None or
                                bot_state.current_market["slug"] != market["slug"]):
                            log.info(f"📍 New market: {market['question']} "
                                     f"(ends at {datetime.fromtimestamp(market['end_time'], tz=timezone.utc).strftime('%H:%M:%S UTC')})")
                            # Subscribe WebSocket to new market tokens
                            if _ws_feed is not None:
                                _ws_feed.subscribe(
                                    market["up_token"],
                                    market["down_token"]
                                )
                        bot_state.current_market = market
                else:
                    with bot_state.lock:
                        bot_state.current_market = None
                        bot_state.countdown = None
                    if not getattr(bot_state, '_waiting_market_logged', False):
                        log.info("⏳ Waiting for the next market to start...")
                        bot_state._waiting_market_logged = True
                
                # Reset the waiting log state if we found a market
                if market:
                    bot_state._waiting_market_logged = False

                last_scan = now

            # ── Check resolution of pending trades ──
            if now - last_resolve_check > 5:
                check_and_resolve_pending()

                # ── Auto-claim winnings after resolution ──
                if get_trading_mode() in ("real", "shadow"):
                    try:
                        from trader import auto_claim_wins
                        auto_claim_wins()
                    except Exception as e:
                        log.warning(f"Auto-claim error: {e}")

                    # ── Balance sync: correct ledger drift vs on-chain ──
                    # MUST run AFTER auto-claim so on-chain balance includes
                    # claimed winnings. Running before claim sees lower on-chain
                    # and over-corrects starting_balance downward.
                    try:
                        from balance_sync import sync_real_starting_balance
                        sync_real_starting_balance()
                    except Exception as e:
                        log.warning(f"Post-claim balance sync failed: {e}")
                
                last_resolve_check = now

            # ── Fetch prices for current market ──
            market = bot_state.current_market
            if not market:
                with bot_state.lock:
                    bot_state.up_price = None
                    bot_state.down_price = None
                    bot_state.display_up_price = None
                    bot_state.display_down_price = None
                    bot_state.countdown = None
                    bot_state.last_update = datetime.utcnow().isoformat()
                time.sleep(POLL_INTERVAL)
                continue

            # Calculate countdown
            countdown = market["end_time"] - time.time()
            if countdown < 0:
                # Market expired, clear and rescan
                with bot_state.lock:
                    bot_state.current_market = None
                    bot_state.countdown = None
                last_scan = 0  # Force immediate rescan
                time.sleep(1)
                continue

            # Fetch prices -- prefer WebSocket, fallback to REST
            ws_available = (
                _ws_feed is not None
                and _ws_feed.connected
                and not _ws_feed.use_rest_fallback
            )

            if ws_available:
                # Real-time prices from WebSocket cache
                up_price = _ws_feed.get_price(market["up_token"], side="buy")
                down_price = _ws_feed.get_price(market["down_token"], side="buy")

                # Check staleness: if WS data is older than 10s, fallback to REST
                up_age = _ws_feed.get_age(market["up_token"])
                down_age = _ws_feed.get_age(market["down_token"])
                ws_stale = (
                    (up_age is not None and up_age > 10) or
                    (down_age is not None and down_age > 10)
                )

                if up_price is None or down_price is None or ws_stale:
                    # WS has no data yet or data is stale -- use REST
                    up_price = fetch_price(market["up_token"])
                    down_price = fetch_price(market["down_token"])
                    if (up_price is None or up_price == 0):
                        up_price = fetch_midpoint(market["up_token"])
                    if (down_price is None or down_price == 0):
                        down_price = fetch_midpoint(market["down_token"])
            else:
                # REST fallback
                up_price = fetch_price(market["up_token"])
                down_price = fetch_price(market["down_token"])

                # If CLOB price fails, try midpoint
                if up_price is None or up_price == 0:
                    up_price = fetch_midpoint(market["up_token"])
                if down_price is None or down_price == 0:
                    down_price = fetch_midpoint(market["down_token"])

            # ── Display prices: REST midpoint only (no WebSocket) ──
            # Fetched every 3s to avoid rate limiting. Midpoint = avg(bid,ask),
            # naturally stable and free from best_ask spikes on illiquid books.
            if now - last_display_fetch >= 3:
                d_up = fetch_midpoint(market["up_token"])
                d_down = fetch_midpoint(market["down_token"])
                with bot_state.lock:
                    if d_up is not None and d_up > 0:
                        bot_state.display_up_price = d_up
                    if d_down is not None and d_down > 0:
                        bot_state.display_down_price = d_down
                    # If only one side succeeded, derive the other
                    if bot_state.display_up_price and not bot_state.display_down_price:
                        bot_state.display_down_price = 1.0 - bot_state.display_up_price
                    elif bot_state.display_down_price and not bot_state.display_up_price:
                        bot_state.display_up_price = 1.0 - bot_state.display_down_price
                last_display_fetch = now

            with bot_state.lock:
                bot_state.up_price = up_price
                bot_state.down_price = down_price
                bot_state.countdown = countdown
                bot_state.last_error = None
                bot_state.last_update = datetime.utcnow().isoformat()

            slug = market["slug"]

            # ── Stop Profit Guard (runs every iteration, uses cached on-chain balance) ──
            stop_profit_on = get_setting("stop_profit_enabled") == "true"
            if stop_profit_on and get_trading_mode() in ("real", "shadow"):
                from datetime import timedelta as _td
                now_utc = datetime.now(timezone.utc)
                today_wib = (now_utc + _td(hours=7)).strftime("%Y-%m-%d")

                # ── Persistent flag: if stop profit was already reached today, stay stopped ──
                reached_date = get_setting("stop_profit_reached_date")
                if reached_date == today_wib:
                    if not getattr(bot_state, '_stop_profit_logged', False):
                        log.info(
                            f"🎯 Stop Profit already reached today ({today_wib}). "
                            f"Pausing trades until midnight WIB."
                        )
                        bot_state._stop_profit_logged = True
                    # Still record skipped event so dashboard stays populated
                    if slug not in bot_state.skipped_slugs:
                        record_skipped_event(
                            market_slug=slug,
                            market_question=market.get("question", ""),
                            reason="stop_profit_reached",
                            up_price=bot_state.up_price,
                            down_price=bot_state.down_price,
                            countdown=bot_state.countdown
                        )
                        bot_state.skipped_slugs[slug] = time.time()
                    time.sleep(POLL_INTERVAL)
                    continue

                sp_target = float(get_setting("stop_profit_target") or 2.5)
                daily_pnl = None
                try:
                    # Check if anchor needs reset (new day or not set)
                    anchor_date = get_setting("real_daily_start_date")
                    daily_start = get_setting("real_daily_start_balance")

                    if anchor_date != today_wib or not daily_start:
                        # NEW DAY: refresh anchor from on-chain (force fresh, not cache)
                        # GUARD: Only set anchor if no real trades exist today yet.
                        # If trades exist, the bot restarted mid-day — do NOT overwrite
                        # the anchor with current (post-trade) on-chain balance.
                        from database import get_conn
                        _conn = get_conn()
                        today_trade_count = _conn.execute(
                            "SELECT COUNT(*) FROM trades "
                            "WHERE trading_mode IN ('real','shadow') "
                            "AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')"
                        ).fetchone()[0]
                        _conn.close()

                        if today_trade_count > 0 and not daily_start:
                            # Mid-day restart with no anchor — can't safely determine
                            # start-of-day balance. Log warning and skip stop profit.
                            log.warning(
                                f"Daily anchor missing but {today_trade_count} trades exist today. "
                                f"Cannot safely set anchor mid-day. Skipping stop profit check."
                            )
                        elif today_trade_count == 0:
                            # Truly a new day, no trades yet — safe to set anchor
                            from trader import get_real_balance
                            chain_bal = get_real_balance()
                            if chain_bal is not None:
                                update_setting("real_daily_start_balance", f"{chain_bal:.6f}")
                                update_setting("real_daily_start_date", today_wib)
                                daily_start = f"{chain_bal:.6f}"
                                bot_state._stop_profit_logged = False
                                log.info(
                                    f"Daily PnL reset (midnight WIB). "
                                    f"New anchor: ${chain_bal:.4f} for {today_wib}"
                                )
                        else:
                            # anchor_date != today but trades exist — day rolled over,
                            # update date but keep existing anchor if set
                            update_setting("real_daily_start_date", today_wib)
                            log.info(f"Daily date updated to {today_wib}, anchor kept: ${float(daily_start):.4f}")

                    # ── Primary: on-chain PnL (ground truth when stable) ──
                    chain_bal = get_cached_chain_balance()
                    if chain_bal is not None and daily_start:
                        daily_pnl = chain_bal - float(daily_start)
                        log.debug(
                            f"Stop Profit (on-chain): "
                            f"${chain_bal:.4f} - ${float(daily_start):.4f} "
                            f"= PnL ${daily_pnl:.4f} / target ${sp_target:.2f}"
                        )
                except Exception as e:
                    log.debug(f"On-chain stop profit check failed: {e}")

                # ── Cross-check: DB PnL (mendeteksi WIN lebih cepat) ──
                # DB sudah tahu trade WIN saat resolve, walau uang belum masuk wallet.
                # Gunakan MAX(on-chain, DB) agar stop profit tidak telat trigger.
                # NOTE: Must use current mode so shadow PnL is checked correctly.
                current_mode = get_trading_mode()
                db_pnl = get_daily_profit_total(trading_mode=current_mode)
                if daily_pnl is None:
                    daily_pnl = db_pnl
                    log.debug(f"Stop Profit (DB only, mode={current_mode}): PnL=${daily_pnl:.4f}")
                elif db_pnl > daily_pnl:
                    log.info(
                        f"⚠️ Stop Profit cross-check: DB PnL ${db_pnl:.4f} > "
                        f"on-chain PnL ${daily_pnl:.4f} — using DB (unclaimed wins detected)"
                    )
                    daily_pnl = db_pnl

                if daily_pnl >= sp_target:
                    # Set persistent flag so it survives restart
                    update_setting("stop_profit_reached_date", today_wib)
                    if not getattr(bot_state, '_stop_profit_logged', False):
                        msg = (
                            f"🎯 Stop Profit reached! "
                            f"Daily PnL ${daily_pnl:.4f} >= target ${sp_target:.2f}. "
                            f"Pausing trades until midnight WIB."
                        )
                        log.info(msg)
                        add_notification(msg)
                        bot_state._stop_profit_logged = True
                    # Still record skipped event so dashboard stays populated
                    if slug not in bot_state.skipped_slugs:
                        record_skipped_event(
                            market_slug=slug,
                            market_question=market.get("question", ""),
                            reason="stop_profit_reached",
                            up_price=bot_state.up_price,
                            down_price=bot_state.down_price,
                            countdown=bot_state.countdown
                        )
                        bot_state.skipped_slugs[slug] = time.time()
                    time.sleep(POLL_INTERVAL)
                    continue
                else:
                    bot_state._stop_profit_logged = False


            # ── Balance-Based Stop Loss Guard ──
            # If STOP_LOSS_BALANCE > 0, check on-chain balance every loop.
            # When balance drops to or below this floor, KILL SWITCH activates.
            if STOP_LOSS_BALANCE > 0 and get_trading_mode() in ("real", "shadow"):
                chain_bal = get_cached_chain_balance()
                if chain_bal is not None and chain_bal <= STOP_LOSS_BALANCE:
                    msg = (
                        f"🛑 STOP LOSS BALANCE TRIGGERED! "
                        f"On-chain balance ${chain_bal:.4f} <= floor ${STOP_LOSS_BALANCE:.2f}. "
                        f"Killing bot to protect remaining funds."
                    )
                    log.warning(msg)
                    add_notification(msg)
                    kill_switch()
                    # Record skip for dashboard visibility
                    record_skipped_event(
                        market_slug=slug,
                        market_question=market.get("question", ""),
                        reason=f"stop_loss_balance (${chain_bal:.4f} <= ${STOP_LOSS_BALANCE:.2f})",
                        up_price=bot_state.up_price,
                        down_price=bot_state.down_price,
                        countdown=bot_state.countdown
                    )
                    time.sleep(POLL_INTERVAL)
                    continue

            # ── Strategy check ──
            if slug not in bot_state.traded_slugs:
                # ── Database-level dedup guard (survives restarts) ──
                if is_slug_already_traded(slug):
                    log.warning(f"⛔ DB DEDUP: {slug} already traded in DB, syncing to memory")
                    bot_state.traded_slugs[slug] = time.time()
                    time.sleep(POLL_INTERVAL)
                    continue

                side_or_none, price_or_reason = should_trade(countdown, up_price, down_price)
                if side_or_none and isinstance(price_or_reason, (int, float)):
                    # ── Scheduler Guard (Wait until 00:00 WIB) ──
                    scheduler_on = get_setting("scheduler_enabled") == "true"
                    if scheduler_on:
                        from datetime import timedelta
                        now_utc = datetime.now(timezone.utc)
                        now_wib = now_utc + timedelta(hours=7)
                        wib_hour = now_wib.hour
                        
                        # If it is exactly 00:xx WIB, disable the scheduler and resume trading forever
                        if wib_hour == 0:
                            update_setting("scheduler_enabled", "false")
                            log.info("⏰ 00:00 WIB reached! Scheduler finished. Resuming normal trades.")
                            bot_state._scheduler_logged = False
                        else:
                            # Pause trading until 00:00 WIB
                            if not getattr(bot_state, '_scheduler_logged', False):
                                log.info(f"⏰ Scheduler active: Trading paused until 00:00 WIB. Current WIB hour: {wib_hour}")
                                bot_state._scheduler_logged = True
                            time.sleep(POLL_INTERVAL)
                            continue

                    side = side_or_none
                    price = price_or_reason

                    # ── Pre-mark slug BEFORE trade to prevent concurrent re-entry ──
                    bot_state.traded_slugs[slug] = time.time()

                    # -- Order Book Depth Check --
                    current_mode = get_trading_mode()
                    if current_mode in ("real", "shadow"):
                        db_bet = get_setting("real_bet_amount")
                        current_bet = float(db_bet) if db_bet else REAL_BET_AMOUNT
                        token_for_check = market.get(f"{side.lower()}_token", "")
                        has_depth, liquidity = check_book_depth(
                            token_for_check, current_bet
                        )
                        if not has_depth and liquidity >= 0:
                            # Not enough liquidity -- skip but keep trying
                            del bot_state.traded_slugs[slug]
                            log.info(
                                f"📊 SKIP (low liquidity ${liquidity:.2f}): "
                                f"{side} @ ¢{price*100:.1f} | {slug}"
                            )
                            time.sleep(POLL_INTERVAL)
                            continue

                    # -- Execute trade (parallel via thread pool) --
                    if current_mode in ("real", "shadow"):
                        future = _order_executor.submit(
                            execute_real_trade, market, side, price
                        )
                        try:
                            success = future.result(timeout=30)
                        except Exception as exec_err:
                            log.error(f"Order execution error: {exec_err}")
                            success = False
                    else:
                        success = execute_paper_trade(market, side, price)

                    if not success:
                        # Rollback pre-mark ONLY if order was definitively not placed
                        # (FOK killed/rejected). Keep the mark for timeouts to be safe.
                        del bot_state.traded_slugs[slug]
                        log.info(f"↩️ Slug {slug} unmarked (order not filled)")
                else:
                    # Log skipped event (only once per slug)
                    reason = str(price_or_reason)
                    if slug not in bot_state.skipped_slugs:
                        # Only log skip when we're in or past the buy window
                        if countdown <= COUNTDOWN_BUY_START:
                            record_skipped_event(
                                market_slug=slug,
                                market_question=market.get("question", ""),
                                reason=reason,
                                up_price=up_price,
                                down_price=down_price,
                                countdown=countdown
                            )
                            bot_state.skipped_slugs[slug] = time.time()

            # Cleanup old tracked slugs — FIFO order (remove OLDEST first)
            # OrderedDict preserves insertion order, so popitem(last=False)
            # always removes the oldest entry.
            while len(bot_state.traded_slugs) > 100:
                bot_state.traded_slugs.popitem(last=False)
            while len(bot_state.skipped_slugs) > 200:
                bot_state.skipped_slugs.popitem(last=False)
            while len(bot_state.disqualified_slugs) > 200:
                bot_state.disqualified_slugs.popitem(last=False)

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            log.error(f"Bot error: {e}")
            with bot_state.lock:
                bot_state.last_error = str(e)
            time.sleep(POLL_INTERVAL * 2)


def start_bot():
    """Start the bot in a daemon thread."""
    thread = threading.Thread(target=bot_loop, daemon=True, name="polybot")
    thread.start()
    log.info("Bot thread started")
    return thread
