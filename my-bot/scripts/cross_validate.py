#!/usr/bin/env python3
"""
跨标的交叉验证脚本

对同一策略在多个标的上执行回测，横向对比策略的普适性。
用于验证策略是否过拟合到单一标的，以及识别策略适用的标的类型。

用法:
  # 手工回测引擎（默认）
  python scripts/cross_validate.py --strategy breakout --symbols 002475.SZ,002624.SZ,688981.SH

  # Qlib 引擎（Phase 3）
  python scripts/cross_validate.py --strategy breakout_60 --symbols sh600519,sz000858 --engine qlib

输出: JSON 格式的跨标的对比结果，包含每个标的的指标和汇总统计。
"""

import json, sys, os, argparse, subprocess
from pathlib import Path
from datetime import datetime

# ─── 策略定义库 ───

STRATEGIES = {
    "breakout_60": {
        "name": "放量突破60元回踩",
        "description": "股价从60元下方放量突破60元，回踩不破60元后入场",
        "params": {
            "breakout_price": 60.0,
            "volume_threshold": 1.5,  # 突破日成交量为5日均量的1.5倍
            "pullback_days": 3,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 12.0,
        },
    },
    "pullback_low": {
        "name": "回调低吸",
        "description": "上升趋势中回调至MA20附近，缩量企稳后入场",
        "params": {
            "ma_period": 20,
            "pullback_pct": 0.05,  # 回调至MA20下方5%以内
            "volume_shrink_pct": 0.7,  # 缩量至5日均量的70%
            "stop_loss_pct": 5.0,
            "take_profit_pct": 10.0,
        },
    },
    "vcp_breakout": {
        "name": "VCP收缩突破",
        "description": "波动收缩形态末端放量突破",
        "params": {
            "vcp_periods": 3,
            "contraction_ratio": 0.5,  # 每次收缩幅度为前一次的50%
            "breakout_volume_ratio": 1.5,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 15.0,
        },
    },
}

CROSS_VALIDATION_SCHEMA = {
    "meta": {
        "strategy": "",
        "symbols": [],
        "date": "",
        "version": "1.0",
    },
    "results": [],  # per-symbol results
    "summary": {
        "total_symbols": 0,
        "deploy_count": 0,
        "refine_count": 0,
        "abandon_count": 0,
        "avg_win_rate": 0.0,
        "avg_profit_factor": 0.0,
        "avg_expectancy": 0.0,
        "cross_symbol_consistency": "",  # HIGH / MEDIUM / LOW
    },
}


def load_kline_data(filepath):
    """Load K-line data from Wind JSON export file."""
    try:
        data = json.loads(Path(filepath).read_text(encoding='utf-8'))
        # Handle Wind MCP response format
        if 'content' in data:
            content = json.loads(data['content'][0]['text'])
            rows = content['data']['rows']
        elif 'data' in data and 'rows' in data['data']:
            rows = data['data']['rows']
        else:
            raise ValueError(f"Unknown data format in {filepath}")

        records = []
        for row in rows:
            records.append({
                'date': row[0][:10],
                'open': float(row[1]),
                'close': float(row[2]),
                'high': float(row[3]),
                'low': float(row[4]),
                'volume': float(row[6]) if len(row) > 6 else 0,
            })
        return records
    except Exception as e:
        return None


def run_breakout_60(records, params):
    """放量突破60元回踩策略"""
    signals = []
    trades = []
    i = 30

    while i < len(records) - params.get('pullback_days', 3):
        r = records[i]
        close = r['close']
        vol = r['volume']

        # Check if price was below 60 recently
        past_30_closes = [records[j]['close'] for j in range(max(0, i-30), i)]
        if close <= params['breakout_price'] or min(past_30_closes, default=999) >= params['breakout_price']:
            i += 1
            continue

        # Volume confirmation
        past_5_vols = [records[j]['volume'] for j in range(max(0, i-5), i)]
        avg_vol = sum(past_5_vols) / len(past_5_vols) if past_5_vols else vol
        if vol < avg_vol * params['volume_threshold']:
            i += 1
            continue

        # Entry signal
        entry_price = close
        entry_date = r['date']
        signals.append({'date': entry_date, 'price': entry_price, 'type': 'BUY'})

        # Simulate trade
        stop_loss = entry_price * (1 - params['stop_loss_pct'] / 100)
        take_profit = entry_price * (1 + params['take_profit_pct'] / 100)

        trade = {'entry_date': entry_date, 'entry_price': entry_price, 'exit_date': None, 'exit_price': None, 'pnl_pct': 0}
        for j in range(i + 1, min(i + 60, len(records))):
            future = records[j]
            if future['low'] <= stop_loss:
                trade['exit_date'] = future['date']
                trade['exit_price'] = stop_loss
                trade['pnl_pct'] = -params['stop_loss_pct']
                trade['exit_reason'] = 'stop_loss'
                break
            elif future['high'] >= take_profit:
                trade['exit_date'] = future['date']
                trade['exit_price'] = take_profit
                trade['pnl_pct'] = params['take_profit_pct']
                trade['exit_reason'] = 'take_profit'
                break
        else:
            # Time exit
            last = records[min(i + 60, len(records)) - 1]
            trade['exit_date'] = last['date']
            trade['exit_price'] = last['close']
            trade['pnl_pct'] = (last['close'] - entry_price) / entry_price * 100
            trade['exit_reason'] = 'time_exit'

        trades.append(trade)
        i += 10  # Skip ahead to avoid overlapping signals

    return signals, trades


def run_pullback_low(records, params):
    """回调低吸策略"""
    signals = []
    trades = []
    i = 30

    while i < len(records) - 5:
        r = records[i]
        close = r['close']
        vol = r['volume']

        # Calculate MA
        ma_period = params['ma_period']
        if i < ma_period:
            i += 1
            continue

        ma = sum(records[j]['close'] for j in range(i - ma_period, i)) / ma_period

        # Check: price near MA (within pullback_pct below)
        if close > ma or close < ma * (1 - params['pullback_pct']):
            i += 1
            continue

        # Check: volume shrinking
        past_5_vols = [records[j]['volume'] for j in range(max(0, i-5), i)]
        avg_vol = sum(past_5_vols) / len(past_5_vols) if past_5_vols else vol
        if vol > avg_vol * params['volume_shrink_pct']:
            i += 1
            continue

        # Entry
        entry_price = close
        entry_date = r['date']
        signals.append({'date': entry_date, 'price': entry_price, 'type': 'BUY'})

        stop_loss = entry_price * (1 - params['stop_loss_pct'] / 100)
        take_profit = entry_price * (1 + params['take_profit_pct'] / 100)

        trade = {'entry_date': entry_date, 'entry_price': entry_price, 'exit_date': None, 'exit_price': None, 'pnl_pct': 0}
        for j in range(i + 1, min(i + 60, len(records))):
            future = records[j]
            if future['low'] <= stop_loss:
                trade['exit_date'] = future['date']
                trade['exit_price'] = stop_loss
                trade['pnl_pct'] = -params['stop_loss_pct']
                trade['exit_reason'] = 'stop_loss'
                break
            elif future['high'] >= take_profit:
                trade['exit_date'] = future['date']
                trade['exit_price'] = take_profit
                trade['pnl_pct'] = params['take_profit_pct']
                trade['exit_reason'] = 'take_profit'
                break
        else:
            last = records[min(i + 60, len(records)) - 1]
            trade['exit_date'] = last['date']
            trade['exit_price'] = last['close']
            trade['pnl_pct'] = (last['close'] - entry_price) / entry_price * 100
            trade['exit_reason'] = 'time_exit'

        trades.append(trade)
        i += 5

    return signals, trades


STRATEGY_RUNNERS = {
    "breakout_60": run_breakout_60,
    "pullback_low": run_pullback_low,
    "vcp_breakout": None,  # TODO: implement
}


def calculate_metrics(trades):
    """Calculate backtest metrics from trade list."""
    if not trades:
        return {
            'total_trades': 0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'expectancy': 0.0,
            'max_drawdown': 0.0,
            'verdict': 'Abandon',
            'verdict_reason': '无交易信号',
        }

    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]

    total = len(trades)
    win_rate = len(wins) / total * 100 if total > 0 else 0

    total_profit = sum(t['pnl_pct'] for t in wins)
    total_loss = abs(sum(t['pnl_pct'] for t in losses))

    profit_factor = total_profit / total_loss if total_loss > 0 else (999 if total_profit > 0 else 0)
    avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    expectancy = (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss

    # Graded verdict (from v4.1 backtester thresholds)
    if total == 0:
        verdict, reason = 'Abandon', '无交易信号'
    elif total >= 30:
        if win_rate >= 50 and profit_factor >= 1.5 and expectancy > 0:
            verdict, reason = 'Deploy', f'{total}笔交易, 胜率{win_rate:.1f}%, PF={profit_factor:.2f}'
        else:
            verdict, reason = 'Refine', f'{total}笔交易但指标不达标'
    elif total >= 10:
        if win_rate >= 45 and profit_factor >= 1.3 and expectancy > 0:
            verdict, reason = 'Deploy', f'{total}笔交易(需人工确认), 胜率{win_rate:.1f}%'
        else:
            verdict, reason = 'Refine', f'{total}笔交易, 待优化'
    elif total >= 5:
        verdict, reason = 'Refine', f'{total}笔交易(小样本), 需人工override'
    else:
        verdict, reason = 'Abandon', f'仅{total}笔交易, 样本不足'

    return {
        'total_trades': total,
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'expectancy': round(expectancy, 2),
        'verdict': verdict,
        'verdict_reason': reason,
    }


def cross_validate(strategy_name, symbols, data_dir):
    """Run cross-symbol validation."""
    strategy = STRATEGIES.get(strategy_name)
    if not strategy:
        print(f"Unknown strategy: {strategy_name}")
        print(f"Available: {list(STRATEGIES.keys())}")
        sys.exit(1)

    runner = STRATEGY_RUNNERS.get(strategy_name)
    if not runner:
        print(f"Strategy {strategy_name} not yet implemented")
        sys.exit(1)

    results = []
    for symbol in symbols:
        # Try to find data file
        clean_code = symbol.replace('.', '_').replace('.SZ', '').replace('.SH', '').replace('.HK', '')
        data_path = Path(data_dir) / f"{clean_code}_kline.json"

        if not data_path.exists():
            # Try alternative paths
            alt_paths = [
                Path(data_dir) / f"{symbol.replace('.', '_')}_kline.json",
                Path(data_dir) / f"{clean_code}.json",
            ]
            found = False
            for p in alt_paths:
                if p.exists():
                    data_path = p
                    found = True
                    break
            if not found:
                results.append({
                    'symbol': symbol,
                    'error': f'Data file not found: {data_path}',
                    'metrics': None,
                })
                continue

        records = load_kline_data(data_path)
        if not records:
            results.append({
                'symbol': symbol,
                'error': f'Failed to load data from {data_path}',
                'metrics': None,
            })
            continue

        signals, trades = runner(records, strategy['params'])
        metrics = calculate_metrics(trades)

        results.append({
            'symbol': symbol,
            'signal_count': len(signals),
            'trade_count': len(trades),
            'metrics': metrics,
            'trades': [{
                'entry': t['entry_date'],
                'exit': t['exit_date'],
                'pnl_pct': round(t['pnl_pct'], 2),
                'reason': t.get('exit_reason', 'unknown'),
            } for t in trades],
        })

    # Summary
    valid_results = [r for r in results if r.get('metrics')]
    if valid_results:
        deploy_count = sum(1 for r in valid_results if r['metrics']['verdict'] == 'Deploy')
        refine_count = sum(1 for r in valid_results if r['metrics']['verdict'] == 'Refine')
        abandon_count = sum(1 for r in valid_results if r['metrics']['verdict'] == 'Abandon')

        avg_wr = sum(r['metrics']['win_rate'] for r in valid_results) / len(valid_results)
        avg_pf = sum(r['metrics']['profit_factor'] for r in valid_results) / len(valid_results)
        avg_exp = sum(r['metrics']['expectancy'] for r in valid_results) / len(valid_results)

        # Consistency: std of expectancy across symbols
        exps = [r['metrics']['expectancy'] for r in valid_results]
        exp_std = (sum((e - avg_exp) ** 2 for e in exps) / len(exps)) ** 0.5 if len(exps) > 1 else 0
        if exp_std < 1.0:
            consistency = 'HIGH'
        elif exp_std < 3.0:
            consistency = 'MEDIUM'
        else:
            consistency = 'LOW'

        summary = {
            'total_symbols': len(symbols),
            'valid_results': len(valid_results),
            'deploy_count': deploy_count,
            'refine_count': refine_count,
            'abandon_count': abandon_count,
            'avg_win_rate': round(avg_wr, 1),
            'avg_profit_factor': round(avg_pf, 2),
            'avg_expectancy': round(avg_exp, 2),
            'expectancy_std': round(exp_std, 2),
            'cross_symbol_consistency': consistency,
            'consistency_interpretation': {
                'HIGH': '策略在各标的间表现一致，普适性好',
                'MEDIUM': '策略在不同标的间有差异，需区分适用标的类型',
                'LOW': '策略高度依赖特定标的，可能过拟合',
            }.get(consistency, ''),
        }
    else:
        summary = {
            'total_symbols': len(symbols),
            'valid_results': 0,
            'error': 'No valid results',
        }

    output = {
        'meta': {
            'strategy': strategy_name,
            'strategy_name': strategy['name'],
            'symbols': symbols,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'version': '1.0',
        },
        'results': results,
        'summary': summary,
    }

    return output


def main():
    parser = argparse.ArgumentParser(description='Cross-symbol backtest validation')
    parser.add_argument('--strategy', help='Strategy name (breakout_60, pullback_low, vcp_breakout)')
    parser.add_argument('--symbols', help='Comma-separated symbol codes')
    parser.add_argument('--data-dir', default='data/kline', help='Directory containing K-line data files')
    parser.add_argument('--output', help='Output JSON file path')
    parser.add_argument('--list-strategies', action='store_true', help='List available strategies')

    # Qlib 引擎 (Phase 3)
    parser.add_argument('--engine', default='manual', choices=['manual', 'qlib'],
                        help='回测引擎: manual (手工, 默认) 或 qlib (Qlib 引擎)')
    parser.add_argument('--start', default='2020-01-01', help='[Qlib] 起始日期')
    parser.add_argument('--end', default=None, help='[Qlib] 结束日期')
    parser.add_argument('--account', type=float, default=1_000_000, help='[Qlib] 初始资金')
    args = parser.parse_args()

    if args.list_strategies:
        for name, s in STRATEGIES.items():
            print(f"  {name}: {s['name']} — {s['description']}")
        return

    if not args.strategy or not args.symbols:
        parser.error("--strategy and --symbols are required (use --list-strategies to see available strategies)")

    symbols = [s.strip() for s in args.symbols.split(',')]

    # Qlib 引擎模式
    if args.engine == 'qlib':
        import subprocess

        end_time = args.end or datetime.now().strftime('%Y-%m-%d')

        # 通过 run_backtest.py 子进程调用，避免 scripts/qlib 包名冲突
        run_script = Path(__file__).resolve().parent / "qlib" / "run_backtest.py"
        cmd = [
            sys.executable, str(run_script),
            "--symbols", args.symbols,
            "--strategy", args.strategy,
            "--start", args.start,
            "--end", end_time,
            "--account", str(args.account),
            "--no-gate1",
        ]
        if args.output:
            cmd.extend(["--output", args.output])

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    # 手工引擎模式（原有逻辑）
    result = cross_validate(args.strategy, symbols, args.data_dir)

    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output_json, encoding='utf-8')
        print(f"Results written to {args.output}")

    print(output_json)


if __name__ == '__main__':
    main()