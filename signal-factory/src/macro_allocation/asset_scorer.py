# SPDX-License-Identifier: MIT
# asset_scorer.py --- Layer 0 七大类资产多因子评分引擎
#
# 对 7 类资产进行六维因子评分，产出 0-100 的综合得分与配置建议。
#
# 六维因子（默认权重）：
#   动量 25%          — 1M/3M/6M 收益率，标准化到 0-100
#   趋势 20%          — 价格 vs MA20/MA60/MA120 比率，标准化到 0-100
#   宏观匹配 25%       — 当前宏观象限下该资产的历史偏好排名
#   估值/利差 15%      — 资产专属估值指标（债券收益率水位、黄金三区制评分★v2.0、A股 ERP 等）
#   波动/相关性 10%    — 近期波动率（越低越好）+ 分散化收益
#   资金流向 5%        — 量价关系代理指标
#
# 资产类别特定的权重覆盖：
#   overseas_equity  — momentum 30%, trend 20%, macro_match 20%, valuation 10%, volatility 15%, fund_flow 5%
#   commodity        — momentum 25%, trend 25%, macro_match 20%, valuation 15%, volatility 10%, fund_flow 5%
#   bond             — momentum 15%, trend 15%, macro_match 25%, valuation 25%, volatility 10%, fund_flow 10%
#   domestic_equity  — 使用默认权重
#   cash             — momentum 0%, trend 0%, macro_match 30%, valuation 40%, volatility 20%, fund_flow 10%
#
# 建议分级：
#   >70  超配 (overweight)
#   40-70 中性 (neutral)
#   20-40 低配 (underweight)
#   <20  回避 (avoid)

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 11 类资产定义（与 macro_data.ASSET_UNIVERSE 保持同步）────────────

ASSET_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "oil":     {"name": "石油",     "etf": "162411", "category": "commodity"},
    "gold":    {"name": "黄金",     "etf": "518880", "category": "commodity"},
    "bond":    {"name": "债券",     "etf": "511010", "category": "bond"},
    "nasdaq":  {"name": "纳指",     "etf": "513100", "category": "overseas_equity"},
    "hk_tech": {"name": "恒生科技", "etf": "513130", "category": "overseas_equity"},
    "a_share": {"name": "A股",      "etf": "159919", "category": "domestic_equity"},
    "cash":    {"name": "现金/回购", "etf": "GC001",  "category": "cash"},
    # v1.2 新增
    "star_50": {"name": "科创50",   "etf": "588000", "category": "domestic_equity"},
    "nikkei":  {"name": "日经225",  "etf": "513880", "category": "overseas_equity"},
    "india":   {"name": "印度SENSEX","etf": "164824","category": "overseas_equity"},
    "cb_bond": {"name": "可转债",   "etf": "511380", "category": "domestic_hybrid"},
}

# ── 默认因子权重 ────────────────────────────────────────────────────

DEFAULT_WEIGHTS: Dict[str, float] = {
    "momentum":    0.25,
    "trend":       0.20,
    "macro_match": 0.25,
    "valuation":   0.15,
    "volatility":  0.10,
    "fund_flow":   0.05,
}

# ── 资产类别权重覆盖 ────────────────────────────────────────────────

CATEGORY_WEIGHT_OVERRIDES: Dict[str, Dict[str, float]] = {
    "overseas_equity": {
        "momentum": 0.30, "trend": 0.20, "macro_match": 0.20,
        "valuation": 0.10, "volatility": 0.15, "fund_flow": 0.05,
    },
    "commodity": {
        "momentum": 0.25, "trend": 0.25, "macro_match": 0.20,
        "valuation": 0.15, "volatility": 0.10, "fund_flow": 0.05,
    },
    "bond": {
        "momentum": 0.15, "trend": 0.15, "macro_match": 0.25,
        "valuation": 0.25, "volatility": 0.10, "fund_flow": 0.10,
    },
    "domestic_equity": {
        "momentum": 0.25, "trend": 0.20, "macro_match": 0.25,
        "valuation": 0.15, "volatility": 0.10, "fund_flow": 0.05,
    },
    "domestic_hybrid": {
        # 可转债：股性+债性混合，momentum介于equity和bond之间
        "momentum": 0.20, "trend": 0.20, "macro_match": 0.25,
        "valuation": 0.15, "volatility": 0.10, "fund_flow": 0.10,
    },
    "cash": {
        "momentum": 0.00, "trend": 0.00, "macro_match": 0.30,
        "valuation": 0.40, "volatility": 0.20, "fund_flow": 0.10,
    },
}

# 向后兼容（保留原 SCORING_WEIGHTS）
SCORING_WEIGHTS = DEFAULT_WEIGHTS

# 建议阈值
RECOMMENDATION_THRESHOLDS = [
    (70, "超配", "overweight"),
    (40, "中性", "neutral"),
    (20, "低配", "underweight"),
    (0,  "回避", "avoid"),
]


# ── 数据类 ──────────────────────────────────────────────────────────

@dataclass
class FactorScore:
    """单因子评分明细"""
    name: str           # 因子名称（momentum / trend / macro_match / valuation / volatility / fund_flow）
    weight: float       # 该因子在总评分中的权重
    raw_value: float    # 原始计算值（未标准化）
    score: float        # 标准化后的得分 0-100
    contribution: float  # 加权贡献 = weight * score


@dataclass
class AssetScore:
    """单个资产的完整评分"""
    asset: str                          # 资产键（oil/gold/bond/nasdaq/hk_tech/a_share/cash）
    asset_name: str                     # 中文名称
    category: str                       # 资产类别
    total_score: float                  # 综合得分 0-100
    factor_scores: List[FactorScore]    # 各因子得分明细
    recommendation: str                 # 配置建议：超配/中性/低配/回避


@dataclass
class AssetScoreResult:
    """一次评分周期的完整结果"""
    date: str                           # 评分日期 (YYYY-MM-DD)
    scores: Dict[str, AssetScore]       # asset_key -> AssetScore
    ranking: List[str]                  # 按总分降序的 asset_key 列表
    summary: str                        # 中文摘要


# 向后兼容（保留原 ScoreBreakdown）
@dataclass
class ScoreBreakdown:
    """单个资产的评分明细（向后兼容）"""
    asset_id: str
    total_score: float
    momentum_score: float = 50.0
    trend_score: float = 50.0
    macro_match_score: float = 50.0
    valuation_score: float = 50.0
    vol_corr_score: float = 50.0
    fund_flow_score: float = 50.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "total_score": self.total_score,
            "momentum": self.momentum_score,
            "trend": self.trend_score,
            "macro_match": self.macro_match_score,
            "valuation": self.valuation_score,
            "vol_correlation": self.vol_corr_score,
            "fund_flow": self.fund_flow_score,
        }


# ═══════════════════════════════════════════════════════════════════════
# AssetScorer
# ═══════════════════════════════════════════════════════════════════════


class AssetScorer:
    """多因子资产评分器。

    对 7 类资产（石油、黄金、债券、纳指、恒生科技、A股、现金/回购）
    进行六维因子评分，返回 0-100 的综合得分与配置建议。

    因子权重可通过构造参数或 YAML 配置文件覆盖。
    资产类别（commodity / bond / overseas_equity / domestic_equity / cash）
    有独立的权重覆盖表，在评分时自动匹配。

    Parameters
    ----------
    config_path : str or Path, optional
        指向 macro_allocation.yaml 的路径。若提供则从中读取 scoring.* 配置。
    weights : dict, optional
        直接指定的因子权重，优先级高于 YAML 配置。
    """

    ASSETS: Dict[str, Dict[str, str]] = ASSET_DEFINITIONS

    DEFAULT_WEIGHTS: Dict[str, float] = DEFAULT_WEIGHTS

    CATEGORY_WEIGHT_OVERRIDES: Dict[str, Dict[str, float]] = CATEGORY_WEIGHT_OVERRIDES

    def __init__(
        self,
        config_path: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None,
        collector: Any = None,          # 向后兼容：MacroDataCollector
        classifier: Any = None,         # 向后兼容：RegimeClassifier
    ):
        self.config_path = Path(config_path) if config_path else None
        self._collector = collector     # 保留但不使用（新 API 不再内部加载数据）
        self._classifier = classifier

        # 加载配置中的权重
        config_weights: Dict[str, float] = {}
        if self.config_path and self.config_path.exists():
            config_weights = self._load_weights_from_config()

        # 优先级：直接传入 > YAML 配置 > 模块默认
        if weights is not None:
            self.weights = dict(weights)
        elif config_weights:
            self.weights = config_weights
        else:
            self.weights = dict(DEFAULT_WEIGHTS)

        # 从 YAML 加载类别覆盖（可选）
        self.category_overrides = dict(CATEGORY_WEIGHT_OVERRIDES)
        if self.config_path and self.config_path.exists():
            self._merge_category_overrides_from_config()

    # ── 配置加载 ──────────────────────────────────────────────────

    def _load_weights_from_config(self) -> Dict[str, float]:
        """从 YAML 配置文件读取 scoring.weights 段。"""
        try:
            import yaml
        except ImportError:
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            scoring = cfg.get("scoring", {})
            w = scoring.get("weights", {})
            if isinstance(w, dict) and w:
                return {k: float(v) for k, v in w.items()
                        if k in DEFAULT_WEIGHTS}
        except Exception:
            pass
        return {}

    def _merge_category_overrides_from_config(self) -> None:
        """从 YAML 配置文件合并类别权重覆盖（如果定义了 scoring.category_overrides）。"""
        try:
            import yaml
        except ImportError:
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            overrides = cfg.get("scoring", {}).get("category_overrides", {})
            if isinstance(overrides, dict):
                for cat, wmap in overrides.items():
                    if isinstance(wmap, dict):
                        existing = self.category_overrides.setdefault(cat, {})
                        existing.update({k: float(v) for k, v in wmap.items()
                                         if k in DEFAULT_WEIGHTS})
        except Exception:
            pass

    def _get_weights_for_category(self, category: str) -> Dict[str, float]:
        """获取某资产类别的有效权重映射，若未覆盖则返回默认权重。"""
        override = self.category_overrides.get(category)
        if override is not None:
            merged = dict(self.weights)
            merged.update(override)
            return merged
        return dict(self.weights)

    # ── 主动观点阈值 ──────────────────────────────────────────────

    def _load_active_view_thresholds(self) -> Dict[str, float]:
        """从 YAML 读取主动观点阈值（overweight / neutral / underweight / zero）。"""
        try:
            import yaml
        except ImportError:
            return {}
        if not self.config_path or not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("scoring", {}).get("active_views", {})
        except Exception:
            return {}

    # ══════════════════════════════════════════════════════════════
    # 主评分入口
    # ══════════════════════════════════════════════════════════════

    def score_all(
        self,
        etf_prices: Dict[str, pd.DataFrame],
        indicators: Optional[Dict[str, Any]] = None,
        regime_result: Optional[Any] = None,
        current_date: Optional[str] = None,
    ) -> AssetScoreResult:
        """对所有 7 类资产执行六维因子评分。

        Parameters
        ----------
        etf_prices : dict
            asset_key → pd.DataFrame，每支 ETF 必须包含 'close' 列。
            对 cash 键可为空 DataFrame（使用 GC001 代理逻辑）。
        indicators : dict, optional
            补充指标（如 bond_yield, gold_real_rate, erp 等）。
            可为 None，此时估值因子退化为价格分位。
        regime_result : tuple(MacroRegime, RegimeScores) or None
            macro_regime.classify() 的返回值。
            若为 None 则宏观匹配因子全部给中性分 50。
        current_date : str, optional
            评分日期 (YYYY-MM-DD)，默认今天。

        Returns
        -------
        AssetScoreResult
            包含各资产评分明细、排名及中文摘要。
        """
        if current_date is None:
            current_date = datetime.now().strftime("%Y-%m-%d")

        indicators = indicators or {}

        # 展开 regime_result
        regime_info: Dict[str, int] = {}
        if regime_result is not None:
            # regime_result 可能是 (MacroRegime, RegimeScores) 元组
            if isinstance(regime_result, (tuple, list)) and len(regime_result) >= 1:
                regime_obj = regime_result[0]
                if hasattr(regime_obj, "value"):
                    from src.macro_allocation.macro_regime import REGIME_MAP
                    regime_info = REGIME_MAP.get(regime_obj, {})
            elif hasattr(regime_result, "value"):
                from src.macro_allocation.macro_regime import REGIME_MAP
                regime_info = REGIME_MAP.get(regime_result, {})

        scores: Dict[str, AssetScore] = {}

        print(f"[AssetScorer] 开始评分 {len(self.ASSETS)} 类资产 (date={current_date})")

        for asset_key in self.ASSETS:
            asset_def = self.ASSETS[asset_key]
            asset_name = asset_def["name"]
            category = asset_def["category"]

            prices = etf_prices.get(asset_key)
            all_prices = {k: v for k, v in etf_prices.items() if k != asset_key}

            factor_scores: List[FactorScore] = []

            # 获取该类别的权重
            cat_weights = self._get_weights_for_category(category)

            # 逐因子评分
            for factor_name in ["momentum", "trend", "macro_match", "valuation", "volatility", "fund_flow"]:
                factor_weight = cat_weights.get(factor_name, 0.0)

                if factor_weight <= 0 and asset_key != "cash":
                    # 零权重因子仍记录占位
                    factor_scores.append(FactorScore(
                        name=factor_name, weight=0.0, raw_value=0.0,
                        score=50.0, contribution=0.0,
                    ))
                    continue

                method = getattr(self, f"_score_{factor_name}", None)
                if method is None:
                    factor_scores.append(FactorScore(
                        name=factor_name, weight=factor_weight, raw_value=0.0,
                        score=50.0, contribution=50.0 * factor_weight,
                    ))
                    continue

                try:
                    raw, score = method(asset_key, prices, category, indicators,
                                        regime_info, all_prices)
                except Exception as e:
                    print(f"  WARN {asset_key}/{factor_name}: {e}")
                    raw, score = 0.0, 50.0

                contribution = score * factor_weight
                factor_scores.append(FactorScore(
                    name=factor_name,
                    weight=factor_weight,
                    raw_value=round(raw, 4),
                    score=round(score, 2),
                    contribution=round(contribution, 2),
                ))

            # 综合得分
            total = round(sum(fs.contribution for fs in factor_scores), 2)
            recommendation = self._get_recommendation(total)

            scores[asset_key] = AssetScore(
                asset=asset_key,
                asset_name=asset_name,
                category=category,
                total_score=total,
                factor_scores=factor_scores,
                recommendation=recommendation,
            )

            print(f"  {asset_key:8s} ({asset_name}): {total:6.1f} → {recommendation}")

        # 排名
        ranking = sorted(scores.keys(), key=lambda k: scores[k].total_score, reverse=True)

        # 摘要
        top3 = [(k, scores[k].asset_name, scores[k].total_score) for k in ranking[:3]]
        bottom3 = [(k, scores[k].asset_name, scores[k].total_score) for k in ranking[-3:]]
        summary_parts = [
            f"评分日期 {current_date}，共 7 类资产。",
            f"最优：{' > '.join(f'{n}({s:.0f})' for _, n, s in top3)}",
            f"最弱：{' > '.join(f'{n}({s:.0f})' for _, n, s in reversed(bottom3))}",
        ]
        summary = " ".join(summary_parts)

        print(f"[AssetScorer] 评分完成。最优: {top3[0][1]}({top3[0][2]:.0f})")

        return AssetScoreResult(
            date=current_date,
            scores=scores,
            ranking=ranking,
            summary=summary,
        )

    # ══════════════════════════════════════════════════════════════
    # 因子评分方法
    # ══════════════════════════════════════════════════════════════

    def _score_momentum(
        self,
        asset_key: str,
        prices: Optional[pd.DataFrame],
        category: str,
        indicators: Dict[str, Any],
        regime_info: Dict[str, int],
        all_prices: Dict[str, pd.DataFrame],
    ) -> Tuple[float, float]:
        """动量因子：1M/3M/6M 收益率加权，标准化到 0-100。

        现金返回中性 50。
        overseas_equity 对 6M 动量给更高权重（0.4），domestic 对 1M 给更高权重（0.4）。
        """
        if asset_key == "cash" or prices is None or prices.empty:
            return 0.0, 50.0

        if "close" not in prices.columns or len(prices) < 21:
            return 0.0, 50.0

        closes = prices["close"].values
        current = closes[-1]

        ret_1m = self._safe_return(closes, current, -21)
        ret_3m = self._safe_return(closes, current, -63)
        ret_6m = self._safe_return(closes, current, -126)

        # 权重根据类别调整
        if category == "overseas_equity":
            w1, w3, w6 = 0.30, 0.30, 0.40
        elif category == "domestic_equity":
            w1, w3, w6 = 0.40, 0.35, 0.25
        elif category == "domestic_hybrid":
            w1, w3, w6 = 0.35, 0.35, 0.30  # 可转债：介于equity和bond之间
        elif category == "bond":
            w1, w3, w6 = 0.50, 0.30, 0.20
        else:  # commodity
            w1, w3, w6 = 0.40, 0.35, 0.25

        composite = w1 * ret_1m + w3 * ret_3m + w6 * ret_6m

        # 映射到 0-100：假设 ±20% 为极端
        score = self._normalize_score(composite, -20.0, 20.0, invert=False)
        return round(composite, 4), round(score, 2)

    def _score_trend(
        self,
        asset_key: str,
        prices: Optional[pd.DataFrame],
        category: str,
        indicators: Dict[str, Any],
        regime_info: Dict[str, int],
        all_prices: Dict[str, pd.DataFrame],
    ) -> Tuple[float, float]:
        """趋势因子：价格 vs MA20/MA60/MA120 与均线排列。

        价格在均线之上的比例 + 均线多头排列 + 趋势强度 = 0-100。
        bond 类降低均线权重（债券趋势性弱）。
        """
        if asset_key == "cash" or prices is None or prices.empty:
            return 0.0, 50.0

        if "close" not in prices.columns or len(prices) < 60:
            return 0.0, 50.0

        closes = prices["close"].values
        current = closes[-1]

        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else current
        ma60 = float(np.mean(closes[-60:])) if len(closes) >= 60 else current
        ma120 = float(np.mean(closes[-120:])) if len(closes) >= 120 else current

        # 价格在均线之上
        above_20 = 1.0 if current > ma20 else 0.0
        above_60 = 1.0 if current > ma60 else 0.0
        above_120 = 1.0 if current > ma120 else 0.0

        # 均线多头排列 (MA20 > MA60 > MA120)
        if ma20 > ma60 > ma120:
            alignment = 1.0
        elif ma20 > ma60:
            alignment = 0.5
        else:
            alignment = 0.0

        # 趋势强度 (价格偏离 MA20 的程度，最大 10%)
        dev = min(abs(current / max(ma20, 1e-9) - 1.0) * 100.0, 10.0) / 10.0

        if category == "bond":
            trend_raw = above_20 * 60.0 + alignment * 20.0 + dev * 20.0
        else:
            trend_raw = above_20 * 50.0 + alignment * 30.0 + dev * 20.0

        score = min(max(trend_raw, 0.0), 100.0)
        return round(trend_raw, 4), round(score, 2)

    def _score_macro_match(
        self,
        asset_key: str,
        prices: Optional[pd.DataFrame],
        category: str,
        indicators: Dict[str, Any],
        regime_info: Dict[str, int],
        all_prices: Dict[str, pd.DataFrame],
    ) -> Tuple[float, float]:
        """宏观匹配因子：当前宏观象限下该资产的历史偏好排名。

        regime_info: {asset_key: rank}，rank 1=最优, 5=最差。
        无数据时返回中性 50。
        cash 永远中性 50。
        """
        if asset_key == "cash":
            return 50.0, 50.0

        rank = regime_info.get(asset_key, 3)
        rank_to_score = {1: 90.0, 2: 70.0, 3: 50.0, 4: 30.0, 5: 10.0}
        score = rank_to_score.get(rank, 50.0)
        return float(rank), round(score, 2)

    def _score_valuation(
        self,
        asset_key: str,
        prices: Optional[pd.DataFrame],
        category: str,
        indicators: Dict[str, Any],
        regime_info: Dict[str, int],
        all_prices: Dict[str, pd.DataFrame],
    ) -> Tuple[float, float]:
        """估值/利差因子：资产专属估值指标。

        - bond: 若 indicators 提供 bond_yield，则用收益率水位（高收益率=好买点）。
          无数据时用价格分位代理。
        - gold: ★v2.0 三区制评分（财政风险 / 货币紧缩 / 正常）。
          使用 US 10Y/2Y 收益率判断利率区制，整合 ETF 成交量动量。
          详见 _score_gold_valuation()。
          无数据时用 CN 10Y 代理或价格分位 × 0.7 回退。
        - a_share: 若 indicators 提供 erp（股权风险溢价），高 ERP → 高得分。
          无数据时用价格分位分段。
        - 权益/石油: 价格分位。
        - cash: GC001 利率越高越好（现金持有收益高），默认 50。
        """
        if asset_key == "cash":
            # GC001 利率：从 indicators 或默认 50
            gc001_rate = float(indicators.get("gc001_rate", indicators.get("SHIBOR_ON", 1.5)))
            # 高利率 = 现金持有收益高，映射到 0-100 (假设 0%-5%)
            raw = gc001_rate
            score = self._normalize_score(gc001_rate, 0.0, 5.0, invert=False)
            return raw, round(score, 2)

        if prices is None or prices.empty or "close" not in prices.columns:
            return 0.0, 50.0

        closes = prices["close"].values
        current = closes[-1]

        if asset_key == "bond":
            bond_yield = indicators.get("bond_yield", indicators.get("10Y国债收益率"))
            if bond_yield is not None and not (isinstance(bond_yield, float) and np.isnan(bond_yield)):
                # 高收益率 → 高得分（好买点），假设 2%-5% 区间
                raw = float(bond_yield)
                score = self._normalize_score(raw, 2.0, 5.0, invert=False)
                return raw, round(score, 2)
            # 回退：价格分位（低价格=高收益率=好买点）
            lookback = min(len(closes), 1260)
            historical = closes[-lookback:]
            pct = float((historical < current).mean() * 100.0)
            score = 100.0 - pct
            return pct, round(score, 2)

        elif asset_key == "gold":
            raw, score = self._score_gold_valuation(prices, indicators)
            return raw, score

        elif asset_key == "a_share":
            erp = indicators.get("erp")
            if erp is None or (isinstance(erp, float) and np.isnan(erp)):
                # ERP not explicitly provided → derive from available indicators
                pe_ttm = indicators.get("沪深300PE_TTM")
                cn_10y = indicators.get("bond_yield", indicators.get("10Y国债收益率"))
                if pe_ttm is not None and cn_10y is not None:
                    # ERP = Earnings Yield - 10Y Bond Yield
                    # Earnings Yield ≈ 100 / PE_TTM (%)
                    earnings_yield = 100.0 / float(pe_ttm)
                    erp = earnings_yield - float(cn_10y)
            if erp is not None and not (isinstance(erp, float) and np.isnan(erp)):
                # 高 ERP → 高得分，假设 0%-10%
                raw = float(erp)
                score = self._normalize_score(raw, 0.0, 10.0, invert=False)
                return raw, round(score, 2)
            # 回退：价格分位分段
            lookback = min(len(closes), 1260)
            historical = closes[-lookback:]
            pct = float((historical < current).mean() * 100.0)
            if pct < 20:
                score = 90.0
            elif pct < 40:
                score = 70.0
            elif pct < 60:
                score = 50.0
            elif pct < 80:
                score = 30.0
            else:
                score = 10.0
            return pct, round(score, 2)

        else:
            # 石油 / 纳指 / 恒生科技：价格分位
            lookback = min(len(closes), 1260)
            historical = closes[-lookback:]
            pct = float((historical < current).mean() * 100.0)
            score = 100.0 - pct
            return pct, round(score, 2)

    def _score_gold_valuation(
        self,
        prices: Optional[pd.DataFrame],
        indicators: Dict[str, Any],
    ) -> Tuple[float, float]:
        """Gold valuation with fiscal-risk-aware regime detection.

        Three regimes based on US Treasury yields:
          FISCAL_RISK  — US 10Y > 4.5% AND term spread > 0
                          (fiscal/debt concerns driving yields → gold bullish)
          TIGHTENING   — US 10Y > 3.5% AND term spread ≤ 0
                          (monetary tightening dominant → traditional gold-negative)
          NORMAL       — US 10Y ≤ 3.5%
                          (rate normalization → neutral-to-positive for gold)

        Degradation path:
          1. Full: us_10y + us_2y available → three-regime scoring
          2. Partial: only us_10y available → simplified (no spread check)
          3. CN proxy: only CN 10Y available → high-rate proxy + price percentile
          4. Fallback: nothing available → price percentile × 0.7 (legacy)

        Also adds ETF volume momentum bonus (+5 or +10) when volume data is available.
        """
        # ── Gather US yield indicators ──
        us_10y = indicators.get("us_10y_yield")
        us_2y = indicators.get("us_2y_yield")
        cn_10y = indicators.get("10Y国债收益率")

        if us_10y is not None and not (isinstance(us_10y, float) and np.isnan(us_10y)):
            us_10y = float(us_10y)
        else:
            us_10y = None

        if us_2y is not None and not (isinstance(us_2y, float) and np.isnan(us_2y)):
            us_2y = float(us_2y)
        else:
            us_2y = None

        # ── Determine regime and base score ──
        regime: Optional[str] = None
        raw: float = 0.0
        score: float = 50.0

        if us_10y is not None and us_2y is not None:
            # ── Full three-regime scoring ──
            term_spread = us_10y - us_2y
            real_rate_proxy = us_10y - 2.5  # crude: nominal - assumed LT inflation

            if us_10y > 4.5 and term_spread > 0:
                # FISCAL_RISK: high yields driven by fiscal/debt concerns
                # Gold benefits as non-sovereign safe haven
                regime = "FISCAL_RISK"
                # Score starts at 85, decays as real rate rises further
                # At real_rate_proxy=1.0 (nom 3.5): 85
                # At real_rate_proxy=2.5 (nom 5.0): 70
                # At real_rate_proxy=4.0 (nom 6.5): 55
                score = 85.0 - max(0.0, real_rate_proxy - 1.0) * 10.0
                score = max(55.0, min(85.0, score))
                raw = real_rate_proxy

            elif us_10y > 3.5 and term_spread <= 0:
                # TIGHTENING: inverted curve = monetary tightening dominant
                regime = "TIGHTENING"
                # Traditional: higher real rate → lower gold
                # But dampened vs old framework (central bank buying provides floor)
                score = 80.0 - max(0.0, real_rate_proxy - 0.5) * 15.0
                score = max(15.0, min(80.0, score))
                raw = real_rate_proxy

            else:
                # NORMAL: rates not at extreme levels
                regime = "NORMAL"
                score = 75.0 - max(0.0, real_rate_proxy) * 12.0
                score = max(25.0, min(85.0, score))
                raw = real_rate_proxy

        elif us_10y is not None:
            # ── Simplified: only US 10Y, no 2Y for spread check ──
            real_rate_proxy = us_10y - 2.5

            if us_10y > 4.5:
                # Assume fiscal risk (no spread data to confirm)
                regime = "FISCAL_RISK (no spread)"
                score = 85.0 - max(0.0, real_rate_proxy - 1.0) * 10.0
                score = max(55.0, min(85.0, score))
            elif us_10y > 3.5:
                regime = "TIGHTENING (no spread)"
                score = 80.0 - max(0.0, real_rate_proxy - 0.5) * 15.0
                score = max(15.0, min(80.0, score))
            else:
                regime = "NORMAL (no spread)"
                score = 75.0 - max(0.0, real_rate_proxy) * 12.0
                score = max(25.0, min(85.0, score))
            raw = real_rate_proxy

        elif cn_10y is not None:
            # ── CN 10Y proxy: high CN rates → global tightening context ──
            cn_10y_val = float(cn_10y)
            raw = cn_10y_val
            if cn_10y_val > 3.0:
                # High CN rates → fiscal/tightening pressure → dampened negative for gold
                score = 70.0 - (cn_10y_val - 3.0) * 15.0
                score = max(35.0, min(80.0, score))
                regime = f"CN_PROXY (CN 10Y={cn_10y_val:.2f}%)"
            else:
                score = 60.0
                regime = "CN_PROXY (normal)"

        else:
            # ── Ultimate fallback: price percentile (legacy behavior) ──
            if prices is not None and not prices.empty and "close" in prices.columns:
                closes = prices["close"].values
                lookback = min(len(closes), 1260)
                historical = closes[-lookback:]
                current = closes[-1]
                pct = float((historical < current).mean() * 100.0)
                score = 100.0 - pct * 0.7
                raw = pct
                regime = "FALLBACK (price percentile)"
            else:
                raw = 0.0
                score = 50.0
                regime = "FALLBACK (no data)"

        # ── ETF volume momentum bonus ──
        volume_bonus = 0.0
        if prices is not None and not prices.empty and "volume" in prices.columns and len(prices) >= 20:
            volume = prices["volume"].values
            if len(volume) >= 20:
                recent_vol = float(np.nanmean(volume[-5:]))
                avg_vol_20 = float(np.nanmean(volume[-20:]))
                if avg_vol_20 > 0:
                    vol_ratio = recent_vol / avg_vol_20
                    if vol_ratio > 1.5:
                        volume_bonus = 10.0
                    elif vol_ratio > 1.2:
                        volume_bonus = 5.0

        score = min(95.0, max(10.0, score + volume_bonus))

        return round(raw, 4), round(score, 2)

    def _score_volatility(
        self,
        asset_key: str,
        prices: Optional[pd.DataFrame],
        category: str,
        indicators: Dict[str, Any],
        regime_info: Dict[str, int],
        all_prices: Dict[str, pd.DataFrame],
    ) -> Tuple[float, float]:
        """波动/相关性因子：低波动 + 低相关 = 高分。

        波动率：近 63 个交易日年化波动率，越低越好。
        相关性：与其他资产的平均绝对相关性，越低越好（分散化收益）。
        cash 基于 GC001 利率稳定性（低波动 = 高得分）。
        """
        if asset_key == "cash":
            gc001_rate = float(indicators.get("gc001_rate", indicators.get("SHIBOR_ON", 1.5)))
            # 现金波动几乎为 0，给高波动得分
            vol_score = 90.0
            corr_score = 100.0  # 现金与其他资产几无相关
            score = vol_score * 0.5 + corr_score * 0.5
            return gc001_rate, round(score, 2)

        if prices is None or prices.empty or "close" not in prices.columns or len(prices) < 63:
            return 0.0, 50.0

        closes = prices["close"].values

        adj_closes = closes[-64:] if len(closes) >= 64 else closes
        returns = np.diff(adj_closes) / np.maximum(np.abs(adj_closes[:-1]), 1e-12)
        volatility = float(np.nanstd(returns) * np.sqrt(252) * 100.0)

        # 波动率越低越好
        vol_score = max(10.0, min(80.0, 80.0 - (volatility - 10.0) * (60.0 / 30.0)))

        # 分散化收益：与其他资产的平均绝对相关性
        corr_scores: List[float] = []
        for other_key, other_df in all_prices.items():
            if other_key == "cash" or other_df is None or other_df.empty:
                continue
            if "close" not in other_df.columns or len(other_df) < 63:
                continue
            other_closes = other_df["close"].values
            other_adj = other_closes[-64:] if len(other_closes) >= 64 else other_closes
            other_returns = np.diff(other_adj) / np.maximum(np.abs(other_adj[:-1]), 1e-12)
            if len(returns) != len(other_returns):
                # align to same length
                min_len = min(len(returns), len(other_returns))
                if min_len < 10:
                    continue
                returns_aligned = returns[-min_len:]
                other_aligned = other_returns[-min_len:]
            else:
                returns_aligned = returns
                other_aligned = other_returns
            try:
                corr_matrix = np.corrcoef(returns_aligned, other_aligned)
                if corr_matrix.shape == (2, 2):
                    corr = float(corr_matrix[0, 1])
                    if not np.isnan(corr):
                        corr_scores.append(abs(corr))
            except Exception:
                pass

        if corr_scores:
            avg_corr = float(np.nanmean(corr_scores))
            corr_score = max(10.0, (1.0 - avg_corr) * 100.0)
        else:
            corr_score = 50.0

        raw = volatility
        score = vol_score * 0.5 + corr_score * 0.5
        return raw, round(score, 2)

    def _score_fund_flow(
        self,
        asset_key: str,
        prices: Optional[pd.DataFrame],
        category: str,
        indicators: Dict[str, Any],
        regime_info: Dict[str, int],
        all_prices: Dict[str, pd.DataFrame],
    ) -> Tuple[float, float]:
        """资金流向因子：量价关系代理（成交量变化）。

        近 5 日均量 vs 近 20 日均量比。
        >1.5 显著放量 → 80, >1.2 → 65, >0.8 → 50, >0.5 → 35, <0.5 → 20。
        cash 永远 50。
        """
        if asset_key == "cash":
            return 0.0, 50.0

        if prices is None or prices.empty or "volume" not in prices.columns or len(prices) < 20:
            return 0.0, 50.0

        volume = prices["volume"].values
        if len(volume) < 20:
            return 0.0, 50.0

        recent_vol = float(np.nanmean(volume[-5:]))
        avg_vol_20 = float(np.nanmean(volume[-20:]))

        if avg_vol_20 <= 0:
            return 0.0, 50.0

        ratio = recent_vol / avg_vol_20

        if ratio > 1.5:
            score = 80.0
        elif ratio > 1.2:
            score = 65.0
        elif ratio > 0.8:
            score = 50.0
        elif ratio > 0.5:
            score = 35.0
        else:
            score = 20.0

        return round(ratio, 4), round(score, 2)

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def _safe_return(
        closes: np.ndarray,
        current: float,
        offset: int,
    ) -> float:
        """安全计算收益率：从 offset 位置到当前的百分比收益。"""
        idx = max(0, len(closes) + offset)
        if idx >= len(closes) or idx < 0:
            return 0.0
        base = closes[idx]
        if base <= 0:
            return 0.0
        return float((current / base - 1.0) * 100.0)

    @staticmethod
    def _normalize_score(
        value: float,
        min_val: float,
        max_val: float,
        invert: bool = False,
    ) -> float:
        """将原始值映射到 0-100 区间。

        Parameters
        ----------
        value : float
            待标准化的原始值。
        min_val : float
            区间下限（低于此值钳制到 0）。
        max_val : float
            区间上限（高于此值钳制到 100）。
        invert : bool
            若为 True，则高值 → 低分（如波动率）。

        Returns
        -------
        float
            0-100 之间的标准化得分。
        """
        if np.isnan(value) or np.isinf(value):
            return 50.0
        if max_val <= min_val:
            return 50.0
        normalized = (value - min_val) / (max_val - min_val) * 100.0
        score = min(max(normalized, 0.0), 100.0)
        if invert:
            score = 100.0 - score
        return float(score)

    @staticmethod
    def _get_recommendation(score: float) -> str:
        """根据综合得分返回配置建议（中文）。

        >70 → 超配, 40-70 → 中性, 20-40 → 低配, <20 → 回避
        """
        for threshold, label_en, _label_cn in RECOMMENDATION_THRESHOLDS:
            if score >= threshold:
                return label_en
        return "回避"

    # ══════════════════════════════════════════════════════════════
    # 向后兼容 API
    # ══════════════════════════════════════════════════════════════

    def score_all_compat(
        self,
        date: Optional[str] = None,
        regime: Any = None,
        regime_scores: Any = None,
    ) -> List[ScoreBreakdown]:
        """向后兼容：内部加载数据后调用 score_all，返回 ScoreBreakdown 列表。

        当通过旧的调用方式 (score_all(date=..., regime=...)) 时触发。
        """
        if self._collector is None:
            try:
                from src.macro_allocation.macro_data import get_macro_collector
                self._collector = get_macro_collector()
            except Exception:
                pass

        if self._classifier is None:
            try:
                from src.macro_allocation.macro_regime import get_regime_classifier
                self._classifier = get_regime_classifier()
            except Exception:
                pass

        # 加载 ETF 价格数据
        etf_prices: Dict[str, pd.DataFrame] = {}
        if self._collector is not None:
            cache = getattr(self._collector, 'load_all_cached_data', lambda: {})()
            etf_prices = cache.get("etf_data", {})

        # 运行新评分
        current_date = date or datetime.now().strftime("%Y-%m-%d")
        result = self.score_all(
            etf_prices=etf_prices,
            indicators={},
            regime_result=(regime, regime_scores) if regime is not None else None,
            current_date=current_date,
        )

        # 转换为 ScoreBreakdown 列表
        breakdowns: List[ScoreBreakdown] = []
        for asset_key in result.ranking:
            as_ = result.scores[asset_key]
            fs_map = {fs.name: fs.score for fs in as_.factor_scores}
            breakdowns.append(ScoreBreakdown(
                asset_id=asset_key,
                total_score=as_.total_score,
                momentum_score=fs_map.get("momentum", 50.0),
                trend_score=fs_map.get("trend", 50.0),
                macro_match_score=fs_map.get("macro_match", 50.0),
                valuation_score=fs_map.get("valuation", 50.0),
                vol_corr_score=fs_map.get("volatility", 50.0),
                fund_flow_score=fs_map.get("fund_flow", 50.0),
            ))
        return breakdowns

    def get_score_summary(
        self, scores: List[ScoreBreakdown]
    ) -> Dict[str, Any]:
        """生成评分摘要（向后兼容）。"""
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "assets": [s.to_dict() for s in scores],
            "top_3": [s.asset_id for s in scores[:3]],
            "bottom_3": [s.asset_id for s in scores[-3:]],
            "overweight": [s.asset_id for s in scores if s.total_score > 60],
            "underweight": [s.asset_id for s in scores if s.total_score < 40],
            "neutral": [s.asset_id for s in scores if 40 <= s.total_score <= 60],
        }


# ── 模块级单例 ──────────────────────────────────────────────────────

_scorer: Optional[AssetScorer] = None


def get_asset_scorer(
    config_path: Optional[str] = None,
) -> AssetScorer:
    """获取或创建模块级 AssetScorer 单例。

    Parameters
    ----------
    config_path : str, optional
        配置文件路径，仅在首次创建时生效。
    """
    global _scorer
    if _scorer is None:
        if config_path is None:
            config_path = "d:/AgentAlpha/signal-factory/config/macro_allocation.yaml"
        _scorer = AssetScorer(config_path=config_path)
    return _scorer