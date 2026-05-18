# =============================================================================
# Polymarket BTC Up/Down Bot — Entry Point
# =============================================================================
# Run this to start both the bot engine and the web dashboard.
#
#   python run.py
#
# Dashboard will be available at http://localhost:8081
# =============================================================================

import sys
import os
import logging
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


def main():
    mode_labels = {
        "paper": "📝 PAPER TRADING (simulasi)",
        "shadow": "👻 SHADOW MODE (log only, no real orders)",
        "real": "🔴 REAL TRADING (UANG ASLI!)",
    }
    mode_label = mode_labels.get(TRADING_MODE, f"❓ UNKNOWN ({TRADING_MODE})")

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
                    log.error(f"❌ Wallet validation failed: {msg}")
                    log.error("Fix your .env configuration and try again.")
                    sys.exit(1)
                else:
                    log.warning(f"⚠️ Wallet not configured: {msg}")
                    log.warning("Shadow mode will run but won't validate wallet balance.")
            else:
                log.info(f"✅ {msg}")
        except Exception as e:
            if TRADING_MODE == "real":
                log.error(f"❌ Wallet setup error: {e}")
                sys.exit(1)
            else:
                log.warning(f"⚠️ Wallet setup skipped: {e}")

        if TRADING_MODE == "real":
            print("\n⚠️  WARNING: Bot is in REAL TRADING mode!")
            print("    Real money (USDC) will be used for trades.")
            if os.environ.get("CLOUD_DEPLOY", "").lower() == "true":
                print("    ☁️  Cloud deployment detected — auto-starting...\n")
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
    except KeyboardInterrupt:
        log.info("Shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
