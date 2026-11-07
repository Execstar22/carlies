# =============================================================================
# Polymarket BTC Up/Down Bot — Flask Dashboard
# =============================================================================

from flask import Flask, render_template, jsonify, request
import json
import time
from database import (
    init_db, get_trades, get_stats, get_setting, update_setting,
    get_current_balance, reset_paper_trading, get_pending_trades,
    get_skipped_events, get_skipped_stats,
    get_trades_filtered, get_skipped_events_filtered, get_projection_chart_data
)
from bot import bot_state, kill_switch
from config import BET_AMOUNT, TRADING_MODE, REAL_BET_AMOUNT, MAX_BET_AMOUNT, get_trading_mode, COUNTDOWN_BUY_START, DEFAULT_CAPITAL

app = Flask(__name__)

# #region agent log
def _agent_log(hypothesisId, message, data=None, runId="pre-fix"):
    try:
        payload = {
            "sessionId": "1aece9",
            "timestamp": int(time.time() * 1000),
            "location": "app.py",
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


@app.route("/")
def index():
    """Serve the dashboard page."""
    import os
    project_title = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
    return render_template("index.html", project_title=project_title)


@app.route("/api/stats")
def api_stats():
    """Get trading statistics."""
    stats = get_stats()
    return jsonify(stats)


@app.route("/api/trades")
def api_trades():
    """Get trade history."""
    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)
    scope = request.args.get("scope", "all", type=str)
    date_from = request.args.get("from", type=str)
    date_to = request.args.get("to", type=str)
    result = request.args.get("result", type=str)
    side = request.args.get("side", type=str)
    trades = get_trades_filtered(
        limit=limit,
        offset=offset,
        scope=scope,
        date_from=date_from,
        date_to=date_to,
        result=result,
        side=side
    )
    return jsonify(trades)


@app.route("/api/skipped")
def api_skipped():
    """Get skipped events history."""
    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)
    scope = request.args.get("scope", "all", type=str)
    date_from = request.args.get("from", type=str)
    date_to = request.args.get("to", type=str)
    reason = request.args.get("reason", type=str)
    events = get_skipped_events_filtered(
        limit=limit,
        offset=offset,
        scope=scope,
        date_from=date_from,
        date_to=date_to,
        reason=reason
    )
    return jsonify(events)


@app.route("/api/live")
def api_live():
    """Get live bot state and market data."""
    state = bot_state.to_dict()
    mode = get_trading_mode()
    if mode == "shadow":
        from database import get_shadow_balance
        state["balance"] = get_shadow_balance()
    elif mode == "real":
        # Real balance fetched separately via /api/wallet to avoid slow Polymarket API call per poll
        state["balance"] = None
    else:
        state["balance"] = get_current_balance()
    state["trading_mode"] = mode
    state["countdown_buy_start"] = COUNTDOWN_BUY_START
    return jsonify(state)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """Get current settings."""
    return jsonify({
        "capital": float(get_setting("capital") or 5.0),
        "bot_running": get_setting("bot_running") == "true",
        "theme": get_setting("theme") or "dark",
        "trading_mode": get_trading_mode(),
        "real_bet_amount": float(get_setting("real_bet_amount") or REAL_BET_AMOUNT),
        "max_bet_amount": MAX_BET_AMOUNT,
        "shadow_capital": float(get_setting("shadow_capital") or 10.0),
        "stop_profit_enabled": get_setting("stop_profit_enabled") == "true",
        "stop_profit_target": float(get_setting("stop_profit_target") or 2.5),
        "scheduler_enabled": get_setting("scheduler_enabled") == "true",
        "cap_scale_up_enabled": get_setting("cap_scale_up_enabled") != "false",
        "cap_scale_up_active": get_setting("cap_scale_up_active") == "true",
        "cap_scale_up_amount": float(get_setting("cap_scale_up_amount") or 5.0),
    })


@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    """Update settings."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    if "capital" in data:
        new_capital = float(data["capital"])
        if new_capital <= 0:
            return jsonify({"error": "Capital must be positive"}), 400
        # In live mode, capital = bet amount per trade
        update_setting("real_bet_amount", f"{new_capital:.4f}")
        update_setting("capital", f"{new_capital:.6f}")
        # Manual bet change cancels Cap Scale Up
        if get_setting("cap_scale_up_active") == "true":
            update_setting("cap_scale_up_active", "false")
            update_setting("cap_scale_up_original_bet", "")

    if "shadow_capital" in data:
        new_shadow = float(data["shadow_capital"])
        if new_shadow <= 0:
            return jsonify({"error": "Shadow capital must be positive"}), 400
        update_setting("shadow_capital", f"{new_shadow:.4f}")
        update_setting("shadow_balance", f"{new_shadow:.6f}")

    if "bot_running" in data:
        update_setting("bot_running", "true" if data["bot_running"] else "false")
        bot_state.running = data["bot_running"]

    if "theme" in data:
        update_setting("theme", data["theme"])

    if "trading_mode" in data:
        mode = data["trading_mode"].lower()
        if mode in ("shadow", "real"):
            update_setting("trading_mode", mode)

    if "stop_profit_enabled" in data:
        update_setting("stop_profit_enabled", "true" if data["stop_profit_enabled"] else "false")

    if "stop_profit_target" in data:
        val = float(data["stop_profit_target"])
        if val > 0:
            update_setting("stop_profit_target", f"{val:.4f}")

    if "scheduler_enabled" in data:
        update_setting("scheduler_enabled", "true" if data["scheduler_enabled"] else "false")

    if "cap_scale_up_enabled" in data:
        enabled = bool(data["cap_scale_up_enabled"])
        update_setting("cap_scale_up_enabled", "true" if enabled else "false")
        # If disabling, also deactivate any active cap scale up and restore bet
        if not enabled and get_setting("cap_scale_up_active") == "true":
            original_bet = get_setting("cap_scale_up_original_bet")
            if original_bet:
                update_setting("real_bet_amount", original_bet)
            update_setting("cap_scale_up_active", "false")
            update_setting("cap_scale_up_original_bet", "")

    if "cap_scale_up_amount" in data:
        val = float(data["cap_scale_up_amount"])
        if val > 0:
            update_setting("cap_scale_up_amount", f"{val:.4f}")

    return jsonify({"status": "ok"})


@app.route("/api/wallet")
def api_wallet():
    """Get real wallet info (only meaningful in real/shadow mode)."""
    import os
    mode = get_trading_mode()
    if mode not in ("real", "shadow"):
        return jsonify({"mode": mode, "balance": None})
    try:
        from trader import get_real_balance
        balance = get_real_balance()
        funder = os.environ.get("POLY_FUNDER_ADDRESS", "")
        funder_short = (funder[:6] + "..." + funder[-4:]) if len(funder) > 10 else funder
        return jsonify({
            "mode": mode,
            "balance": balance,
            "funder": funder_short,
        })
    except Exception as e:
        return jsonify({"mode": mode, "balance": None, "error": str(e)})


@app.route("/api/kill-switch", methods=["POST"])
def api_kill_switch():
    """Emergency stop: pause bot and cancel all pending orders."""
    kill_switch()
    return jsonify({"status": "killed", "message": "Bot stopped, orders cancelled"})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Wipe all trade history and reset for fresh start."""
    data = request.get_json() or {}
    confirm = data.get("confirm")
    if confirm != "RESET":
        return jsonify({"error": "Send {\"confirm\": \"RESET\"} to confirm"}), 400

    mode = get_trading_mode()

    # In real mode, sync real_starting_balance with on-chain balance BEFORE
    # clearing history so the ledger chain re-anchors correctly.
    if mode == "real":
        try:
            from trader import get_real_balance
            real_bal = get_real_balance()
            if real_bal is not None:
                update_setting("real_starting_balance", f"{real_bal:.6f}")
            else:
                # Fallback: can't reach chain — warn but allow reset
                import logging
                logging.getLogger("app").warning(
                    "Cannot fetch on-chain balance during reset; "
                    "real_starting_balance may be stale"
                )
        except Exception as e:
            import logging
            logging.getLogger("app").warning(f"Reset balance sync failed: {e}")

    from database import get_conn
    conn = get_conn()
    conn.execute("DELETE FROM trades")
    conn.execute("DELETE FROM skipped_events")
    conn.commit()
    conn.close()

    if mode == "real":
        # Don't touch paper balance fields; just ensure capital reflects
        # the current real bet amount for stats display.
        pass
    elif mode == "shadow":
        update_setting("shadow_balance", get_setting("shadow_capital") or "10.0")
    else:
        update_setting("current_balance", str(DEFAULT_CAPITAL))
        update_setting("capital", str(DEFAULT_CAPITAL))

    # ── Clear all runtime flags so bot doesn't stay stuck ──
    # Stop Profit: clear today's reached flag so bot resumes trading
    update_setting("stop_profit_reached_date", "")
    # Daily PnL anchor: clear so it re-anchors fresh on next cycle
    update_setting("real_daily_start_balance", "")
    update_setting("real_daily_start_date", "")
    # Cap Scale Up: deactivate and restore original bet
    if get_setting("cap_scale_up_active") == "true":
        original_bet = get_setting("cap_scale_up_original_bet")
        if original_bet:
            update_setting("real_bet_amount", original_bet)
        update_setting("cap_scale_up_active", "false")
        update_setting("cap_scale_up_original_bet", "")

    # Clear in-memory bot state caches
    bot_state.traded_slugs.clear()
    bot_state.skipped_slugs.clear()
    bot_state.disqualified_slugs.clear()
    bot_state.notifications.clear()
    bot_state._stop_profit_logged = False

    return jsonify({"status": "ok", "message": "All history and flags cleared"})


@app.route("/api/claim", methods=["POST"])
def api_claim():
    """Manually trigger claiming of all unclaimed winning trades."""
    if get_trading_mode() not in ("real", "shadow"):
        return jsonify({"error": "Claim not available in paper mode"}), 400
    try:
        from trader import auto_claim_wins
        claimed = auto_claim_wins()
        return jsonify({"status": "ok", "claimed": claimed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/claims/status")
def api_claims_status():
    """Get count of unclaimed winning trades."""
    from database import get_unclaimed_wins
    unclaimed = get_unclaimed_wins()
    return jsonify({
        "unclaimed_count": len(unclaimed),
        "unclaimed_total_pnl": round(sum(t.get("pnl", 0) for t in unclaimed), 4),
    })

@app.route("/api/projection")
def api_projection():
    """
    Calculate PnL projection based on today's performance only (UTC+7).
    """
    from datetime import datetime, timedelta, timezone

    custom_capital = request.args.get("capital", 5.0, type=float)
    stats = get_stats()
    trades = get_trades(limit=10000)

    tz_utc_7 = timezone(timedelta(hours=7))
    now_local = datetime.now(timezone.utc).astimezone(tz_utc_7)
    today_date = now_local.date()

    resolved_today = []
    wins = 0
    losses = 0

    for t in trades:
        if t.get("result") == "PENDING":
            continue

        if "timestamp" not in t:
            continue

        try:
            dt = datetime_from_iso(t["timestamp"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            
            dt_local = dt.astimezone(tz_utc_7)
            if dt_local.date() == today_date:
                resolved_today.append(t)
                res = t.get("result")
                if res == "WIN":
                    wins += 1
                elif res == "LOSS":
                    losses += 1
        except Exception:
            pass

    if not resolved_today:
        return jsonify({
            "custom_capital": custom_capital,
            "projected_pnl": 0,
            "projected_balance": custom_capital,
            "projected_roi": 0,
            "avg_pnl_per_trade": 0,
            "win_rate": 0,
            "total_resolved": 0,
            "avg_return_pct": 0,
            "projected_daily_trades": 0,
            "projected_daily_pnl": 0,
            "projected_weekly_pnl": 0,
            "projected_monthly_pnl": 0,
        })

    total_resolved = len(resolved_today)
    win_rate = round((wins / total_resolved) * 100, 2) if total_resolved > 0 else 0

    # Calculate average per-trade return percentage for today
    trade_returns = []
    for t in resolved_today:
        bet = t.get("bet_amount", 0)
        if bet > 0:
            ret_pct = t.get("pnl", 0) / bet
            trade_returns.append(ret_pct)

    avg_return_pct = sum(trade_returns) / len(trade_returns) if trade_returns else 0

    # Scale bet size with custom capital
    starting = stats.get("starting_capital", 0)
    baseline = starting if starting > 0 else custom_capital
    scale = (custom_capital / baseline) if baseline > 0 else 1.0
    bet_size = max(0.01, min(custom_capital, BET_AMOUNT * scale))
    
    avg_pnl_per_trade = bet_size * avg_return_pct
    trades_per_day = total_resolved
    projected_daily = avg_pnl_per_trade * trades_per_day

    projected_pnl = projected_daily
    projected_balance = custom_capital + projected_pnl
    total_return_pct = projected_pnl / custom_capital if custom_capital > 0 else 0

    # #region agent log
    _agent_log(
        "H1",
        "projection_calc_today_only",
        {
            "custom_capital": custom_capital,
            "starting_capital": starting,
            "baseline": baseline,
            "scale": scale,
            "BET_AMOUNT": BET_AMOUNT,
            "bet_size": bet_size,
            "avg_return_pct": avg_return_pct,
            "trades_per_day": trades_per_day,
            "projected_daily": projected_daily,
            "today_date": str(today_date)
        },
    )
    # #endregion agent log

    return jsonify({
        "custom_capital": custom_capital,
        "projected_pnl": round(projected_pnl, 4),
        "projected_balance": round(projected_balance, 4),
        "projected_roi": round(total_return_pct * 100, 2),
        "win_rate": win_rate,
        "total_resolved": total_resolved,
        "avg_return_pct": round(avg_return_pct * 100, 2),
        "projected_daily_trades": round(trades_per_day, 1),
        "projected_daily_pnl": round(projected_daily, 4),
        "projected_weekly_pnl": round(projected_daily * 7, 4),
        "projected_monthly_pnl": round(projected_daily * 30, 4),
    })


@app.route("/api/projection/charts")
def api_projection_charts():
    """Get aggregated win/loss data for projection charts."""
    period = request.args.get("period", "today", type=str)
    date_from = request.args.get("from", type=str)
    date_to = request.args.get("to", type=str)
    if period not in {"today", "weekly", "monthly", "yearly"}:
        period = "today"
    data = get_projection_chart_data(period=period, date_from=date_from, date_to=date_to)
    return jsonify(data)


def datetime_from_iso(s):
    """Parse ISO datetime string."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f")
