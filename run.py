# =============================================================================
# Polymarket BTC Up/Down Bot — Entry Point
# =============================================================================
# Run this to start both the bot engine and the web dashboard.
#
#   python run.py
#
# Dashboard will be available at http://localhost:9007
# =============================================================================
import sys
import os

# ── Windows Console Fixes (must run BEFORE any print/import with emoji) ──
if sys.platform == 'win32':
    # Fix 1: Force UTF-8 encoding using reconfigure() — keeps TextIOWrapper
    # intact so Flask/logging/threading all work normally.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        # Python < 3.7 fallback: set env var (only helps child processes)
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    # Fix 2: Disable QuickEdit mode — prevents terminal freeze on mouse click
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        mode.value &= ~0x0040  # Disable ENABLE_QUICK_EDIT_MODE
        kernel32.SetConsoleMode(handle, mode)
    except Exception:
        pass

# Set window title
from pathlib import Path
project_name = Path(__file__).resolve().parent.name
os.system(f"title {project_name}")

import logging
import socket
import subprocess
import signal

from database import init_db, update_setting, get_conn, migrate_db_schema
from bot import start_bot
from app import app
from config import DASHBOARD_HOST, DASHBOARD_PORT, TRADING_MODE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("main")


def _kill_previous_instance(port):
    """Kill any previous Python process holding our port (zombie from last run)."""
    if sys.platform != 'win32':
        return
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        if result != 0:
            return  # Port is free

        out = subprocess.check_output(
            f'netstat -ano | findstr ":{port}" | findstr "LISTENING"',
            shell=True, text=True, stderr=subprocess.DEVNULL
        ).strip()
        if out:
            pid = int(out.split()[-1])
            if pid != os.getpid():
                os.kill(pid, signal.SIGTERM)
                log.info(f"[AUTO-CLEANUP] Killed previous bot (PID {pid}) on port {port}")
                import time; time.sleep(1)
    except Exception:
        pass


def main():
    mode_labels = {
        "paper": "PAPER TRADING (simulasi)",
        "shadow": "SHADOW MODE (log only, no real orders)",
        "real": "REAL TRADING (UANG ASLI!)",
    }
    mode_label = mode_labels.get(TRADING_MODE, f"UNKNOWN ({TRADING_MODE})")

    print(f"""
    +----------------------------------------------------------+
    |  [BOT] PolyBots - Polymarket BTC Up/Down Trader          |
    |                                                          |
    |  Mode: {mode_label:<51}|
    |  Dashboard:  http://localhost:{DASHBOARD_PORT}                        |
    |  Strategy:   Buy UP/DOWN when countdown 2:30-0:30        |
    |              and price <= 82 cents (Limit Agresif FOK)   |
    +----------------------------------------------------------+
    """)

    # Initialize database
    log.info("Initializing database...")
    init_db()

    # Run schema migration for real trading columns
    log.info("Running database migration...")
    migrate_db_schema()

    # Validate wallet if in real or shadow mode
    if TRADING_MODE in ("real", "shadow"):
        log.info(f"Trading mode: {TRADING_MODE.upper()}")
        try:
            from trader import validate_wallet
            success, msg = validate_wallet()
            if not success:
                if TRADING_MODE == "real":
                    log.error(f"Wallet validation failed: {msg}")
                    log.error("Fix your .env configuration and try again.")
                    sys.exit(1)
                else:
                    log.warning(f"Wallet not configured: {msg}")
                    log.warning("Shadow mode will run but won't validate wallet balance.")
            else:
                log.info(f"{msg}")
        except Exception as e:
            if TRADING_MODE == "real":
                log.error(f"Wallet setup error: {e}")
                sys.exit(1)
            else:
                log.warning(f"Wallet setup skipped: {e}")

        # ── Auto-sync real_starting_balance with on-chain ──
        try:
            from balance_sync import sync_real_starting_balance
            sync_real_starting_balance()
        except Exception as e:
            log.warning(f"Balance sync skipped: {e}")

        # ── Store on-chain balance as daily start anchor for stop-profit ──
        # GUARD: Only set anchor if no real trades exist today yet.
        # If trades exist, this is a mid-day restart — do NOT overwrite
        # the anchor with current (post-trade) on-chain balance.
        try:
            from trader import get_real_balance
            from database import update_setting, get_conn
            conn = get_conn()
            today_trade_count = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE trading_mode IN ('real','shadow') "
                "AND date(datetime(timestamp, '+7 hours')) = date('now', '+7 hours')"
            ).fetchone()[0]
            conn.close()

            if today_trade_count == 0:
                chain_bal = get_real_balance()
                if chain_bal is not None:
                    update_setting("real_daily_start_balance", f"{chain_bal:.6f}")
                    log.info(f"Daily start balance anchored to on-chain: ${chain_bal:.6f}")
            else:
                log.info(
                    f"Mid-day restart detected ({today_trade_count} trades today). "
                    f"Keeping existing daily anchor — NOT overwriting."
                )
        except Exception as e:
            log.warning(f"Daily start balance anchor skipped: {e}")

        if TRADING_MODE == "real":
            print("\n  WARNING: Bot is in REAL TRADING mode!")
            print("    Real money (USDC) will be used for trades.")
            if os.environ.get("CLOUD_DEPLOY", "").lower() == "true":
                print("    Cloud deployment detected — auto-starting...\n")
            else:
                print("    Press ENTER to continue or Ctrl+C to abort...\n")
                try:
                    input()
                except (KeyboardInterrupt, EOFError):
                    print("\nAborted.")
                    sys.exit(0)

    # Start bot in background thread
    log.info("Starting bot engine...")
    start_bot()

    # Auto-kill zombie bot from previous run
    _kill_previous_instance(DASHBOARD_PORT)

    # Start Flask dashboard (main thread)
    log.info(f"Starting dashboard on http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    log.info("Registered routes:")
    for rule in app.url_map.iter_rules():
        methods = ','.join(sorted(rule.methods - {'OPTIONS', 'HEAD'}))
        log.info(f"  {methods:8s} {rule.rule}")

    try:
        app.run(
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except OSError as e:
        if "10048" in str(e) or "address already in use" in str(e).lower():
            log.error(f"Port {DASHBOARD_PORT} is still in use! Close the other process and try again.")
        else:
            log.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        sys.exit(0)
    except Exception as e:
        log.error(f"FATAL: Flask crashed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

