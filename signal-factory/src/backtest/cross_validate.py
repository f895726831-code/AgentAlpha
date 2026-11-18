"""
交叉验证模块 — 跨标的 + 跨时间段的策略回测验证

确保策略不是针对单一标的或单一时段过拟合的产物。

验证维度:
  1. 跨标的验证: 同一策略在多个标的上的一致性
  2. 跨时间段验证: 同一策略在不同时间段的表现稳定性
  3. 参数敏感性: 策略参数微调后的表现变化
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def cross_validate(
    symbols: List[str],
    strategy_name: str,
    start_time: str,
    end_time: str,
    min_symbols: int = 3,
    min_win_rate: float = 30.0,
    max_win_rate_std: float = 20.0,
    max_pf_std: float = 1.0,
) -> dict:
    """
    跨标的交叉验证。

    对每个标的独立运行回测，统计策略在不同标的上的一致性。

    参数:
        symbols: 标的列表
        strategy_name: 策略名称
        start_time: 起始日期
        end_time: 结束日期
        min_symbols: 最少有效标的数
        min_win_rate: 最低平均胜率 (%)
        max_win_rate_std: 胜率最大标准差 (%)
        max_pf_std: 盈利因子最大标准差

    返回:
        dict: {
            passed: bool,
            summary: {...},
            per_symbol: [...],
            warnings: [...],
        }
    """
    from src.backtest.adapter import init_qlib, run_backtest
    from src.backtest.signal_generator import get_signal_from_qlib, get_strategy_info

    init_qlib()

    results = []
    warnings = []

    for symbol in symbols:
        try:
            signal = get_signal_from_qlib(
                symbol, strategy_name,
                start_time=start_time, end_time=end_time,
            )
            buy_count = int((signal == 1).sum())
            if buy_count == 0:
                warnings.append(f"{symbol}: 无买入信号")
                continue

            bt = run_backtest(
                symbol, strategy_name, signal,
                start_time=start_time, end_time=end_time,
            )
            summary = bt.get("summary", {})

            results.append({
                "symbol": symbol,
                "trades": summary.get("total_trades", 0),
                "win_rate": summary.get("win_rate", 0),
                "profit_factor": summary.get("profit_factor", 0),
                "sharpe": summary.get("sharpe_ratio", 0),
                "max_dd": summary.get("max_drawdown", 0),
                "expectancy": summary.get("expectancy", 0),
                "verdict": summary.get("verdict", "Abandon"),
            })
        except Exception as e:
            warnings.append(f"{symbol}: 回测失败 - {e}")

    if len(results) < min_symbols:
        return {
            "passed": False,
            "summary": {
                "tested_symbols": len(results),
                "min_required": min_symbols,
                "verdict": "数据不足",
            },
            "per_symbol": results,
            "warnings": warnings + [f"有效标的 {len(results)} < {min_symbols}，无法交叉验证"],
        }

    # 统计指标
    win_rates = [r["win_rate"] for r in results]
    profit_factors = [r["profit_factor"] for r in results]
    sharpes = [r["sharpe"] for r in results]
    expectancies = [r["expectancy"] for r in results]

    avg_win_rate = np.mean(win_rates)
    std_win_rate = np.std(win_rates)
    avg_pf = np.mean(profit_factors)
    std_pf = np.std(profit_factors)
    avg_sharpe = np.mean(sharpes)
    avg_expectancy = np.mean(expectancies)

    deploy_count = sum(1 for r in results if r["verdict"] == "Deploy")
    positive_count = sum(1 for e in expectancies if e > 0)

    # 判断
    checks = {
        "avg_win_rate": avg_win_rate >= min_win_rate,
        "std_win_rate": std_win_rate <= max_win_rate_std,
        "std_pf": std_pf <= max_pf_std,
        "avg_expectancy": avg_expectancy > 0,
    }

    passed = all(checks.values())

    failed_checks = [k for k, v in checks.items() if not v]

    return {
        "passed": passed,
        "summary": {
            "tested_symbols": len(results),
            "deploy_count": deploy_count,
            "positive_expectancy_count": positive_count,
            "avg_win_rate": round(avg_win_rate, 1),
            "std_win_rate": round(std_win_rate, 1),
            "avg_profit_factor": round(avg_pf, 2),
            "std_profit_factor": round(std_pf, 2),
            "avg_sharpe": round(avg_sharpe, 2),
            "avg_expectancy": round(avg_expectancy, 2),
            "checks": checks,
            "verdict": "PASS" if passed else f"FAIL: {', '.join(failed_checks)}",
        },
        "per_symbol": results,
        "warnings": warnings,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }


def time_window_validate(
    symbol: str,
    strategy_name: str,
    start_time: str,
    end_time: str,
    window_years: int = 2,
    min_windows: int = 2,
) -> dict:
    """
    跨时间段交叉验证。

    将完整回测区间切分为多个滚动窗口，验证策略在各窗口的表现稳定性。

    参数:
        symbol: 标的代码
        strategy_name: 策略名称
        start_time: 总起始日期
        end_time: 总结束日期
        window_years: 每个窗口的年数
        min_windows: 最少有效窗口数

    返回:
        dict: {
            passed: bool,
            windows: [...],
            stability_score: float,
        }
    """
    from src.backtest.adapter import init_qlib, run_backtest
    from src.backtest.signal_generator import get_signal_from_qlib

    init_qlib()

    start_dt = pd.Timestamp(start_time)
    end_dt = pd.Timestamp(end_time)
    total_years = (end_dt - start_dt).days / 365.25

    if total_years < window_years * min_windows:
        return {
            "passed": False,
            "windows": [],
            "stability_score": 0.0,
            "error": f"数据区间不足: {total_years:.1f} 年 < {window_years * min_windows} 年",
        }

    window_days = int(window_years * 252)
    windows = []

    current_start = start_dt
    while current_start + pd.Timedelta(days=window_days) <= end_dt:
        window_end = current_start + pd.Timedelta(days=window_days)

        try:
            signal = get_signal_from_qlib(
                symbol, strategy_name,
                start_time=str(current_start),
                end_time=str(window_end),
            )

            if (signal == 1).sum() == 0:
                current_start += pd.Timedelta(days=126)  # 滑动半年
                continue

            bt = run_backtest(
                symbol, strategy_name, signal,
                start_time=str(current_start),
                end_time=str(window_end),
            )
            summary = bt.get("summary", {})

            windows.append({
                "start": str(current_start),
                "end": str(window_end),
                "trades": summary.get("total_trades", 0),
                "win_rate": summary.get("win_rate", 0),
                "profit_factor": summary.get("profit_factor", 0),
                "sharpe": summary.get("sharpe_ratio", 0),
                "expectancy": summary.get("expectancy", 0),
            })
        except Exception:
            pass

        current_start += pd.Timedelta(days=126)  # 滑动半年

    if len(windows) < min_windows:
        return {
            "passed": False,
            "windows": windows,
            "stability_score": 0.0,
            "error": f"有效窗口 {len(windows)} < {min_windows}",
        }

    # 稳定性评分
    win_rates = [w["win_rate"] for w in windows]
    profit_factors = [w["profit_factor"] for w in windows]

    wr_stability = 1.0 - min(np.std(win_rates) / max(abs(np.mean(win_rates)), 1), 1.0) if win_rates else 0
    pf_stability = 1.0 - min(np.std(profit_factors) / max(abs(np.mean(profit_factors)), 0.1), 1.0) if profit_factors else 0

    stability_score = round((wr_stability + pf_stability) / 2 * 100, 1)

    return {
        "passed": stability_score >= 50,
        "windows": windows,
        "stability_score": stability_score,
        "window_count": len(windows),
        "date": datetime.now().strftime("%Y-%m-%d"),
    }


def parameter_sensitivity(
    symbol: str,
    strategy_name: str,
    param_name: str,
    param_values: List[float],
    start_time: str,
    end_time: str,
) -> dict:
    """
    参数敏感性分析。

    对策略的关键参数进行微调，观察回测指标的变化幅度。
    如果参数微小变化导致指标剧烈波动 → 过拟合风险高。

    参数:
        symbol: 标的代码
        strategy_name: 策略名称
        param_name: 要测试的参数名
        param_values: 参数值列表
        start_time: 起始日期
        end_time: 结束日期

    返回:
        dict: {
            sensitivity_score: float,  # 0-100, 越高越稳定
            results: [...],
        }
    """
    from src.backtest.adapter import init_qlib, run_backtest
    from src.backtest.signal_generator import get_signal_from_qlib, get_strategy_info

    init_qlib()

    results = []

    for val in param_values:
        try:
            overrides = {param_name: val}
            signal = get_signal_from_qlib(
                symbol, strategy_name,
                start_time=start_time, end_time=end_time,
                override_params=overrides,
            )

            if (signal == 1).sum() == 0:
                continue

            bt = run_backtest(
                symbol, strategy_name, signal,
                start_time=start_time, end_time=end_time,
            )
            summary = bt.get("summary", {})

            results.append({
                "param_value": val,
                "trades": summary.get("total_trades", 0),
                "win_rate": summary.get("win_rate", 0),
                "profit_factor": summary.get("profit_factor", 0),
                "sharpe": summary.get("sharpe_ratio", 0),
                "expectancy": summary.get("expectancy", 0),
            })
        except Exception:
            continue

    if len(results) < 2:
        return {"sensitivity_score": 0, "results": results, "error": "数据不足"}

    # 计算指标变化幅度
    win_rates = [r["win_rate"] for r in results]
    pfs = [r["profit_factor"] for r in results]

    wr_range = max(win_rates) - min(win_rates)
    pf_range = max(pfs) - min(pfs)

    # 稳定性评分：变化越小越好
    wr_score = max(0, 100 - wr_range * 2)
    pf_score = max(0, 100 - pf_range * 50)

    sensitivity_score = round((wr_score + pf_score) / 2, 1)

    return {
        "sensitivity_score": sensitivity_score,
        "verdict": "Stable" if sensitivity_score >= 60 else "Sensitive" if sensitivity_score >= 30 else "Fragile",
        "param_name": param_name,
        "param_range": [min(param_values), max(param_values)],
        "results": results,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }