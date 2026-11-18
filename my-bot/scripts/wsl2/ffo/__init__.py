"""
FFO (Factor Feature Oracle) — AlphaBench 风格的快速因子评估引擎

提供单命令快速因子评估，无需编写完整回测代码。

用法:
    from ffo import evaluate

    result = evaluate("Rank($close, 20)", start="2024-01-01", end="2026-01-01")
    print(result.report())

    # 或通过 API:
    curl -X POST localhost:8700/ffo/evaluate \
      -d '{"formula": "Rank($close, 20)", "start": "2024-01-01", "end": "2026-01-01"}'

设计原则:
    - 秒级评估: 单个因子 < 1 秒（CSI 500 全量数据）
    - 标准化输出: IC, RankIC, ICIR, 胜率, 年化收益, 换手率
    - 无状态: 每次调用独立评估，无副作用
    - 可扩展: 操作符通过 OPERATOR_REGISTRY 注册
"""

from .evaluator import FactorEvaluator, EvaluationResult
from .reporter import format_report, format_report_json
from .batch import batch_evaluate, batch_evaluate_from_csv

__all__ = [
    "FactorEvaluator",
    "EvaluationResult",
    "format_report",
    "format_report_json",
    "batch_evaluate",
    "batch_evaluate_from_csv",
]

# 便捷函数
def evaluate(
    formula: str,
    start: str = "2024-01-01",
    end: str = None,
    universe: str = "csi500",
    data_dir: str = None,
) -> "EvaluationResult":
    """
    快速评估单个因子公式。

    参数:
        formula:  因子公式，如 "Rank($close, 20)" 或 "($close - Delay($close,5)) / Delay($close,5)"
        start:    回测起始日期 (YYYY-MM-DD)
        end:      回测结束日期 (默认: 今天)
        universe: 股票池 ("csi500" / "csi300" / "all")
        data_dir: Qlib bin 数据目录 (默认: 自动探测)

    返回:
        EvaluationResult 对象，包含 IC/ICIR/胜率/年化收益等指标

    示例:
        >>> r = evaluate("Rank($close, 20)")
        >>> print(f"IC: {r.ic_mean:.4f}, ICIR: {r.ic_ir:.3f}")
    """
    evaluator = FactorEvaluator(data_dir=data_dir)
    return evaluator.evaluate(formula, start, end, universe)