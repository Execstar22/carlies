import re
import datetime

def run_simulation_and_export(file_path, output_path, initial_capital, bet_size):
    with open(file_path, 'r') as f:
        lines = f.readlines()

    balance = initial_capital
    peak = initial_capital
    drawdown = 0
    min_balance = initial_capital
    
    wins = 0
    losses = 0
    draws = 0
    
    output_lines = []
    output_lines.append(f"=== RINCIAN SIMULASI 177 TRANSAKSI ===")
    output_lines.append(f"Modal Awal: ${initial_capital:.2f}")
    output_lines.append(f"Ukuran Bet: ${bet_size:.2f}\n")
    output_lines.append(f"{'No':<5} | {'Waktu/Market':<45} | {'Side':<5} | {'Result':<6} | {'Simulated PnL':<15} | {'Balance':<10}")
    output_lines.append("-" * 100)
    
    trade_no = 0
    current_market = ""
    
    for line in lines:
        market_match = re.search(r'\[(.*?)\] Mode:.*Market: (\S+)', line)
        if market_match:
            time_str = market_match.group(1).split('.')[0]
            market_name = market_match.group(2)
            current_market = f"[{time_str}] {market_name}"
            
        if "Result:" in line:
            trade_no += 1
            
            bet_match = re.search(r'Bet:\s*\$([\d\.]+)', line)
            orig_bet = float(bet_match.group(1)) if bet_match else 2.0
            
            res_match = re.search(r'Result:\s*([A-Z]+)', line)
            result = res_match.group(1) if res_match else "UNKNOWN"
            
            side_match = re.search(r'Side:\s*([A-Z]+)', line)
            side = side_match.group(1) if side_match else "N/A"
            
            pnl_match = re.search(r'PnL:\s*\$([\-\d\.]+)', line)
            orig_pnl = float(pnl_match.group(1)) if pnl_match else 0.0
            
            if result == 'WIN':
                wins += 1
                ratio = orig_pnl / orig_bet
                trade_pnl = ratio * bet_size
            elif result == 'LOSS':
                losses += 1
                trade_pnl = -bet_size
            else:
                draws += 1
                trade_pnl = 0
                
            balance += trade_pnl
            
            if balance > peak:
                peak = balance
            if balance < min_balance:
                min_balance = balance
            
            current_drawdown = peak - balance
            if current_drawdown > drawdown:
                drawdown = current_drawdown
                
            pnl_str = f"${trade_pnl:.4f}"
            if trade_pnl > 0:
                pnl_str = f"+{pnl_str}"
                
            output_lines.append(f"{trade_no:<5} | {current_market:<45} | {side:<5} | {result:<6} | {pnl_str:<15} | ${balance:.4f}")
            
            if balance <= 0:
                output_lines.append("\n!!! AKUN TERLIKUIDASI (BALANCE MENCAPAI <= 0) !!!")
                break

    with open(output_path, 'w') as f:
        f.write('\n'.join(output_lines))
        
    total_trades = wins + losses + draws
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    
    print(f"=== SIMULATION RESULTS ===")
    print(f"Initial Capital : ${initial_capital:.2f}")
    print(f"Bet Size        : ${bet_size:.2f}")
    print(f"Total Trades    : {total_trades}")
    print(f"Wins            : {wins}")
    print(f"Losses          : {losses}")
    print(f"Win Rate        : {win_rate:.2f}%")
    print(f"--------------------------")
    print(f"Final Balance   : ${balance:.2f}")
    print(f"Net Profit      : ${balance - initial_capital:.2f}")
    print(f"Highest Balance : ${peak:.2f}")
    print(f"Lowest Balance  : ${min_balance:.2f}")
    print(f"Max Drawdown    : ${drawdown:.2f}")
    print(f"\nFile rincian berhasil disimpan di: {output_path}")

if __name__ == '__main__':
    input_file = 'c:/Users/aL/Pictures/REALBOTS NO LQ/PnL_Gabungan.txt'
    output_file = 'c:/Users/aL/Pictures/REALBOTS NO LQ/Rincian_Simulasi_177_Trade_15_5_1_1.txt'
    run_simulation_and_export(input_file, output_file, 15.5, 1.1)
