import sqlite3
from datetime import datetime, timezone

# Trade 2928 order_id: shadow-1782600457
# Trade 2782 order_id: shadow-1782600521

t2928_order_epoch = 1782600457
t2782_order_epoch = 1782600521
end_time = 1782600600

countdown_2928 = end_time - t2928_order_epoch
countdown_2782 = end_time - t2782_order_epoch

print("=== Using order_id epoch (actual execution time) ===")
print(f"\nTrade 2928 (polybots2 - DOWN):")
print(f"  order_id: shadow-{t2928_order_epoch}")
print(f"  Order placed at: {datetime.fromtimestamp(t2928_order_epoch, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print(f"  Countdown at execution: {countdown_2928}s ({countdown_2928//60}m {countdown_2928%60}s)")
print(f"  Entry price: $0.75")

print(f"\nTrade 2782 (polybots1 - UP):")
print(f"  order_id: shadow-{t2782_order_epoch}")
print(f"  Order placed at: {datetime.fromtimestamp(t2782_order_epoch, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print(f"  Countdown at execution: {countdown_2782}s ({countdown_2782//60}m {countdown_2782%60}s)")
print(f"  Entry price: $0.80")

print(f"\n=== Summary ===")
print(f"  Time difference between trades: {abs(t2928_order_epoch - t2782_order_epoch)}s")
print(f"  Trade 2928 entered {t2782_order_epoch - t2928_order_epoch}s BEFORE trade 2782")
print(f"  Countdown 2928: {countdown_2928}s (~{countdown_2928//60}:{countdown_2928%60:02d})")
print(f"  Countdown 2782: {countdown_2782}s (~{countdown_2782//60}:{countdown_2782%60:02d})")
print(f"  Both within 0-150s window? 2928={0 <= countdown_2928 <= 150}, 2782={0 <= countdown_2782 <= 150}")

print(f"\n=== Price Check ===")
print(f"  Config: PRICE_FLOOR=0.75, PRICE_CEILING=0.85")
print(f"  Trade 2928: DOWN @ $0.75 -> within [0.75, 0.85] = True")
print(f"  Trade 2782: UP @ $0.80 -> within [0.75, 0.85] = True")

print(f"\n=== ROOT CAUSE ===")
print(f"  Kedua bot jalan INDEPENDEN pada market yang SAMA")
print(f"  Bot2: saat countdown=143s, DOWN price=$0.75 masuk range -> BUY DOWN")
print(f"  Bot1: saat countdown=79s, UP price=$0.80 masuk range -> BUY UP")
print(f"  Gap 64 detik karena kondisi harga terpenuhi pada waktu berbeda")
print(f"  Bot2 masuk LEBIH AWAL karena DOWN=$0.75 sudah berada di floor")
print(f"  Bot1 masuk 64s KEMUDIAN ketika UP=$0.80 match target price")
print(f"  Hasilnya: Bot1 (UP) WIN, Bot2 (DOWN) LOSS -> mereka bertaruh BERLAWANAN!")
