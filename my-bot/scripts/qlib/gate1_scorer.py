"""
AgentAlpha Gate 1 评分 — 桥接层 (底层: signal-factory)

从 QSF src/backtest/scorer.py 导入 score_gate1，保持 AgentAlpha API 兼容。
"""

import sys
from pathlib import Path
from typing import Dict, Any, Optional

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

from src.backtest.scorer import score_gate1 as _qsf_score_gate1


def load_risk_policy(policy_path: Optional[str] = None) -> dict:
    """
    加载风险策略配置（兼容 AgentAlpha 接口）。
    QSF 不使用 risk-policy.json，返回默认策略。
    """
    return {
        "gate1": {
            "thresholds": {
                "min_sample": 30,
                "min_win_rate": 45.0,
                "min_profit_factor": 1.3,
                "min_expectancy": 0.0,
            },
            "weights": {
                "sample_size": 0.20,
                "expectancy": 0.25,
                "risk_control": 0.25,
                "robustness": 0.20,
                "execution_feasibility": 0.10,
            },
        },
        "anti_overfitting": {
            "oos_enabled": True,
            "train_ratio": 0.7,
            "max_oos_decay": 0.5,
        },
    }


def score_gate1(
    backtest_result: dict,
    risk_policy: Optional[dict] = None,
    strategy_variants: int = 1,
) -> dict:
    """
    Gate 1 评分（兼容 AgentAlpha 接口）。

    适配 QSF 的 score_gate1 函数，将 risk_policy 和 strategy_variants 映射到
    QSF 的参数格式。

    参数:
        backtest_result: Qlib 回测结果字典
        risk_policy: 风险策略配置（QSF 不使用，保留兼容）
        strategy_variants: 策略变体数量（映射到 multiple_comparison_count）

    返回:
        dict with score, verdict, dimensions, mandatory_questions
    """
    summary = backtest_result.get("summary", {})
    meta = backtest_result.get("meta", {})

    param_count = meta.get("param_count", 4)
    multiple_comparison_count = strategy_variants or 1
    pit_safe = meta.get("pit_safe", True)

    return _qsf_score_gate1(
        backtest_result=backtest_result,
        param_count=param_count,
        multiple_comparison_count=multiple_comparison_count,
        pit_safe=pit_safe,
    )