"""
Gate 1 评分卡 — 从 Qlib 回测结果计算 5 维评分

从 FM_trade scripts/qlib/gate1_scorer.py 迁移，增强：
  - 与 PIT 验证结果联动
  - 因子 IC 衰减纳入稳健性评估
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

import math
import numpy as np


def score_gate1(
    backtest_result: dict,
    param_count: int = 0,
    multiple_comparison_count: int = 1,
    pit_safe: bool = True,
    ic_series: Optional[List[float]] = None,
    mining_rounds: int = 1,
) -> dict:
    """
    对回测结果进行 Gate 1 五维评分。

    参数:
        backtest_result: adapter.run_backtest() 的返回结果
        param_count: 策略参数数量
        multiple_comparison_count: 多重比较次数
        pit_safe: 是否通过 PIT 验证

    返回:
        Gate 1 评分卡
    """
    summary = backtest_result.get("summary", {})
    portfolio = backtest_result.get("portfolio") or {}
    meta = backtest_result.get("meta", {})

    if not summary or summary.get("total_trades", 0) == 0:
        return _empty_scorecard(meta, "无交易数据")

    # 1. 样本量 (20%)
    sample_size = _score_sample_size(summary.get("total_trades", 0))

    # 2. 期望值 (25%)
    expectancy = _score_expectancy(
        summary.get("win_rate", 0),
        summary.get("profit_factor", 0),
        summary.get("expectancy", 0),
    )

    # 3. 风控 (25%)
    risk_control = _score_risk_control(
        summary.get("max_drawdown", 0),
        summary.get("sharpe_ratio", 0),
    )

    # 4. 稳健性 (20%)
    robustness = _score_robustness(
        backtest_result,
        portfolio.get("trading_days", 0),
        pit_safe,
    )

    # 5. 执行可行性 (10%)
    execution_feasibility = _score_execution_feasibility(
        summary.get("total_trades", 0),
        portfolio.get("trading_days", 252),
    )

    # 原始分
    raw_score = (
        sample_size * 20 +
        expectancy * 25 +
        risk_control * 25 +
        robustness * 20 +
        execution_feasibility * 10
    )

    # 复杂度罚分
    complexity_penalty = _complexity_penalty(param_count)

    # 多重比较罚分
    mc_penalty = _multiple_comparison_penalty(multiple_comparison_count)

    # PIT 罚分
    pit_penalty = 0 if pit_safe else -20

    adjusted_score = raw_score + complexity_penalty + mc_penalty + pit_penalty

    # 判決
    verdict, verdict_reason = _verdict(adjusted_score, raw_score, pit_safe)

    # 样本外验证
    oos = _check_oos(backtest_result)

    # IC 衰减检测
    ic_decay = _compute_ic_decay(ic_series) if ic_series else {"status": "skipped", "reason": "无 IC 序列"}

    # ICIR bootstrap
    icir_bootstrap = _bootstrap_icir(ic_series) if ic_series else {"status": "skipped", "reason": "无 IC 序列"}

    # Bonferroni 修正
    bonferroni = _bonferroni_correct(0.05, multiple_comparison_count)

    # 挖掘次数折减
    mining_discount = _mining_rounds_discount(
        summary.get("sharpe_ratio", 0), mining_rounds
    )

    # 必答三问
    mandatory_questions = _generate_mandatory_questions(backtest_result)

    return {
        "evaluation_date": datetime.now().strftime("%Y-%m-%d"),
        "symbol": meta.get("symbol", ""),
        "strategy": meta.get("strategy", ""),
        "dimensions": {
            "sample_size": round(sample_size, 2),
            "expectancy": round(expectancy, 2),
            "risk_control": round(risk_control, 2),
            "robustness": round(robustness, 2),
            "execution_feasibility": round(execution_feasibility, 2),
        },
        "raw_score": round(raw_score, 1),
        "complexity_penalty": complexity_penalty,
        "multiple_comparison_penalty": mc_penalty,
        "pit_penalty": pit_penalty,
        "adjusted_score": round(adjusted_score, 1),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "anti_overfitting": {
            "layer_1_oos": oos,
            "layer_2_complexity": {
                "param_count": param_count,
                "penalty": complexity_penalty,
            },
            "layer_3_walk_forward": {
                "status": "skipped",
                "reason": "需要 ≥3 年数据（可选）",
            },
            "layer_4_ic_decay": ic_decay,
            "layer_5_icir_bootstrap": icir_bootstrap,
            "layer_6_multiple_testing": {
                "bonferroni": bonferroni,
                "mining_rounds": mining_rounds,
                "mining_discount": mining_discount,
                "discounted_sharpe": round(
                    max(0, summary.get("sharpe_ratio", 0) * mining_discount), 3
                ),
            },
            "pit_verification": {
                "passed": pit_safe,
                "penalty": pit_penalty,
            },
        },
        "mandatory_questions": mandatory_questions,
    }


def _empty_scorecard(meta: dict, reason: str) -> dict:
    return {
        "evaluation_date": datetime.now().strftime("%Y-%m-%d"),
        "symbol": meta.get("symbol", ""),
        "strategy": meta.get("strategy", ""),
        "dimensions": {"sample_size": 0, "expectancy": 0, "risk_control": 0, "robustness": 0, "execution_feasibility": 0},
        "raw_score": 0, "complexity_penalty": 0, "multiple_comparison_penalty": 0,
        "pit_penalty": -20, "adjusted_score": 0,
        "verdict": "Abandon", "verdict_reason": reason,
        "anti_overfitting": {
            "layer_1_oos": {"status": "skipped", "reason": "无交易"},
            "layer_2_complexity": {"param_count": 0, "penalty": 0},
            "layer_3_walk_forward": {"status": "skipped", "reason": "无交易"},
            "pit_verification": {"passed": False, "penalty": -20},
        },
        "mandatory_questions": [],
    }


def _score_sample_size(n_trades: int) -> float:
    """样本量评分"""
    if n_trades >= 100: return 1.0
    if n_trades >= 50: return 0.8
    if n_trades >= 30: return 0.6
    if n_trades >= 15: return 0.4
    if n_trades >= 5: return 0.2
    return 0.0


def _score_expectancy(win_rate: float, profit_factor: float, expectancy: float) -> float:
    """期望值评分"""
    score = 0.0
    if win_rate >= 50: score += 0.4
    elif win_rate >= 40: score += 0.2
    if profit_factor >= 1.5: score += 0.3
    elif profit_factor >= 1.2: score += 0.15
    if expectancy > 0.5: score += 0.3
    elif expectancy > 0: score += 0.15
    return min(score, 1.0)


def _score_risk_control(max_dd: float, sharpe: float) -> float:
    """风控评分"""
    score = 0.0
    if abs(max_dd) <= 10: score += 0.5
    elif abs(max_dd) <= 20: score += 0.3
    elif abs(max_dd) <= 30: score += 0.15
    if sharpe >= 1.0: score += 0.5
    elif sharpe >= 0.5: score += 0.3
    elif sharpe >= 0: score += 0.1
    return min(score, 1.0)


def _score_robustness(result: dict, n_days: int, pit_safe: bool) -> float:
    """稳健性评分"""
    score = 0.0
    if n_days >= 500: score += 0.4
    elif n_days >= 250: score += 0.2
    summary = result.get("summary", {})
    if summary.get("profit_factor", 0) >= 2.0: score += 0.3
    elif summary.get("profit_factor", 0) >= 1.5: score += 0.15
    if pit_safe: score += 0.3
    return min(score, 1.0)


def _score_execution_feasibility(n_trades: int, n_days: int) -> float:
    """执行可行性评分"""
    if n_days == 0: return 0.0
    turnover_rate = n_trades / n_days
    if turnover_rate < 0.05: return 1.0
    if turnover_rate < 0.1: return 0.8
    if turnover_rate < 0.2: return 0.5
    return 0.3


def _complexity_penalty(param_count) -> int:
    try:
        param_count = int(param_count)
    except (ValueError, TypeError):
        param_count = 0
    if param_count <= 3: return -5
    if param_count <= 6: return -10
    return -15


def _multiple_comparison_penalty(n: int) -> float:
    if n <= 1: return 0.0
    if n <= 3: return -2.0
    if n <= 5: return -5.0
    return -10.0


def _verdict(score: float, raw_score: float, pit_safe: bool) -> Tuple[str, str]:
    if not pit_safe:
        return "Refine", f"PIT 验证未通过，策略需修复未来函数 (调整后得分 {score:.0f})"
    if score >= 70:
        return "Deploy", f"策略达标 (调整后得分 {score:.0f})"
    elif score >= 40:
        return "Refine", f"策略需优化 (调整后得分 {score:.0f})"
    else:
        return "Abandon", f"策略建议放弃 (调整后得分 {score:.0f})"


def _check_oos(result: dict) -> dict:
    """样本外验证"""
    trades = result.get("trades", [])
    closed = [t for t in trades if t.get("pnl_pct", 0) != 0]

    if len(closed) < 15:
        return {"status": "skipped", "reason": f"交易数不足 ({len(closed)} < 15)"}

    split = int(len(closed) * 0.7)
    dev_trades = closed[:split]
    val_trades = closed[split:]

    dev_wins = sum(1 for t in dev_trades if t["pnl_pct"] > 0)
    dev_rate = dev_wins / len(dev_trades) * 100 if dev_trades else 0
    val_wins = sum(1 for t in val_trades if t["pnl_pct"] > 0)
    val_rate = val_wins / len(val_trades) * 100 if val_trades else 0

    decay = dev_rate - val_rate

    if decay <= 15:
        return {
            "status": "passed",
            "dev_score": round(dev_rate, 1),
            "val_score": round(val_rate, 1),
            "decay": round(decay, 1),
            "reason": f"样本外衰减 {decay:.1f} 分 ≤ 15，通过",
        }
    else:
        return {
            "status": "failed",
            "dev_score": round(dev_rate, 1),
            "val_score": round(val_rate, 1),
            "decay": round(decay, 1),
            "reason": f"样本外衰减 {decay:.1f} 分 > 15，疑似过拟合",
        }


def _generate_mandatory_questions(result: dict) -> List[dict]:
    """生成必答三问"""
    summary = result.get("summary", {})
    portfolio = result.get("portfolio") or {}
    trades = result.get("trades", [])

    questions = []

    # Q1: 最大回撤
    max_dd = summary.get("max_drawdown", 0)
    max_dd_date = portfolio.get("max_drawdown_date", "未知")
    questions.append({
        "question": "1. 最大回撤发生在什么时候？当时的市场状态是什么？（进攻/震荡/防守）",
        "answer": f"最大回撤 {max_dd}%，发生在 {max_dd_date}",
        "answered": max_dd_date is not None,
    })

    # Q2: 连续亏损
    closed = [t for t in trades if t.get("pnl_pct", 0) != 0]
    max_consecutive = 0
    current = 0
    max_single_loss = 0
    for t in closed:
        if t["pnl_pct"] <= 0:
            current += 1
            max_consecutive = max(max_consecutive, current)
            max_single_loss = min(max_single_loss, t["pnl_pct"])
        else:
            current = 0
    questions.append({
        "question": "2. 连续亏损最多几笔？最大单笔亏损是多少？按当前仓位你能不能扛住？",
        "answer": f"连续亏损 {max_consecutive} 笔，最大单笔亏损 {abs(max_single_loss):.2f}%",
        "answered": True,
    })

    # Q3: 市场环境
    questions.append({
        "question": "3. 策略在上涨/下跌/震荡三种环境下的表现差异有多大？（按 market_regime 分别统计）",
        "answer": "Qlib 回测引擎暂不支持 market_regime 分类统计，需结合 Agent 市场判档分析",
        "answered": False,
    })

    return questions


# ─── Phase 2 增强: IC 衰减 / Bootstrap / Bonferroni / 挖掘折减 ───


def _compute_ic_decay(
    ic_series: List[float],
    window: int = 12,
    decay_threshold: float = 0.02,
) -> dict:
    """
    计算 IC 衰减曲线（滚动月度 IC 均值）。

    检测因子预测能力是否随时间衰减。如果早期 IC 显著高于近期 IC，
    说明因子可能已被市场消化或本身是过拟合产物。

    参数:
        ic_series: 月度 IC 序列
        window: 滚动窗口大小（月数，默认 12）
        decay_threshold: 衰减判定阈值（早期 IC - 近期 IC 的差值）

    返回:
        {
            status: "stable" | "decaying" | "skipped",
            early_ic: float,    # 前 12 个月的 IC 均值
            recent_ic: float,   # 最近 12 个月的 IC 均值
            decay_rate: float,  # 衰减率 (early - recent) / early
            trend: [...],       # 滚动 IC 序列
        }
    """
    if not ic_series or len(ic_series) < window * 2:
        return {
            "status": "skipped",
            "reason": f"IC 序列太短 ({len(ic_series)} 个点，需要 ≥{window * 2})",
            "early_ic": None,
            "recent_ic": None,
            "decay_rate": None,
            "trend": [],
        }

    ic_arr = np.array(ic_series, dtype=float)
    early_ic = float(np.mean(ic_arr[:window]))
    recent_ic = float(np.mean(ic_arr[-window:]))

    if abs(early_ic) < 1e-9:
        decay_rate = 0.0
    else:
        decay_rate = round((early_ic - recent_ic) / abs(early_ic), 4)

    is_decaying = early_ic > decay_threshold and recent_ic < decay_threshold

    # 滚动 IC
    rolling = []
    for i in range(len(ic_arr) - window + 1):
        rolling.append(round(float(np.mean(ic_arr[i : i + window])), 6))

    return {
        "status": "decaying" if is_decaying else "stable",
        "early_ic": round(early_ic, 4),
        "recent_ic": round(recent_ic, 4),
        "decay_rate": decay_rate,
        "is_decaying": is_decaying,
        "trend": rolling,
    }


def _bootstrap_icir(
    ic_series: List[float],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
) -> dict:
    """
    用 Bootstrap 方法计算 ICIR 的置信区间。

    标准 ICIR = mean(IC) / std(IC)。Bootstrap 通过重采样估计 ICIR 的分布，
    给出置信区间，判断 IC 是否统计显著。

    参数:
        ic_series: 月度 IC 序列
        n_bootstrap: Bootstrap 重采样次数
        confidence: 置信水平

    返回:
        {
            icir: float,
            ci_lower: float,
            ci_upper: float,
            significant: bool,  # CI 下限 > 0
        }
    """
    if not ic_series or len(ic_series) < 5:
        return {
            "status": "skipped",
            "reason": f"IC 序列太短 ({len(ic_series)} 个点，需要 ≥5)",
            "icir": None,
            "ci_lower": None,
            "ci_upper": None,
            "significant": None,
        }

    ic_arr = np.array(ic_series, dtype=float)

    # 原始 ICIR
    mu = np.mean(ic_arr)
    sigma = np.std(ic_arr, ddof=1)
    icir = float(mu / sigma) if sigma > 1e-9 else 0.0

    # Bootstrap
    rng = np.random.default_rng(42)
    n = len(ic_arr)
    bootstrap_icirs = []
    for _ in range(n_bootstrap):
        sample = rng.choice(ic_arr, size=n, replace=True)
        s_mu = np.mean(sample)
        s_sigma = np.std(sample, ddof=1)
        if s_sigma > 1e-9:
            bootstrap_icirs.append(s_mu / s_sigma)
        else:
            bootstrap_icirs.append(0.0)

    bootstrap_icirs = np.sort(bootstrap_icirs)
    alpha = (1 - confidence) / 2
    ci_lower = float(np.percentile(bootstrap_icirs, alpha * 100))
    ci_upper = float(np.percentile(bootstrap_icirs, (1 - alpha) * 100))

    return {
        "status": "ok",
        "icir": round(icir, 4),
        "ci_lower": round(ci_lower, 4),
        "ci_upper": round(ci_upper, 4),
        "significant": ci_lower > 0,
        "n_bootstrap": n_bootstrap,
        "confidence": confidence,
    }


def _bonferroni_correct(
    alpha: float = 0.05,
    n_comparisons: int = 1,
) -> dict:
    """
    Bonferroni 多重假设检验修正。

    当同时检验多个因子/策略时，随着比较次数增加，假阳性概率上升。
    Bonferroni 修正将显著性水平 α 除以比较次数。

    参数:
        alpha: 原始显著性水平（默认 0.05）
        n_comparisons: 比较次数

    返回:
        {
            original_alpha: float,
            corrected_alpha: float,
            n_comparisons: int,
            holm_critical: float,  # Holm-Bonferroni 最严格临界值
        }
    """
    if n_comparisons <= 1:
        return {
            "original_alpha": alpha,
            "corrected_alpha": alpha,
            "n_comparisons": 1,
            "holm_critical": alpha,
            "significant_after_correction": True,
        }

    corrected = alpha / n_comparisons
    holm_critical = alpha / n_comparisons  # 最严格（第一步）

    return {
        "original_alpha": alpha,
        "corrected_alpha": round(corrected, 6),
        "n_comparisons": n_comparisons,
        "holm_critical": round(holm_critical, 6),
        "significant_after_correction": corrected > 0.001,
    }


def _mining_rounds_discount(
    sharpe: float,
    mining_rounds: int,
) -> float:
    """
    挖掘次数折减 — Harvey et al. (2016) 多重测试折减。

    当同一个因子经过多次迭代优化后，其 Sharpe ratio 可能被高估。
    使用 sqrt(log(1 + N)) 折减因子来惩罚多次挖掘。

    参数:
        sharpe: 原始 Sharpe ratio
        mining_rounds: 挖掘/验证轮数

    返回:
        折减因子（乘以原始 Sharpe 得到折减后的 Sharpe）
    """
    if mining_rounds <= 1:
        return 1.0

    raw = int(mining_rounds)
    discount = 1.0 / math.sqrt(math.log(1 + raw))
    return round(discount, 4)