"""
FFO 因子评估器 — 计算 IC/ICIR/胜率/年化收益/换手率等评估指标

评估流程:
  1. 解析公式 → 计算因子值 (via FactorComputer)
  2. 对齐因子值与未来收益
  3. 计算日频 IC (Pearson + Spearman Rank IC)
  4. 聚合为汇总指标
  5. 返回 EvaluationResult
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from .parser import parse as parse_formula
from .data_loader import load_qlib_data, get_returns_matrix, get_field_matrix
from .computer import FactorComputer


@dataclass
class EvaluationResult:
    """因子评估结果"""

    formula: str
    universe: str
    start_date: str
    end_date: str
    n_days: int = 0
    n_stocks: int = 0

    # IC 指标
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    ic_positive_rate: float = 0.0  # IC > 0 的日占比
    ic_significant_rate: float = 0.0  # |IC| > 2*std 的日占比

    # Rank IC 指标 (Spearman)
    rank_ic_mean: float = 0.0
    rank_ic_std: float = 0.0
    rank_ic_ir: float = 0.0
    rank_ic_positive_rate: float = 0.0

    # 分组收益
    long_annual_return: float = 0.0   # Top 20% 年化收益
    short_annual_return: float = 0.0  # Bottom 20% 年化收益
    long_short_spread: float = 0.0    # Top - Bottom 年化差

    # 换手率
    avg_turnover: float = 0.0  # 日均换手率 (Top 20% 持仓变化比例)
    avg_correlation: float = 0.0  # 与 1 日反转因子的平均相关性

    # 元数据
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """转为字典"""
        return {
            "formula": self.formula,
            "universe": self.universe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "n_days": self.n_days,
            "n_stocks": self.n_stocks,
            "ic": {
                "mean": round(self.ic_mean, 6),
                "std": round(self.ic_std, 6),
                "ir": round(self.ic_ir, 4),
                "positive_rate": round(self.ic_positive_rate, 4),
                "significant_rate": round(self.ic_significant_rate, 4),
            },
            "rank_ic": {
                "mean": round(self.rank_ic_mean, 6),
                "std": round(self.rank_ic_std, 6),
                "ir": round(self.rank_ic_ir, 4),
                "positive_rate": round(self.rank_ic_positive_rate, 4),
            },
            "long_short": {
                "long_annual_return": round(self.long_annual_return, 4),
                "short_annual_return": round(self.short_annual_return, 4),
                "spread": round(self.long_short_spread, 4),
            },
            "avg_turnover": round(self.avg_turnover, 4),
            "avg_correlation": round(self.avg_correlation, 4),
            "error": self.error,
            "warnings": self.warnings,
            "evaluated_at": self.evaluated_at,
        }

    def report(self) -> str:
        """生成可读报告"""
        from .reporter import format_report
        return format_report(self)


def _extract_field_refs(ast) -> list:
    """从 AST 中提取所有 FieldRef 名称（小写）"""
    from .parser import FieldRef, BinOp, UnaryOp, FuncCall
    fields = []

    def walk(node):
        if isinstance(node, FieldRef):
            fields.append(node.name.lower())
        elif isinstance(node, BinOp):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, UnaryOp):
            walk(node.operand)
        elif isinstance(node, FuncCall):
            for arg in node.args:
                walk(arg)

    walk(ast)
    return fields


class FactorEvaluator:
    """因子评估器"""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir

    def evaluate(
        self,
        formula: str,
        start: str = "2024-01-01",
        end: str = None,
        universe: str = "csi500",
    ) -> EvaluationResult:
        """
        评估单个因子公式。

        参数:
            formula:  因子公式字符串
            start:    起始日期
            end:      结束日期 (默认: 最近交易日)
            universe: 股票池

        返回:
            EvaluationResult
        """
        result = EvaluationResult(
            formula=formula,
            universe=universe,
            start_date=start,
            end_date=end or datetime.now().strftime("%Y-%m-%d"),
        )

        try:
            # 1. 解析公式
            ast = parse_formula(formula)

            # 2. 从 AST 提取字段引用，确保加载对应数据
            needed_fields = _extract_field_refs(ast)
            # 始终加载基础 OHLCV 字段；returns 不应请求 Qlib（由 data_loader 自动计算）
            base_fields = {"close", "open", "high", "low", "volume", "vwap"}
            all_fields = list(base_fields | set(needed_fields) - {"returns"})
            all_fields_dollar = [f"${f}" for f in all_fields]

            # 加载数据（比评估范围多取一些用于时序操作符的 warm-up）
            data_start = pd.Timestamp(start) - pd.DateOffset(days=365)
            data_start_str = data_start.strftime("%Y-%m-%d")

            data = load_qlib_data(
                data_dir=self.data_dir,
                universe=universe,
                start=data_start_str,
                end=end,
                fields=all_fields_dollar,
            )

            if data.empty:
                result.error = "无数据"
                return result

            # 3. 计算因子值
            computer = FactorComputer(data)
            factor_values = computer.evaluate(ast)

            # 截取评估区间（已经 warm-up 过）
            factor_values = factor_values[factor_values.index >= pd.Timestamp(start)]

            if factor_values.empty or factor_values.dropna(how="all").empty:
                result.error = "因子值全部为 NaN"
                return result

            result.n_days = len(factor_values)
            result.n_stocks = factor_values.shape[1]

            # 4. 计算收益率
            returns = get_returns_matrix(data)
            returns = returns[returns.index >= pd.Timestamp(start)]

            # 对齐: 因子值(t) 预测 收益率(t+1)
            factor_aligned = factor_values.shift(0)  # 当前日期
            forward_returns = returns.shift(-1)       # 下期收益

            # 5. 计算 IC
            self._compute_ic(result, factor_aligned, forward_returns)

            # 6. 计算分组收益
            self._compute_group_returns(result, factor_aligned, forward_returns)

            # 7. 计算换手率
            self._compute_turnover(result, factor_aligned)

        except Exception as e:
            result.error = str(e)
            result.warnings.append(f"评估异常: {type(e).__name__}: {e}")

        return result

    def _compute_ic(
        self,
        result: EvaluationResult,
        factor_values: pd.DataFrame,
        forward_returns: pd.DataFrame,
    ):
        """计算 IC 和 Rank IC"""
        common_dates = factor_values.index.intersection(forward_returns.index)

        ic_daily = []
        rank_ic_daily = []

        for date in common_dates:
            fv = factor_values.loc[date].dropna()
            fr = forward_returns.loc[date].dropna()

            # 交集股票
            common_stocks = fv.index.intersection(fr.index)
            if len(common_stocks) < 30:
                continue

            fv_c = fv.loc[common_stocks]
            fr_c = fr.loc[common_stocks]

            # Pearson IC
            ic = fv_c.corr(fr_c)
            if not np.isnan(ic):
                ic_daily.append(ic)

            # Spearman Rank IC
            rank_ic = fv_c.rank().corr(fr_c.rank())
            if not np.isnan(rank_ic):
                rank_ic_daily.append(rank_ic)

        if ic_daily:
            ic_series = pd.Series(ic_daily)
            result.ic_mean = ic_series.mean()
            result.ic_std = ic_series.std()
            result.ic_ir = result.ic_mean / result.ic_std if result.ic_std > 0 else 0
            result.ic_positive_rate = (ic_series > 0).mean()
            result.ic_significant_rate = (ic_series.abs() > 2 * result.ic_std).mean()

        if rank_ic_daily:
            ric_series = pd.Series(rank_ic_daily)
            result.rank_ic_mean = ric_series.mean()
            result.rank_ic_std = ric_series.std()
            result.rank_ic_ir = result.rank_ic_mean / result.rank_ic_std if result.rank_ic_std > 0 else 0
            result.rank_ic_positive_rate = (ric_series > 0).mean()

    def _compute_group_returns(
        self,
        result: EvaluationResult,
        factor_values: pd.DataFrame,
        forward_returns: pd.DataFrame,
    ):
        """计算 Top/Bottom 20% 分组年化收益"""
        common_dates = factor_values.index.intersection(forward_returns.index)
        trading_days_per_year = 244

        long_rets = []
        short_rets = []

        for date in common_dates:
            fv = factor_values.loc[date].dropna()
            fr = forward_returns.loc[date].dropna()
            common = fv.index.intersection(fr.index)
            if len(common) < 50:
                continue

            fv_c = fv.loc[common]
            fr_c = fr.loc[common]

            n = len(common)
            top_n = max(5, n // 5)
            bot_n = max(5, n // 5)

            top_stocks = fv_c.nlargest(top_n).index
            bot_stocks = fv_c.nsmallest(bot_n).index

            long_rets.append(fr_c.loc[top_stocks].mean())
            short_rets.append(fr_c.loc[bot_stocks].mean())

        if long_rets:
            result.long_annual_return = np.mean(long_rets) * trading_days_per_year
            result.short_annual_return = np.mean(short_rets) * trading_days_per_year
            result.long_short_spread = (np.mean(long_rets) - np.mean(short_rets)) * trading_days_per_year

    def _compute_turnover(
        self,
        result: EvaluationResult,
        factor_values: pd.DataFrame,
    ):
        """计算日均换手率"""
        if len(factor_values) < 2:
            return

        turnovers = []
        for i in range(1, len(factor_values)):
            prev_top = set(factor_values.iloc[i-1].dropna().nlargest(
                max(5, factor_values.shape[1] // 5)).index)
            curr_top = set(factor_values.iloc[i].dropna().nlargest(
                max(5, factor_values.shape[1] // 5)).index)

            if prev_top:
                changed = len(prev_top - curr_top) + len(curr_top - prev_top)
                turnover = changed / (2 * len(prev_top))
                turnovers.append(turnover)

        if turnovers:
            result.avg_turnover = np.mean(turnovers)


# ─── 便捷函数 ───

def evaluate_formula(
    formula: str,
    start: str = "2024-01-01",
    end: str = None,
    universe: str = "csi500",
    data_dir: str = None,
) -> EvaluationResult:
    """便捷函数: 评估因子公式"""
    evaluator = FactorEvaluator(data_dir=data_dir)
    return evaluator.evaluate(formula, start, end, universe)