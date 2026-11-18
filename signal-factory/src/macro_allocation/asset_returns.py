# SPDX-License-Identifier: MIT
# asset_returns.py — 11 类资产历史收益计算
#
# 从 ETF/代理数据计算对齐后的日度收益率矩阵。
# 现金使用固定利率（1Y 定存约 1.5-2.0% 年化）。
#
# 使用方式:
#   from src.macro_allocation.asset_returns import AssetReturns
#   ar = AssetReturns(data_dir="d:/AgentAlpha/signal-factory/data/macro_assets")
#   returns_df = ar.load_aligned_returns()  # DataFrame: date × asset_id
#   annual_stats = ar.get_annual_stats()

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 常量 ──────────────────────────────────────────────────────────

DEFAULT_DATA_DIR = Path("d:/AgentAlpha/signal-factory/data/macro_assets")

# ETF 文件名 → 资产 ID 映射
ETF_FILE_MAP: Dict[str, str] = {
    "etf_oil.csv": "oil",
    "etf_gold.csv": "gold",
    "etf_bond.csv": "bond",
    "etf_nasdaq.csv": "nasdaq",
    "etf_hk_tech.csv": "hk_tech",
    "etf_a_share.csv": "a_share",
    "etf_star_50.csv": "star_50",
    "etf_nikkei.csv": "nikkei",
    "etf_india.csv": "india",
    "etf_cb_bond.csv": "cb_bond",
}

# 资产中文名
ASSET_NAMES: Dict[str, str] = {
    "oil": "华宝油气",
    "gold": "黄金ETF",
    "bond": "国债ETF",
    "nasdaq": "纳指ETF",
    "hk_tech": "恒生科技ETF",
    "a_share": "沪深300ETF",
    "star_50": "科创50ETF",
    "nikkei": "日经225ETF",
    "india": "印度基金LOF",
    "cb_bond": "转债ETF",
    "cash": "现金/回购",
}

# 现金年化收益率
CASH_ANNUAL_RETURN = 0.018  # 1.8%


@dataclass
class AssetStats:
    """单个资产的统计摘要"""
    asset_id: str
    asset_name: str
    annual_return: float
    annual_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    start_date: str
    end_date: str
    n_days: int


class AssetReturns:
    """11 类资产历史收益加载与统计"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self._price_cache: Optional[Dict[str, pd.DataFrame]] = None

    # ── 加载 ──────────────────────────────────────────────────────

    def load_prices(self) -> Dict[str, pd.DataFrame]:
        """加载所有 ETF 价格数据，返回 {asset_id: DataFrame(date索引, close列)}"""
        if self._price_cache is not None:
            return self._price_cache

        prices: Dict[str, pd.DataFrame] = {}
        for filename, asset_id in ETF_FILE_MAP.items():
            fp = self.data_dir / filename
            if not fp.exists():
                print(f"  [WARN] 缺少数据文件: {fp}")
                continue

            df = pd.read_csv(fp, parse_dates=["date"], index_col="date")
            if "close" not in df.columns and "收盘" in df.columns:
                df = df.rename(columns={"收盘": "close"})
            if "close" not in df.columns:
                print(f"  [WARN] {filename} 无 close 列, 列名: {df.columns.tolist()}")
                continue

            df = df[["close"]].copy()
            df.columns = [asset_id]
            prices[asset_id] = df

        self._price_cache = prices
        return prices

    def load_aligned_returns(self, log_return: bool = True) -> pd.DataFrame:
        """
        加载所有资产的对齐日度收益率。

        Returns:
            DataFrame: date × asset_id, 每列为该资产的日度收益率
        """
        prices = self.load_prices()

        # 合并所有价格序列
        all_dfs = []
        for asset_id, df in prices.items():
            s = df[asset_id]
            all_dfs.append(s)

        if not all_dfs:
            raise ValueError("无可用资产价格数据")

        price_df = pd.concat(all_dfs, axis=1)

        # 前向填充缺失值（非交易日/节假日）
        price_df = price_df.ffill()

        # 计算收益率
        if log_return:
            returns_df = np.log(price_df / price_df.shift(1))
        else:
            returns_df = price_df.pct_change()

        # 去掉 NaN 行
        returns_df = returns_df.dropna(how="all")

        # 添加现金（固定日收益率）
        cash_daily = (1 + CASH_ANNUAL_RETURN) ** (1 / 252) - 1
        returns_df["cash"] = cash_daily

        return returns_df

    def load_aligned_prices(self) -> pd.DataFrame:
        """加载对齐后的价格 DataFrame（date × asset_id），现金为累计收益"""
        prices = self.load_prices()
        all_dfs = []
        for asset_id, df in prices.items():
            all_dfs.append(df[asset_id])

        price_df = pd.concat(all_dfs, axis=1).ffill()

        # 现金：从最早日期累计
        cash_daily = (1 + CASH_ANNUAL_RETURN) ** (1 / 252) - 1
        n_days = len(price_df)
        price_df["cash"] = 1.0 * (1 + cash_daily) ** np.arange(n_days)

        return price_df

    # ── 统计 ──────────────────────────────────────────────────────

    def get_annual_stats(self) -> pd.DataFrame:
        """计算所有资产的年化统计指标"""
        returns_df = self.load_aligned_returns()

        stats_rows = []
        for asset_id in returns_df.columns:
            r = returns_df[asset_id].dropna()
            if len(r) < 60:
                continue

            ann_ret = float(r.mean() * 252)
            ann_vol = float(r.std() * np.sqrt(252))
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0

            # 最大回撤
            cum = (1 + r).cumprod()
            running_max = cum.cummax()
            drawdown = (cum - running_max) / running_max
            max_dd = float(drawdown.min())

            stats_rows.append({
                "asset_id": asset_id,
                "asset_name": ASSET_NAMES.get(asset_id, asset_id),
                "annual_return": ann_ret,
                "annual_volatility": ann_vol,
                "sharpe_ratio": sharpe,
                "max_drawdown": max_dd,
                "start_date": str(r.index.min().date()),
                "end_date": str(r.index.max().date()),
                "n_days": len(r),
            })

        return pd.DataFrame(stats_rows).set_index("asset_id")

    def get_correlation_matrix(self) -> pd.DataFrame:
        """资产收益率相关性矩阵"""
        returns_df = self.load_aligned_returns()
        return returns_df.corr()

    def get_covariance_matrix(self, annualize: bool = True) -> pd.DataFrame:
        """
        协方差矩阵。

        Args:
            annualize: 是否年化（×252）
        """
        returns_df = self.load_aligned_returns()
        cov = returns_df.cov()
        if annualize:
            cov = cov * 252
        return cov

    def get_common_date_range(self) -> Tuple[str, str]:
        """获取所有资产共有的日期范围"""
        returns_df = self.load_aligned_returns()
        valid = returns_df.dropna()
        return str(valid.index.min().date()), str(valid.index.max().date())


# ── 便捷函数 ──────────────────────────────────────────────────────

def get_asset_returns(data_dir: Optional[Path] = None) -> AssetReturns:
    return AssetReturns(data_dir)


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    ar = AssetReturns()
    returns = ar.load_aligned_returns()
    print(f"资产收益矩阵: {returns.shape[0]} 天 × {returns.shape[1]} 资产")
    print(f"日期范围: {returns.index.min().date()} → {returns.index.max().date()}")
    print(f"\n资产列表: {list(returns.columns)}")
    print(f"\n年化统计:")
    print(ar.get_annual_stats().to_string())
    print(f"\n相关性矩阵:")
    print(ar.get_correlation_matrix().round(3).to_string())