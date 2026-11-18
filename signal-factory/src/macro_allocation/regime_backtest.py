# SPDX-License-Identifier: MIT
# regime_backtest.py — 历史宏观 regime 分类回测
#
# 从宏观指标生成 2015-2026 年的历史 regime 标签序列。
# 使用规则基方法（阈值+趋势），4 类 STABLE regime:
#   1. Risk-On  (risk_on):  增长↑ + 利率↓ + 风险偏好↑
#   2. Risk-Off (risk_off): 增长↓ + 风险偏好↓
#   3. Stagflation (stagflation): 增长↓ + 通胀↑ + 利率↑
#   4. Overheat  (overheat):  增长↑ + 通胀↑ + 利率↑
#
# 实现策略:
#   - 每月计算各维度 z-score（24月滚动窗口）
#   - 规则基分类 + 边界情况的置信度评分
#   - 输出每日 regime 标签（前向填充）
#
# 验证标准:
#   - 正确识别 2015 股灾 (Risk-Off)
#   - 正确识别 2020 疫情冲击 (Risk-Off → Risk-On)
#   - 正确识别 2024 924 行情 (Risk-On)
#   - 4 类 regime 下资产收益分布有统计显著差异

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_DATA_DIR = Path("d:/AgentAlpha/signal-factory/data/macro_assets")

# ── Regime 定义 ──────────────────────────────────────────────────

STABLE_REGIMES = ["risk_on", "risk_off", "stagflation", "overheat", "neutral"]

REGIME_LABELS_CN = {
    "risk_on": "Risk-On/增长",
    "risk_off": "Risk-Off/衰退",
    "stagflation": "滞胀",
    "overheat": "过热",
    "neutral": "中性/均衡",
}

# ── 指标列名映射 ────────────────────────────────────────────────

# edb_indicators.csv 中的关键列
COL_PMI = "制造业PMI"
COL_M2_YOY = "M2同比"
COL_SF_TOTAL = "社会融资_亿"  # 社融总量
COL_10Y = "10Y国债收益率"
COL_SHIBOR = "SHIBOR_ON"
COL_CSI300_PE = "沪深300PE_TTM"
COL_WTI = "WTI原油"
COL_GOLD = "黄金价格"
COL_US_10Y = "us_10y_yield"


@dataclass
class RegimePeriod:
    """一段 regime 时期"""
    regime: str
    start_date: str
    end_date: str
    n_days: int
    confidence: float


@dataclass
class RegimeHistory:
    """历史 regime 分类结果"""
    daily_labels: pd.Series        # date → regime label
    monthly_scores: pd.DataFrame   # 每月各维度评分
    periods: List[RegimePeriod]    # regime 连续时段
    summary: Dict[str, float]      # 各类 regime 占比


class StableRegimeClassifier:
    """
    STABLE 4-Regime 历史分类器。

    使用滚动 z-score + 规则基方法从宏观指标分类。
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self._macro_df: Optional[pd.DataFrame] = None
        self._daily_regime: Optional[pd.Series] = None

    # ── 数据加载 ────────────────────────────────────────────────

    def load_macro_data(self) -> pd.DataFrame:
        """加载宏观指标数据"""
        if self._macro_df is not None:
            return self._macro_df

        fp = self.data_dir / "edb_indicators.csv"
        if not fp.exists():
            raise FileNotFoundError(f"找不到宏观数据: {fp}")

        df = pd.read_csv(fp, parse_dates=["date"], index_col="date")
        self._macro_df = df
        return df

    # ── 维度评分 ────────────────────────────────────────────────

    def _compute_dimension_scores(self, df: pd.DataFrame,
                                   window: int = 24) -> pd.DataFrame:
        """
        计算四个维度的月度 z-score。

        Args:
            df: 宏观数据 DataFrame（日频，指标稀疏）
            window: 滚动窗口月数

        Returns:
            DataFrame: 月度 × 4 列 (growth, inflation, rates, risk_appetite)
        """
        # 重采样到月频（取月末值）
        monthly = df.resample("ME").last()

        # 前向填充月度稀疏数据
        for col in monthly.columns:
            monthly[col] = monthly[col].ffill()

        scores = pd.DataFrame(index=monthly.index)

        # ── 增长维度 (Growth) ──
        # PMI + M2 增速
        if COL_PMI in monthly.columns:
            pmi = monthly[COL_PMI].ffill()
            scores["growth_pmi"] = pmi
        else:
            scores["growth_pmi"] = 50.0  # 中性

        if COL_M2_YOY in monthly.columns:
            m2 = monthly[COL_M2_YOY].ffill()
            scores["growth_m2"] = m2
        else:
            scores["growth_m2"] = 8.0

        # ── 通胀维度 (Inflation) ──
        # WTI 原油同比变化 + 黄金趋势
        if COL_WTI in monthly.columns:
            wti = monthly[COL_WTI].ffill()
            scores["inflation_wti"] = wti.pct_change(12)  # YoY
        else:
            scores["inflation_wti"] = 0.0

        if COL_GOLD in monthly.columns:
            gold = monthly[COL_GOLD].ffill()
            scores["inflation_gold"] = gold.pct_change(6)  # 6M trend
        else:
            scores["inflation_gold"] = 0.0

        # ── 利率维度 (Rates) ──
        if COL_10Y in monthly.columns:
            scores["rates_10y"] = monthly[COL_10Y].ffill()
        else:
            scores["rates_10y"] = 3.0

        if COL_SHIBOR in monthly.columns:
            scores["rates_shibor"] = monthly[COL_SHIBOR].ffill()
        else:
            scores["rates_shibor"] = 2.0

        # ── 风险偏好 (Risk Appetite) ──
        if COL_CSI300_PE in monthly.columns:
            pe = monthly[COL_CSI300_PE].ffill()
            # PE 分位数（滚动 5 年）
            scores["risk_pe"] = pe
            scores["risk_pe_percentile"] = pe.rolling(60, min_periods=12).apply(
                lambda x: (x.iloc[-1] > x.iloc[:-1]).mean() if len(x) > 1 else 0.5
            )
        else:
            scores["risk_pe"] = 12.0
            scores["risk_pe_percentile"] = 0.5

        # ── 计算 z-scores ──
        z_cols = []
        for col in ["growth_pmi", "growth_m2", "inflation_wti",
                     "rates_10y", "risk_pe_percentile"]:
            if col in scores.columns:
                z_name = f"z_{col}"
                scores[z_name] = scores[col].rolling(window, min_periods=12).apply(
                    lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0
                )
                z_cols.append(z_name)

        # ── 趋势维度 (3-month change) ──
        if "growth_pmi" in scores.columns:
            scores["z_growth_trend"] = scores["growth_pmi"].diff(3).rolling(
                window, min_periods=12
            ).apply(
                lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0
            )

        if "rates_10y" in scores.columns:
            # 利率变化方向: 上升→紧缩, 下降→宽松
            scores["rates_delta_3m"] = scores["rates_10y"].diff(3)
            scores["z_rates_delta"] = scores["rates_delta_3m"].rolling(
                window, min_periods=12
            ).apply(
                lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0
            )

        if "inflation_wti" in scores.columns:
            scores["z_inflation_trend"] = scores["inflation_wti"].diff(3).rolling(
                window, min_periods=12
            ).apply(
                lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0
            )

        # ── 综合维度 z-score ──
        # Growth = PMI level (60%) + PMI trend (40%)
        growth_parts = []
        if "z_growth_pmi" in scores.columns:
            growth_parts.append(("z_growth_pmi", 0.6))
        if "z_growth_trend" in scores.columns:
            growth_parts.append(("z_growth_trend", 0.4))
        if growth_parts:
            scores["z_growth"] = sum(
                scores[c] * w for c, w in growth_parts
            ) / sum(w for _, w in growth_parts)

        # Inflation = WTI level (50%) + WTI trend (50%)
        infl_parts = []
        if "z_inflation_wti" in scores.columns:
            infl_parts.append(("z_inflation_wti", 0.5))
        if "z_inflation_trend" in scores.columns:
            infl_parts.append(("z_inflation_trend", 0.5))
        if infl_parts:
            scores["z_inflation"] = sum(
                scores[c] * w for c, w in infl_parts
            ) / sum(w for _, w in infl_parts)

        # Rates = level (30%) + delta direction (70%) — 方向比水平更重要
        rate_parts = []
        if "z_rates_10y" in scores.columns:
            rate_parts.append(("z_rates_10y", 0.3))
        if "z_rates_delta" in scores.columns:
            rate_parts.append(("z_rates_delta", 0.7))
        if rate_parts:
            scores["z_rates"] = sum(
                scores[c] * w for c, w in rate_parts
            ) / sum(w for _, w in rate_parts)

        if "z_risk_pe_percentile" in scores.columns:
            scores["z_risk"] = scores["z_risk_pe_percentile"]

        return scores

    # ── Regime 分类 ─────────────────────────────────────────────

    def classify(self, window: int = 24) -> RegimeHistory:
        """
        运行全历史 regime 分类。

        Returns:
            RegimeHistory: 包含日度标签、月度评分、连续时段、汇总
        """
        df = self.load_macro_data()
        scores = self._compute_dimension_scores(df, window)

        # ── 规则基分类 ──
        regimes = pd.Series(index=scores.index, dtype=str)

        for i in range(len(scores)):
            z_g = scores["z_growth"].iloc[i] if "z_growth" in scores.columns else 0
            z_i = scores["z_inflation"].iloc[i] if "z_inflation" in scores.columns else 0
            z_r = scores["z_rates"].iloc[i] if "z_rates" in scores.columns else 0
            z_ra = scores["z_risk"].iloc[i] if "z_risk" in scores.columns else 0

            if pd.isna(z_g) or pd.isna(z_i):
                regimes.iloc[i] = "risk_on"  # 默认
                continue

            # 规则分类 (使用趋势和水平综合判断)
            # 优先级: stagflation > overheat > risk_on > risk_off

            # Stagflation: 通胀压力 + 增长走弱
            if z_i > 0.2 and z_g < -0.1:
                regimes.iloc[i] = "stagflation"
            # Overheat: 增长强 + 通胀高 + 利率升
            elif z_g > 0.3 and z_i > 0.3 and z_r > 0:
                regimes.iloc[i] = "overheat"
            # Risk-On: 增长强 + 利率降 (宽松支持)
            elif z_g > 0 and z_r < 0.3:
                regimes.iloc[i] = "risk_on"
            # Risk-On: 增长一般但利率快速下降 (货币宽松驱动)
            elif z_g > -0.3 and z_r < -0.5:
                regimes.iloc[i] = "risk_on"
            # Stagflation: 高通胀 + 利率升 (不论增长)
            elif z_i > 0.5 and z_r > 0.3:
                regimes.iloc[i] = "stagflation"
            # Overheat: 增长强 + 通胀高
            elif z_g > 0.5 and z_i > 0.2:
                regimes.iloc[i] = "overheat"
            # Risk-Off: 增长弱 (默认防御)
            elif z_g < -0.2:
                regimes.iloc[i] = "risk_off"
            # 边界: 略偏风险偏好判断
            elif z_ra > 0.3 and z_g > -0.1:
                regimes.iloc[i] = "risk_on"
            else:
                regimes.iloc[i] = "risk_off"

        # ── 3-month smoothing (regime changes are slow) ──
        # 少数服从多数: 前后各 1 个月的众数
        smoothed = regimes.copy()
        for i in range(1, len(regimes) - 1):
            window_regimes = regimes.iloc[i-1:i+2]
            mode = window_regimes.mode()
            if len(mode) > 0:
                smoothed.iloc[i] = mode.iloc[0]

        regimes = smoothed

        # ── 扩展到日频（前向填充） ──
        daily_regime = regimes.reindex(df.index, method="ffill")
        # 回填开头
        daily_regime = daily_regime.ffill().bfill()

        # ── 计算置信度 ──
        # 基于 z-score 绝对值之和
        conf_series = pd.Series(0.5, index=scores.index)
        for dim in ["z_growth", "z_inflation", "z_rates", "z_risk"]:
            if dim in scores.columns:
                conf_series += scores[dim].abs() * 0.05
        conf_series = conf_series.clip(0.3, 0.95)
        daily_conf = conf_series.reindex(df.index, method="ffill").ffill().bfill()

        # ── 连续时段 ──
        periods = []
        if len(daily_regime) > 0:
            current_regime = daily_regime.iloc[0]
            start_idx = daily_regime.index[0]
            for i in range(1, len(daily_regime)):
                if daily_regime.iloc[i] != current_regime:
                    end_idx = daily_regime.index[i - 1]
                    periods.append(RegimePeriod(
                        regime=current_regime,
                        start_date=str(start_idx.date()),
                        end_date=str(end_idx.date()),
                        n_days=(end_idx - start_idx).days,
                        confidence=float(daily_conf.loc[start_idx:end_idx].mean()),
                    ))
                    current_regime = daily_regime.iloc[i]
                    start_idx = daily_regime.index[i]
            # 最后一段
            periods.append(RegimePeriod(
                regime=current_regime,
                start_date=str(start_idx.date()),
                end_date=str(daily_regime.index[-1].date()),
                n_days=(daily_regime.index[-1] - start_idx).days,
                confidence=float(daily_conf.loc[start_idx:].mean()),
            ))

        # ── 汇总 ──
        summary = {}
        for r in STABLE_REGIMES:
            summary[r] = float((daily_regime == r).mean())

        self._daily_regime = daily_regime

        return RegimeHistory(
            daily_labels=daily_regime,
            monthly_scores=scores,
            periods=periods,
            summary=summary,
        )

    def get_daily_regime(self) -> pd.Series:
        """获取缓存的历史 regime 日度标签"""
        if self._daily_regime is None:
            self.classify()
        return self._daily_regime

    # ── 验证检查 ────────────────────────────────────────────────

    def validate_known_events(self, history: RegimeHistory) -> Dict[str, bool]:
        """
        验证关键历史事件是否正确分类。

        Checks:
          - 2015 股灾 (2015-06 → 2015-09): risk_off
          - 2020 疫情 (2020-02 → 2020-03): risk_off
          - 2020 下半年复苏 (2020-07 → 2020-12): risk_on
          - 2024 924 行情 (2024-09 → 2024-10): risk_on
        """
        labels = history.daily_labels
        checks = {}

        # 2015 股灾
        mask_2015 = (labels.index >= "2015-06-15") & (labels.index <= "2015-09-30")
        checks["2015_crash_risk_off"] = (labels[mask_2015] == "risk_off").mean() > 0.5

        # 2020 疫情
        mask_covid = (labels.index >= "2020-02-01") & (labels.index <= "2020-03-31")
        checks["2020_covid_risk_off"] = (labels[mask_covid] == "risk_off").mean() > 0.5

        # 2020 复苏
        mask_recovery = (labels.index >= "2020-07-01") & (labels.index <= "2020-12-31")
        checks["2020_recovery_risk_on"] = (labels[mask_recovery] == "risk_on").mean() > 0.5

        # 2024 924
        mask_924 = (labels.index >= "2024-09-24") & (labels.index <= "2024-10-31")
        checks["2024_924_risk_on"] = (labels[mask_924] == "risk_on").mean() > 0.5

        return checks

    def generate_report(self, history: Optional[RegimeHistory] = None) -> str:
        """生成 regime 分类报告 (Markdown)"""
        if history is None:
            history = self.classify()

        checks = self.validate_known_events(history)

        lines = [
            "# STABLE Regime 历史分类报告",
            f"\n> 生成时间: {datetime.now().isoformat()}",
            f"\n> 日期范围: {history.daily_labels.index.min().date()} → {history.daily_labels.index.max().date()}",
            f"\n## Regime 分布",
            f"\n| Regime | 中文 | 占比 |",
            f"|--------|------|------|",
        ]
        for r in STABLE_REGIMES:
            pct = history.summary.get(r, 0) * 100
            lines.append(f"| {r} | {REGIME_LABELS_CN.get(r, r)} | {pct:.1f}% |")

        lines.append(f"\n## 关键事件验证")
        lines.append(f"\n| 事件 | 预期 | 实际 | 通过 |")
        lines.append(f"|------|------|------|------|")
        for event, passed in checks.items():
            expected = "risk_off" if "risk_off" in event else "risk_on"
            lines.append(f"| {event} | {expected} | — | {'PASS' if passed else 'FAIL'} |")

        lines.append(f"\n## Regime 转换历史")
        lines.append(f"\n| 开始 | 结束 | Regime | 天数 | 置信度 |")
        lines.append(f"|------|------|--------|------|--------|")
        for p in history.periods[-30:]:  # 最近 30 段
            cn = REGIME_LABELS_CN.get(p.regime, p.regime)
            lines.append(f"| {p.start_date} | {p.end_date} | {cn} | {p.n_days} | {p.confidence:.2f} |")

        return "\n".join(lines)


# ── 便捷函数 ────────────────────────────────────────────────────

def get_regime_classifier(data_dir: Optional[Path] = None) -> StableRegimeClassifier:
    return StableRegimeClassifier(data_dir)


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    clf = StableRegimeClassifier()
    history = clf.classify()
    print(clf.generate_report(history))

    # 检查 regime 间资产收益差异
    print("\n\n## 各 Regime 下资产年化收益 (%):")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from src.macro_allocation.asset_returns import AssetReturns
    ar = AssetReturns()
    returns = ar.load_aligned_returns()

    # 对齐日期
    common_idx = returns.index.intersection(history.daily_labels.index)
    returns_aligned = returns.loc[common_idx]
    regimes_aligned = history.daily_labels.loc[common_idx]

    for regime in STABLE_REGIMES:
        mask = regimes_aligned == regime
        if mask.sum() < 60:
            continue
        r = returns_aligned[mask]
        ann = r.mean() * 252 * 100
        print(f"\n{REGIME_LABELS_CN.get(regime, regime)} ({mask.sum()} 天):")
        for asset in ann.index:
            if asset != "cash":
                print(f"  {asset}: {ann[asset]:.1f}%")