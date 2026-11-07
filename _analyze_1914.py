"""Analyze timing of trades #1913-#1916 to find premature resolution."""
from datetime import datetime, timezone

trades = [
    {"id": 1913, "timestamp": "2026-06-01T18:47:54.226978", "end_time": 1780339800.0,
     "resolved_at": "2026-06-01T18:49:53.627238", "slug": "btc-updown-5m-1780339500"},
    {"id": 1914, "timestamp": "2026-06-01T18:52:31.283172", "end_time": 1780340100.0,
     "resolved_at": "2026-06-01T18:54:00.020176", "slug": "btc-updown-5m-1780339800"},
    {"id": 1915, "timestamp": "2026-06-01T18:57:40.054895", "end_time": 1780340400.0,
     "resolved_at": "2026-06-01T19:01:16.326545", "slug": "btc-updown-5m-1780340100"},
    {"id": 1916, "timestamp": "2026-06-01T19:03:12.591230", "end_time": 1780340700.0,
     "resolved_at": "2026-06-01T19:05:23.687946", "slug": "btc-updown-5m-1780340400"},
]

for t in trades:
    end_ts = t["end_time"]
    end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
    resolved_dt = datetime.fromisoformat(t["resolved_at"]).replace(tzinfo=timezone.utc)
    resolved_ts = resolved_dt.timestamp()

    diff_seconds = resolved_ts - end_ts

    slug_ts = int(t["slug"].split("-")[-1])
    slug_dt = datetime.fromtimestamp(slug_ts, tz=timezone.utc)
    slug_end = slug_ts + 300  # 5 minutes
    slug_end_dt = datetime.fromtimestamp(slug_end, tz=timezone.utc)

    trade_dt = datetime.fromisoformat(t["timestamp"]).replace(tzinfo=timezone.utc)
    trade_ts = trade_dt.timestamp()

    print(f"=== Trade #{t['id']} ===")
    print(f"  Slug:             {t['slug']}")
    print(f"  Market window:    {slug_dt.strftime('%H:%M:%S')} -> {slug_end_dt.strftime('%H:%M:%S')} UTC")
    print(f"  end_time (DB):    {end_dt.strftime('%H:%M:%S')} UTC  (ts={end_ts})")
    print(f"  Trade placed:     {trade_dt.strftime('%H:%M:%S')} UTC  (ts={trade_ts})")
    print(f"  Resolved at:      {resolved_dt.strftime('%H:%M:%S')} UTC  (ts={resolved_ts})")
    print(f"  Resolution vs market end: {diff_seconds:+.1f}s  ({'PREMATURE!' if diff_seconds < 0 else 'OK (after end)'})")
    print()
