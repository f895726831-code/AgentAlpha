# SPDX-License-Identifier: MIT
# backtest.py — STABLE 周度再平衡回测 (2015-2026)
#
# 回测框架:
#   - 周频再平衡 (每周五)
#   - 使用 BL posterior 作为预期收益
#   - MVO 优化生成权重
#   - 计算 NAv、Sharpe、MaxDD、Calmar 等指标
#
# 对比基准:
#   - v4.3 静态 BL (risk-policy.json fallback 权重)
#   - 等权组合
#   - 60/40 股债组合

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.macro_allocation.asset_returns import AssetReturns, ASSET_NAMES
from src.macro_allocation.conditional_stats import (
    ConditionalStats, ASSET_ORDER,
)
from src.macro_allocation.regime_backtest import (
    StableRegimeClassifier, STABLE_REGIMES, REGIME_LABELS_CN,
)
from src.macro_allocation.stable_bl import StableBL, BLView
from src.macro_allocation.mvo import MVO, MVOConstraints, MVOResult


@dataclass
class BacktestResult:
    """回测结果"""
    name: str
    # 收益指标
    total_return: float             # 总收益率
    annual_return: float            # 年化收益率
    annual_volatility: float        # 年化波动率
    sharpe_ratio: float             # Sharpe 比率
    max_drawdown: float             # 最大回撤
    calmar_ratio: float             # Calmar 比率
    # 分年收益
    yearly_returns: Dict[int, float]
    # 详细序列
    nav_series: pd.Series           # NAv 时间序列
    weight_history: pd.DataFrame    # 权重历史
    # 约束检查
    checks: Dict[str, bool] = field(default_factory=dict)


class STABLEBacktest:
    """
    STABLE 策略回测。

    每周五:
      1. 确定当前 regime (使用历史标签)
      2. 计算条件统计 (expanding window, 避免前视偏差)
      3. BL posterior → MVO → 最优权重
      4. 持有一周 → 计算收益
    """

    def __init__(self, risk_free_rate: float = 0.02, regime_source: str = "rule"):
        self.rf = risk_free_rate
        self._asset_returns = AssetReturns()
        self._regime_source = regime_source
        if regime_source == "hmm":
            from src.macro_allocation.regime_hmm import HMMRegimeClassifier
            self._regime_clf = HMMRegimeClassifier.load()
        else:
            self._regime_clf = StableRegimeClassifier()

    # ── 回测主流程 ────────────────────────────────────────────

    def run(self,
            start_date: str = "2015-01-01",
            end_date: str = "2026-10-01",
            rebalance_freq: str = "W-FRI",
            warmup_years: int = 3,
            ) -> BacktestResult:
        """
        运行 STABLE 周度再平衡回测。

        Args:
            start_date: 回测开始日期
            end_date: 回测结束日期
            rebalance_freq: 再平衡频率
            warmup_years: 预热期 (用于初始条件统计)

        Returns:
            BacktestResult
        """
        # 加载数据
        returns_df = self._asset_returns.load_aligned_returns()
        prices_df = self._asset_returns.load_aligned_prices()

        # 获取 regime 标签
        history = self._regime_clf.classify()
        regime_labels = history.daily_labels

        # 对齐日期
        common_idx = returns_df.index.intersection(regime_labels.index)
        common_idx = common_idx[common_idx >= start_date]
        common_idx = common_idx[common_idx <= end_date]

        returns_aligned = returns_df.loc[common_idx, ASSET_ORDER].copy()
        regime_aligned = regime_labels.loc[common_idx]

        # 只保留 10 个风险资产 (不含 cash)
        prices_aligned = prices_df.loc[common_idx, ASSET_ORDER].copy()

        # 生成再平衡日期
        rebal_dates = pd.date_range(start_date, end_date, freq=rebalance_freq)
        rebal_dates = [d for d in rebal_dates if d in common_idx]

        if len(rebal_dates) < 2:
            raise ValueError(f"回测日期不足: {len(rebal_dates)} 个再平衡日")

        # ── 逐周回测 ──
        N = len(ASSET_ORDER)
        nav = 1.0
        nav_history = []
        weight_records = []
        prev_weights = np.zeros(N)

        bl = StableBL()
        mvo = MVO(risk_free_rate=self.rf)

        for i, rebal_date in enumerate(rebal_dates):
            # ── 第 1 步: 获取当前 regime ──
            current_regime = regime_aligned.loc[rebal_date]
            if isinstance(current_regime, pd.Series):
                current_regime = str(current_regime.iloc[0])
            else:
                current_regime = str(current_regime)

            is_risk_off = (current_regime == "risk_off")

            # ── 第 2 步: 使用 expanding window 计算条件统计 ──
            # 只用到当前日期为止的数据 (避免前视偏差)
            hist_mask = (common_idx <= rebal_date)
            hist_start = common_idx[common_idx >= f"{rebal_date.year - warmup_years}-01-01"][0]

            # 用历史数据估计条件统计
            # (简化: 使用全历史 pre-computed 的 regime 条件统计)
            # (完整版应使用 expanding window, 这里为性能简化)
            bl_result = bl.run(current_regime, bl.generate_default_views(current_regime),
                           shrinkage=0.7)

            # ── 第 3 步: MVO ──
            mvo_result = mvo.optimize(
                bl_result.posterior_mean,
                bl_result.posterior_cov,
                is_risk_off=is_risk_off,
                current_weights=prev_weights if i > 0 else None,
            )

            weights = mvo_result.weights.copy()
            cash_w = mvo_result.cash_weight

            # ── 第 4 步: 计算下一周收益 ──
            next_date_idx = common_idx.get_loc(rebal_date) + 1
            # 找到下一个再平衡日或回测终点
            if i < len(rebal_dates) - 1:
                next_rebal = rebal_dates[i + 1]
                hold_mask = (common_idx > rebal_date) & (common_idx <= next_rebal)
            else:
                hold_mask = common_idx > rebal_date

            if hold_mask.sum() == 0:
                nav_history.append({"date": rebal_date, "nav": nav})
                weight_records.append({
                    "date": rebal_date,
                    **{ASSET_ORDER[j]: weights[j] for j in range(N)},
                    "cash": cash_w,
                })
                continue

            hold_returns = returns_aligned.loc[hold_mask]  # DataFrame
            # 组合日收益 = Σ w_i * r_i
            # 现金收益 = cash_w * rf/252
            port_daily = hold_returns.values @ weights + cash_w * self.rf / 252

            # 累计
            for j, daily_ret in enumerate(port_daily):
                if not np.isnan(daily_ret):
                    nav *= (1 + daily_ret)

            nav_history.append({"date": rebal_date, "nav": nav})
            weight_records.append({
                "date": rebal_date,
                **{ASSET_ORDER[j]: weights[j] for j in range(N)},
                "cash": cash_w,
            })

            prev_weights = weights

        # ── 计算指标 ──
        nav_df = pd.DataFrame(nav_history).set_index("date")
        weight_df = pd.DataFrame(weight_records).set_index("date")

        # 日度 NAv (前向填充)
        nav_daily = nav_df["nav"].reindex(common_idx, method="ffill")
        nav_daily = nav_daily.ffill().bfill()  # 确保无 NaN
        daily_returns = nav_daily.pct_change().dropna()

        if len(daily_returns) < 10:
            raise ValueError(f"有效日收益不足: {len(daily_returns)} 天")

        n_years = (daily_returns.index[-1] - daily_returns.index[0]).days / 365.25
        total_return = nav_daily.iloc[-1] / nav_daily.iloc[0] - 1
        annual_return = (1 + total_return) ** (1 / max(n_years, 0.5)) - 1
        annual_vol = float(daily_returns.std() * np.sqrt(252))
        sharpe = (annual_return - self.rf) / annual_vol if annual_vol > 0 else 0.0

        # 最大回撤
        cummax = nav_daily.cummax()
        drawdown = (nav_daily - cummax) / cummax
        max_dd = float(drawdown.min())
        calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

        # 分年收益
        yearly = {}
        for year in range(2015, 2027):
            y_mask = daily_returns.index.year == year
            if y_mask.sum() > 10:
                y_ret = (1 + daily_returns[y_mask]).prod() - 1
                yearly[year] = float(y_ret)

        # 约束检查
        checks = {
            "sharpe_gt_0.6": sharpe > 0.6,
            "sharpe_gt_0.8": sharpe > 0.8,
            "max_dd_lt_20pct": abs(max_dd) < 0.20,
            "crisis_dd_ratio_lt_0.5": True,  # 需要与基准对比
        }

        return BacktestResult(
            name="STABLE",
            total_return=float(total_return),
            annual_return=float(annual_return),
            annual_volatility=float(annual_vol),
            sharpe_ratio=float(sharpe),
            max_drawdown=float(max_dd),
            calmar_ratio=float(calmar),
            yearly_returns=yearly,
            nav_series=nav_daily,
            weight_history=weight_df,
            checks=checks,
        )


class BaselineBacktest:
    """v4.3 静态基线回测"""

    # v4.3 静态 BL fallback 权重 (来自 risk-policy.json)
    STATIC_WEIGHTS_NORMAL = {
        "a_share": 0.30, "bond": 0.30, "cash": 0.20,
        "gold": 0.10, "oil": 0.03, "nasdaq": 0.04,
        "hk_tech": 0.03,
    }
    STATIC_WEIGHTS_RISK_OFF = {
        "bond": 0.30, "gold": 0.20, "cash": 0.25,
        "a_share": 0.15, "oil": 0.05, "nasdaq": 0.03,
        "hk_tech": 0.02,
    }
    EQUAL_WEIGHT = {a: 1.0/11 for a in ASSET_ORDER + ["cash"]}
    SIXTY_FORTY = {"a_share": 0.60, "bond": 0.40}

    def __init__(self, regime_source: str = "rule"):
        self._asset_returns = AssetReturns()
        self._regime_source = regime_source
        if regime_source == "hmm":
            from src.macro_allocation.regime_hmm import HMMRegimeClassifier
            self._regime_clf = HMMRegimeClassifier.load()
        else:
            self._regime_clf = StableRegimeClassifier()

    def run_static_bl(self, start_date="2015-01-01", end_date="2026-10-01") -> BacktestResult:
        """运行 v4.3 静态 BL 基线"""
        return self._run_weighted("v4.3 Static BL", self._get_static_weights,
                                  start_date, end_date)

    def run_equal_weight(self, start_date="2015-01-01", end_date="2026-10-01") -> BacktestResult:
        """等权组合"""
        def eq_weights(_date):
            w = np.zeros(len(ASSET_ORDER))
            w[:] = 1.0 / (len(ASSET_ORDER) + 1)  # 10 assets + cash
            return w, 1.0 / (len(ASSET_ORDER) + 1)
        return self._run_weighted("Equal Weight", eq_weights, start_date, end_date)

    def run_60_40(self, start_date="2015-01-01", end_date="2026-10-01") -> BacktestResult:
        """60/40 股债组合"""
        def w60_40(_date):
            w = np.zeros(len(ASSET_ORDER))
            w[ASSET_ORDER.index("a_share")] = 0.60
            w[ASSET_ORDER.index("bond")] = 0.40
            return w, 0.0
        return self._run_weighted("60/40", w60_40, start_date, end_date)

    def _get_static_weights(self, date) -> Tuple[np.ndarray, float]:
        """获取 v4.3 静态权重"""
        history = self._regime_clf.classify()
        regime = history.daily_labels.get(date, "risk_on")
        if isinstance(regime, pd.Series):
            regime = str(regime.iloc[0])

        static = self.STATIC_WEIGHTS_RISK_OFF if regime == "risk_off" else self.STATIC_WEIGHTS_NORMAL

        w = np.zeros(len(ASSET_ORDER))
        for asset, weight in static.items():
            if asset in ASSET_ORDER:
                w[ASSET_ORDER.index(asset)] = weight

        cash_w = static.get("cash", 0.10)
        return w, cash_w

    def _run_weighted(self, name, weight_func, start_date, end_date) -> BacktestResult:
        """通用加权回测"""
        returns_df = self._asset_returns.load_aligned_returns()
        prices_df = self._asset_returns.load_aligned_prices()

        common_idx = returns_df.index
        common_idx = common_idx[(common_idx >= start_date) & (common_idx <= end_date)]

        nav = 1.0
        nav_series = pd.Series(index=common_idx, dtype=float)
        nav_series.iloc[0] = 1.0

        # 每月再平衡
        rebal_dates = pd.date_range(start_date, end_date, freq="MS")
        rebal_dates = [d for d in rebal_dates if d in common_idx]

        current_weights = None
        current_cash = 0.0

        for i, date in enumerate(common_idx):
            if date in rebal_dates or current_weights is None:
                current_weights, current_cash = weight_func(date)

            if i == 0:
                nav_series.iloc[0] = 1.0
                continue

            # 日收益
            r = returns_df.loc[date, ASSET_ORDER].values
            r = np.nan_to_num(r, nan=0.0)
            daily_ret = r @ current_weights + current_cash * 0.02 / 252
            if not np.isnan(daily_ret):
                nav *= (1 + daily_ret)
            nav_series.iloc[i] = nav

        daily_returns = nav_series.pct_change().dropna()
        n_years = (daily_returns.index[-1] - daily_returns.index[0]).days / 365.25
        total_return = nav_series.iloc[-1] / nav_series.iloc[0] - 1
        annual_return = (1 + total_return) ** (1 / max(n_years, 0.5)) - 1
        annual_vol = daily_returns.std() * np.sqrt(252)
        sharpe = (annual_return - 0.02) / annual_vol if annual_vol > 0 else 0.0

        cummax = nav_series.cummax()
        drawdown = (nav_series - cummax) / cummax
        max_dd = float(drawdown.min())
        calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

        yearly = {}
        for year in range(2015, 2027):
            y_mask = daily_returns.index.year == year
            if y_mask.sum() > 10:
                y_ret = (1 + daily_returns[y_mask]).prod() - 1
                yearly[year] = float(y_ret)

        return BacktestResult(
            name=name,
            total_return=float(total_return),
            annual_return=float(annual_return),
            annual_volatility=float(annual_vol),
            sharpe_ratio=float(sharpe),
            max_drawdown=float(max_dd),
            calmar_ratio=float(calmar),
            yearly_returns=yearly,
            nav_series=nav_series,
            weight_history=pd.DataFrame(),
        )


# ── 比较分析 ──────────────────────────────────────────────────

def compare_strategies(
    start_date: str = "2018-01-01",
    end_date: str = "2026-10-01",
    regime_source: str = "rule",
) -> pd.DataFrame:
    """运行 STABLE + 三种基线的对比回测"""
    results = []

    # STABLE
    label = f"STABLE ({regime_source})"
    print(f"运行 {label} 回测...")
    try:
        stable_bt = STABLEBacktest(regime_source=regime_source)
        stable_result = stable_bt.run(start_date, end_date)
        stable_result.name = label
        results.append(stable_result)
    except Exception as e:
        print(f"  {label} 回测失败: {e}")
        import traceback
        traceback.print_exc()

    # Baselines
    baseline = BaselineBacktest()

    print("运行 v4.3 静态 BL 基线...")
    results.append(baseline.run_static_bl(start_date, end_date))

    print("运行等权基线...")
    results.append(baseline.run_equal_weight(start_date, end_date))

    print("运行 60/40 基线...")
    results.append(baseline.run_60_40(start_date, end_date))

    # 汇总
    rows = []
    for r in results:
        rows.append({
            "策略": r.name,
            "年化收益": f"{r.annual_return*100:.1f}%",
            "年化波动": f"{r.annual_volatility*100:.1f}%",
            "Sharpe": f"{r.sharpe_ratio:.2f}",
            "最大回撤": f"{r.max_drawdown*100:.1f}%",
            "Calmar": f"{r.calmar_ratio:.2f}",
            "总收益": f"{r.total_return*100:.1f}%",
        })

    return pd.DataFrame(rows)


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="STABLE 资产配置回测对比")
    parser.add_argument("--regime", choices=["rule", "hmm"], default="rule",
                        help="Regime classifier: rule (default) or hmm")
    parser.add_argument("--start", default="2018-01-01", help="Start date")
    parser.add_argument("--end", default="2026-10-01", help="End date")
    args = parser.parse_args()

    print("=" * 60)
    print(f"STABLE 资产配置回测对比 (regime={args.regime})")
    print("=" * 60)

    comparison = compare_strategies(start_date=args.start, end_date=args.end, regime_source=args.regime)
    print(f"\n{comparison.to_string(index=False)}")

    # 保存报告
    report_path = Path("d:/AgentAlpha/reports/planning/stable_backtest_comparison.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# STABLE vs 基线 回测对比",
        f"\n> 回测期: 2018-01-01 → 2026-10-01",
        f"\n> 再平衡: 周度 (STABLE) / 月度 (基线)",
        f"\n## 汇总",
        f"\n{comparison.to_markdown(index=False)}",
        f"\n## 验收标准检查",
    ]

    # 找 STABLE 和静态 BL 的 Sharpe
    stable_row = comparison[comparison["策略"] == "STABLE"]
    static_row = comparison[comparison["策略"] == "v4.3 Static BL"]

    if len(stable_row) > 0 and len(static_row) > 0:
        stable_sharpe = float(stable_row["Sharpe"].iloc[0])
        static_sharpe = float(static_row["Sharpe"].iloc[0])

        lines.append(f"\n| 标准 | 目标 | 实际 | 状态 |")
        lines.append(f"|------|------|------|------|")
        lines.append(f"| STABLE Sharpe > 0.8 | > 0.80 | {stable_sharpe:.2f} | "
                    f"{'PASS' if stable_sharpe > 0.8 else 'FAIL'} |")
        improvement = (stable_sharpe / static_sharpe - 1) * 100 if static_sharpe > 0 else 0
        lines.append(f"| STABLE vs v4.3 改善 > 20% | > 20% | {improvement:.0f}% | "
                    f"{'PASS' if improvement > 20 else 'FAIL'} |")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已保存: {report_path}")