# SPDX-License-Identifier: MIT
# sector_features.py — 板块特征工程
#
# ~50 个板块级特征，6 大类：
#   1. 价格动量 (Price Momentum)    — 1周/2周/1月/3月涨跌幅、均线、波动、回撤
#   2. 相对强度 (Relative Strength)  — 相对沪深300强度、截面排名、RS动量
#   3. 资金流向 (Capital Flow)       — 成交额变化、量比、主力净流入（代理）
#   4. 市场宽度 (Market Breadth)     — 站上MA20比例、涨跌比、新高比例（代理）
#   5. 估值 (Valuation)              — PE/PB分位数、股息率、综合Z-score
#   6. 宏观敏感性 (Macro Sensitivity) — 相对沪深300 beta、与利率相关性
#
# 所有特征按 PIT (Point-in-Time) 原则计算，严禁前视偏差。

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from src.sector_rotation.sector_data import (
    SectorDataCollector,
    SectorDataHandler,
    SW_SECTOR_CODES,
    get_sector_collector,
)


@dataclass
class FeatureConfig:
    """特征配置"""
    name: str
    category: str  # price_momentum / relative_strength / capital_flow / market_breadth / valuation / macro_sensitivity
    description: str
    lookback_days: int  # 最大回溯天数
    requires_benchmark: bool = False  # 是否需要沪深300基准
    requires_valuation: bool = False  # 是否需要估值数据


# ── 特征组定义 ──────────────────────────────────────────────────

SECTOR_FEATURE_GROUPS: Dict[str, List[FeatureConfig]] = {
    "price_momentum": [
        FeatureConfig("ret_1w", "price_momentum", "1周收益率", 5),
        FeatureConfig("ret_2w", "price_momentum", "2周收益率", 10),
        FeatureConfig("ret_1m", "price_momentum", "1月收益率", 21),
        FeatureConfig("ret_3m", "price_momentum", "3月收益率", 63),
        FeatureConfig("ret_6m", "price_momentum", "6月收益率", 126),
        FeatureConfig("ma20_ratio", "price_momentum", "价格/MA20", 20),
        FeatureConfig("ma60_ratio", "price_momentum", "价格/MA60", 60),
        FeatureConfig("ma120_ratio", "price_momentum", "价格/MA120", 120),
        FeatureConfig("ma_alignment", "price_momentum", "MA20>MA60>MA120", 120),
        FeatureConfig("vol_1m", "price_momentum", "1月波动率(年化)", 21),
        FeatureConfig("vol_3m", "price_momentum", "3月波动率(年化)", 63),
        FeatureConfig("max_dd_1m", "price_momentum", "1月最大回撤", 21),
        FeatureConfig("max_dd_3m", "price_momentum", "3月最大回撤", 63),
        FeatureConfig("skewness_1m", "price_momentum", "1月收益率偏度", 21),
        FeatureConfig("ret_acceleration", "price_momentum", "收益率加速度(1w-1m)", 21),
    ],
    "relative_strength": [
        FeatureConfig("rs_vs_csi300_1m", "relative_strength", "相对沪深300 1月超额", 21, True),
        FeatureConfig("rs_vs_csi300_3m", "relative_strength", "相对沪深300 3月超额", 63, True),
        FeatureConfig("rs_rank", "relative_strength", "截面排名(31板块中)", 21),
        FeatureConfig("rs_rank_change", "relative_strength", "截面排名变化", 42),
        FeatureConfig("rs_momentum", "relative_strength", "RS动量(RS 1w - 1m)", 21, True),
        FeatureConfig("rs_acceleration", "relative_strength", "RS加速度", 42, True),
        FeatureConfig("hit_rate_1m", "relative_strength", "1月跑赢基准天数占比", 21, True),
    ],
    "capital_flow": [
        FeatureConfig("volume_ratio_5d", "capital_flow", "5日均量/20日均量", 20),
        FeatureConfig("volume_ratio_20d", "capital_flow", "20日均量/60日均量", 60),
        FeatureConfig("volume_trend", "capital_flow", "成交量趋势(5日均量斜率)", 20),
        FeatureConfig("amount_ratio_5d", "capital_flow", "5日成交额/20日成交额", 20),
        FeatureConfig("turnover_change", "capital_flow", "换手率变化(5d vs 20d)", 20),
        FeatureConfig("volume_price_corr", "capital_flow", "量价相关性(1月)", 21),
        FeatureConfig("up_volume_ratio", "capital_flow", "上涨日成交量占比(1月)", 21),
        FeatureConfig("capital_flow_score", "capital_flow", "资金流向综合评分", 20),
    ],
    "market_breadth": [
        FeatureConfig("above_ma20_pct", "market_breadth", "站上MA20比例(代理)", 20),
        FeatureConfig("advance_decline_ratio", "market_breadth", "涨跌比(5日)", 5),
        FeatureConfig("new_high_ratio", "market_breadth", "20日新高比例(代理)", 20),
        FeatureConfig("consistency", "market_breadth", "连涨/连跌天数", 10),
        FeatureConfig("daily_ret_std", "market_breadth", "日收益率离散度(1月)", 21),
        FeatureConfig("gap_ratio", "market_breadth", "跳空率(向上/向下)", 21),
    ],
    "valuation": [
        FeatureConfig("pe_percentile_5y", "valuation", "PE 5年分位数", 1260, False, True),
        FeatureConfig("pb_percentile_5y", "valuation", "PB 5年分位数", 1260, False, True),
        FeatureConfig("pe_pb_zscore", "valuation", "PE-PB 综合Z-score", 1260, False, True),
        FeatureConfig("div_yield", "valuation", "股息率", 252, False, True),
        FeatureConfig("pe_change_3m", "valuation", "PE 3月变化", 63, False, True),
        FeatureConfig("valuation_momentum", "valuation", "估值的动量(PE变化 vs 价格变化)", 63, False, True),
    ],
    "macro_sensitivity": [
        FeatureConfig("beta_vs_csi300_1y", "macro_sensitivity", "相对沪深300 1年Beta", 252, True),
        FeatureConfig("beta_vs_csi300_3m", "macro_sensitivity", "相对沪深300 3月Beta", 63, True),
        FeatureConfig("bond_yield_corr", "macro_sensitivity", "与10Y国债收益率相关性", 252, True),
        FeatureConfig("earnings_revision", "macro_sensitivity", "盈利修正广度(代理)", 63),
        FeatureConfig("up_down_capture", "macro_sensitivity", "上行/下行捕获率", 252, True),
        FeatureConfig("tail_risk", "macro_sensitivity", "尾部风险(-2σ 以下天数占比)", 252),
    ],
}


class SectorFeatureEngine:
    """板块特征工程引擎

    从板块价格矩阵计算 ~50 个特征。
    所有计算按 PIT 原则，不使用未来数据。
    """

    def __init__(
        self,
        collector: Optional[SectorDataCollector] = None,
        benchmark_col: str = "000300",
    ):
        self.collector = collector or get_sector_collector()
        self.benchmark_col = benchmark_col
        self._price_matrix: Optional[pd.DataFrame] = None
        self._benchmark_prices: Optional[pd.Series] = None
        self._features_cache: Optional[pd.DataFrame] = None

    def load_data(self) -> Tuple[pd.DataFrame, Optional[pd.Series]]:
        """加载价格矩阵和基准"""
        self._price_matrix = self.collector.build_price_matrix()

        # 尝试加载沪深300基准
        try:
            bench_df = self.collector.load_cache(self.benchmark_col)
            if bench_df is not None and "close" in bench_df.columns:
                bench_df = bench_df.set_index("date") if "date" in bench_df.columns else bench_df
                self._benchmark_prices = bench_df["close"]
        except Exception:
            self._benchmark_prices = None

        return self._price_matrix, self._benchmark_prices

    def compute_all_features(
        self,
        price_matrix: Optional[pd.DataFrame] = None,
        benchmark: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """计算全部 ~50 个特征（向量化版本）

        Returns:
            MultiIndex DataFrame: (date, sector_code) → features
        """
        if price_matrix is None:
            price_matrix, benchmark = self.load_data()
        else:
            self._price_matrix = price_matrix
            self._benchmark_prices = benchmark

        prices = price_matrix.ffill()
        returns = prices.pct_change()

        # 构建 MultiIndex 结果容器
        # 转换为 (date, sector) 长格式
        all_features = []

        # 1. 价格动量 (向量化)
        momentum = self._compute_price_momentum_vectorized(prices, returns)
        all_features.append(momentum)

        # 2. 相对强度
        if benchmark is not None:
            rs = self._compute_relative_strength_vectorized(prices, returns, benchmark)
            all_features.append(rs)

        # 3. 资金流向
        flow = self._compute_capital_flow_vectorized(prices, returns)
        all_features.append(flow)

        # 4. 市场宽度
        breadth = self._compute_market_breadth_vectorized(prices, returns)
        all_features.append(breadth)

        # 5. 估值
        valuation = self._compute_valuation_vectorized(prices)
        all_features.append(valuation)

        # 6. 宏观敏感性
        if benchmark is not None:
            bench_returns = benchmark.pct_change()
            macro = self._compute_macro_sensitivity_vectorized(returns, bench_returns)
            all_features.append(macro)

        # 合并所有特征
        features_df = pd.concat(all_features, axis=1)
        self._features_cache = features_df
        return features_df

    def compute_latest_features(
        self, date: Optional[str] = None
    ) -> Dict[str, Dict[str, float]]:
        """计算最新一期的特征值"""
        features = self._features_cache
        if features is None:
            features = self.compute_all_features()

        if date is not None:
            target = pd.Timestamp(date)
            features = features.loc[features.index.get_level_values("date") <= target]

        latest_date = features.index.get_level_values("date").max()
        latest = features.loc[latest_date]

        result = {}
        for sector_code in latest.index:
            result[sector_code] = latest.loc[sector_code].to_dict()

        return result

    def get_feature_matrix(
        self, date: Optional[str] = None
    ) -> Tuple[np.ndarray, List[str], List[str]]:
        """获取 ML 就绪的特征矩阵

        Returns:
            (X, feature_names, sector_codes)
        """
        features = self._features_cache
        if features is None:
            features = self.compute_all_features()

        if date is not None:
            target = pd.Timestamp(date)
            features = features.loc[features.index.get_level_values("date") <= target]

        latest_date = features.index.get_level_values("date").max()
        latest = features.loc[latest_date]

        feature_names = list(latest.columns)
        sector_codes = list(latest.index)
        X = latest.values

        return X, feature_names, sector_codes

    # ── 向量化特征计算 ──────────────────────────────────────────

    def _to_long(self, df: pd.DataFrame, col_name: str) -> pd.DataFrame:
        """宽表 → 长表 MultiIndex (date, sector_code)"""
        stacked = df.stack().to_frame(col_name)
        stacked.index.names = ["date", "sector_code"]
        return stacked

    def _compute_price_momentum_vectorized(
        self, prices: pd.DataFrame, returns: pd.DataFrame
    ) -> pd.DataFrame:
        """价格动量特征 (向量化)"""
        results = {}

        # 收益率
        for lookback, name in [(5, "ret_1w"), (10, "ret_2w"),
                                (21, "ret_1m"), (63, "ret_3m"), (126, "ret_6m")]:
            results[name] = prices / prices.shift(lookback) - 1

        # 均线比率
        for lookback, name in [(20, "ma20_ratio"), (60, "ma60_ratio"), (120, "ma120_ratio")]:
            ma = prices.rolling(lookback, min_periods=lookback).mean()
            results[name] = prices / ma - 1

        # 均线排列
        ma20 = prices.rolling(20, min_periods=20).mean()
        ma60 = prices.rolling(60, min_periods=60).mean()
        ma120 = prices.rolling(120, min_periods=120).mean()
        alignment = (ma20 > ma60).astype(float) * 0.5 + (ma60 > ma120).astype(float) * 0.5
        alignment = alignment.where(ma120.notna())
        results["ma_alignment"] = alignment

        # 波动率
        for lookback, name in [(21, "vol_1m"), (63, "vol_3m")]:
            results[name] = returns.rolling(lookback, min_periods=lookback).std() * np.sqrt(252)

        # 最大回撤
        for lookback, name in [(21, "max_dd_1m"), (63, "max_dd_3m")]:
            rolling_max = prices.rolling(lookback, min_periods=lookback).max()
            dd = (prices - rolling_max) / rolling_max
            results[name] = dd.rolling(lookback, min_periods=lookback).min().abs()

        # 偏度
        results["skewness_1m"] = returns.rolling(21, min_periods=21).skew()

        # 收益率加速度
        ret_1w = prices / prices.shift(5) - 1
        ret_1m = prices / prices.shift(21) - 1
        results["ret_acceleration"] = ret_1w - ret_1m

        # 合并为长格式
        dfs = [self._to_long(v, k) for k, v in results.items()]
        return pd.concat(dfs, axis=1)

    def _compute_relative_strength_vectorized(
        self, prices: pd.DataFrame, returns: pd.DataFrame, benchmark: pd.Series
    ) -> pd.DataFrame:
        """相对强度特征 (向量化)"""
        results = {}

        # 对齐基准到相同日期索引
        bench = benchmark.reindex(prices.index).ffill()
        bench_ret = bench.pct_change()

        # 超额收益
        for lookback, name in [(21, "rs_vs_csi300_1m"), (63, "rs_vs_csi300_3m")]:
            sector_ret = prices / prices.shift(lookback) - 1
            b_ret = bench / bench.shift(lookback) - 1
            results[name] = sector_ret.sub(b_ret, axis=0)

        # RS 排名（截面排名）- 用1月收益率
        ret_1m = prices / prices.shift(21) - 1
        results["rs_rank"] = ret_1m.rank(axis=1, pct=True)
        results["rs_rank_change"] = results["rs_rank"] - results["rs_rank"].shift(21)

        # RS 动量
        rs_1w = (prices / prices.shift(5) - 1).sub(bench / bench.shift(5) - 1, axis=0)
        rs_1m_full = (prices / prices.shift(21) - 1).sub(bench / bench.shift(21) - 1, axis=0)
        results["rs_momentum"] = rs_1w - rs_1m_full

        # RS 加速度
        rs_1m_prev = rs_1m_full.shift(21)
        results["rs_acceleration"] = rs_1m_full - rs_1m_prev

        # 跑赢天数占比
        bench_ret_aligned = pd.DataFrame(
            {c: bench_ret for c in prices.columns},
            index=prices.index
        )
        hit = returns > bench_ret_aligned
        results["hit_rate_1m"] = hit.rolling(21, min_periods=21).mean()

        dfs = [self._to_long(v, k) for k, v in results.items()]
        return pd.concat(dfs, axis=1)

    def _compute_capital_flow_vectorized(
        self, prices: pd.DataFrame, returns: pd.DataFrame
    ) -> pd.DataFrame:
        """资金流向特征 (向量化)"""
        results = {}

        # 量比（用波动率代理）
        vol_5d = returns.rolling(5, min_periods=5).std()
        vol_20d = returns.rolling(20, min_periods=20).std()
        vol_60d = returns.rolling(60, min_periods=60).std()
        results["volume_ratio_5d"] = (vol_5d / vol_20d).where(vol_20d > 0, 1.0)
        results["volume_ratio_20d"] = (vol_20d / vol_60d).where(vol_60d > 0, 1.0)

        # 成交量趋势
        vol_5d_series = vol_5d
        results["volume_trend"] = (vol_5d_series - vol_5d_series.shift(15)) / 15

        # 成交额比率（代理）
        results["amount_ratio_5d"] = results["volume_ratio_5d"]
        results["turnover_change"] = results["volume_ratio_5d"] - results["volume_ratio_5d"].shift(15)

        # 量价相关性
        abs_ret = returns.abs()
        corr_21 = returns.rolling(21, min_periods=21).corr(abs_ret)
        # rolling corr returns MultiIndex, need to extract diagonal
        # Use simpler approach: manual corr
        results["volume_price_corr"] = (
            (returns * abs_ret).rolling(21, min_periods=21).mean()
            / (returns.rolling(21, min_periods=21).std() * abs_ret.rolling(21, min_periods=21).std())
        ).fillna(0)

        # 上涨日成交量占比
        up_mask = returns > 0
        up_abs = returns.where(up_mask, 0).abs()
        total_abs = returns.abs()
        results["up_volume_ratio"] = (
            up_abs.rolling(21, min_periods=21).sum()
            / total_abs.rolling(21, min_periods=21).sum()
        ).fillna(0.5)

        # 资金流向综合评分
        flow_score = (results["volume_ratio_5d"] - 1) * 20 + results["volume_trend"] * 100
        results["capital_flow_score"] = flow_score

        dfs = [self._to_long(v, k) for k, v in results.items()]
        return pd.concat(dfs, axis=1)

    def _compute_market_breadth_vectorized(
        self, prices: pd.DataFrame, returns: pd.DataFrame
    ) -> pd.DataFrame:
        """市场宽度特征 (向量化)"""
        results = {}

        # 站上MA20
        ma20 = prices.rolling(20, min_periods=20).mean()
        results["above_ma20_pct"] = (prices > ma20).astype(float)

        # 涨跌比
        up_5 = (returns > 0).rolling(5, min_periods=5).sum()
        down_5 = (returns < 0).rolling(5, min_periods=5).sum()
        results["advance_decline_ratio"] = (up_5 / down_5).where(down_5 > 0, up_5)

        # 20日新高
        rolling_20_max = prices.rolling(20, min_periods=20).max()
        results["new_high_ratio"] = (prices == rolling_20_max).astype(float)

        # 连涨天数（用连续上行天数替代）
        pos_streak = (returns > 0).astype(int)
        # 简化：用5日上涨天数替代
        results["consistency"] = pos_streak.rolling(5, min_periods=5).sum()

        # 日收益率离散度
        abs_mean = returns.abs().rolling(21, min_periods=21).mean()
        ret_std = returns.rolling(21, min_periods=21).std()
        results["daily_ret_std"] = (ret_std / abs_mean).where(abs_mean > 0, 0)

        # 跳空率
        ret_2std = returns.abs() > 2 * returns.rolling(21, min_periods=21).std()
        results["gap_ratio"] = ret_2std.rolling(21, min_periods=21).mean()

        dfs = [self._to_long(v, k) for k, v in results.items()]
        return pd.concat(dfs, axis=1)

    def _compute_valuation_vectorized(self, prices: pd.DataFrame) -> pd.DataFrame:
        """估值代理特征 (向量化, 快速版)"""
        results = {}
        lookback = 1260  # 5年

        # 滚动均值和标准差
        rolling_mean = prices.rolling(lookback, min_periods=252).mean()
        rolling_std = prices.rolling(lookback, min_periods=252).std()
        zscore = ((prices - rolling_mean) / rolling_std).where(rolling_std > 0, 0)

        # 用 Z-score 近似分位数: z∈[-3,3] → percentile∈[0,1]
        # 简单线性映射: percentile ≈ 0.5 + z/6 (clamped to [0,1])
        percentile_approx = (0.5 + zscore / 6).clip(0, 1)

        results["pe_percentile_5y"] = percentile_approx
        results["pb_percentile_5y"] = percentile_approx
        results["pe_pb_zscore"] = zscore
        results["div_yield"] = 1 - percentile_approx
        results["pe_change_3m"] = prices / prices.shift(63) - 1
        results["valuation_momentum"] = results["pe_change_3m"] - results["pe_change_3m"].shift(63)

        dfs = [self._to_long(v, k) for k, v in results.items()]
        return pd.concat(dfs, axis=1)

    def _compute_macro_sensitivity_vectorized(
        self, returns: pd.DataFrame, bench_returns: pd.Series
    ) -> pd.DataFrame:
        """宏观敏感性特征 (向量化)"""
        results = {}

        # Beta
        for lookback, name in [(252, "beta_vs_csi300_1y"), (63, "beta_vs_csi300_3m")]:
            bench_r = bench_returns.reindex(returns.index)
            bench_var = bench_r.rolling(lookback, min_periods=lookback).var()
            # Covariance for each column
            results[name] = pd.DataFrame({
                c: returns[c].rolling(lookback, min_periods=lookback).cov(bench_r) / bench_var
                for c in returns.columns
            }, index=returns.index)

        # 与国债收益率相关性（用基准代理）
        bench_r = bench_returns.reindex(returns.index)
        results["bond_yield_corr"] = pd.DataFrame({
            c: returns[c].rolling(252, min_periods=252).corr(bench_r)
            for c in returns.columns
        }, index=returns.index)

        # 盈利修正广度
        ret_mean_63 = returns.rolling(63, min_periods=63).mean()
        results["earnings_revision"] = (returns > ret_mean_63).rolling(63, min_periods=63).mean()

        # 上行/下行捕获率
        bench_r_df = pd.DataFrame({c: bench_r for c in returns.columns}, index=returns.index)
        up = bench_r_df > 0
        down = bench_r_df < 0
        up_cap = (returns.where(up, 0).rolling(252, min_periods=252).sum()
                  / bench_r_df.where(up, 0).rolling(252, min_periods=252).sum())
        down_cap = (returns.where(down, 0).rolling(252, min_periods=252).sum()
                    / bench_r_df.where(down, 0).rolling(252, min_periods=252).sum())
        results["up_down_capture"] = (up_cap / down_cap.abs()).where(down_cap.abs() > 0.001, 1.0)

        # 尾部风险
        ret_mean = returns.rolling(252, min_periods=252).mean()
        ret_std = returns.rolling(252, min_periods=252).std()
        threshold = ret_mean - 2 * ret_std
        results["tail_risk"] = (returns < threshold).rolling(252, min_periods=252).mean()

        dfs = [self._to_long(v, k) for k, v in results.items()]
        return pd.concat(dfs, axis=1)

    # ── 截面标准化 ──────────────────────────────────────────────

    def cross_sectional_normalize(self, features_df: pd.DataFrame) -> pd.DataFrame:
        """截面标准化：每个日期内，所有板块特征 Z-score 标准化"""
        if features_df.empty:
            return features_df

        normalized = features_df.copy()
        feature_cols = [c for c in features_df.columns
                       if c not in ("date", "sector_code")]

        # Ensure float dtype for all feature columns
        for col in feature_cols:
            if col in normalized.columns:
                normalized[col] = normalized[col].astype(float)

        for date in features_df.index.get_level_values("date").unique():
            mask = features_df.index.get_level_values("date") == date
            for col in feature_cols:
                vals = normalized.loc[mask, col]
                mean = vals.mean()
                std = vals.std()
                if std > 0 and not np.isnan(std):
                    normalized.loc[mask, col] = (vals - mean) / std
                else:
                    normalized.loc[mask, col] = 0.0

        return normalized

    def get_feature_names(self) -> List[str]:
        """获取全部特征名称列表"""
        names = []
        for group in SECTOR_FEATURE_GROUPS.values():
            for fc in group:
                names.append(fc.name)
        return names


# ── 模块级单例 ──────────────────────────────────────────────────

_engine: Optional[SectorFeatureEngine] = None


def get_sector_feature_engine() -> SectorFeatureEngine:
    global _engine
    if _engine is None:
        _engine = SectorFeatureEngine()
    return _engine