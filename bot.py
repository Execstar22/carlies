# =============================================================================
# Polymarket BTC Up/Down Bot — Core Engine
# =============================================================================

import time
import math
import json
import logging
import requests
import threading
from datetime import datetime, timezone

from config import (
    GAMMA_API_BASE, CLOB_API_BASE, MARKET_SLUG_PREFIX,
    MARKET_INTERVAL_SECONDS, COUNTDOWN_BUY_START, COUNTDOWN_BUY_END,
    PRICE_FLOOR, PRICE_TARGET, PRICE_CEILING,
    BET_AMOUNT, POLL_INTERVAL, MARKET_SCAN_INTERVAL,
    TRADING_MODE, REAL_BET_AMOUNT, MAX_BET_AMOUNT, MAX_DAILY_LOSS,
    get_trading_mode
)
from database import (
    get_current_balance, record_trade, resolve_trade,
    get_pending_trades, get_setting, update_setting,
    record_skipped_event, reset_paper_trading,
    record_real_trade, get_daily_loss_total, get_daily_profit_total
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
        self.traded_slugs = set()
        self.skipped_slugs = set()  # Track slugs already logged as skipped
        self.disqualified_slugs = set()  # Track slugs disqualified before buy window
        self.notifications = []           # Store recent notifications
        self.lock = threading.Lock()

    def to_dict(self):
        with self.lock:
            return {
                "running": self.running,
                "current_market": self.current_market,
                "up_price": self.up_price,
                "down_price": self.down_price,
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
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
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
        resp = requests.get(url, params=params, timeout=10)
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
        resp = requests.get(url, params=params, timeout=10)
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
                payout_resolved = (
                    (p0 >= 0.99 and p1 <= 0.01) or
                    (p1 >= 0.99 and p0 <= 0.01)
                )
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

    # Daily loss safety check (0 = disabled/no limit)
    daily_loss = get_daily_loss_total()
    if MAX_DAILY_LOSS > 0 and daily_loss >= MAX_DAILY_LOSS:
        msg = (
            f"🛑 STOP LOSS REACHED (${daily_loss:.4f} >= ${MAX_DAILY_LOSS:.2f}), "
            f"skipping trade"
        )
        log.warning(msg)
        add_notification(msg)
        return False

    token_id = market.get(f"{side.lower()}_token", "")
    if not token_id:
        log.error(f"No token ID for side {side}")
        return False

    mode = get_trading_mode()
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
        order_price = PRICE_CEILING
        log.info(
            f"📤 FOK slippage: signal ¢{price*100:.1f} → "
            f"order limit ¢{order_price*100:.1f}"
        )
        result = place_real_order(token_id, "BUY", bet_amount, order_price)


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

    shares = bet_amount / price
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

def bot_loop():
    """Main bot loop — runs in a background thread."""
    log.info("🤖 Bot started!")
    last_scan = 0
    last_resolve_check = 0

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
                        bot_state.current_market = market
                else:
                    with bot_state.lock:
                        bot_state.current_market = None
                        bot_state.countdown = None

                last_scan = now

            # ── Check resolution of pending trades ──
            if now - last_resolve_check > 60:
                check_and_resolve_pending()

                # ── Auto-claim winnings after resolution ──
                if get_trading_mode() in ("real", "shadow"):
                    try:
                        from trader import auto_claim_wins
                        auto_claim_wins()
                    except Exception as e:
                        log.warning(f"Auto-claim error: {e}")
                
                last_resolve_check = now

            # ── Fetch prices for current market ──
            market = bot_state.current_market
            if not market:
                with bot_state.lock:
                    bot_state.up_price = None
                    bot_state.down_price = None
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

            # Fetch prices
            up_price = fetch_price(market["up_token"])
            down_price = fetch_price(market["down_token"])

            # If CLOB price fails, try midpoint
            if up_price is None or up_price == 0:
                up_price = fetch_midpoint(market["up_token"])
            if down_price is None or down_price == 0:
                down_price = fetch_midpoint(market["down_token"])

            with bot_state.lock:
                bot_state.up_price = up_price
                bot_state.down_price = down_price
                bot_state.countdown = countdown
                bot_state.last_error = None
                bot_state.last_update = datetime.utcnow().isoformat()

            slug = market["slug"]

            # ── Strategy check ──
            if slug not in bot_state.traded_slugs:
                side_or_none, price_or_reason = should_trade(countdown, up_price, down_price)
                if side_or_none and isinstance(price_or_reason, (int, float)):
                    # ── Stop Profit Guard ──
                    stop_profit_on = get_setting("stop_profit_enabled") == "true"
                    if stop_profit_on:
                        sp_target = float(get_setting("stop_profit_target") or 2.5)
                        daily_pnl = get_daily_profit_total()
                        if daily_pnl >= sp_target:
                            if not getattr(bot_state, '_stop_profit_logged', False):
                                log.info(f"🎯 Stop Profit reached! Daily PnL ${daily_pnl:.4f} >= target ${sp_target:.2f}. Pausing trades.")
                                bot_state._stop_profit_logged = True
                            time.sleep(POLL_INTERVAL)
                            continue
                        else:
                            bot_state._stop_profit_logged = False

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
                    current_mode = get_trading_mode()
                    if current_mode in ("real", "shadow"):
                        success = execute_real_trade(market, side, price)
                    else:
                        success = execute_paper_trade(market, side, price)
                    if success:
                        bot_state.traded_slugs.add(slug)
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
                            bot_state.skipped_slugs.add(slug)

            # Cleanup old tracked slugs (keep only recent)
            if len(bot_state.traded_slugs) > 100:
                bot_state.traded_slugs = set(list(bot_state.traded_slugs)[-50:])
            if len(bot_state.skipped_slugs) > 200:
                bot_state.skipped_slugs = set(list(bot_state.skipped_slugs)[-100:])
            if len(bot_state.disqualified_slugs) > 200:
                bot_state.disqualified_slugs = set(list(bot_state.disqualified_slugs)[-100:])

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
