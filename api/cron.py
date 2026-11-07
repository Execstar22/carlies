# =============================================================================
# Vercel Cron — Bot Tick (runs every 1 minute via Vercel Cron)
# =============================================================================
# Replaces the continuous bot_loop() for serverless deployment.
# Each invocation does ONE cycle: discover market → check prices → trade/skip.
# =============================================================================

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
from http.server import BaseHTTPRequestHandler

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["VERCEL"] = "1"

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("bot_tick")

# HTTP session for API calls
_session = requests.Session()

# ── Config ──
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
MARKET_SLUG_PREFIX = "btc-updown-5m"
MARKET_INTERVAL_SECONDS = 300
COUNTDOWN_BUY_START = int(os.environ.get("COUNTDOWN_BUY_START", "210"))
COUNTDOWN_BUY_END = int(os.environ.get("COUNTDOWN_BUY_END", "0"))
PRICE_FLOOR = float(os.environ.get("PRICE_FLOOR", "0.93"))
PRICE_TARGET = float(os.environ.get("PRICE_TARGET", "0.95"))
PRICE_CEILING = float(os.environ.get("PRICE_CEILING", "0.97"))
TRADING_MODE = os.environ.get("TRADING_MODE", "shadow").strip().lower()
REAL_BET_AMOUNT = float(os.environ.get("REAL_BET_AMOUNT", "1.00"))
MAX_BET_AMOUNT = float(os.environ.get("MAX_BET_AMOUNT", "10.00"))
MAX_DAILY_LOSS = float(os.environ.get("MAX_DAILY_LOSS", "0.0"))
BET_AMOUNT = 2.0
DEFAULT_CAPITAL = 10.0
DB_PATH = os.environ.get("DATABASE_URL", "/tmp/polybots.db")
MIN_BOOK_DEPTH_MULTIPLIER = 0.0
STOP_LOSS_BALANCE = float(os.environ.get("STOP_LOSS_BALANCE", "0.0"))


# ── Database (SQLite — uses /tmp on Vercel) ──
import sqlite3

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, market_slug TEXT NOT NULL,
            market_question TEXT DEFAULT '', side TEXT NOT NULL,
            entry_price REAL NOT NULL, shares REAL NOT NULL,
            bet_amount REAL NOT NULL, result TEXT DEFAULT 'PENDING',
            pnl REAL DEFAULT 0.0, balance_after REAL DEFAULT 0.0,
            resolved_at TEXT DEFAULT NULL, token_id TEXT DEFAULT '',
            end_time REAL DEFAULT 0, trading_mode TEXT DEFAULT 'paper',
            order_id TEXT DEFAULT '', order_status TEXT DEFAULT '',
            tx_hash TEXT DEFAULT '', condition_id TEXT DEFAULT '',
            claimed INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS skipped_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, market_slug TEXT NOT NULL,
            market_question TEXT DEFAULT '', reason TEXT NOT NULL,
            up_price REAL DEFAULT NULL, down_price REAL DEFAULT NULL,
            countdown REAL DEFAULT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )
    """)
    defaults = {
        "capital": str(DEFAULT_CAPITAL), "current_balance": str(DEFAULT_CAPITAL),
        "shadow_capital": "10.0", "shadow_balance": "10.0",
        "bot_running": "true", "stop_profit_enabled": "false",
        "stop_profit_target": "2.5",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()

def get_setting(key):
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else None
    except Exception:
        return None

def update_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def is_slug_already_traded(slug):
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE market_slug = ?", (slug,)).fetchone()
    conn.close()
    return row["cnt"] > 0

def get_trading_mode():
    val = get_setting("trading_mode")
    return val if val else TRADING_MODE

def get_daily_loss_total(mode):
    conn = get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(ABS(pnl)), 0) as total_loss FROM trades
        WHERE result = 'LOSS' AND trading_mode = ?
        AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
    """, (mode,)).fetchone()
    conn.close()
    return float(row["total_loss"]) if row else 0.0

def get_daily_profit_total(mode):
    conn = get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0) as net_pnl FROM trades
        WHERE result IN ('WIN', 'LOSS') AND trading_mode = ?
        AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
    """, (mode,)).fetchone()
    conn.close()
    return float(row["net_pnl"]) if row else 0.0


# ── Market Discovery ──

def discover_active_market():
    now = time.time()
    current_slot = int(now // MARKET_INTERVAL_SECONDS) * MARKET_INTERVAL_SECONDS
    slots = [current_slot - MARKET_INTERVAL_SECONDS, current_slot, current_slot + MARKET_INTERVAL_SECONDS]

    for ts in slots:
        slug = f"{MARKET_SLUG_PREFIX}-{ts}"
        try:
            resp = _session.get(
                f"{GAMMA_API_BASE}/events",
                params={"slug": slug, "_": int(time.time())},
                headers={"Cache-Control": "no-cache"},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            raw = resp.text.strip()
            if not raw:
                continue
            data = json.loads(raw)
            if not data:
                continue
        except Exception:
            continue

        event = data[0]
        markets = event.get("markets", [])
        if not markets:
            continue

        market = markets[0]
        if market.get("closed", False):
            continue

        end_date = market.get("endDate") or event.get("endDate")
        end_time = None
        if end_date:
            try:
                if end_date.endswith("Z"):
                    end_time = datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp()
                else:
                    end_time = datetime.fromisoformat(end_date).timestamp()
            except Exception:
                end_time = ts + MARKET_INTERVAL_SECONDS
        if end_time is None:
            end_time = ts + MARKET_INTERVAL_SECONDS
        if end_time < now:
            continue

        clob_ids_raw = market.get("clobTokenIds", [])
        outcomes_raw = market.get("outcomes", [])
        if isinstance(clob_ids_raw, str):
            try: clob_ids = json.loads(clob_ids_raw)
            except: continue
        else:
            clob_ids = clob_ids_raw
        if isinstance(outcomes_raw, str):
            try: outcomes = json.loads(outcomes_raw)
            except: outcomes = []
        else:
            outcomes = outcomes_raw
        if len(clob_ids) < 2:
            continue

        up_token = down_token = None
        for i, outcome in enumerate(outcomes):
            name = str(outcome).upper()
            if "UP" in name or "YES" in name:
                up_token = clob_ids[i] if i < len(clob_ids) else None
            elif "DOWN" in name or "NO" in name:
                down_token = clob_ids[i] if i < len(clob_ids) else None
        if not up_token: up_token = clob_ids[0]
        if not down_token: down_token = clob_ids[1] if len(clob_ids) > 1 else None

        return {
            "slug": slug,
            "question": market.get("question", event.get("title", slug)),
            "end_time": end_time,
            "up_token": up_token,
            "down_token": down_token,
            "market_id": market.get("id"),
            "condition_id": market.get("conditionId", ""),
        }
    return None


# ── Price Fetching (REST only) ──

def fetch_price(token_id, side="buy"):
    try:
        resp = _session.get(f"{CLOB_API_BASE}/price", params={"token_id": token_id, "side": side}, timeout=10)
        if resp.status_code == 200:
            return float(resp.json().get("price", 0))
    except Exception:
        pass
    return None

def fetch_midpoint(token_id):
    try:
        resp = _session.get(f"{CLOB_API_BASE}/midpoint", params={"token_id": token_id}, timeout=10)
        if resp.status_code == 200:
            return float(resp.json().get("mid", 0))
    except Exception:
        pass
    return None


# ── Strategy ──

def should_trade(countdown, up_price, down_price):
    if countdown is None:
        return None, "no_countdown"
    if not (COUNTDOWN_BUY_END <= countdown <= COUNTDOWN_BUY_START):
        if countdown > COUNTDOWN_BUY_START:
            return None, f"countdown_too_early ({countdown:.0f}s > {COUNTDOWN_BUY_START}s)"
        else:
            return None, f"countdown_too_late ({countdown:.0f}s < {COUNTDOWN_BUY_END}s)"

    candidates = []
    if up_price is not None and PRICE_FLOOR <= up_price <= PRICE_CEILING:
        candidates.append(("UP", up_price))
    if down_price is not None and PRICE_FLOOR <= down_price <= PRICE_CEILING:
        candidates.append(("DOWN", down_price))

    if not candidates:
        parts = []
        if up_price is not None: parts.append(f"UP=${up_price:.2f}")
        if down_price is not None: parts.append(f"DOWN=${down_price:.2f}")
        return None, f"price_out_of_range ({', '.join(parts)} not in ${PRICE_FLOOR:.2f}-${PRICE_CEILING:.2f})"

    candidates.sort(key=lambda x: abs(x[1] - PRICE_TARGET))
    return candidates[0][0], candidates[0][1]


# ── Trade Execution ──

def execute_trade(market, side, price, mode):
    """Execute a trade (paper, shadow, or real)."""
    token_id = market.get(f"{side.lower()}_token", "")
    if not token_id:
        return False, "no_token_id"

    if mode == "paper":
        bet_amount = BET_AMOUNT
        shares = bet_amount / price
        conn = get_conn()
        conn.execute("""
            INSERT INTO trades (timestamp, market_slug, market_question, side,
            entry_price, shares, bet_amount, result, pnl, balance_after,
            token_id, end_time, trading_mode, order_id, order_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0.0, 0.0, ?, ?, 'paper', '', '')
        """, (
            datetime.utcnow().isoformat(), market["slug"], market["question"],
            side, price, shares, bet_amount, token_id, market.get("end_time", 0)
        ))
        conn.commit()
        conn.close()
        return True, f"paper trade {side} @ {price:.4f}"

    # Real/Shadow mode
    db_bet = get_setting("real_bet_amount")
    bet_amount = min(float(db_bet) if db_bet else REAL_BET_AMOUNT, MAX_BET_AMOUNT)

    # Daily loss check
    if MAX_DAILY_LOSS > 0:
        daily_loss = get_daily_loss_total(mode)
        if daily_loss >= MAX_DAILY_LOSS:
            return False, f"daily_loss_limit ({daily_loss:.4f} >= {MAX_DAILY_LOSS:.2f})"

    if mode == "shadow":
        # Shadow: log but don't place real order
        from trader import shadow_order
        result = shadow_order(token_id, "BUY", bet_amount, price, market["slug"])
    else:
        # Real mode
        from trader import place_real_order, get_real_balance
        real_balance = get_real_balance()
        if real_balance is None:
            return False, "cannot_fetch_balance"
        if real_balance < bet_amount:
            return False, f"insufficient_balance ({real_balance:.4f} < {bet_amount})"
        result = place_real_order(token_id, "BUY", bet_amount, price, limit_price=PRICE_CEILING)

    if result is None:
        return False, "order_failed"

    # Check FOK killed
    resp_data = result.get("response", {})
    if isinstance(resp_data, dict):
        resp_status = str(resp_data.get("status", "")).upper()
        if resp_status in ("KILLED", "UNMATCHED", "NOT_MATCHED", "REJECTED"):
            return False, f"order_killed ({resp_status})"

    # Record trade
    amount_d = Decimal(str(bet_amount)).quantize(Decimal("0.01"))
    price_d = Decimal(str(price)).quantize(Decimal("0.01"))
    shares = float((amount_d / price_d).quantize(Decimal("0.0001"), rounding=ROUND_DOWN))

    conn = get_conn()
    conn.execute("""
        INSERT INTO trades (timestamp, market_slug, market_question, side,
        entry_price, shares, bet_amount, result, pnl, balance_after,
        token_id, end_time, trading_mode, order_id, order_status, condition_id, claimed)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0.0, 0.0, ?, ?, ?, ?, ?, ?, 0)
    """, (
        datetime.utcnow().isoformat(), market["slug"], market["question"],
        side, price, shares, bet_amount, token_id, market.get("end_time", 0),
        mode, result.get("order_id", ""), result.get("status", ""),
        market.get("condition_id", "")
    ))
    conn.commit()
    conn.close()
    return True, f"{mode} trade {side} @ {price:.4f}, bet ${bet_amount:.2f}"


# ── Resolution Check ──

def check_and_resolve_pending():
    conn = get_conn()
    pending = conn.execute("SELECT * FROM trades WHERE result = 'PENDING'").fetchall()
    conn.close()
    resolved_count = 0

    for trade in [dict(r) for r in pending]:
        slug = trade["market_slug"]
        try:
            resp = _session.get(
                f"{GAMMA_API_BASE}/events",
                params={"slug": slug, "_": int(time.time())},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not data:
                continue
        except Exception:
            continue

        event = data[0]
        markets = event.get("markets", [])
        if not markets:
            continue
        market = markets[0]

        is_closed = market.get("closed", False)
        uma_status = market.get("umaResolutionStatus", "")
        auto_resolved = market.get("automaticallyResolved", False)
        flag_resolved = is_closed and (uma_status == "resolved" or auto_resolved)

        outcome_prices_raw = market.get("outcomePrices", "[]")
        if isinstance(outcome_prices_raw, str):
            try: outcome_prices = json.loads(outcome_prices_raw)
            except: outcome_prices = []
        else:
            outcome_prices = outcome_prices_raw

        payout_resolved = False
        if outcome_prices and len(outcome_prices) >= 2:
            try:
                p0, p1 = float(outcome_prices[0]), float(outcome_prices[1])
                payout_final = (p0 >= 0.99 and p1 <= 0.01) or (p1 >= 0.99 and p0 <= 0.01)
                if payout_final and trade.get("end_time") and trade["end_time"] > 0:
                    payout_resolved = time.time() > trade["end_time"]
                else:
                    payout_resolved = payout_final
            except:
                pass

        if not (flag_resolved or payout_resolved):
            continue

        trade_side = trade["side"].upper()
        won = False
        if outcome_prices and len(outcome_prices) >= 2:
            try:
                up_payout = float(outcome_prices[0])
                down_payout = float(outcome_prices[1])
                won = up_payout > 0.5 if trade_side == "UP" else down_payout > 0.5
            except:
                pass

        # Resolve
        if won:
            pnl = trade["shares"] - trade["bet_amount"]
        else:
            pnl = -trade["bet_amount"]

        conn2 = get_conn()
        conn2.execute(
            "UPDATE trades SET result = ?, pnl = ?, resolved_at = ? WHERE id = ?",
            ("WIN" if won else "LOSS", pnl, datetime.utcnow().isoformat(), trade["id"])
        )
        conn2.commit()
        conn2.close()
        resolved_count += 1
        log.info(f"Trade #{trade['id']} resolved: {'WIN' if won else 'LOSS'} (PnL: {pnl:.4f})")

    return resolved_count


# ── Main Tick Function ──

def bot_tick():
    """One cycle of the bot logic. Called by Vercel Cron."""
    init_db()
    actions = []

    # Check if bot is paused
    if get_setting("bot_running") == "false":
        return {"status": "paused", "actions": ["bot_running=false"]}

    mode = get_trading_mode()

    # 1. Resolve pending trades first
    resolved = check_and_resolve_pending()
    if resolved > 0:
        actions.append(f"resolved {resolved} trades")

    # 2. Auto-claim (real/shadow)
    if mode in ("real", "shadow"):
        try:
            from trader import auto_claim_wins
            claimed = auto_claim_wins()
            if claimed:
                actions.append(f"claimed {claimed} wins")
        except Exception as e:
            log.warning(f"Auto-claim error: {e}")

    # 3. Discover market
    market = discover_active_market()
    if not market:
        actions.append("no active market")
        return {"status": "ok", "actions": actions}

    slug = market["slug"]
    countdown = market["end_time"] - time.time()
    actions.append(f"market={slug}, countdown={countdown:.0f}s")

    # 4. Stop Profit check
    now_utc = datetime.now(timezone.utc)
    today_wib = (now_utc + timedelta(hours=7)).strftime("%Y-%m-%d")

    if get_setting("stop_profit_enabled") == "true" and mode in ("real", "shadow"):
        reached_date = get_setting("stop_profit_reached_date")
        if reached_date == today_wib:
            actions.append("stop_profit_reached_today")
            return {"status": "ok", "actions": actions}

        sp_target = float(get_setting("stop_profit_target") or 2.5)
        daily_pnl = get_daily_profit_total(mode)
        if daily_pnl >= sp_target:
            update_setting("stop_profit_reached_date", today_wib)
            actions.append(f"stop_profit_triggered (PnL={daily_pnl:.4f} >= {sp_target})")
            return {"status": "ok", "actions": actions}

    # 5. Already traded this slug?
    if is_slug_already_traded(slug):
        actions.append(f"already_traded {slug}")
        return {"status": "ok", "actions": actions}

    # 6. Fetch prices (REST)
    up_price = fetch_price(market["up_token"])
    down_price = fetch_price(market["down_token"])
    if up_price is None or up_price == 0:
        up_price = fetch_midpoint(market["up_token"])
    if down_price is None or down_price == 0:
        down_price = fetch_midpoint(market["down_token"])

    actions.append(f"prices: UP={up_price}, DOWN={down_price}")

    # 7. Strategy decision
    side_or_none, price_or_reason = should_trade(countdown, up_price, down_price)

    if side_or_none and isinstance(price_or_reason, (int, float)):
        side = side_or_none
        price = price_or_reason

        # Scheduler guard
        if get_setting("scheduler_enabled") == "true":
            now_wib = now_utc + timedelta(hours=7)
            if now_wib.hour != 0:
                actions.append(f"scheduler_paused (hour={now_wib.hour})")
                return {"status": "ok", "actions": actions}
            else:
                update_setting("scheduler_enabled", "false")

        # Execute trade
        success, msg = execute_trade(market, side, price, mode)
        actions.append(f"trade: {msg}" if success else f"trade_failed: {msg}")
    else:
        reason = str(price_or_reason)
        actions.append(f"skip: {reason}")
        # Record skipped
        if countdown <= COUNTDOWN_BUY_START:
            conn = get_conn()
            conn.execute("""
                INSERT INTO skipped_events (timestamp, market_slug, market_question,
                reason, up_price, down_price, countdown)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.utcnow().isoformat(), slug,
                market.get("question", ""), reason,
                up_price, down_price, countdown
            ))
            conn.commit()
            conn.close()

    return {"status": "ok", "actions": actions}


# ── Vercel Handler ──

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Verify cron secret (optional security)
        auth = self.headers.get("Authorization", "")
        cron_secret = os.environ.get("CRON_SECRET", "")
        if cron_secret and auth != f"Bearer {cron_secret}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return

        try:
            result = bot_tick()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            log.error(f"Bot tick error: {e}", exc_info=True)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

