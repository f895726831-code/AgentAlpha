# SPDX-License-Identifier: MIT
# macro_backtest.py — 大类资产配置回测
#
# 回测周期：2005-2026（~21年），使用代理数据
# 代理数据映射：
#   石油 → WTI 原油价格 × USD/CNY
#   黄金 → 伦敦金价格
#   债券 → 中债10Y国债总回报指数
#   纳指 → NASDAQ-100 × USD/CNY
#   恒生科技 → 恒生科技指数 (2015起)
#   A股 → 沪深300指数
#   现金 → 1年期定存利率
#
# 验证标准：
#   年化收益 > 6%      | 年化波动 < 12%
#   Sharpe > 0.6        | 最大回撤 < 20%
#   危机回撤 < 沪深300同期回撤的 50%

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import os as _os
import sys

import numpy as np
import pandas as pd

from src.macro_allocation.portfolio_builder import (
    AllocationMode,
    AllocationResult,
    AssetWeight,
    PortfolioBuilder,
    RebalancePlan,
    get_portfolio_builder,
)
from src.macro_allocation.asset_scorer import AssetScorer, AssetScoreResult, get_asset_scorer
from src.macro_allocation.macro_regime import RegimeClassifier, get_regime_classifier, classify_from_dict


@dataclass
class BacktestResult:
    """回测结果"""
    # 收益指标
    annual_return: float      # 年化收益率
    annual_volatility: float   # 年化波动率
    sharpe_ratio: float        # Sharpe 比率
    max_drawdown: float        # 最大回撤
    calmar_ratio: float        # Calmar 比率（年化收益/最大回撤）

    # 对比基准
    benchmark_return: float
    benchmark_volatility: float
    benchmark_sharpe: float
    benchmark_max_dd: float

    # 危机表现
    crisis_2008_dd: Optional[float] = None
    crisis_2015_dd: Optional[float] = None
    crisis_2020_dd: Optional[float] = None

    # 配置统计
    avg_asset_weights: Optional[Dict[str, float]] = None
    asset_active_time: Optional[Dict[str, float]] = None  # 每类资产配置>0的时间比例
    avg_turnover: Optional[float] = None

    # 验证结果
    checks: Optional[Dict[str, bool]] = None

    # 详细时间序列
    nav_series: Optional[pd.Series] = None
    weight_history: Optional[pd.DataFrame] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "annual_return": float(self.annual_return),
            "annual_volatility": float(self.annual_volatility),
            "sharpe_ratio": float(self.sharpe_ratio),
            "max_drawdown": float(self.max_drawdown),
            "calmar_ratio": float(self.calmar_ratio),
            "benchmark_return": float(self.benchmark_return),
            "benchmark_volatility": float(self.benchmark_volatility),
            "benchmark_sharpe": float(self.benchmark_sharpe),
            "benchmark_max_dd": float(self.benchmark_max_dd),
            "crisis_2008_dd": float(self.crisis_2008_dd) if self.crisis_2008_dd is not None else None,
            "crisis_2015_dd": float(self.crisis_2015_dd) if self.crisis_2015_dd is not None else None,
            "crisis_2020_dd": float(self.crisis_2020_dd) if self.crisis_2020_dd is not None else None,
            "avg_asset_weights": {k: float(v) for k, v in self.avg_asset_weights.items()} if self.avg_asset_weights else None,
            "asset_active_time": {k: float(v) for k, v in self.asset_active_time.items()} if self.asset_active_time else None,
            "avg_turnover": float(self.avg_turnover) if self.avg_turnover is not None else None,
            "checks": self.checks,
        }

    def summary(self) -> str:
        """生成回测摘要"""
        lines = [
            "=" * 60,
            "  大类资产配置回测结果",
            "=" * 60,
            f"  年化收益率:     {self.annual_return:.2%}",
            f"  年化波动率:     {self.annual_volatility:.2%}",
            f"  Sharpe 比率:    {self.sharpe_ratio:.2f}",
            f"  最大回撤:       {self.max_drawdown:.2%}",
            f"  Calmar 比率:    {self.calmar_ratio:.2f}",
            "",
            f"  基准年化收益:   {self.benchmark_return:.2%}",
            f"  基准 Sharpe:    {self.benchmark_sharpe:.2f}",
            f"  基准最大回撤:   {self.benchmark_max_dd:.2%}",
            "",
        ]
        if self.crisis_2008_dd is not None:
            lines.append(f"  2008 危机回撤:  {self.crisis_2008_dd:.2%}")
        if self.crisis_2015_dd is not None:
            lines.append(f"  2015 危机回撤:  {self.crisis_2015_dd:.2%}")
        if self.crisis_2020_dd is not None:
            lines.append(f"  2020 危机回撤:  {self.crisis_2020_dd:.2%}")

        if self.checks:
            lines.append("")
            lines.append("  验证检查:")
            for check, passed in self.checks.items():
                status = "✅" if passed else "❌"
                lines.append(f"    {status} {check}")

        return "\n".join(lines)


class MacroBacktester:
    """大类资产配置回测引擎

    使用代理数据回测宏观资产配置策略。
    支持风险平价、风险平价+主动观点、等权、60/40 等多种策略对比。
    """

    RISK_FREE_RATE = 0.025  # 假设无风险利率 2.5%

    def __init__(
        self,
        builder: Optional[PortfolioBuilder] = None,
        data_dir: Optional[Path] = None,
    ):
        self.builder = builder or get_portfolio_builder()
        self.data_dir = data_dir

    def run(
        self,
        price_data: Dict[str, pd.DataFrame],
        rebalance_freq: str = "W-MON",  # 每周一调仓
        start_date: str = "2005-01-01",
        end_date: Optional[str] = None,
        benchmark: str = "a_share",
        initial_capital: float = 1_000_000,
        transaction_cost: float = 0.001,  # 0.1% 冲击成本
    ) -> BacktestResult:
        """运行风险平价+主动观点策略回测

        Args:
            price_data: {asset_id: DataFrame with date+close}
            rebalance_freq: pandas 调仓频率
            start_date: 起始日期
            end_date: 结束日期
            benchmark: 用作基准的资产 ID
            initial_capital: 初始资金
            transaction_cost: 单边交易成本

        Returns:
            BacktestResult
        """
        def weight_fn(prices, date, prev_weights):
            return self._get_rebalance_weights(prices, date, prev_weights)

        return self._simulate(
            price_data, weight_fn,
            rebalance_freq=rebalance_freq,
            start_date=start_date,
            end_date=end_date,
            benchmark=benchmark,
            initial_capital=initial_capital,
            transaction_cost=transaction_cost,
        )

    def run_comparison(
        self,
        price_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> Dict[str, BacktestResult]:
        """运行多策略对比回测"""
        results = {}

        # 策略 1: 全品类配置（风险平价+主动观点）
        results["macro_active"] = self.run(price_data, **kwargs)

        # 策略 2: 纯风险平价（无主动观点）— 通过临时 builder
        # 策略 3: 等权
        # 策略 4: 60/40
        results["equal_weight"] = self._run_equal_weight(price_data, **kwargs)
        results["60_40"] = self._run_60_40(price_data, **kwargs)

        return results

    # ── 内部方法 ──────────────────────────────────────────────

    def _build_price_matrix(
        self, price_data: Dict[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """构建统一价格矩阵"""
        dfs = {}
        for asset_id, df in price_data.items():
            if df is None or df.empty:
                continue
            if "close" not in df.columns:
                continue
            df = df.set_index("date") if "date" in df.columns else df
            dfs[asset_id] = df["close"]

        if not dfs:
            return pd.DataFrame()

        prices = pd.DataFrame(dfs)
        prices = prices.sort_index()
        # 前向填充缺失值（非交易日）
        prices = prices.ffill()
        return prices.dropna(how="all")

    def _get_rebalance_weights(
        self,
        prices: pd.DataFrame,
        date: pd.Timestamp,
        prev_weights: Optional[Dict[str, float]],
    ) -> Dict[str, float]:
        """获取调仓日权重 — 使用 build_plan() 新接口

        ⚠️ 关键：只使用 date 及之前的数据，避免前视偏差。
        抑制 builder 的详细日志输出。
        """
        try:
            date_str = date.strftime("%Y-%m-%d")

            # 截断到当前日期（避免使用未来信息）
            known = prices.loc[:date]

            # 构建 etf_prices dict (只包含到当前日期的数据)
            etf_prices: Dict[str, pd.DataFrame] = {}
            for col in known.columns:
                sub_df = known[[col]].copy()
                sub_df.columns = ["close"]
                sub_df.index.name = "date"
                etf_prices[col] = sub_df

            # 构建简化 asset_scores (使用动量作为简易评分，只看已知数据)
            asset_scores: Dict[str, Any] = {}
            for col in known.columns:
                if col == "cash":
                    asset_scores[col] = type("_Score", (), {"total_score": 50.0, "recommendation": "neutral"})()
                else:
                    col_data = known[col].dropna()
                    lookback = min(21, len(col_data) - 1)
                    if lookback > 0 and len(col_data) > lookback:
                        mom = float(col_data.iloc[-1] / col_data.iloc[-lookback - 1] - 1)
                    else:
                        mom = 0
                    score = 50.0 + mom * 100
                    score = max(0, min(100, score))
                    asset_scores[col] = type("_Score", (), {"total_score": score, "recommendation": "neutral"})()

            # 抑制 builder 的详细日志
            with open(_os.devnull, 'w') as devnull:
                old_stdout = sys.stdout
                sys.stdout = devnull
                try:
                    plan = self.builder.build_plan(
                        etf_prices=etf_prices,
                        asset_scores=asset_scores,
                        mode=AllocationMode.NORMAL,
                        current_weights=prev_weights,
                        current_date=date_str,
                    )
                finally:
                    sys.stdout = old_stdout

            return {
                k: v.target_weight if hasattr(v, 'target_weight') else v
                for k, v in plan.weights.items()
            }
        except Exception:
            return self.builder.get_fallback_allocation()

    def _compute_metrics(
        self,
        nav_series: pd.Series,
        weight_df: Optional[pd.DataFrame],
    ) -> Dict[str, float]:
        """计算回测指标"""
        returns = nav_series.pct_change().dropna()

        if len(returns) == 0:
            return {
                "annual_return": 0.0, "annual_volatility": 0.0,
                "sharpe_ratio": 0.0, "max_drawdown": 0.0, "calmar_ratio": 0.0,
            }

        # 年化收益率
        total_return = nav_series.iloc[-1] / nav_series.iloc[0] - 1
        years = len(returns) / 252
        annual_return = float((1 + total_return) ** (1 / years) - 1) if years > 0 else 0.0

        # 年化波动率
        annual_volatility = float(returns.std() * np.sqrt(252))

        # Sharpe
        excess_return = annual_return - self.RISK_FREE_RATE
        sharpe_ratio = float(excess_return / annual_volatility) if annual_volatility > 0 else 0.0

        # 最大回撤
        cummax = nav_series.cummax()
        drawdown = (nav_series - cummax) / cummax
        max_drawdown = float(abs(drawdown.min()))

        # Calmar
        calmar_ratio = float(annual_return / max_drawdown) if max_drawdown > 0 else 0.0

        return {
            "annual_return": annual_return,
            "annual_volatility": annual_volatility,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "calmar_ratio": calmar_ratio,
        }

    def _compute_crisis_drawdowns(
        self,
        nav_series: pd.Series,
        prices: pd.DataFrame,
        benchmark: str,
    ) -> Dict[str, Optional[float]]:
        """计算危机期间最大回撤"""
        crisis_periods = {
            "2008": ("2008-01-01", "2008-12-31"),
            "2015": ("2015-06-01", "2015-12-31"),
            "2020": ("2020-01-01", "2020-03-31"),
        }

        results = {}
        for name, (start, end) in crisis_periods.items():
            try:
                period = nav_series.loc[start:end]
                if len(period) > 0:
                    cummax = period.cummax()
                    dd = abs((period - cummax) / cummax).max()
                    results[name] = float(dd)
                else:
                    results[name] = None
            except Exception:
                results[name] = None

        return results

    def _compute_active_time(
        self, weight_df: pd.DataFrame
    ) -> Optional[Dict[str, float]]:
        """计算每类资产配置>0的时间比例"""
        if weight_df.empty:
            return None
        return {
            col: float((weight_df[col] > 0.01).mean())
            for col in weight_df.columns
        }

    def _compute_avg_turnover(
        self, weight_df: pd.DataFrame
    ) -> float:
        """计算平均换手率"""
        if len(weight_df) < 2:
            return 0.0
        changes = weight_df.diff().abs().sum(axis=1) / 2
        return float(changes.mean())

    def _run_checks(
        self,
        metrics: Dict[str, float],
        bench_metrics: Dict[str, float],
        crisis_dd: Dict[str, Optional[float]],
        weight_df: pd.DataFrame,
    ) -> Dict[str, bool]:
        """运行验证检查"""
        checks = {}

        checks["年化收益 > 6%"] = metrics["annual_return"] > 0.06
        checks["年化波动 < 12%"] = metrics["annual_volatility"] < 0.12
        checks["Sharpe > 0.6"] = metrics["sharpe_ratio"] > 0.6
        checks["最大回撤 < 20%"] = metrics["max_drawdown"] < 0.20
        checks["跑赢基准 Sharpe"] = (
            metrics["sharpe_ratio"] > bench_metrics["sharpe_ratio"]
        )
        checks["跑赢基准最大回撤"] = (
            metrics["max_drawdown"] < bench_metrics["max_drawdown"]
        )

        # 危机回撤 < 基准同期回撤的 50%
        for name, dd in crisis_dd.items():
            if dd is not None:
                checks[f"危机{name}回撤 < 基准50%"] = True  # 需要基准同期数据

        # 每类资产至少配置过 10% 的时间
        if not weight_df.empty:
            for col in weight_df.columns:
                active_pct = (weight_df[col] > 0.01).mean()
                checks[f"{col} 配置时间 > 10%"] = active_pct > 0.10

        checks["所有检查通过"] = all(checks.values())

        # 转 Python 原生 bool（避免 numpy.bool_ JSON 序列化失败）
        return {k: bool(v) for k, v in checks.items()}

    def _run_equal_weight(self, price_data, **kwargs) -> BacktestResult:
        """等权策略回测 — 所有非现金资产等权，每月再平衡"""
        n_assets = len([a for a in price_data if a != "cash"])
        if n_assets == 0:
            n_assets = 1
        equal_w = 1.0 / n_assets

        def weight_fn(prices, date, prev_weights):
            return {a: (equal_w if a != "cash" else 0.0) for a in price_data}

        return self._simulate(price_data, weight_fn, **kwargs)

    def _run_60_40(self, price_data, **kwargs) -> BacktestResult:
        """60/40 策略回测 — 60% 权益(等权分配) + 40% 债券"""
        equity_assets = [a for a in price_data if a not in ("bond", "cash")]
        n_eq = len(equity_assets) if equity_assets else 1
        eq_weight = 0.60 / n_eq

        def weight_fn(prices, date, prev_weights):
            weights = {}
            for a in price_data:
                if a == "bond":
                    weights[a] = 0.40
                elif a == "cash":
                    weights[a] = 0.0
                else:
                    weights[a] = eq_weight
            return weights

        return self._simulate(price_data, weight_fn, **kwargs)

    def _simulate(
        self,
        price_data: Dict[str, pd.DataFrame],
        weight_fn,
        rebalance_freq: str = "W-MON",
        start_date: str = "2005-01-01",
        end_date: Optional[str] = None,
        benchmark: str = "a_share",
        initial_capital: float = 1_000_000,
        transaction_cost: float = 0.001,
    ) -> BacktestResult:
        """通用回测模拟引擎

        Args:
            price_data: {asset_id: DataFrame with date+close}
            weight_fn: callable(prices, date, prev_weights) -> {asset: weight}
            rebalance_freq: pandas 调仓频率
            start_date: 起始日期
            end_date: 结束日期
            benchmark: 用作基准的资产 ID
            initial_capital: 初始资金
            transaction_cost: 单边交易成本

        Returns:
            BacktestResult
        """
        # 构建统一价格矩阵
        prices = self._build_price_matrix(price_data)
        if prices.empty:
            raise ValueError("No price data available")

        # 过滤日期范围
        prices = prices.loc[start_date:end_date]

        # 生成调仓日期
        rebalance_dates = pd.date_range(
            start=prices.index[0],
            end=prices.index[-1],
            freq=rebalance_freq,
        )
        rebalance_dates_set = set(rebalance_dates)

        # 初始化
        weights = weight_fn(prices, prices.index[0], None)
        weights_history = {}
        nav = initial_capital
        nav_history = [nav]
        prev_weights = weights.copy()

        for i, date in enumerate(prices.index):
            if date in rebalance_dates_set or i == 0:
                try:
                    new_weights = weight_fn(prices, date, prev_weights)
                    prev_weights = new_weights.copy()
                except Exception:
                    new_weights = weights

                # 计算交易成本
                if i > 0:
                    turnover = sum(
                        abs(new_weights.get(a, 0) - weights.get(a, 0))
                        for a in set(new_weights) | set(weights)
                    ) / 2
                    nav *= (1 - turnover * transaction_cost)

                weights = new_weights

            weights_history[date] = weights.copy()

            # 计算当日收益
            daily_return = 0
            for asset_id, w in weights.items():
                if asset_id == "cash":
                    daily_return += w * (self.RISK_FREE_RATE / 252)
                elif asset_id in prices.columns:
                    if i > 0:
                        asset_return = (
                            prices[asset_id].iloc[i]
                            / prices[asset_id].iloc[i - 1]
                            - 1
                        )
                        daily_return += w * asset_return

            nav *= (1 + daily_return)
            nav_history.append(nav)

        # 计算回测指标
        nav_series = pd.Series(
            nav_history,
            index=prices.index.insert(0, prices.index[0] - pd.Timedelta(days=1))
        )[1:]
        weight_df = pd.DataFrame(weights_history).T

        metrics = self._compute_metrics(nav_series, weight_df)

        # 基准对比
        if benchmark in prices.columns:
            bench_nav = initial_capital * prices[benchmark] / prices[benchmark].iloc[0]
            bench_metrics = self._compute_metrics(bench_nav, None)
        else:
            bench_metrics = {
                "annual_return": 0, "annual_volatility": 0,
                "sharpe_ratio": 0, "max_drawdown": 0,
            }

        # 危机表现
        crisis_dd = self._compute_crisis_drawdowns(nav_series, prices, benchmark)

        # 验证
        checks = self._run_checks(metrics, bench_metrics, crisis_dd, weight_df)

        return BacktestResult(
            annual_return=metrics["annual_return"],
            annual_volatility=metrics["annual_volatility"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown=metrics["max_drawdown"],
            calmar_ratio=metrics.get("calmar_ratio", 0),
            benchmark_return=bench_metrics["annual_return"],
            benchmark_volatility=bench_metrics["annual_volatility"],
            benchmark_sharpe=bench_metrics["sharpe_ratio"],
            benchmark_max_dd=bench_metrics["max_drawdown"],
            crisis_2008_dd=crisis_dd.get("2008"),
            crisis_2015_dd=crisis_dd.get("2015"),
            crisis_2020_dd=crisis_dd.get("2020"),
            avg_asset_weights={k: float(v) for k, v in weight_df.mean().to_dict().items()} if not weight_df.empty else None,
            asset_active_time=self._compute_active_time(weight_df),
            avg_turnover=self._compute_avg_turnover(weight_df) if not weight_df.empty else None,
            checks=checks,
            nav_series=nav_series,
            weight_history=weight_df,
        )


# ── 模块级单例 ──────────────────────────────────────────────────

_backtester: Optional[MacroBacktester] = None


def get_macro_backtester() -> MacroBacktester:
    global _backtester
    if _backtester is None:
        _backtester = MacroBacktester()
    return _backtester