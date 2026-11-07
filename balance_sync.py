def sync_real_starting_balance():
    """
    Sync real_starting_balance with on-chain pUSD balance on startup ONLY.

    Formula: ledger_balance = starting + resolved_pnl - pending_bets
    If ledger_balance != on_chain, correct starting_balance and all balance_after.

    IMPORTANT: This should ONLY run at startup when:
    - All previous wins have been claimed (on-chain reflects true balance)
    - No trades are actively settling
    DO NOT run this during active trading — on-chain may not reflect unclaimed wins.
    """
    import logging
    log = logging.getLogger("balance_sync")

    try:
        from trader import get_real_balance
        from database import get_conn

        on_chain = get_real_balance()
        if on_chain is None:
            log.warning("Could not fetch on-chain balance for sync")
            return False

        conn = get_conn()

        # Sum resolved PnL for real trades
        resolved_pnl = float(conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades "
            "WHERE trading_mode='real' AND result != 'PENDING'"
        ).fetchone()[0])

        # Sum pending bet amounts
        pending_bets = float(conn.execute(
            "SELECT COALESCE(SUM(bet_amount), 0) FROM trades "
            "WHERE trading_mode='real' AND result = 'PENDING'"
        ).fetchone()[0])

        # Current stored starting balance
        old_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'real_starting_balance'"
        ).fetchone()
        old_starting = float(old_row["value"]) if old_row else 0.0

        # What the ledger THINKS the current balance is:
        ledger_balance = old_starting + resolved_pnl - pending_bets

        # Compare ledger endpoint with on-chain reality:
        drift = abs(ledger_balance - on_chain)

        if drift > 0.01:  # More than 1 cent difference
            # Correct starting so that (starting + pnl - pending) == on_chain
            new_starting = on_chain - resolved_pnl + pending_bets

            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('real_starting_balance', ?)",
                (f"{new_starting:.6f}",)
            )

            # Recalculate all balance_after with corrected starting
            trades = conn.execute(
                "SELECT id, bet_amount, result, pnl FROM trades "
                "WHERE trading_mode='real' ORDER BY id ASC"
            ).fetchall()

            cum_pnl = 0.0
            cum_pending = 0.0
            updated = 0
            for t in trades:
                if t['result'] in ('WIN', 'LOSS'):
                    cum_pnl += t['pnl']
                else:
                    cum_pending += t['bet_amount']
                correct_bal = new_starting + cum_pnl - cum_pending
                conn.execute(
                    "UPDATE trades SET balance_after = ? WHERE id = ?",
                    (correct_bal, t['id'])
                )
                updated += 1

            conn.commit()
            log.info(
                f"Balance sync CORRECTED: starting ${old_starting:.4f} -> ${new_starting:.4f} "
                f"(ledger was ${ledger_balance:.4f}, on-chain=${on_chain:.4f}, "
                f"drift=${drift:.4f}, {updated} balances recalculated)"
            )
        else:
            log.info(
                f"Balance sync OK: starting=${old_starting:.4f}, "
                f"on-chain=${on_chain:.4f}, drift=${drift:.4f}"
            )

        conn.close()
        return True

    except Exception as e:
        log.warning(f"Balance sync failed: {e}")
        return False
