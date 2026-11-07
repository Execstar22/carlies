import sqlite3
import os

# Check ALL databases for order_killed events
dbs = [
    'polybots.db',
    'TRADEDB/polybots.db',
    'TRADEDB/polybots1.db',
    'TRADEDB/polybots2.db',
    'polybotsSIANG.db',
    'polybotsBACKUP 25 Juni.db',
    'polybots_backup_20260611_052302.db',
]

print("=" * 80)
print("SEARCHING ALL DATABASES FOR ORDER_KILLED / NON-PRICE SKIP REASONS")
print("=" * 80)

for db_path in dbs:
    if not os.path.exists(db_path):
        continue
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    tables = [r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    
    if 'skipped_events' in tables:
        # Count non-price reasons
        c.execute("""SELECT * FROM skipped_events 
            WHERE reason NOT LIKE 'price_out_of_range%' 
            AND reason NOT LIKE 'stop_profit_reached%'
            ORDER BY id DESC LIMIT 20""")
        rows = c.fetchall()
        if rows:
            print(f"\n>>> {db_path}: Found {len(rows)} non-price/non-stop-profit skips:")
            for r in rows:
                d = dict(r)
                print(f"  #{d['id']} | {d['timestamp']} | {d.get('market_question','')}")
                print(f"           reason: {d['reason']}")
                print()
        
        # Specifically search for order_killed, liquidity, balance, FOK
        for keyword in ['order_killed', 'liquidity', 'balance', 'FOK', 'KILLED', 'failed', 'error', 'timeout']:
            c.execute(f"SELECT COUNT(*) as cnt FROM skipped_events WHERE reason LIKE '%{keyword}%'")
            cnt = c.fetchone()['cnt']
            if cnt > 0:
                print(f"  >>> {db_path}: {cnt} events with '{keyword}' in reason")
                c.execute(f"SELECT * FROM skipped_events WHERE reason LIKE '%{keyword}%' ORDER BY id DESC LIMIT 5")
                for r in c.fetchall():
                    d = dict(r)
                    print(f"    #{d['id']} | {d['timestamp']} | reason: {d['reason']}")
    
    if 'trades' in tables:
        # Check for non-matched, non-SHADOW trades (real orders that failed)
        try:
            c.execute("""SELECT * FROM trades 
                WHERE order_status IS NOT NULL 
                AND order_status != 'matched' 
                AND order_status != 'SHADOW'
                AND order_status != ''
                ORDER BY id DESC LIMIT 10""")
            rows = c.fetchall()
            if rows:
                print(f"\n>>> {db_path}: Found {len(rows)} REAL trades with non-matched status:")
                for r in rows:
                    d = dict(r)
                    print(f"  #{d['id']} | {d['timestamp']} | {d.get('market_question','')}")
                    print(f"           side={d.get('side','')}, price={d.get('entry_price','')}")
                    print(f"           order_status={d.get('order_status','')}")
                    print(f"           order_id={d.get('order_id','')}")
                    print()
        except Exception as e:
            pass
    
    conn.close()

print("\n" + "=" * 80)
print("DONE - If nothing printed above, NO order_killed events exist in any DB")
print("=" * 80)
