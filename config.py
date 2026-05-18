# =============================================================================
# Polymarket BTC Up/Down Bot — Configuration
# =============================================================================

import os
from dotenv import load_dotenv
load_dotenv()

# --- Trading Mode ---
# "shadow" = Bot jalan tapi HANYA LOG, tidak place order (untuk testing)
# "real"   = Eksekusi order nyata (UANG ASLI!)
TRADING_MODE = os.environ.get("TRADING_MODE", "shadow").strip().lower()


def get_trading_mode():
    """Get current trading mode from DB (runtime-toggleable), fallback to .env."""
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM settings WHERE key = 'trading_mode'").fetchone()
        conn.close()
        if row:
            return row["value"]
    except Exception:
        pass
    return TRADING_MODE

# --- API Endpoints ---
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# --- Market Settings ---
MARKET_SLUG_PREFIX = "btc-updown-5m"
MARKET_INTERVAL_SECONDS = 300  # 5 minutes

# --- Strategy Settings ---
# Countdown window: buy when remaining time is between these values (seconds)
COUNTDOWN_BUY_START = 150  # ⚡ HPA: Eksekusi mulai sisa 2 menit 30 detik
COUNTDOWN_BUY_END = 0      # ⚡ HPA: Eksekusi hingga detik 0

# --- Price Boundaries (Nomenklatur Harga) ---
# PRICE_FLOOR   : Batas termurah. Harga di bawah ini dianggap berisiko (crash/akan loss) -> Skip.
# PRICE_TARGET  : Harga incaran utama/ideal yang akan diprioritaskan oleh bot.
# PRICE_CEILING : Batas termahal mutlak (Safety Net). Toleransi slippage maksimal untuk eksekusi FOK.
PRICE_FLOOR = 0.78
PRICE_TARGET = 0.81
PRICE_CEILING = 0.85

# --- Capital & Bet Amount ---
DEFAULT_CAPITAL = 10.0       # Starting capital in USD (paper mode)
BET_AMOUNT = 2.0             # Fixed bet amount per trade in USD (paper mode)

# Real trading bet amount — loaded from .env, default $0.50
REAL_BET_AMOUNT = float(os.environ.get("REAL_BET_AMOUNT", "1.00"))
MAX_BET_AMOUNT = float(os.environ.get("MAX_BET_AMOUNT", "3.00"))
MAX_DAILY_LOSS = float(os.environ.get("MAX_DAILY_LOSS", "0.0"))  # 0.0 = unlimited; loaded from .env

# Polygon RPC for on-chain claims (redeeming winnings)
POLYGON_RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://polygon-rpc.com")

# --- Polling ---
POLL_INTERVAL = 3              # ⚡ HPA: API Polling 3 detik
MARKET_SCAN_INTERVAL = 30   # Seconds between scanning for new markets

# --- Database ---
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polybots.db")

# --- Dashboard ---
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = 6061
