# SPDX-License-Identifier: MIT
# conditional_stats.py — Regime 条件下的资产收益分布统计
#
# 计算每个 STABLE regime 下的:
#   - 条件均值 (年化)
#   - 条件协方差矩阵 (年化)
#   - 条件 Sharpe
#   - ANOVA 检验 (regime 间差异显著性)
#
# 输出: 供 stable_bl.py 的 Black-Litterman prior 使用

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from src.macro_allocation.asset_returns import AssetReturns, ASSET_NAMES
from src.macro_allocation.regime_backtest import (
    StableRegimeClassifier,
    STABLE_REGIMES,
    REGIME_LABELS_CN,
)

# ── 资产排序 (固定顺序, 不含 cash) ──
ASSET_ORDER = [
    "oil", "gold", "bond", "nasdaq", "hk_tech",
    "a_share", "star_50", "nikkei", "india", "cb_bond",
]


@dataclass
class RegimeConditionalStats:
    """单个 regime 下的条件统计"""
    regime: str
    regime_label: str
    n_obs: int                          # 观测天数
    mean_daily: np.ndarray              # 日度均值 (10,)
    mean_annual: np.ndarray             # 年化均值 (10,)
    cov_daily: np.ndarray               # 日度协方差 (10, 10)
    cov_annual: np.ndarray              # 年化协方差 (10, 10)
    corr: np.ndarray                    # 相关性矩阵 (10, 10)
    vol_annual: np.ndarray              # 年化波动率 (10,)
    sharpe: np.ndarray                  # 年化 Sharpe (10,)
    assets: List[str] = field(default_factory=lambda: ASSET_ORDER)


@dataclass
class ConditionalStatsResult:
    """全 regime 条件统计结果"""
    stats: Dict[str, RegimeConditionalStats]  # regime → stats
    unconditional_mean: np.ndarray            # 无条件均值
    unconditional_cov: np.ndarray             # 无条件协方差
    anova_results: pd.DataFrame               # ANOVA 检验结果
    regime_labels: pd.Series                  # 日度 regime 标签
    returns: pd.DataFrame                     # 对齐后的收益矩阵
    date_range: Tuple[str, str]


class ConditionalStats:
    """
    Regime 条件统计计算器。

    使用预计算的 regime 标签对齐资产收益，计算每个 regime 的条件分布。
    """

    def __init__(self):
        self._asset_returns = AssetReturns()
        self._regime_clf = StableRegimeClassifier()
        self._result: Optional[ConditionalStatsResult] = None

    def compute(self, min_obs: int = 60) -> ConditionalStatsResult:
        """
        计算所有 regime 的条件统计。

        Args:
            min_obs: 每个 regime 最少观测天数要求

        Returns:
            ConditionalStatsResult
        """
        # 加载数据
        returns_df = self._asset_returns.load_aligned_returns()
        history = self._regime_clf.classify()
        regime_labels = history.daily_labels

        # 对齐日期 — 使用 2020 年后的数据确保足够资产覆盖
        common_idx = returns_df.index.intersection(regime_labels.index)
        # 限制到 2018 年后 (大部分 ETF 已上市)
        common_idx = common_idx[common_idx >= "2018-01-01"]
        returns_aligned = returns_df.loc[common_idx, ASSET_ORDER].copy()
        regime_aligned = regime_labels.loc[common_idx]

        # ── 无条件统计 (使用成对观测) ──
        unconditional_mean = returns_aligned.mean().values * 252
        unconditional_cov = returns_aligned.cov(min_periods=60).values * 252
        # 修复 NaN
        unconditional_mean = np.nan_to_num(unconditional_mean, nan=0.0)
        unconditional_cov = np.nan_to_num(unconditional_cov, nan=0.0)
        # 确保对角线为正
        for i in range(len(unconditional_cov)):
            if unconditional_cov[i, i] <= 0:
                unconditional_cov[i, i] = 0.01  # 10% vol²

        # ── 条件统计 ──
        stats: Dict[str, RegimeConditionalStats] = {}
        for regime in STABLE_REGIMES:
            mask = regime_aligned == regime
            n_obs = mask.sum()

            if n_obs < min_obs:
                print(f"  [WARN] {regime}: 仅 {n_obs} 观测, 跳过 (< {min_obs})")
                continue

            r_df = returns_aligned.loc[mask]  # DataFrame 保留列名

            # 使用成对观测计算 (允许部分 NaN)
            mean_daily = r_df.mean().values
            cov_daily = r_df.cov(min_periods=30).values

            # 修复 NaN: 用无条件统计填充
            mean_daily = np.where(np.isnan(mean_daily), unconditional_mean / 252, mean_daily)
            cov_daily = np.where(np.isnan(cov_daily), unconditional_cov / 252, cov_daily)

            # 确保对角线为正
            for i in range(len(cov_daily)):
                if cov_daily[i, i] <= 0:
                    cov_daily[i, i] = unconditional_cov[i, i] / 252 if unconditional_cov[i, i] > 0 else 1e-4

            mean_annual = mean_daily * 252
            cov_annual = cov_daily * 252
            vol_annual = np.sqrt(np.maximum(np.diag(cov_annual), 1e-8))

            # Sharpe ratio
            sharpe = mean_annual / np.maximum(vol_annual, 1e-8)

            # Correlation
            std = np.sqrt(np.maximum(np.diag(cov_daily), 1e-16))
            corr = cov_daily / np.outer(std, std)
            np.fill_diagonal(corr, 1.0)  # 确保对角线为 1

            stats[regime] = RegimeConditionalStats(
                regime=regime,
                regime_label=REGIME_LABELS_CN.get(regime, regime),
                n_obs=n_obs,
                mean_daily=mean_daily,
                mean_annual=mean_annual,
                cov_daily=cov_daily,
                cov_annual=cov_annual,
                corr=corr,
                vol_annual=vol_annual,
                sharpe=sharpe,
            )

        # ── ANOVA 检验 ──
        anova_rows = []
        for i, asset in enumerate(ASSET_ORDER):
            groups = []
            for regime in STABLE_REGIMES:
                if regime in stats:
                    mask = regime_aligned == regime
                    groups.append(returns_aligned.loc[mask, asset].dropna().values)

            if len(groups) >= 2:
                f_stat, p_value = scipy_stats.f_oneway(*groups)
                anova_rows.append({
                    "asset": asset,
                    "asset_name": ASSET_NAMES.get(asset, asset),
                    "f_statistic": f_stat,
                    "p_value": p_value,
                    "significant_5pct": p_value < 0.05,
                    "significant_1pct": p_value < 0.01,
                })

        anova_df = pd.DataFrame(anova_rows).set_index("asset")

        self._result = ConditionalStatsResult(
            stats=stats,
            unconditional_mean=unconditional_mean,
            unconditional_cov=unconditional_cov,
            anova_results=anova_df,
            regime_labels=regime_aligned,
            returns=returns_aligned,
            date_range=(str(common_idx.min().date()), str(common_idx.max().date())),
        )

        return self._result

    def get_bl_prior(self, current_regime: str,
                      shrinkage: float = 0.5) -> Tuple[np.ndarray, np.ndarray]:
        """
        获取 Black-Litterman prior (条件均值, 条件协方差)。

        Args:
            current_regime: 当前 regime 标签
            shrinkage: Bayes shrinkage 系数 (0=纯条件, 1=纯无条件)

        Returns:
            (mean_annual, cov_annual): 年化均值向量和协方差矩阵
        """
        if self._result is None:
            self.compute()

        stats = self._result.stats.get(current_regime)
        if stats is None:
            # 回退到无条件 — 每个 regime 只警告一次
            if not hasattr(self, '_warned_regimes'):
                self._warned_regimes = set()
            if current_regime not in self._warned_regimes:
                self._warned_regimes.add(current_regime)
                print(f"  [WARN] {current_regime} 无足够数据, 使用无条件统计")
            return self._result.unconditional_mean, self._result.unconditional_cov

        # Bayes shrinkage: shrink conditional → unconditional
        cond_mean = stats.mean_annual
        cond_cov = stats.cov_annual
        uncond_mean = self._result.unconditional_mean
        uncond_cov = self._result.unconditional_cov

        # Shrink means
        shrunk_mean = (1 - shrinkage) * cond_mean + shrinkage * uncond_mean

        # Shrink covariance: convex combination
        shrunk_cov = (1 - shrinkage) * cond_cov + shrinkage * uncond_cov

        return shrunk_mean, shrunk_cov

    def generate_report(self) -> str:
        """生成条件统计报告 (Markdown)"""
        if self._result is None:
            self.compute()

        r = self._result

        lines = [
            "# Regime 条件统计报告",
            f"\n> 日期范围: {r.date_range[0]} → {r.date_range[1]}",
            f"\n## 各 Regime 年化收益 (%)",
        ]

        # 表头
        assets_header = "| Regime | 天数 | " + " | ".join(ASSET_ORDER) + " |"
        lines.append(assets_header)
        sep = "|--------|------|" + "|".join(["------" for _ in ASSET_ORDER]) + "|"
        lines.append(sep)

        # 无条件行
        uncond_ret = r.unconditional_mean * 100
        uncond_row = "| 无条件 | — | " + " | ".join(f"{v:.1f}" for v in uncond_ret) + " |"
        lines.append(uncond_row)

        # 各 regime
        for regime in STABLE_REGIMES:
            if regime not in r.stats:
                continue
            s = r.stats[regime]
            row = f"| {s.regime_label} | {s.n_obs} | "
            row += " | ".join(f"{v*100:.1f}" for v in s.mean_annual)
            row += " |"
            lines.append(row)

        # ANOVA
        lines.append(f"\n## ANOVA 检验 (Regime 间差异显著性)")
        lines.append(f"\n| 资产 | F-stat | p-value | 5%显著 | 1%显著 |")
        lines.append(f"|------|--------|---------|--------|--------|")
        for asset, row in r.anova_results.iterrows():
            sig5 = "YES" if row["significant_5pct"] else "no"
            sig1 = "YES" if row["significant_1pct"] else "no"
            lines.append(
                f"| {asset} | {row['f_statistic']:.2f} | "
                f"{row['p_value']:.4f} | {sig5} | {sig1} |"
            )

        # 各 regime Sharpe
        lines.append(f"\n## 各 Regime 年化 Sharpe")
        lines.append(f"\n| Regime | " + " | ".join(ASSET_ORDER) + " |")
        lines.append("|--------|" + "|".join(["------" for _ in ASSET_ORDER]) + "|")
        for regime in STABLE_REGIMES:
            if regime not in r.stats:
                continue
            s = r.stats[regime]
            row = f"| {s.regime_label} | "
            row += " | ".join(f"{v:.2f}" for v in s.sharpe)
            row += " |"
            lines.append(row)

        # 协方差矩阵 (risk_on)
        if "risk_on" in r.stats:
            lines.append(f"\n## Risk-On 相关性矩阵")
            corr = r.stats["risk_on"].corr
            lines.append(f"\n| | " + " | ".join(ASSET_ORDER) + " |")
            lines.append("|---|" + "|".join(["---" for _ in ASSET_ORDER]) + "|")
            for i, asset in enumerate(ASSET_ORDER):
                row = f"| {asset} | "
                row += " | ".join(f"{corr[i, j]:.2f}" for j in range(len(ASSET_ORDER)))
                row += " |"
                lines.append(row)

        return "\n".join(lines)


# ── 便捷函数 ────────────────────────────────────────────────────

def get_conditional_stats() -> ConditionalStats:
    return ConditionalStats()


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    cs = ConditionalStats()
    result = cs.compute()

    print(f"日期范围: {result.date_range[0]} → {result.date_range[1]}")
    print(f"\nANOVA 检验:")
    print(result.anova_results.to_string())

    print(f"\n各 regime 观测数:")
    for regime in STABLE_REGIMES:
        if regime in result.stats:
            s = result.stats[regime]
            print(f"  {s.regime_label}: {s.n_obs} 天, "
                  f"A股 Sharpe={s.sharpe[ASSET_ORDER.index('a_share')]:.2f}, "
                  f"黄金 Sharpe={s.sharpe[ASSET_ORDER.index('gold')]:.2f}, "
                  f"债券 Sharpe={s.sharpe[ASSET_ORDER.index('bond')]:.2f}")

    # 完整报告
    report = cs.generate_report()
    report_path = Path("d:/AgentAlpha/reports/planning/stable_conditional_stats.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\n报告已保存: {report_path}")