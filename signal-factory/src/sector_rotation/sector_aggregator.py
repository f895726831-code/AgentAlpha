# SPDX-License-Identifier: MIT
# sector_aggregator.py — B 方案基准线：个股 alpha 聚合到板块级别
#
# 将个股 AlphaSignalGenerator 的 alpha 分数聚合到申万行业板块。
# 这是板块轮动 ML 模型的基准线（Baseline）。
# 如果板块 ML 模型不能跑赢这个简单的聚合，则放弃。
#
# 聚合方法：
#   - mean: 板块内个股 alpha 均值
#   - weighted_mean: 按市值加权均值
#   - top_decile: 只看 Top 10% 个股的 alpha 均值
#   - signal_ratio: 正信号个股占比

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.sector_rotation.sector_data import (
    SW_SECTOR_CODES,
    SectorDataCollector,
    get_sector_collector,
)


class AggregationMethod(Enum):
    MEAN = "mean"
    WEIGHTED_MEAN = "weighted_mean"
    TOP_DECILE = "top_decile"
    SIGNAL_RATIO = "signal_ratio"
    MEDIAN = "median"


@dataclass
class SectorAggregationResult:
    """板块聚合结果"""
    date: str
    method: str
    sector_rankings: List[Dict[str, Any]]  # sorted by aggregated_score desc
    top_5_sectors: List[str]
    bottom_5_sectors: List[str]
    stats: Dict[str, float]  # 聚合统计

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "method": self.method,
            "top_5": self.top_5_sectors,
            "bottom_5": self.bottom_5_sectors,
            "stats": self.stats,
            "sector_rankings": self.sector_rankings,
        }


class SectorAggregator:
    """个股 Alpha → 板块聚合器

    B 方案基准线：将个股 alpha 信号聚合到板块级别。
    用于验证板块 ML 模型是否有增量价值。
    """

    def __init__(
        self,
        collector: Optional[SectorDataCollector] = None,
        sector_map: Optional[Dict[str, str]] = None,
    ):
        self.collector = collector or get_sector_collector()
        self.sector_map = sector_map or self._load_sector_map()

    def aggregate(
        self,
        stock_signals: Dict[str, float],  # {symbol: alpha_score}
        method: AggregationMethod = AggregationMethod.MEAN,
        stock_market_caps: Optional[Dict[str, float]] = None,
        date: Optional[str] = None,
    ) -> SectorAggregationResult:
        """聚合个股信号到板块

        Args:
            stock_signals: {windcode: alpha_score}
            method: 聚合方法
            stock_market_caps: {windcode: market_cap}（加权方法需要）
            date: 日期

        Returns:
            SectorAggregationResult
        """
        # 映射个股到板块
        sector_signals: Dict[str, List[float]] = {}
        sector_caps: Dict[str, List[float]] = {}

        for symbol, score in stock_signals.items():
            sw_code = self._map_stock_to_sector(symbol)
            if sw_code is None:
                continue

            if sw_code not in sector_signals:
                sector_signals[sw_code] = []
                sector_caps[sw_code] = []

            sector_signals[sw_code].append(score)

            if stock_market_caps and symbol in stock_market_caps:
                sector_caps[sw_code].append(stock_market_caps[symbol])
            else:
                sector_caps[sw_code].append(1.0)  # 等权

        # 聚合
        aggregated = {}
        for sw_code, scores in sector_signals.items():
            caps = sector_caps.get(sw_code, [1.0] * len(scores))

            if method == AggregationMethod.MEAN:
                aggregated[sw_code] = np.mean(scores)
            elif method == AggregationMethod.WEIGHTED_MEAN:
                total_cap = sum(caps)
                aggregated[sw_code] = (
                    np.average(scores, weights=caps) if total_cap > 0 else np.mean(scores)
                )
            elif method == AggregationMethod.TOP_DECILE:
                n_top = max(1, int(len(scores) * 0.1))
                top_indices = np.argsort(scores)[-n_top:]
                aggregated[sw_code] = np.mean([scores[i] for i in top_indices])
            elif method == AggregationMethod.SIGNAL_RATIO:
                aggregated[sw_code] = np.mean([s > 0 for s in scores])
            elif method == AggregationMethod.MEDIAN:
                aggregated[sw_code] = np.median(scores)

        # 排序
        sorted_sectors = sorted(
            aggregated.items(), key=lambda x: x[1], reverse=True
        )

        rankings = []
        for rank, (sw_code, score) in enumerate(sorted_sectors, 1):
            sector_info = SW_SECTOR_CODES.get(sw_code, {})
            rankings.append({
                "rank": rank,
                "sw_code": sw_code,
                "sector_name": sector_info.get("name", sw_code),
                "category": sector_info.get("category", "unknown"),
                "aggregated_score": float(score),
                "stock_count": len(sector_signals.get(sw_code, [])),
            })

        top_5 = [r["sw_code"] for r in rankings[:5]]
        bottom_5 = [r["sw_code"] for r in rankings[-5:]]

        stats = {
            "total_sectors": len(aggregated),
            "total_stocks": sum(len(s) for s in sector_signals.values()),
            "mean_score": float(np.mean(list(aggregated.values()))),
            "std_score": float(np.std(list(aggregated.values()))),
            "positive_ratio": float(np.mean([s > 0 for s in aggregated.values()])),
        }

        return SectorAggregationResult(
            date=date or pd.Timestamp.now().strftime("%Y-%m-%d"),
            method=method.value,
            sector_rankings=rankings,
            top_5_sectors=top_5,
            bottom_5_sectors=bottom_5,
            stats=stats,
        )

    def compare_methods(
        self,
        stock_signals: Dict[str, float],
        stock_market_caps: Optional[Dict[str, float]] = None,
    ) -> Dict[str, SectorAggregationResult]:
        """对比所有聚合方法"""
        results = {}
        for method in AggregationMethod:
            results[method.value] = self.aggregate(
                stock_signals, method, stock_market_caps
            )
        return results

    def build_baseline_timeseries(
        self,
        historical_signals: Dict[str, Dict[str, float]],  # {date: {symbol: score}}
        method: AggregationMethod = AggregationMethod.MEAN,
    ) -> pd.DataFrame:
        """构建历史基准线时间序列

        Args:
            historical_signals: {date: {symbol: alpha_score}}
            method: 聚合方法

        Returns:
            DataFrame: date × sector_code, values = aggregated scores
        """
        all_dates = {}
        for date, signals in historical_signals.items():
            result = self.aggregate(signals, method, date=date)
            date_scores = {
                r["sw_code"]: r["aggregated_score"]
                for r in result.sector_rankings
            }
            all_dates[date] = date_scores

        return pd.DataFrame(all_dates).T.sort_index()

    # ── 内部方法 ──────────────────────────────────────────────

    def _load_sector_map(self) -> Dict[str, str]:
        """加载个股→板块映射"""
        # 尝试从 AgentAlpha 的 sector-map.json 加载
        fm_trade_map = Path("D:/AgentAlpha/my-bot/data/sector-map.json")
        if fm_trade_map.exists():
            import json
            with open(fm_trade_map, encoding="utf-8") as f:
                raw_map = json.load(f)

            # 将精细化板块映射到申万一级行业
            # sector-map.json 格式: "688981.SH": "半导体-晶圆代工"
            # 需要映射到 SW 代码
            return self._map_fine_to_sw(raw_map)

        # Fallback：返回空映射
        return {}

    def _map_fine_to_sw(self, fine_map: Dict[str, str]) -> Dict[str, str]:
        """将精细化板块映射到申万一级行业"""
        # 精细化板块 → 申万一级行业映射
        category_to_sw = {
            "半导体": "801080",  # 电子
            "电子": "801080",
            "计算机": "801750",
            "通信": "801770",
            "传媒": "801760",
            "医药": "801150",
            "食品饮料": "801120",
            "银行": "801780",
            "非银金融": "801790",
            "房地产": "801180",
            "建筑": "801720",
            "建材": "801710",
            "化工": "801030",
            "钢铁": "801040",
            "有色金属": "801050",
            "煤炭": "801020",
            "石油": "801950",
            "电力": "801160",
            "新能源": "801960",
            "汽车": "801880",
            "机械": "801890",
            "军工": "801740",
            "家电": "801110",
            "纺织": "801130",
            "轻工": "801140",
            "商贸": "801200",
            "休闲": "801210",
            "交通": "801170",
            "环保": "801970",
            "农林": "801010",
        }

        result = {}
        for symbol, fine_sector in fine_map.items():
            for category, sw_code in category_to_sw.items():
                if category in fine_sector:
                    result[symbol] = sw_code
                    break

        return result

    def _map_stock_to_sector(self, symbol: str) -> Optional[str]:
        """个股代码 → 申万行业代码"""
        # 尝试直接匹配
        if symbol in self.sector_map:
            return self.sector_map[symbol]

        # 尝试格式转换：sh600316 → 600316.SH, sz002371 → 002371.SZ
        if symbol.startswith("sh"):
            wcode = symbol[2:] + ".SH"
        elif symbol.startswith("sz"):
            wcode = symbol[2:] + ".SZ"
        elif symbol.startswith("SH"):
            wcode = symbol[2:] + ".SH"
        elif symbol.startswith("SZ"):
            wcode = symbol[2:] + ".SZ"
        else:
            wcode = symbol

        if wcode in self.sector_map:
            return self.sector_map[wcode]

        # 从科创板代码推断（688xxx → 电子）
        if symbol.startswith("sh688") or symbol.startswith("688"):
            return "801080"  # 电子

        return None


# ── 模块级单例 ──────────────────────────────────────────────────

_aggregator: Optional[SectorAggregator] = None


def get_sector_aggregator() -> SectorAggregator:
    global _aggregator
    if _aggregator is None:
        _aggregator = SectorAggregator()
    return _aggregator