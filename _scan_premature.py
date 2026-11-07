"""Scan ALL trades for premature resolution (resolved before market end_time)."""
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("polybots.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT id, timestamp, market_slug, side, result, pnl, resolved_at, end_time, trading_mode
    FROM trades
    WHERE result IN ('WIN', 'LOSS') AND resolved_at IS NOT NULL AND end_time > 0
    ORDER BY id DESC
    LIMIT 100
""").fetchall()
conn.close()

premature_count = 0
total_checked = 0
premature_trades = []

for r in rows:
    total_checked += 1
    end_ts = r["end_time"]
    resolved_dt = datetime.fromisoformat(r["resolved_at"]).replace(tzinfo=timezone.utc)
    resolved_ts = resolved_dt.timestamp()
    diff = resolved_ts - end_ts

    if diff < 0:
        premature_count += 1
        premature_trades.append({
            "id": r["id"],
            "slug": r["market_slug"],
            "side": r["side"],
            "result": r["result"],
            "diff_seconds": diff,
            "resolved_at": r["resolved_at"],
            "end_time_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%H:%M:%S"),
            "mode": r["trading_mode"],
        })

print(f"Checked {total_checked} resolved trades (last 100)")
print(f"Premature resolutions: {premature_count}")
print()

if premature_trades:
    print("=== PREMATURE RESOLUTIONS ===")
    for t in premature_trades:
        print(f"  #{t['id']:4d} | {t['slug']:<35s} | {t['side']:4s} | {t['result']:4s} | "
              f"resolved {abs(t['diff_seconds']):.1f}s BEFORE end ({t['end_time_utc']} UTC) | {t['mode']}")
