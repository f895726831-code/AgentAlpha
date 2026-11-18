"""
FFO 批量评估 — 一次评估多个因子公式

输入方式:
  1. Python list: batch_evaluate(["formula1", "formula2", ...])
  2. CSV 文件: batch_evaluate_from_csv("factors.csv")

CSV 格式:
  formula, name (可选), start (可选), end (可选), universe (可选)
  "Rank($close, 20)", "动量因子", "2024-01-01", "2026-01-01", "csi500"
  "($close - Delay($close,5))/Delay($close,5)", "5日收益", , , "csi300"
"""

import csv
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .evaluator import FactorEvaluator, EvaluationResult
from .reporter import format_report_short


def batch_evaluate(
    formulas: List[str],
    start: str = "2024-01-01",
    end: str = None,
    universe: str = "csi500",
    data_dir: str = None,
    verbose: bool = True,
) -> List[EvaluationResult]:
    """
    批量评估多个因子公式。

    参数:
        formulas: 因子公式字符串列表
        start/end/universe: 共享的评估参数
        data_dir: Qlib bin 数据目录
        verbose: 是否打印进度

    返回:
        EvaluationResult 列表
    """
    evaluator = FactorEvaluator(data_dir=data_dir)
    results = []

    n = len(formulas)
    for i, formula in enumerate(formulas):
        if verbose:
            print(f"[{i+1}/{n}] 评估: {formula[:60]}...", end=" ", flush=True)

        result = evaluator.evaluate(formula, start=start, end=end, universe=universe)

        if verbose:
            print(format_report_short(result))

        results.append(result)

    return results


def batch_evaluate_from_csv(
    csv_path: str,
    data_dir: str = None,
    verbose: bool = True,
) -> List[EvaluationResult]:
    """
    从 CSV 文件批量评估因子。

    CSV 列:
        formula (必需): 因子公式
        name (可选): 因子名称
        start (可选): 起始日期 (默认 2024-01-01)
        end (可选): 结束日期
        universe (可选): 股票池 (默认 csi500)

    返回:
        EvaluationResult 列表
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

    # 读取 CSV
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return []

    # 检查必需列
    if "formula" not in rows[0]:
        # 尝试: 第一列可能是 formula
        first_key = list(rows[0].keys())[0]
        rows = [{"formula": r[first_key], **{k: v for k, v in r.items() if k != first_key}} for r in rows]

    evaluator = FactorEvaluator(data_dir=data_dir)
    results = []

    n = len(rows)
    for i, row in enumerate(rows):
        formula = row.get("formula", "").strip()
        if not formula:
            continue

        name = row.get("name", f"factor_{i}").strip()
        start = row.get("start", "").strip() or "2024-01-01"
        end = row.get("end", "").strip() or None
        universe = row.get("universe", "").strip() or "csi500"

        if verbose:
            print(f"[{i+1}/{n}] {name}: {formula[:50]}...", end=" ", flush=True)

        result = evaluator.evaluate(formula, start=start, end=end, universe=universe)
        result.formula = f"{name}: {formula}"  # 添加名称前缀

        if verbose:
            print(format_report_short(result))

        results.append(result)

    return results


def results_to_dataframe(results: List[EvaluationResult]) -> pd.DataFrame:
    """将评估结果列表转为 DataFrame"""
    rows = []
    for r in results:
        row = {
            "formula": r.formula,
            "universe": r.universe,
            "n_days": r.n_days,
            "n_stocks": r.n_stocks,
            "ic_mean": r.ic_mean,
            "ic_std": r.ic_std,
            "ic_ir": r.ic_ir,
            "ic_pos_rate": r.ic_positive_rate,
            "rank_ic_mean": r.rank_ic_mean,
            "rank_ic_ir": r.rank_ic_ir,
            "long_return": r.long_annual_return,
            "short_return": r.short_annual_return,
            "ls_spread": r.long_short_spread,
            "avg_turnover": r.avg_turnover,
            "error": r.error or "",
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    # 按 abs(IC) 降序排列
    if not df.empty:
        df["abs_ic"] = df["ic_mean"].abs()
        df = df.sort_values("abs_ic", ascending=False).drop(columns=["abs_ic"])
    return df


def results_to_csv(results: List[EvaluationResult], output_path: str):
    """将评估结果保存为 CSV"""
    df = results_to_dataframe(results)
    df.to_csv(output_path, index=False, encoding="utf-8")
    return output_path


def filter_results(
    results: List[EvaluationResult],
    min_abs_ic: float = 0.02,
    min_ic_ir: float = 0.3,
    max_turnover: float = 1.0,
) -> List[EvaluationResult]:
    """筛选满足条件的评估结果"""
    filtered = []
    for r in results:
        if r.error:
            continue
        if abs(r.ic_mean) < min_abs_ic:
            continue
        if abs(r.ic_ir) < min_ic_ir:
            continue
        if r.avg_turnover > max_turnover:
            continue
        filtered.append(r)
    return filtered