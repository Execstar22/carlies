import sqlite3

conn = sqlite3.connect("polybots.db")
conn.execute("UPDATE settings SET value = '' WHERE key = 'stop_profit_reached_date'")
conn.commit()

row = conn.execute("SELECT value FROM settings WHERE key = 'stop_profit_reached_date'").fetchone()
print(f"stop_profit_reached_date = [{row[0]}]")
print("Done! Bot akan kembali trading di market berikutnya.")
conn.close()
