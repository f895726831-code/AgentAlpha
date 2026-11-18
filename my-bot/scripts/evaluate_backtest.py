#!/usr/bin/env python3
"""
回测评估器 — 支持经典指标 + 贝叶斯后验概率 + Qlib 引擎

用法:
  # 经典评估（手工回测）
  python scripts/evaluate_backtest.py --trades data/trades_002475.json

  # 贝叶斯模式（小样本友好）
  python scripts/evaluate_backtest.py --trades data/trades_002475.json --bayesian

  # Qlib 引擎模式（Phase 3）
  python scripts/evaluate_backtest.py --qlib --symbol sh600519 --strategy breakout_60

  # 直接传 JSON 数组
  echo '[{"pnl_pct":12.3},{"pnl_pct":-5.0},{"pnl_pct":8.1}]' | python scripts/evaluate_backtest.py --stdin --bayesian

输出: 评分卡 JSON，包含经典指标、贝叶斯后验概率、分级判決
"""

import json, sys, argparse, math, io
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ─── 贝叶斯计算 ───

def beta_cdf(x, a, b):
    """Beta CDF using regularized incomplete beta function (approximation via integration)."""
    # Use Simpson's rule for numerical integration
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0

    n = 2000
    h = x / n
    result = 0.0

    for i in range(n + 1):
        t = i * h
        if i == 0 or i == n:
            coeff = 1.0
        elif i % 2 == 1:
            coeff = 4.0
        else:
            coeff = 2.0

        if t > 0 and t < 1:
            result += coeff * (t ** (a - 1)) * ((1 - t) ** (b - 1))

    result *= h / 3.0

    # Normalize with Beta function
    beta_ab = math.exp(math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    return result / beta_ab


def normal_cdf(x):
    """Standard normal CDF approximation."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bayesian_win_rate(wins, losses, prior_alpha=2.0, prior_beta=2.0):
    """
    Beta-Binomial conjugate model for win rate.

    Prior: Beta(α, β) — weakly informative prior Beta(2, 2)
    Posterior: Beta(α + wins, β + losses)

    Returns:
      - posterior_mean: E[win_rate | data]
      - posterior_std: SD[win_rate | data]
      - p_above_50: P(win_rate > 0.50 | data)
      - p_above_40: P(win_rate > 0.40 | data)
      - credible_interval: 90% HDI (approximate)
    """
    alpha_post = prior_alpha + wins
    beta_post = prior_beta + losses

    posterior_mean = alpha_post / (alpha_post + beta_post)
    posterior_var = (alpha_post * beta_post) / ((alpha_post + beta_post) ** 2 * (alpha_post + beta_post + 1))
    posterior_std = math.sqrt(posterior_var)

    # P(win_rate > 0.50)
    p_above_50 = 1.0 - beta_cdf(0.50, alpha_post, beta_post)

    # P(win_rate > 0.40)
    p_above_40 = 1.0 - beta_cdf(0.40, alpha_post, beta_post)

    # 90% credible interval (approximate via normal)
    ci_lower = max(0, posterior_mean - 1.645 * posterior_std)
    ci_upper = min(1, posterior_mean + 1.645 * posterior_std)

    return {
        'model': 'Beta-Binomial',
        'prior': f'Beta({prior_alpha}, {prior_beta})',
        'posterior': f'Beta({alpha_post:.1f}, {beta_post:.1f})',
        'posterior_mean': round(posterior_mean, 4),
        'posterior_std': round(posterior_std, 4),
        'p_winrate_above_50': round(p_above_50, 4),
        'p_winrate_above_40': round(p_above_40, 4),
        'credible_interval_90': [round(ci_lower, 4), round(ci_upper, 4)],
        'interpretation': _interpret_win_rate_posterior(p_above_50, p_above_40, posterior_mean),
    }


def _interpret_win_rate_posterior(p_above_50, p_above_40, posterior_mean):
    if p_above_50 > 0.90:
        return '强证据：胜率极可能 > 50%'
    elif p_above_50 > 0.70:
        return '中等证据：胜率可能 > 50%'
    elif p_above_40 > 0.80:
        return '弱证据：胜率可能 > 40%，但 > 50% 不确定'
    elif p_above_40 > 0.50:
        return f'不确定：胜率后验均值 {posterior_mean:.1%}，样本不足'
    else:
        return '证据指向胜率 < 40%，策略可能无效'


def bayesian_expectancy(pnl_pcts, prior_mean=0.0, prior_sd=5.0):
    """
    Normal-Normal conjugate model for expectancy (mean return per trade).

    Prior: N(μ₀, σ₀²) — skeptical prior centered at 0
    Likelihood: N(μ, σ²) with known σ² estimated from data
    Posterior: N(μ_n, σ_n²)

    Returns:
      - posterior_mean: E[expectancy | data]
      - posterior_std: SD[expectancy | data]
      - p_positive: P(expectancy > 0 | data)
      - p_above_2: P(expectancy > 2% | data)
      - credible_interval: 90% HDI
    """
    n = len(pnl_pcts)
    if n == 0:
        return {
            'model': 'Normal-Normal',
            'error': 'No trade data',
        }

    sample_mean = sum(pnl_pcts) / n
    sample_var = sum((x - sample_mean) ** 2 for x in pnl_pcts) / (n - 1) if n > 1 else 100.0
    sample_sd = math.sqrt(sample_var)

    # Prior precision
    prior_precision = 1.0 / (prior_sd ** 2)

    # Likelihood precision (known σ² = sample variance)
    likelihood_precision = n / sample_var

    # Posterior precision = prior_precision + likelihood_precision
    posterior_precision = prior_precision + likelihood_precision
    posterior_std = math.sqrt(1.0 / posterior_precision)

    # Posterior mean = (prior_precision × prior_mean + likelihood_precision × sample_mean) / posterior_precision
    posterior_mean = (prior_precision * prior_mean + likelihood_precision * sample_mean) / posterior_precision

    # P(expectancy > 0)
    p_positive = 1.0 - normal_cdf(-posterior_mean / posterior_std) if posterior_std > 0 else 0.5

    # P(expectancy > 2%)
    p_above_2 = 1.0 - normal_cdf((2.0 - posterior_mean) / posterior_std) if posterior_std > 0 else 0.0

    # 90% credible interval
    ci_lower = posterior_mean - 1.645 * posterior_std
    ci_upper = posterior_mean + 1.645 * posterior_std

    # Shrinkage factor: how much the posterior mean is pulled toward the prior
    shrinkage = prior_precision / posterior_precision

    return {
        'model': 'Normal-Normal',
        'prior': f'N({prior_mean}, {prior_sd}²)',
        'sample_mean': round(sample_mean, 2),
        'sample_sd': round(sample_sd, 2),
        'posterior_mean': round(posterior_mean, 2),
        'posterior_std': round(posterior_std, 2),
        'shrinkage': round(shrinkage, 4),
        'p_positive_expectancy': round(p_positive, 4),
        'p_expectancy_above_2pct': round(p_above_2, 4),
        'credible_interval_90': [round(ci_lower, 2), round(ci_upper, 2)],
        'interpretation': _interpret_expectancy_posterior(p_positive, posterior_mean, n),
    }


def _interpret_expectancy_posterior(p_positive, posterior_mean, n):
    if p_positive > 0.95:
        return f'强证据：期望值 > 0（后验概率 {p_positive:.1%}），策略有效'
    elif p_positive > 0.80:
        return f'中等证据：期望值可能 > 0（后验概率 {p_positive:.1%}），建议积累更多样本'
    elif p_positive > 0.60:
        return f'弱证据：期望值方向不确定（后验概率 {p_positive:.1%}），{n} 笔交易不足以判断'
    elif p_positive > 0.40:
        return f'不确定：数据不足以判断策略方向，需要更多交易记录'
    else:
        return f'证据指向期望值 < 0（后验概率 {1-p_positive:.1%}），策略可能无效'


# ─── 经典指标 ───

def classic_metrics(trades):
    """Calculate classic backtest metrics."""
    if not trades:
        return {'error': 'No trade data'}

    pnl_pcts = [t['pnl_pct'] for t in trades]
    n = len(pnl_pcts)

    wins = [p for p in pnl_pcts if p > 0]
    losses = [p for p in pnl_pcts if p <= 0]

    win_rate = len(wins) / n if n > 0 else 0

    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_profit / total_loss if total_loss > 0 else (999 if total_profit > 0 else 0)

    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    expectancy = (win_rate * avg_win + (1 - win_rate) * avg_loss)

    # Sharpe-like ratio (assuming risk-free = 0)
    mean_return = sum(pnl_pcts) / n
    std_return = math.sqrt(sum((p - mean_return) ** 2 for p in pnl_pcts) / (n - 1)) if n > 1 else 100.0
    sharpe = mean_return / std_return if std_return > 0 else 0.0

    # Max drawdown in trade sequence
    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnl_pcts:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)

    # Consecutive losses
    max_consecutive_losses = 0
    current_streak = 0
    for p in pnl_pcts:
        if p <= 0:
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        else:
            current_streak = 0

    return {
        'total_trades': n,
        'win_count': len(wins),
        'loss_count': len(losses),
        'win_rate': round(win_rate * 100, 1),
        'profit_factor': round(profit_factor, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'expectancy': round(expectancy, 2),
        'sharpe_ratio': round(sharpe, 2),
        'max_drawdown': round(max_dd, 2),
        'max_consecutive_losses': max_consecutive_losses,
        'total_return': round(sum(pnl_pcts), 2),
    }


# ─── 逻辑维度评分 ───

def logical_dimension_score(trades, strategy_name=""):
    """
    Score strategy on 5 logical dimensions (v4.1 backtester methodology).
    Each dimension scored 0-1, total 0-5.
    """
    metrics = classic_metrics(trades) if trades else {}
    n = metrics.get('total_trades', 0)

    dims = {}

    # 1. Sample Size: higher is better
    if n >= 30:
        dims['sample_size'] = 1.0
    elif n >= 10:
        dims['sample_size'] = 0.6 + 0.4 * (n - 10) / 20
    elif n >= 5:
        dims['sample_size'] = 0.3 + 0.3 * (n - 5) / 5
    elif n >= 2:
        dims['sample_size'] = 0.1 + 0.2 * (n - 2) / 3
    else:
        dims['sample_size'] = 0.0

    # 2. Expectancy: positive expectancy is essential
    exp = metrics.get('expectancy', 0)
    if exp > 3:
        dims['expectancy'] = 1.0
    elif exp > 0:
        dims['expectancy'] = 0.5 + 0.5 * min(exp / 3, 1)
    else:
        dims['expectancy'] = max(0.1, 0.5 + 0.5 * max(exp / 3, -1))

    # 3. Risk Management: based on profit factor and max drawdown
    pf = metrics.get('profit_factor', 0)
    if pf >= 2.0:
        dims['risk_management'] = 1.0
    elif pf >= 1.0:
        dims['risk_management'] = 0.5 + 0.5 * (pf - 1.0)
    else:
        dims['risk_management'] = 0.5 * pf

    # 4. Robustness: win rate stability
    wr = metrics.get('win_rate', 0)
    max_cl = metrics.get('max_consecutive_losses', 999)
    if wr >= 50 and max_cl <= 3:
        dims['robustness'] = 1.0
    elif wr >= 40:
        dims['robustness'] = 0.5 + 0.5 * min((wr - 40) / 10, 1) * min(3 / max(max_cl, 1), 1)
    else:
        dims['robustness'] = 0.2 + 0.3 * (wr / 40)

    # 5. Execution Realism: more trades with realistic exits
    if n >= 20:
        dims['execution_realism'] = 1.0
    elif n >= 10:
        dims['execution_realism'] = 0.7 + 0.3 * (n - 10) / 10
    elif n >= 5:
        dims['execution_realism'] = 0.4 + 0.3 * (n - 5) / 5
    else:
        dims['execution_realism'] = 0.2 + 0.2 * (n - 1) / 4

    total = sum(dims.values())

    return {
        'dimensions': {k: round(v, 2) for k, v in dims.items()},
        'total': round(total, 2),
        'max': 5.0,
    }


# ─── 分级判決 ───

def graded_verdict(trades, bayesian_result=None):
    """Generate graded verdict using v4.1 thresholds + optional Bayesian evidence."""
    if not trades:
        return {'verdict': 'Abandon', 'confidence': 'N/A', 'reason': '无交易数据'}

    n = len(trades)
    metrics = classic_metrics(trades)
    logical = logical_dimension_score(trades)

    # Bayesian evidence (if available)
    bayes_evidence = ""
    if bayesian_result and 'win_rate' in bayesian_result:
        p_pos = bayesian_result['expectancy']['p_positive_expectancy']
        if p_pos > 0.90:
            bayes_evidence = f" | 贝叶斯: P(期望值>0)={p_pos:.1%} (强)"
        elif p_pos > 0.70:
            bayes_evidence = f" | 贝叶斯: P(期望值>0)={p_pos:.1%} (中)"
        else:
            bayes_evidence = f" | 贝叶斯: P(期望值>0)={p_pos:.1%} (弱)"

    # Graded thresholds
    if n >= 30:
        if metrics['win_rate'] >= 50 and metrics['profit_factor'] >= 1.5 and metrics['expectancy'] > 0:
            return {'verdict': 'Deploy', 'confidence': 'HIGH', 'reason': f'{n}笔交易, 胜率{metrics["win_rate"]}%, PF={metrics["profit_factor"]}{bayes_evidence}'}
        elif metrics['expectancy'] > 0:
            return {'verdict': 'Refine', 'confidence': 'MEDIUM', 'reason': f'{n}笔交易, 指标不达标: 胜率{metrics["win_rate"]}%, PF={metrics["profit_factor"]}{bayes_evidence}'}
        else:
            return {'verdict': 'Abandon', 'confidence': 'HIGH', 'reason': f'{n}笔交易, 期望值为负{metrics["expectancy"]}%{bayes_evidence}'}

    elif n >= 10:
        if metrics['win_rate'] >= 45 and metrics['profit_factor'] >= 1.3 and metrics['expectancy'] > 0 and logical['total'] >= 3.0:
            return {'verdict': 'Deploy', 'confidence': 'MEDIUM', 'reason': f'{n}笔交易(需人工确认), 逻辑维度{logical["total"]}/5{bayes_evidence}'}
        elif metrics['expectancy'] > 0:
            return {'verdict': 'Refine', 'confidence': 'MEDIUM', 'reason': f'{n}笔交易, 待优化, 逻辑维度{logical["total"]}/5{bayes_evidence}'}
        else:
            return {'verdict': 'Abandon', 'confidence': 'MEDIUM', 'reason': f'{n}笔交易, 期望值为负{bayes_evidence}'}

    elif n >= 5:
        if metrics['expectancy'] > 0 and logical['total'] >= 3.0:
            return {'verdict': 'Refine', 'confidence': 'LOW', 'reason': f'{n}笔交易(小样本), 逻辑维度{logical["total"]}/5, 需人工override{bayes_evidence}'}
        elif metrics['expectancy'] > 0:
            return {'verdict': 'Refine', 'confidence': 'LOW', 'reason': f'{n}笔交易(小样本), 逻辑维度{logical["total"]}/5 不达标{bayes_evidence}'}
        else:
            return {'verdict': 'Abandon', 'confidence': 'MEDIUM', 'reason': f'{n}笔交易, 期望值为负{metrics["expectancy"]}%{bayes_evidence}'}

    elif n >= 2:
        if metrics['expectancy'] > 0 and metrics['profit_factor'] > 1.5 and logical['total'] >= 4.0:
            return {'verdict': 'Refine', 'confidence': 'LOW', 'reason': f'{n}笔交易(极小样本), 人工override + 逻辑维度{logical["total"]}/5{bayes_evidence}'}
        else:
            return {'verdict': 'Abandon', 'confidence': 'HIGH', 'reason': f'仅{n}笔交易, 样本严重不足{bayes_evidence}'}

    else:
        return {'verdict': 'Abandon', 'confidence': 'HIGH', 'reason': '无交易信号或仅1笔交易'}


# ─── Main ───

def evaluate(trades, use_bayesian=False):
    """Full evaluation of backtest trades."""
    result = {
        'evaluation_date': datetime.now().strftime('%Y-%m-%d'),
        'trade_count': len(trades),
        'classic': classic_metrics(trades) if trades else None,
        'logical_dimensions': logical_dimension_score(trades),
    }

    if use_bayesian and trades:
        wins = sum(1 for t in trades if t['pnl_pct'] > 0)
        losses = sum(1 for t in trades if t['pnl_pct'] <= 0)
        pnl_pcts = [t['pnl_pct'] for t in trades]

        result['bayesian'] = {
            'win_rate': bayesian_win_rate(wins, losses),
            'expectancy': bayesian_expectancy(pnl_pcts),
        }

    result['verdict'] = graded_verdict(trades, result.get('bayesian'))
    return result


def main():
    parser = argparse.ArgumentParser(description='Backtest evaluator with Bayesian support')
    parser.add_argument('--trades', help='Path to JSON file containing trade list')
    parser.add_argument('--stdin', action='store_true', help='Read trades JSON from stdin')
    parser.add_argument('--bayesian', action='store_true', help='Enable Bayesian posterior probability calculation')
    parser.add_argument('--output', help='Output JSON file path')

    # Qlib 引擎模式 (Phase 3)
    parser.add_argument('--qlib', action='store_true', help='使用 Qlib 引擎执行回测 (Phase 3)')
    parser.add_argument('--symbol', help='[Qlib 模式] 标的代码 (如 sh600519)')
    parser.add_argument('--strategy', help='[Qlib 模式] 策略名称')
    parser.add_argument('--start', default='2020-01-01', help='[Qlib 模式] 起始日期')
    parser.add_argument('--end', default=None, help='[Qlib 模式] 结束日期')
    parser.add_argument('--account', type=float, default=1_000_000, help='[Qlib 模式] 初始资金')
    parser.add_argument('--benchmark', default='SH000300', help='[Qlib 模式] 基准指数')
    parser.add_argument('--gate1', action='store_true', help='[Qlib 模式] 同时输出 Gate 1 评分卡')
    args = parser.parse_args()

    # Qlib 引擎模式
    if args.qlib:
        import subprocess

        if not args.symbol or not args.strategy:
            parser.error("--qlib 模式需要 --symbol 和 --strategy")

        end_time = args.end or datetime.now().strftime('%Y-%m-%d')

        # 通过 run_backtest.py 子进程调用，避免 scripts/qlib 包名冲突
        run_script = Path(__file__).resolve().parent / "qlib" / "run_backtest.py"
        cmd = [
            sys.executable, str(run_script),
            "--symbol", args.symbol,
            "--strategy", args.strategy,
            "--start", args.start,
            "--end", end_time,
            "--account", str(args.account),
        ]
        if args.benchmark:
            cmd.extend(["--benchmark", args.benchmark])
        if args.output:
            cmd.extend(["--output", args.output])
        if args.gate1:
            cmd.append("--gate1")

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    # Load trades (original logic)
    trades = None
    if args.stdin:
        raw = sys.stdin.read().strip()
        if raw:
            trades = json.loads(raw)
    elif args.trades:
        trades_path = Path(args.trades)
        if not trades_path.exists():
            print(f"Error: file not found: {args.trades}", file=sys.stderr)
            sys.exit(1)
        trades = json.loads(trades_path.read_text(encoding='utf-8'))

    if trades is None:
        print("Error: no trades data provided. Use --trades <file>, --stdin, or --qlib", file=sys.stderr)
        sys.exit(1)

    # Ensure trades is a list of dicts with pnl_pct
    if isinstance(trades, dict):
        # Try to extract trades from common formats
        if 'trades' in trades:
            trades = trades['trades']
        elif 'results' in trades:
            trades = trades['results']
        else:
            print("Error: expected a list of trades or object with 'trades' key", file=sys.stderr)
            sys.exit(1)

    # Validate trade format
    valid_trades = []
    for t in trades:
        if isinstance(t, dict) and 'pnl_pct' in t:
            valid_trades.append(t)
    trades = valid_trades

    result = evaluate(trades, use_bayesian=args.bayesian)
    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output_json, encoding='utf-8')
        print(f"Results written to {args.output}")

    print(output_json)


if __name__ == '__main__':
    main()