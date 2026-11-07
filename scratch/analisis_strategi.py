import re

def parse_trades(file_path):
    """Parse all trades from PnL_Gabungan.txt and return list of (result, pnl_ratio) tuples."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    trades = []
    for line in lines:
        if "Result:" not in line:
            continue
        bet_match = re.search(r'Bet:\s*\$([\d\.]+)', line)
        res_match = re.search(r'Result:\s*([A-Z]+)', line)
        pnl_match = re.search(r'PnL:\s*\$([\-\d\.]+)', line)
        if not all([bet_match, res_match, pnl_match]):
            continue
        orig_bet = float(bet_match.group(1))
        result = res_match.group(1)
        orig_pnl = float(pnl_match.group(1))
        win_ratio = orig_pnl / orig_bet if result == 'WIN' else 0
        trades.append((result, win_ratio))
    return trades

def simulate(trades, capital, bet, label=""):
    """Simulate trades with given params. Returns dict of results."""
    balance = capital
    peak = capital
    min_bal = capital
    max_dd = 0
    wins = 0
    losses = 0
    consecutive_losses = 0
    max_consecutive_losses = 0
    
    for i, (result, ratio) in enumerate(trades):
        if result == 'WIN':
            pnl = ratio * bet
            wins += 1
            consecutive_losses = 0
        else:
            pnl = -bet
            losses += 1
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        
        balance += pnl
        peak = max(peak, balance)
        min_bal = min(min_bal, balance)
        max_dd = max(max_dd, peak - balance)
        
        if balance <= 0:
            return {
                'label': label, 'capital': capital, 'bet': bet,
                'final': 0, 'profit': -capital,
                'peak': peak, 'min': 0, 'max_dd': max_dd,
                'wins': wins, 'losses': losses, 'total': wins + losses,
                'wr': wins/(wins+losses)*100, 'max_loss_streak': max_consecutive_losses,
                'stopped': True, 'stopped_at': i+1, 'liquidated': True
            }
    
    total = wins + losses
    return {
        'label': label, 'capital': capital, 'bet': bet,
        'final': balance, 'profit': balance - capital,
        'peak': peak, 'min': min_bal, 'max_dd': max_dd,
        'wins': wins, 'losses': losses, 'total': total,
        'wr': wins/total*100, 'max_loss_streak': max_consecutive_losses,
        'stopped': False
    }

def simulate_tiered(trades, capital, tiers, label=""):
    balance = capital
    peak = capital
    min_bal = capital
    max_dd = 0
    wins = 0
    losses = 0
    consecutive_losses = 0
    max_consecutive_losses = 0
    
    for i, (result, ratio) in enumerate(trades):
        current_bet = tiers[0][1]
        for threshold, bet_size in tiers:
            if balance >= threshold:
                current_bet = bet_size
        
        if result == 'WIN':
            pnl = ratio * current_bet
            wins += 1
            consecutive_losses = 0
        else:
            pnl = -current_bet
            losses += 1
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        
        balance += pnl
        peak = max(peak, balance)
        min_bal = min(min_bal, balance)
        max_dd = max(max_dd, peak - balance)
        
        if balance <= 0:
            return {
                'label': label, 'capital': capital, 'bet': 'tiered',
                'final': 0, 'profit': -capital,
                'peak': peak, 'min': 0, 'max_dd': max_dd,
                'wins': wins, 'losses': losses, 'total': wins + losses,
                'wr': wins/(wins+losses)*100, 'max_loss_streak': max_consecutive_losses,
                'stopped': True, 'stopped_at': i+1, 'liquidated': True
            }
    
    total = wins + losses
    return {
        'label': label, 'capital': capital, 'bet': 'tiered',
        'final': balance, 'profit': balance - capital,
        'peak': peak, 'min': min_bal, 'max_dd': max_dd,
        'wins': wins, 'losses': losses, 'total': total,
        'wr': wins/total*100, 'max_loss_streak': max_consecutive_losses,
        'stopped': False
    }


def find_loss_streaks(trades):
    streaks = []
    current = 0
    for result, _ in trades:
        if result == 'LOSS':
            current += 1
        else:
            if current > 0:
                streaks.append(current)
            current = 0
    if current > 0:
        streaks.append(current)
    return streaks

def main():
    input_file = 'c:/Users/aL/Pictures/REALBOTS NO LQ/PnL_Gabungan.txt'
    trades = parse_trades(input_file)
    
    win_ratios = [ratio for result, ratio in trades if result == 'WIN']
    avg_win_ratio = sum(win_ratios) / len(win_ratios)
    loss_streaks = find_loss_streaks(trades)
    max_loss = max(loss_streaks)
    
    CAPITAL = 10.0
    MIN_BET = 1.1
    TARGET = 20.0
    
    output = []
    output.append("=" * 80)
    output.append(f"  ANALISIS STRATEGI RECOVERY: Modal ${CAPITAL:.0f} -> Target ${TARGET:.0f}")
    output.append(f"  Berdasarkan data 177 transaksi historis")
    output.append(f"  Constraint: Minimum bet Polymarket = ${MIN_BET:.2f}")
    output.append("=" * 80)
    
    strategies = []
    
    # Baseline
    strategies.append(simulate(trades, CAPITAL, 1.10, label="Baseline: Fixed Bet $1.10 (minimum Polymarket)"))
    
    # Opsi 1 - Paling Aman
    tiers_opsi1 = [(0, 1.10), (15, 1.50), (18, 2.00)]
    strategies.append(simulate_tiered(trades, CAPITAL, tiers_opsi1,
        label="OPSI 1 (Paling Aman): Tiered $1.10 -> $1.50 (>$15) -> $2.00 (>$18)"))
    
    # Opsi 2 - Seimbang
    tiers_opsi2 = [(0, 1.10), (13, 1.50), (16, 2.00)]
    strategies.append(simulate_tiered(trades, CAPITAL, tiers_opsi2,
        label="OPSI 2 (Seimbang): Tiered $1.10 -> $1.50 (>$13) -> $2.00 (>$16)"))
    
    # Opsi 3 - Moderat
    tiers_opsi3 = [(0, 1.30), (14, 1.80), (17, 2.00)]
    strategies.append(simulate_tiered(trades, CAPITAL, tiers_opsi3,
        label="OPSI 3 (Moderat/Agresif): Tiered $1.30 -> $1.80 (>$14) -> $2.00 (>$17)"))

    output.append("\n" + "=" * 80)
    output.append("  TABEL PERBANDINGAN STRATEGI")
    output.append("=" * 80)
    output.append("")
    output.append(f"  {'Strategi':<65} | {'Final':>8} | {'Min Bal':>8} | {'DD':>8} | {'Safety':>7}")
    output.append(f"  {'-'*65}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*7}")
    
    for s in strategies:
        lbl = s['label'][:65]
        if s.get('liquidated'):
            output.append(f"  {lbl:<65} | {'MC!':>8} | {'$0.00':>8} | {'-':>8} | {'0%':>7}")
        else:
            output.append(f"  {lbl:<65} | ${s['final']:>7.2f} | ${s['min']:>7.2f} | ${s['max_dd']:>7.2f} | {(s['min']/s['capital'])*100:>5.1f}%")

    output.append("\n" + "=" * 80)
    output.append("  DETAIL STRATEGI")
    output.append("=" * 80)
    
    for s in strategies:
        output.append(f"\n  {'---' * 24}")
        output.append(f"  {s['label']}")
        output.append(f"  {'---' * 24}")
        output.append(f"    Modal Awal      : ${s['capital']:.2f}")
        output.append(f"    Saldo Akhir     : ${s['final']:.4f}")
        output.append(f"    Profit          : ${s['profit']:.4f} ({s['profit']/s['capital']*100:.1f}%)")
        output.append(f"    Saldo Tertinggi : ${s['peak']:.4f}")
        output.append(f"    Saldo Terendah  : ${s['min']:.4f}")
        output.append(f"    Max Drawdown    : ${s['max_dd']:.4f}")
        
        if s.get('liquidated'):
            output.append(f"    >>> LIQUIDATED <<<")
        else:
            reached = s['final'] >= TARGET
            if reached:
                output.append(f"    >>> TARGET ${TARGET:.0f} TERCAPAI! <<<")
            else:
                remaining = TARGET - s['final']
                output.append(f"    Sisa ke target  : ${remaining:.2f} lagi ke ${TARGET:.0f}")

    output_path = 'c:/Users/aL/Pictures/REALBOTS NO LQ/Analisis_Strategi_Recovery_10_20.txt'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output))
    
    print(f"Analisis berhasil disimpan di: {output_path}")
    for s in strategies:
        print(f"[{'OK' if s['final']>=20 else '--'}] {s['label'][:50]}... Final: ${s['final']:.2f} | Min: ${s['min']:.2f}")

if __name__ == '__main__':
    main()
