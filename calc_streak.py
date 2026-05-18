import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polybots.db")

def calculate_streaks():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get trades ordered by ID (chronological)
    cursor.execute("SELECT id, result, bet_amount, pnl FROM trades WHERE result IN ('WIN', 'LOSS') ORDER BY id ASC")
    trades = cursor.fetchall()
    
    # Track naive streaks ($2 per win)
    naive_streaks = 0
    current_naive_streak = 0
    
    # Track actual PNL streaks
    actual_streaks = 0
    current_actual_streak = 0.0
    
    naive_history = []
    actual_history = []
    
    for trade_id, result, bet_amount, pnl in trades:
        if result == 'WIN':
            current_naive_streak += 2.0
            current_actual_streak += pnl if pnl is not None else 0.0
            
            # Check if naive reached $10
            if current_naive_streak >= 10.0:
                naive_streaks += 1
                naive_history.append((trade_id, current_naive_streak))
                current_naive_streak = 0.0 # reset after reaching $10
                
            # Check if actual reached $10
            if current_actual_streak >= 10.0:
                actual_streaks += 1
                actual_history.append((trade_id, current_actual_streak))
                current_actual_streak = 0.0 # reset after reaching $10
                
        elif result == 'LOSS':
            # Streak broken
            current_naive_streak = 0.0
            current_actual_streak = 0.0

    print(f"Berdasarkan hitungan kasar ($2 per WIN), menyentuh $10 sebelum loss sebanyak: {naive_streaks} kali")
    print(f"Berdasarkan PNL aktual (real profit Polymarket), menyentuh $10 sebelum loss sebanyak: {actual_streaks} kali")

    # What if the user meant: "how many times did a win streak reach >= $10 BEFORE a loss"? 
    # That means counting the number of unbroken win streaks that achieved at least $10.
    
    # Let's recount as "number of loss-delimited streaks that reached $10"
    streak_count_naive = 0
    streak_count_actual = 0
    cur_naive = 0.0
    cur_actual = 0.0
    reached_naive = False
    reached_actual = False
    
    for trade_id, result, bet_amount, pnl in trades:
        if result == 'WIN':
            cur_naive += 2.0
            cur_actual += pnl if pnl is not None else 0.0
            if cur_naive >= 10.0:
                reached_naive = True
            if cur_actual >= 10.0:
                reached_actual = True
        elif result == 'LOSS':
            if reached_naive:
                streak_count_naive += 1
            if reached_actual:
                streak_count_actual += 1
                
            cur_naive = 0.0
            cur_actual = 0.0
            reached_naive = False
            reached_actual = False
            
    # Check trailing streak
    if reached_naive:
        streak_count_naive += 1
    if reached_actual:
        streak_count_actual += 1
        
    print(f"Jumlah Streak (deretan tanpa putus) yg mencapai >= $10 (Kasaran): {streak_count_naive} kali")
    print(f"Jumlah Streak (deretan tanpa putus) yg mencapai >= $10 (Aktual): {streak_count_actual} kali")

    conn.close()

if __name__ == '__main__':
    calculate_streaks()
