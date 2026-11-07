# =============================================================================
# Polymarket Bot — Flask Dashboard (Vercel-compatible)
# =============================================================================
# Lightweight dashboard that works on Vercel serverless.
# Bot state is stub (bot runs via /api/cron.py cron job, not in-memory).
# =============================================================================

import os
import sys
import json
import time
import sqlite3
import logging
import threading
from collections import OrderedDict
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("dashboard")

# ── Config ──
TRADING_MODE = os.environ.get("TRADING_MODE", "shadow").strip().lower()
BET_AMOUNT = 2.0
REAL_BET_AMOUNT = float(os.environ.get("REAL_BET_AMOUNT", "1.00"))
MAX_BET_AMOUNT = float(os.environ.get("MAX_BET_AMOUNT", "10.00"))
DEFAULT_CAPITAL = 10.0
COUNTDOWN_BUY_START = 210
DB_PATH = os.environ.get("DATABASE_URL", "/tmp/polybots.db")


# ── Stub Bot State ──
class BotState:
    def __init__(self):
        self.running = False
        self.current_market = None
        self.up_price = None
        self.down_price = None
        self.countdown = None
        self.last_error = "Bot runs via Vercel Cron (every 1 min)"
        self.last_update = None
        self.traded_slugs = OrderedDict()
        self.skipped_slugs = OrderedDict()
        self.disqualified_slugs = OrderedDict()
        self.notifications = [{"msg": "📡 Vercel mode — bot runs via cron", "time": ""}]
        self.lock = threading.Lock()
        self.display_up_price = None
        self.display_down_price = None
        self._stop_profit_logged = False

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
                "countdown_display": "--:--",
                "last_error": self.last_error,
                "last_update": self.last_update,
                "notifications": self.notifications[-20:],
            }

bot_state = BotState()


# ── Database ──
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        market_slug TEXT NOT NULL, market_question TEXT DEFAULT '',
        side TEXT NOT NULL, entry_price REAL NOT NULL, shares REAL NOT NULL,
        bet_amount REAL NOT NULL, result TEXT DEFAULT 'PENDING',
        pnl REAL DEFAULT 0.0, balance_after REAL DEFAULT 0.0,
        resolved_at TEXT DEFAULT NULL, token_id TEXT DEFAULT '',
        end_time REAL DEFAULT 0, trading_mode TEXT DEFAULT 'paper',
        order_id TEXT DEFAULT '', order_status TEXT DEFAULT '',
        tx_hash TEXT DEFAULT '', condition_id TEXT DEFAULT '',
        claimed INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS skipped_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        market_slug TEXT NOT NULL, market_question TEXT DEFAULT '',
        reason TEXT NOT NULL, up_price REAL DEFAULT NULL,
        down_price REAL DEFAULT NULL, countdown REAL DEFAULT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    defaults = {"capital": str(DEFAULT_CAPITAL), "current_balance": str(DEFAULT_CAPITAL),
                "shadow_capital": "10.0", "shadow_balance": "10.0",
                "bot_running": "true", "theme": "dark"}
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()

try:
    _init_db()
except Exception:
    pass

def get_setting(key):
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else None
    except: return None

def update_setting(key, value):
    try:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()
    except: pass

def get_trading_mode():
    val = get_setting("trading_mode")
    return val if val else TRADING_MODE

def get_current_balance():
    val = get_setting("current_balance")
    return float(val) if val else DEFAULT_CAPITAL

def get_shadow_balance():
    val = get_setting("shadow_balance")
    return float(val) if val else 10.0


# ── Flask App ──
_project_root = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(_project_root, "templates"),
            static_folder=os.path.join(_project_root, "static"))


@app.route("/")
def index():
    return render_template("index.html", project_title="carlies")


@app.route("/api/stats")
def api_stats():
    try:
        conn = get_conn()
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        wins = conn.execute("SELECT COUNT(*) FROM trades WHERE result='WIN'").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM trades WHERE result='LOSS'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM trades WHERE result='PENDING'").fetchone()[0]
        total_pnl = float(conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE result != 'PENDING' "
            "AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')"
        ).fetchone()[0])
        capital = float(get_setting("capital") or DEFAULT_CAPITAL)
        conn.close()
        return jsonify({
            "total_trades": total, "wins": wins, "losses": losses, "pending": pending,
            "win_rate": round((wins/(wins+losses)*100), 2) if (wins+losses) > 0 else 0,
            "total_pnl": round(total_pnl, 6),
            "starting_capital": capital,
            "current_balance": round(get_current_balance(), 6),
            "shadow_capital": float(get_setting("shadow_capital") or 10.0),
            "shadow_balance": round(get_shadow_balance(), 6),
            "roi": 0, "avg_entry_price": 0, "avg_pnl_per_trade": 0,
        })
    except:
        return jsonify({"total_trades":0,"wins":0,"losses":0,"pending":0,"win_rate":0,"total_pnl":0,
                        "starting_capital":DEFAULT_CAPITAL,"current_balance":DEFAULT_CAPITAL,
                        "shadow_capital":10.0,"shadow_balance":10.0,"roi":0,"avg_entry_price":0,"avg_pnl_per_trade":0})


@app.route("/api/trades")
def api_trades():
    try:
        conn = get_conn()
        limit = request.args.get("limit", 200, type=int)
        offset = request.args.get("offset", 0, type=int)
        rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except:
        return jsonify([])


@app.route("/api/skipped")
def api_skipped():
    try:
        conn = get_conn()
        limit = request.args.get("limit", 200, type=int)
        offset = request.args.get("offset", 0, type=int)
        rows = conn.execute("SELECT * FROM skipped_events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except:
        return jsonify([])


@app.route("/api/live")
def api_live():
    state = bot_state.to_dict()
    mode = get_trading_mode()
    if mode == "shadow":
        state["balance"] = get_shadow_balance()
    elif mode == "real":
        state["balance"] = None
    else:
        state["balance"] = get_current_balance()
    state["trading_mode"] = mode
    state["countdown_buy_start"] = COUNTDOWN_BUY_START
    return jsonify(state)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
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
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    for key in ["capital", "shadow_capital", "real_bet_amount", "stop_profit_target", "cap_scale_up_amount"]:
        if key in data:
            update_setting(key, f"{float(data[key]):.4f}")
    for key in ["bot_running", "stop_profit_enabled", "scheduler_enabled", "cap_scale_up_enabled"]:
        if key in data:
            update_setting(key, "true" if data[key] else "false")
    if "theme" in data:
        update_setting("theme", data["theme"])
    if "trading_mode" in data:
        mode = data["trading_mode"].lower()
        if mode in ("shadow", "real"):
            update_setting("trading_mode", mode)
    return jsonify({"status": "ok"})


@app.route("/api/wallet")
def api_wallet():
    return jsonify({"mode": get_trading_mode(), "balance": None, "note": "Use /api/bot-tick for live data"})

@app.route("/api/kill-switch", methods=["POST"])
def api_kill_switch():
    update_setting("bot_running", "false")
    return jsonify({"status": "killed", "message": "Bot paused (cron will skip next tick)"})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    data = request.get_json() or {}
    if data.get("confirm") != "RESET":
        return jsonify({"error": "Send {\"confirm\": \"RESET\"} to confirm"}), 400
    try:
        conn = get_conn()
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM skipped_events")
        conn.commit()
        conn.close()
        update_setting("current_balance", str(DEFAULT_CAPITAL))
        update_setting("capital", str(DEFAULT_CAPITAL))
        update_setting("stop_profit_reached_date", "")
        update_setting("real_daily_start_balance", "")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "ok", "message": "All history cleared"})

@app.route("/api/claim", methods=["POST"])
def api_claim():
    return jsonify({"status": "info", "message": "Claims run automatically via cron"})

@app.route("/api/claims/status")
def api_claims_status():
    return jsonify({"unclaimed_count": 0, "unclaimed_total_pnl": 0})

@app.route("/api/projection")
def api_projection():
    return jsonify({"custom_capital": 5.0, "projected_pnl": 0, "projected_balance": 5.0,
                    "projected_roi": 0, "win_rate": 0, "total_resolved": 0,
                    "avg_return_pct": 0, "projected_daily_trades": 0,
                    "projected_daily_pnl": 0, "projected_weekly_pnl": 0, "projected_monthly_pnl": 0})

@app.route("/api/projection/charts")
def api_projection_charts():
    return jsonify({"period": "today", "series": [], "totals": {"win_amount": 0, "loss_amount": 0, "net_amount": 0}})
