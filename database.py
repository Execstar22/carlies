# =============================================================================
# Polymarket BTC Up/Down Bot — Database Layer (SQLite)
# =============================================================================

import sqlite3
import logging
from datetime import datetime
from config import DB_PATH, DEFAULT_CAPITAL

log = logging.getLogger("database")


def get_conn():
    """Get a new SQLite connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            market_question TEXT DEFAULT '',
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            shares REAL NOT NULL,
            bet_amount REAL NOT NULL,
            result TEXT DEFAULT 'PENDING',
            pnl REAL DEFAULT 0.0,
            balance_after REAL DEFAULT 0.0,
            resolved_at TEXT DEFAULT NULL,
            token_id TEXT DEFAULT '',
            end_time REAL DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS skipped_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            market_question TEXT DEFAULT '',
            reason TEXT NOT NULL,
            up_price REAL DEFAULT NULL,
            down_price REAL DEFAULT NULL,
            countdown REAL DEFAULT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Initialize default settings
    defaults = {
        "capital": str(DEFAULT_CAPITAL),
        "current_balance": str(DEFAULT_CAPITAL),
        "shadow_capital": "10.0",
        "shadow_balance": "10.0",
        "bot_running": "true",
        "theme": "dark",
        "stop_profit_enabled": "false",
        "stop_profit_target": "2.5",
        "scheduler_enabled": "false"
    }
    for key, val in defaults.items():
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, val)
        )

    conn.commit()
    conn.close()


def migrate_db_schema():
    """Add new columns for real trading support (safe, idempotent migration)."""
    conn = get_conn()
    c = conn.cursor()

    # Check existing columns
    columns = [row[1] for row in c.execute("PRAGMA table_info(trades)").fetchall()]

    migrations = {
        "trading_mode": "ALTER TABLE trades ADD COLUMN trading_mode TEXT DEFAULT 'paper'",
        "order_id": "ALTER TABLE trades ADD COLUMN order_id TEXT DEFAULT ''",
        "order_status": "ALTER TABLE trades ADD COLUMN order_status TEXT DEFAULT ''",
        "tx_hash": "ALTER TABLE trades ADD COLUMN tx_hash TEXT DEFAULT ''",
        "condition_id": "ALTER TABLE trades ADD COLUMN condition_id TEXT DEFAULT ''",
        "claimed": "ALTER TABLE trades ADD COLUMN claimed INTEGER DEFAULT 0",
    }

    for col, sql in migrations.items():
        if col not in columns:
            c.execute(sql)
            log.info(f"DB Migration: Added column '{col}' to trades table")

    conn.commit()
    conn.close()


def get_setting(key):
    """Get a setting value by key."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def update_setting(key, value):
    """Update or insert a setting."""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, str(value))
    )
    conn.commit()
    conn.close()


def get_current_balance():
    """Get current paper trading balance."""
    val = get_setting("current_balance")
    return float(val) if val else DEFAULT_CAPITAL


def update_balance(new_balance):
    """Update the current balance."""
    update_setting("current_balance", f"{new_balance:.6f}")


def get_shadow_balance():
    val = get_setting("shadow_balance")
    return float(val) if val else 10.0


def update_shadow_balance(new_balance):
    update_setting("shadow_balance", f"{new_balance:.6f}")


def record_trade(market_slug, market_question, side, entry_price,
                 shares, bet_amount, token_id="", end_time=0):
    """Record a new paper trade."""
    balance = get_current_balance()
    new_balance = balance - bet_amount
    update_balance(new_balance)

    conn = get_conn()
    conn.execute("""
        INSERT INTO trades
        (timestamp, market_slug, market_question, side, entry_price,
         shares, bet_amount, result, pnl, balance_after, token_id, end_time,
         trading_mode, order_id, order_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0.0, ?, ?, ?,
                'paper', '', '')
    """, (
        datetime.utcnow().isoformat(),
        market_slug, market_question, side,
        entry_price, shares, bet_amount,
        new_balance, token_id, end_time
    ))
    conn.commit()
    conn.close()
    return new_balance


def record_real_trade(market_slug, market_question, side, entry_price,
                      shares, bet_amount, token_id="", end_time=0,
                      order_id="", order_status="", trading_mode="real",
                      condition_id=""):
    """Record a real or shadow trade (no local balance deduction)."""
    conn = get_conn()
    
    balance_after = 0.0
    if trading_mode == "shadow":
        sb = get_shadow_balance()
        balance_after = sb - bet_amount
        update_shadow_balance(balance_after)

    conn.execute("""
        INSERT INTO trades
        (timestamp, market_slug, market_question, side, entry_price,
         shares, bet_amount, result, pnl, balance_after, token_id, end_time,
         trading_mode, order_id, order_status, condition_id, claimed)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0.0, ?, ?, ?,
                ?, ?, ?, ?, 0)
    """, (
        datetime.utcnow().isoformat(),
        market_slug, market_question, side,
        entry_price, shares, bet_amount,
        balance_after, token_id, end_time,
        trading_mode, order_id, order_status, condition_id
    ))
    conn.commit()
    conn.close()


def update_trade_order_status(trade_id, new_status):
    """Update order status for a real trade."""
    conn = get_conn()
    status_str = new_status if isinstance(new_status, str) else str(new_status)
    conn.execute(
        "UPDATE trades SET order_status = ? WHERE id = ?",
        (status_str, trade_id)
    )
    conn.commit()
    conn.close()


def get_daily_loss_total():
    """Calculate total loss for today (WIB) — used for safety limits."""
    conn = get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(ABS(pnl)), 0) as total_loss
        FROM trades
        WHERE result = 'LOSS'
          AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
    """).fetchone()
    conn.close()
    return float(row["total_loss"]) if row else 0.0


def get_daily_profit_total():
    """Calculate net PnL for today (WIB) — used for stop-profit."""
    conn = get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0) as net_pnl
        FROM trades
        WHERE result IN ('WIN', 'LOSS')
          AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')
    """).fetchone()
    conn.close()
    return float(row["net_pnl"]) if row else 0.0


def resolve_trade(trade_id, won):
    """Resolve a pending trade. If won, payout = shares * $1."""
    conn = get_conn()
    trade = conn.execute(
        "SELECT * FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()

    if not trade or trade["result"] != "PENDING":
        conn.close()
        return None

    trade_mode = trade["trading_mode"] if "trading_mode" in trade.keys() else "paper"

    if won:
        payout = trade["shares"]  # Each share pays $1 on win
        pnl = payout - trade["bet_amount"]
    else:
        payout = 0
        pnl = -trade["bet_amount"]

    # Only adjust local balance for paper trades
    if trade_mode == "paper":
        balance = get_current_balance()
        new_balance = balance + payout
        update_balance(new_balance)
    elif trade_mode == "shadow":
        sb = get_shadow_balance()
        new_balance = sb + payout
        update_shadow_balance(new_balance)
    else:
        # Real trades: balance_after = previous trade's balance_after + pnl
        # This prevents drift caused by summing all historical PnL from starting_balance
        last_trade = conn.execute(
            "SELECT balance_after FROM trades "
            "WHERE trading_mode='real' AND result != 'PENDING' AND id != ? "
            "ORDER BY id DESC LIMIT 1",
            (trade_id,)
        ).fetchone()
        
        if last_trade:
            new_balance = float(last_trade["balance_after"]) + pnl
        else:
            # Fallback if this is the first trade
            starting_row = conn.execute(
                "SELECT value FROM settings WHERE key = 'real_starting_balance'"
            ).fetchone()
            starting_balance = float(starting_row["value"]) if starting_row else 0.0
            new_balance = starting_balance + pnl

    conn.execute("""
        UPDATE trades
        SET result = ?, pnl = ?, balance_after = ?, resolved_at = ?
        WHERE id = ?
    """, (
        "WIN" if won else "LOSS",
        pnl, new_balance,
        datetime.utcnow().isoformat(),
        trade_id
    ))
    conn.commit()
    conn.close()
    return new_balance


def get_trades(limit=200, offset=0):
    """Get trade history ordered by newest first."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trades_filtered(limit=200, offset=0, scope="all", date_from=None, date_to=None,
                        result=None, side=None):
    """Get trade history with optional Jakarta-date and field filters."""
    conn = get_conn()
    where = []
    params = []

    if scope == "today_utc":
        where.append("date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')")
    elif scope == "history":
        where.append("date(datetime(timestamp, '+7 hours')) < date('now', '+7 hours')")

    if date_from:
        where.append("date(datetime(timestamp, '+7 hours')) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(datetime(timestamp, '+7 hours')) <= date(?)")
        params.append(date_to)

    if result:
        where.append("result = ?")
        params.append(result.upper())
    if side:
        where.append("side = ?")
        params.append(side.upper())

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM trades{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_trades():
    """Get all pending (unresolved) trades."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE result = 'PENDING'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats():
    """Get overall trading statistics."""
    conn = get_conn()
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    wins = c.execute(
        "SELECT COUNT(*) FROM trades WHERE result = 'WIN'"
    ).fetchone()[0]
    losses = c.execute(
        "SELECT COUNT(*) FROM trades WHERE result = 'LOSS'"
    ).fetchone()[0]
    pending = c.execute(
        "SELECT COUNT(*) FROM trades WHERE result = 'PENDING'"
    ).fetchone()[0]

    total_pnl = c.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE result != 'PENDING' AND id >= 284"
    ).fetchone()[0]

    avg_entry = c.execute(
        "SELECT COALESCE(AVG(entry_price), 0) FROM trades"
    ).fetchone()[0]

    avg_pnl_per_trade = c.execute(
        "SELECT COALESCE(AVG(pnl), 0) FROM trades WHERE result != 'PENDING'"
    ).fetchone()[0]

    capital = float(get_setting("capital") or DEFAULT_CAPITAL)
    current_balance = get_current_balance()
    shadow_capital = float(get_setting("shadow_capital") or 10.0)
    shadow_balance = get_shadow_balance()

    conn.close()

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": round((wins / (wins + losses) * 100), 2) if (wins + losses) > 0 else 0,
        "total_pnl": round(total_pnl, 6),
        "avg_entry_price": round(avg_entry, 4),
        "avg_pnl_per_trade": round(avg_pnl_per_trade, 6),
        "starting_capital": capital,
        "current_balance": round(current_balance, 6),
        "shadow_capital": shadow_capital,
        "shadow_balance": round(shadow_balance, 6),
        "roi": round(((current_balance - capital) / capital) * 100, 2) if capital > 0 else 0
    }


def reset_paper_trading(new_capital):
    """Update capital and balance without erasing trade history."""
    update_setting("capital", f"{new_capital:.6f}")
    update_balance(new_capital)


def record_skipped_event(market_slug, market_question, reason,
                         up_price=None, down_price=None, countdown=None):
    """Record a skipped event."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO skipped_events
        (timestamp, market_slug, market_question, reason, up_price, down_price, countdown)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        market_slug, market_question, reason,
        up_price, down_price, countdown
    ))
    conn.commit()
    conn.close()


def get_skipped_events(limit=200, offset=0):
    """Get skipped events ordered by newest first."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM skipped_events ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_skipped_events_filtered(limit=200, offset=0, scope="all", date_from=None, date_to=None,
                                reason=None):
    """Get skipped events with optional Jakarta-date and reason filters."""
    conn = get_conn()
    where = []
    params = []

    if scope == "today_utc":
        where.append("date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')")
    elif scope == "history":
        where.append("date(datetime(timestamp, '+7 hours')) < date('now', '+7 hours')")

    if date_from:
        where.append("date(datetime(timestamp, '+7 hours')) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(datetime(timestamp, '+7 hours')) <= date(?)")
        params.append(date_to)

    if reason:
        reason_key = reason.strip().lower()
        if reason_key == "price":
            where.append("reason LIKE 'price_out_of_range%'")
        elif reason_key == "countdown_early":
            where.append("reason LIKE 'countdown_too_early%'")
        elif reason_key == "countdown_late":
            where.append("reason LIKE 'countdown_too_late%'")
        elif reason_key == "order_killed":
            where.append("reason LIKE 'order_killed%'")
        else:
            where.append("reason LIKE ?")
            params.append(f"%{reason}%")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM skipped_events{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_skipped_stats():
    """Get skipped events count."""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM skipped_events").fetchone()[0]
    conn.close()
    return {"total_skipped": total}


def get_projection_chart_data(period="today", date_from=None, date_to=None):
    """
    Aggregate resolved trade amounts for projection charts.
    Uses WIB date bucketing (+7 hours) and returns win/loss amounts as positive numbers.
    """
    conn = get_conn()
    c = conn.cursor()

    # Default date range if not supplied.
    if not date_to:
        date_to = c.execute("SELECT date('now', '+7 hours')").fetchone()[0]
    if not date_from:
        if period == "today":
            date_from = date_to
        elif period == "weekly":
            date_from = c.execute("SELECT date(?, '-6 days')", (date_to,)).fetchone()[0]
        elif period == "monthly":
            date_from = c.execute("SELECT date(?, '-29 days')", (date_to,)).fetchone()[0]
        else:  # yearly
            date_from = c.execute("SELECT date(?, '-364 days')", (date_to,)).fetchone()[0]

    if period == "today":
        bucket_expr = "strftime('%H:00', datetime(timestamp, '+7 hours'))"
    elif period == "weekly":
        bucket_expr = "strftime('%Y-%m-%d', datetime(timestamp, '+7 hours'))"
    elif period == "monthly":
        bucket_expr = "strftime('%Y-%m-%d', datetime(timestamp, '+7 hours'))"
    else:  # yearly
        bucket_expr = "strftime('%Y-%m', datetime(timestamp, '+7 hours'))"

    rows = c.execute(
        f"""
        SELECT
            {bucket_expr} AS bucket,
            SUM(CASE WHEN result = 'WIN' THEN pnl ELSE 0 END) AS win_amount,
            SUM(CASE WHEN result = 'LOSS' THEN ABS(pnl) ELSE 0 END) AS loss_amount
        FROM trades
        WHERE result IN ('WIN', 'LOSS')
          AND date(datetime(timestamp, '+7 hours')) >= date(?)
          AND date(datetime(timestamp, '+7 hours')) <= date(?)
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        (date_from, date_to)
    ).fetchall()

    series = []
    total_win = 0.0
    total_loss = 0.0
    for r in rows:
        win_amount = float(r["win_amount"] or 0)
        loss_amount = float(r["loss_amount"] or 0)
        total_win += win_amount
        total_loss += loss_amount
        series.append({
            "label": r["bucket"],
            "win_amount": round(win_amount, 6),
            "loss_amount": round(loss_amount, 6),
        })

    conn.close()
    return {
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
        "series": series,
        "totals": {
            "win_amount": round(total_win, 6),
            "loss_amount": round(total_loss, 6),
            "net_amount": round(total_win - total_loss, 6),
        }
    }


def get_unclaimed_wins():
    """Get winning real/shadow trades that haven't been claimed yet."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM trades
        WHERE result = 'WIN'
          AND claimed = 0
          AND trading_mode IN ('real', 'shadow')
          AND condition_id != ''
        ORDER BY id ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_trade_claimed(trade_id, tx_hash=""):
    """Mark a trade as claimed (winnings redeemed)."""
    conn = get_conn()
    conn.execute(
        "UPDATE trades SET claimed = 1, tx_hash = ? WHERE id = ?",
        (tx_hash, trade_id)
    )
    conn.commit()
    conn.close()
