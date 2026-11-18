# SPDX-License-Identifier: MIT
# portfolio_builder.py --- Layer 0 组合构建器
#
# 方法：简化风险平价基础 + 主动观点叠加 + 约束裁剪
#
# 流程：
#   1. 计算简化风险平价基础权重（weight ∝ 1/vol, 再归一化到 sum=1）
#   2. 根据资产评分叠加主动观点（>60 超配 +50%, <40 低配 -50%, <20 清仓）
#   3. 应用模式相关约束（单资产上下限、现金下限、类别上限、换手上限）
#   4. 迭代裁剪 + 归一化 + 检查直到收敛
#
# 模式：
#   - NORMAL:          标准约束
#   - RISK_OFF:        防御（A股上限 15%, 债券下限 25%, 黄金下限 15%, 现金下限 20%）
#   - EARNINGS_SEASON: 财报季（预留，当前与 NORMAL 相同）
#
# 约束（通过 macro_allocation.yaml 可配置）：
#   normal: min_per_asset=0.05, max_per_asset=0.35, min_cash=0.10,
#           max_turnover=0.30, max_commodity_total=0.30, max_overseas_total=0.40
#   risk_off: a_share_max=0.15, bond_min=0.25, gold_min=0.15,
#             cash_min=0.20, max_turnover=0.20

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ── 枚举 ──────────────────────────────────────────────────────────

class AllocationMode(Enum):
    """组合构建模式"""
    NORMAL = "normal"
    RISK_OFF = "risk_off"
    EARNINGS_SEASON = "earnings_season"

    @property
    def label(self) -> str:
        labels = {
            AllocationMode.NORMAL: "正常",
            AllocationMode.RISK_OFF: "防御",
            AllocationMode.EARNINGS_SEASON: "财报季",
        }
        return labels.get(self, str(self.value))


# ── 数据类 ────────────────────────────────────────────────────────

@dataclass
class AssetWeight:
    """单个资产的配置权重明细"""
    asset: str              # 资产键
    asset_name: str         # 中文名称
    category: str           # 资产类别
    risk_parity_weight: float    # 纯风险平价权重
    active_adjustment: float     # 主动观点调整量
    target_weight: float         # 最终目标权重
    current_weight: float        # 当前持仓权重（若无则为 0）
    drift: float                 # 目标 vs 当前偏离
    action: str                  # 操作：增持/减持/持有


@dataclass
class RebalancePlan:
    """一次调仓计划的完整结果"""
    date: str                       # 调仓日期
    mode: AllocationMode            # 模式
    mode_label: str                 # 模式中文标签
    weights: Dict[str, AssetWeight] # asset_key -> AssetWeight
    total_drift: float              # 总偏离 (sum |drift| / 2)
    needs_rebalance: bool           # 是否需要调仓
    suggestions: List[str]          # 调仓建议（中文）
    constraints_check: Dict[str, bool]  # 约束校验结果
    summary: str                    # 中文摘要

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "date": self.date,
            "mode": self.mode.value,
            "mode_label": self.mode_label,
            "weights": {
                k: {
                    "asset": v.asset,
                    "asset_name": v.asset_name,
                    "category": v.category,
                    "risk_parity_weight": round(v.risk_parity_weight, 4),
                    "active_adjustment": round(v.active_adjustment, 4),
                    "target_weight": round(v.target_weight, 4),
                    "current_weight": round(v.current_weight, 4),
                    "drift": round(v.drift, 4),
                    "action": v.action,
                }
                for k, v in self.weights.items()
            },
            "total_drift": round(self.total_drift, 4),
            "needs_rebalance": self.needs_rebalance,
            "suggestions": self.suggestions,
            "constraints_check": self.constraints_check,
            "summary": self.summary,
        }


# ═══════════════════════════════════════════════════════════════════
# 默认约束
# ═══════════════════════════════════════════════════════════════════

CONSTRAINTS_NORMAL: Dict[str, Any] = {
    "min_per_asset": 0.03,       # 降低下限，允许更灵活配置
    "max_per_asset": 0.35,
    "min_cash": 0.05,            # 降低现金下限（原 0.10）
    "max_bond": 0.30,            # ★新增：债券上限 30%
    "max_turnover": 0.30,
    "max_commodity_total": 0.30,
    "max_overseas_total": 0.50,  # v1.2: 从 0.40 提高到 0.50（新增日经+印度）
}

CONSTRAINTS_RISK_OFF: Dict[str, Any] = {
    "min_per_asset": 0.05,
    "max_per_asset": 0.35,
    "min_cash": 0.20,
    "max_turnover": 0.20,
    "max_commodity_total": 0.30,
    "max_overseas_total": 0.40,
    "a_share_max": 0.15,
    "bond_min": 0.25,
    "gold_min": 0.15,
}

CONSTRAINTS_EARNINGS_SEASON: Dict[str, Any] = dict(CONSTRAINTS_NORMAL)

MODE_CONSTRAINTS: Dict[AllocationMode, Dict[str, Any]] = {
    AllocationMode.NORMAL: CONSTRAINTS_NORMAL,
    AllocationMode.RISK_OFF: CONSTRAINTS_RISK_OFF,
    AllocationMode.EARNINGS_SEASON: CONSTRAINTS_EARNINGS_SEASON,
}

# 别名
CONSTRAINT_MAP = MODE_CONSTRAINTS

# 11 类资产键
ASSET_KEYS = ["oil", "gold", "bond", "nasdaq", "hk_tech", "a_share", "cash",
              "star_50", "nikkei", "india", "cb_bond"]

# 资产中文名
ASSET_NAMES: Dict[str, str] = {
    "oil": "石油",
    "gold": "黄金",
    "bond": "债券",
    "nasdaq": "纳指",
    "hk_tech": "恒生科技",
    "a_share": "A股",
    "cash": "现金/回购",
    "star_50": "科创50",
    "nikkei": "日经225",
    "india": "印度SENSEX",
    "cb_bond": "可转债",
}

# 资产类别
ASSET_CATEGORIES: Dict[str, str] = {
    "oil": "commodity",
    "gold": "commodity",
    "bond": "bond",
    "nasdaq": "overseas_equity",
    "hk_tech": "overseas_equity",
    "a_share": "domestic_equity",
    "cash": "cash",
    "star_50": "domestic_equity",
    "nikkei": "overseas_equity",
    "india": "overseas_equity",
    "cb_bond": "domestic_hybrid",
}

# 默认波动率假设（年化 %），用于计算风险平价（无价格数据时回退）
DEFAULT_VOL: Dict[str, float] = {
    "oil": 35.0,
    "gold": 18.0,
    "bond": 4.0,
    "nasdaq": 22.0,
    "hk_tech": 30.0,
    "a_share": 22.0,
    "cash": 0.5,
    "star_50": 28.0,
    "nikkei": 20.0,
    "india": 24.0,
    "cb_bond": 14.0,
}

# 主动观点调整系数
ACTIVE_OVERWEIGHT_MULT = 1.50    # 超配 +50%
ACTIVE_UNDERWEIGHT_MULT = 0.50   # 低配 -50%
ACTIVE_ZERO_THRESHOLD = 20.0     # 评分 < 20 → 清仓
ACTIVE_OVERWEIGHT_THRESHOLD = 60.0  # 评分 > 60 → 超配
ACTIVE_NEUTRAL_MIN = 40.0        # 40-60 → 中性

# 调仓触发阈值：总偏离超过此值需要调仓
REBALANCE_THRESHOLD = 0.10


# ═══════════════════════════════════════════════════════════════════
# PortfolioBuilder
# ═══════════════════════════════════════════════════════════════════


class PortfolioBuilder:
    """组合构建器。

    三步流程：
    1. compute_risk_parity()   — 简化风险平价（weight ∝ 1/vol）
    2. apply_active_views()    — 根据评分叠加主动观点
    3. apply_constraints()      — 迭代裁剪 + 归一化 + 边界检查

    Parameters
    ----------
    config_path : str or Path, optional
        指向 macro_allocation.yaml 的路径。若提供则从中读取 portfolio.* 配置。
    scorer : object, optional
        向后兼容的 AssetScorer 实例（新 API 不依赖 scorer）。
    constraints : object, optional
        向后兼容的 AllocationConstraints 实例。
    """

    ASSETS: List[str] = ASSET_KEYS
    CONSTRAINTS: Dict[str, Any] = CONSTRAINTS_NORMAL
    CATEGORIES: Dict[str, str] = ASSET_CATEGORIES

    def __init__(
        self,
        config_path: Optional[str] = None,
        scorer: Any = None,            # 向后兼容
        constraints: Any = None,       # 向后兼容
    ):
        self.config_path = Path(config_path) if config_path else None
        self._scorer = scorer          # 保留但不使用（新 API 不内部加载评分）
        self._legacy_constraints = constraints  # 向后兼容

        # 从 YAML 加载组合约束
        self._mode_constraints: Dict[AllocationMode, Dict[str, Any]] = {}
        for mode, defaults in MODE_CONSTRAINTS.items():
            self._mode_constraints[mode] = dict(defaults)

        if self.config_path and self.config_path.exists():
            self._load_constraints_from_config()

        # 上一期权重（用于换手率计算）
        self._prev_weights: Optional[Dict[str, float]] = None

    # ── 配置加载 ──────────────────────────────────────────────────

    def _load_constraints_from_config(self) -> None:
        """从 YAML 配置文件读取 portfolio.* 约束并合并。"""
        try:
            import yaml
        except ImportError:
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            return

        portfolio = cfg.get("portfolio", {})
        if not isinstance(portfolio, dict):
            return

        # 通用约束映射
        key_map = {
            "min_weight": "min_per_asset",
            "max_weight": "max_per_asset",
            "min_cash": "min_cash",
            "max_turnover": "max_turnover",
            "max_commodity_total": "max_commodity_total",
            "max_overseas_total": "max_overseas_total",
        }
        for yaml_key, attr_key in key_map.items():
            if yaml_key in portfolio:
                val = float(portfolio[yaml_key])
                for mode_dict in self._mode_constraints.values():
                    mode_dict[attr_key] = val

        # risk_off 覆盖
        risk_off = portfolio.get("risk_off", {})
        if isinstance(risk_off, dict):
            ro_map = {
                "a_share_max": "a_share_max",
                "bond_min": "bond_min",
                "gold_min": "gold_min",
                "cash_min": "min_cash",
            }
            for yk, ak in ro_map.items():
                if yk in risk_off:
                    val = float(risk_off[yk])
                    self._mode_constraints.setdefault(
                        AllocationMode.RISK_OFF, {}
                    )[ak] = val
            # >risk_off 也有 max_turnover
            if "max_turnover" in risk_off:
                self._mode_constraints.setdefault(
                    AllocationMode.RISK_OFF, {}
                )["max_turnover"] = float(risk_off["max_turnover"])

    def _get_constraints(self, mode: AllocationMode) -> Dict[str, Any]:
        """获取指定模式的约束字典。"""
        return self._mode_constraints.get(mode, CONSTRAINTS_NORMAL)

    # ══════════════════════════════════════════════════════════════
    # 1. 风险平价
    # ══════════════════════════════════════════════════════════════

    def compute_risk_parity(
        self,
        etf_prices: Dict[str, pd.DataFrame],
        lookback: int = 60,
    ) -> Dict[str, float]:
        """计算简化风险平价基础权重。

        方法：每类资产权重 ∝ 1/年化波动率，再归一化到 sum=1。
        若有足够的收盘价数据（>= lookback 日），从数据中计算实际波动率；
        否则回退到默认波动率假设。

        cash 基于 GC001 利率稳定性（低波动 = 高权重）。

        Parameters
        ----------
        etf_prices : dict
            asset_key → pd.DataFrame，必须包含 'close' 列。
        lookback : int
            计算波动率的回看窗口（交易日数），默认 60。

        Returns
        -------
        dict
            asset_key → 风险平价权重 (0-1)。
        """
        print(f"[PortfolioBuilder] 计算风险平价权重 (lookback={lookback})...")

        inv_vol: Dict[str, float] = {}

        for asset_key in ASSET_KEYS:
            df = etf_prices.get(asset_key)

            if asset_key == "cash":
                # 现金：等效年化波动 5%（限制风险平价中的现金权重）
                inv_vol[asset_key] = 1.0 / 5.0
                continue

            if df is None or df.empty or "close" not in df.columns or len(df) < lookback:
                # 回退到默认波动率
                vol = DEFAULT_VOL.get(asset_key, 20.0)
                inv_vol[asset_key] = 1.0 / vol
                print(f"  {asset_key}: 无价格数据，使用默认波动率 {vol}%")
                continue

            closes = df["close"].values
            window = min(len(closes), lookback)
            if window < 2:
                vol = DEFAULT_VOL.get(asset_key, 20.0)
                inv_vol[asset_key] = 1.0 / vol
                continue

            returns = np.diff(closes[-window:]) / np.maximum(
                np.abs(closes[-window:-1]), 1e-12
            )
            annual_vol = float(np.nanstd(returns) * np.sqrt(252) * 100.0)

            if np.isnan(annual_vol) or annual_vol <= 0:
                annual_vol = DEFAULT_VOL.get(asset_key, 20.0)

            inv_vol[asset_key] = 1.0 / max(annual_vol, 4.0)  # 波动率最低 4%（防债券主导，真实 ETF 约 3-5%）
            print(f"  {asset_key}: 年化波动率 {annual_vol:.1f}%, inv_vol={inv_vol[asset_key]:.4f}")

        total_inv = sum(inv_vol.values())
        if total_inv <= 0:
            # 完全回退
            return {k: 1.0 / len(ASSET_KEYS) for k in ASSET_KEYS}

        rp_weights = {k: v / total_inv for k, v in inv_vol.items()}

        # 现金风险平价权重上限：若超过 40% 则裁剪并重新分配给非现金资产
        MAX_CASH_RP = 0.25
        if rp_weights.get("cash", 0.0) > MAX_CASH_RP:
            excess = rp_weights["cash"] - MAX_CASH_RP
            rp_weights["cash"] = MAX_CASH_RP
            non_cash_keys = [k for k in ASSET_KEYS if k != "cash" and k in rp_weights]
            non_cash_total = sum(rp_weights.get(k, 0.0) for k in non_cash_keys)
            if non_cash_total > 0:
                for k in non_cash_keys:
                    rp_weights[k] += excess * (rp_weights[k] / non_cash_total)
            # 重新归一化
            total2 = sum(rp_weights.values())
            rp_weights = {k: v / total2 for k, v in rp_weights.items()}

        print(f"  风险平价完成，权重范围 {min(rp_weights.values()):.1%} ~ {max(rp_weights.values()):.1%}")
        return rp_weights

    # ══════════════════════════════════════════════════════════════
    # 2. 主动观点叠加
    # ══════════════════════════════════════════════════════════════

    def apply_active_views(
        self,
        risk_parity_weights: Dict[str, float],
        asset_scores: Dict[str, Any],
        mode: AllocationMode = AllocationMode.NORMAL,
    ) -> Dict[str, float]:
        """根据资产评分叠加主动观点。

        评分来源可以是 AssetScoreResult.scores（包含 .total_score 属性）
        或向后兼容的 ScoreBreakdown（包含 .total_score 属性）。

        规则：
        - score > 60      → 超配 +50%  (risk_parity_weight * 1.5)
        - score 40-60     → 中性        (risk_parity_weight)
        - score 20-40     → 低配 -50%  (risk_parity_weight * 0.5)
        - score < 20      → 清仓        (0.0)

        cash 例外：在 risk_off 模式下不减少（由约束层保证最低现金）。

        Parameters
        ----------
        risk_parity_weights : dict
            asset_key → 风险平价权重。
        asset_scores : dict
            asset_key → 评分对象（必须有 .total_score 属性），或 asset_key → float。
        mode : AllocationMode
            构建模式，risk_off 下 cash 不会被清零。

        Returns
        -------
        dict
            asset_key → 主动调整后的权重。
        """
        print(f"[PortfolioBuilder] 叠加主动观点 (mode={mode.value})...")

        active_weights: Dict[str, float] = {}

        for asset_key in ASSET_KEYS:
            rp_w = risk_parity_weights.get(asset_key, 0.0)

            if asset_key not in asset_scores:
                active_weights[asset_key] = rp_w
                print(f"  {asset_key}: 无评分数据，保持风险平价权重")
                continue

            score_obj = asset_scores[asset_key]
            if hasattr(score_obj, "total_score"):
                score = float(score_obj.total_score)
            else:
                score = float(score_obj)

            if score > ACTIVE_OVERWEIGHT_THRESHOLD:
                active_weights[asset_key] = rp_w * ACTIVE_OVERWEIGHT_MULT
                print(f"  {asset_key}: score={score:.1f} → 超配 ({rp_w:.2%} → {active_weights[asset_key]:.2%})")
            elif score >= ACTIVE_NEUTRAL_MIN:
                active_weights[asset_key] = rp_w
                print(f"  {asset_key}: score={score:.1f} → 中性 ({rp_w:.2%})")
            elif score >= ACTIVE_ZERO_THRESHOLD:
                active_weights[asset_key] = rp_w * ACTIVE_UNDERWEIGHT_MULT
                print(f"  {asset_key}: score={score:.1f} → 低配 ({rp_w:.2%} → {active_weights[asset_key]:.2%})")
            else:
                # cash 在 risk_off 下不能被清零
                if asset_key == "cash" and mode == AllocationMode.RISK_OFF:
                    active_weights[asset_key] = rp_w
                    print(f"  {asset_key}: score={score:.1f} → 应清仓但 risk_off 保留 ({rp_w:.2%})")
                else:
                    active_weights[asset_key] = 0.0
                    print(f"  {asset_key}: score={score:.1f} → 清仓 (0%)")

        return active_weights

    # ══════════════════════════════════════════════════════════════
    # 3. 约束应用
    # ══════════════════════════════════════════════════════════════

    def apply_constraints(
        self,
        weights: Dict[str, float],
        mode: AllocationMode = AllocationMode.NORMAL,
        current_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """应用模式相关约束，迭代裁剪 + 归一化直到收敛。

        约束链（按优先级）：
        1. 单资产上下限 (min_per_asset, max_per_asset)
        2. 现金下限 (min_cash) — 不减少现金
        3. 类别上限 (max_commodity_total, max_overseas_total)
        4. 模式特定覆盖 (risk_off: a_share_max, bond_min, gold_min, cash_min)
        5. 换手上限 (max_turnover) — 若提供了 current_weights
        6. 归一化到 sum=1

        采用迭代方式：clip → normalize → check → 最多 10 轮直到所有约束满足。

        Parameters
        ----------
        weights : dict
            待约束的权重。
        mode : AllocationMode
            构建模式。
        current_weights : dict, optional
            当前持仓权重，用于限制单次换手率。

        Returns
        -------
        dict
            约束后的权重（sum=1）。
        """
        constraints = self._get_constraints(mode)
        w = dict(weights)

        print(f"[PortfolioBuilder] 应用约束 (mode={mode.value})...")

        for iteration in range(10):
            violations: List[str] = []

            # 1. 单资产上下限（现金豁免 max_per_asset）
            for asset_key in ASSET_KEYS:
                if asset_key not in w:
                    w[asset_key] = constraints.get("min_per_asset", 0.03)
                w[asset_key] = max(w[asset_key], constraints.get("min_per_asset", 0.03))
                if asset_key != "cash":
                    w[asset_key] = min(w[asset_key], constraints.get("max_per_asset", 0.35))

            # 1b. 债券上限（NORMAL 模式默认 max_bond=30%）
            max_bond = constraints.get("max_bond")
            if max_bond is not None and w.get("bond", 0.0) > max_bond:
                excess = w["bond"] - max_bond
                w["bond"] = max_bond
                other_keys = [k for k in w if k not in ("bond", "cash")]
                other_total = sum(w.get(k, 0.0) for k in other_keys)
                if other_total > 0:
                    for k in other_keys:
                        w[k] = w.get(k, 0.0) + excess * (w[k] / other_total)
                violations.append(f"债券超限 (>{max_bond:.0%})")

            # 2. 现金下限（不减少）
            min_cash = constraints.get("min_cash", 0.05)
            if "cash" in w:
                w["cash"] = max(w["cash"], min_cash)

            # 3. 模式特定覆盖
            if mode == AllocationMode.RISK_OFF:
                if "a_share" in w:
                    max_a = constraints.get("a_share_max", 0.15)
                    if w["a_share"] > max_a:
                        w["a_share"] = max_a
                        violations.append(f"A股超限 (>{max_a:.0%})")
                if "bond" in w:
                    min_b = constraints.get("bond_min", 0.25)
                    if w["bond"] < min_b:
                        # 通过等比例缩减其他资产腾出空间
                        deficit = min_b - w["bond"]
                        other_keys = [k for k in w if k != "bond" and k != "cash"]
                        other_total = sum(w.get(k, 0.0) for k in other_keys)
                        if other_total > deficit:
                            scale = (other_total - deficit) / other_total
                            for k in other_keys:
                                w[k] = w.get(k, 0.0) * scale
                            w["bond"] = min_b
                            violations.append(f"债券不足 (<{min_b:.0%})")
                if "gold" in w:
                    min_g = constraints.get("gold_min", 0.15)
                    if w["gold"] < min_g:
                        deficit = min_g - w["gold"]
                        other_keys = [k for k in w if k != "gold" and k != "cash" and k != "bond"]
                        other_total = sum(w.get(k, 0.0) for k in other_keys)
                        if other_total > deficit:
                            scale = (other_total - deficit) / other_total
                            for k in other_keys:
                                w[k] = w.get(k, 0.0) * scale
                            w["gold"] = min_g
                            violations.append(f"黄金不足 (<{min_g:.0%})")
                # risk_off 更新 cash 下限
                cash_min_ro = constraints.get("min_cash", 0.20)
                if w.get("cash", 0.0) < cash_min_ro:
                    deficit = cash_min_ro - w.get("cash", 0.0)
                    other_keys = [k for k in w if k != "cash" and k != "bond" and k != "gold"]
                    other_total = sum(w.get(k, 0.0) for k in other_keys)
                    if other_total > deficit:
                        scale = (other_total - deficit) / other_total
                        for k in other_keys:
                            w[k] = w.get(k, 0.0) * scale
                        w["cash"] = cash_min_ro
                        violations.append(f"现金不足 (<{cash_min_ro:.0%})")

            # 4. 类别上限
            max_commodity = constraints.get("max_commodity_total", 0.30)
            commodity_total = w.get("oil", 0.0) + w.get("gold", 0.0)
            if commodity_total > max_commodity and commodity_total > 0:
                scale = max_commodity / commodity_total
                w["oil"] = w.get("oil", 0.0) * scale
                w["gold"] = w.get("gold", 0.0) * scale
                violations.append(f"商品总额超限 ({commodity_total:.1%} > {max_commodity:.1%})")

            max_overseas = constraints.get("max_overseas_total", 0.40)
            overseas_total = sum(
                w.get(k, 0.0) for k, cat in ASSET_CATEGORIES.items()
                if cat == "overseas_equity"
            )
            if overseas_total > max_overseas and overseas_total > 0:
                scale = max_overseas / overseas_total
                for k, cat in ASSET_CATEGORIES.items():
                    if cat == "overseas_equity":
                        w[k] = w.get(k, 0.0) * scale
                violations.append(f"海外总额超限 ({overseas_total:.1%} > {max_overseas:.1%})")

            # 5. 换手上限
            if current_weights is not None:
                max_turnover = constraints.get("max_turnover", 0.30)
                turnover = sum(abs(w.get(k, 0.0) - current_weights.get(k, 0.0))
                              for k in ASSET_KEYS) / 2.0
                if turnover > max_turnover and turnover > 0:
                    scale = max_turnover / turnover
                    for k in ASSET_KEYS:
                        cw = current_weights.get(k, 0.0)
                        w[k] = cw + (w.get(k, 0.0) - cw) * scale
                    violations.append(f"换手率超限 ({turnover:.1%} > {max_turnover:.1%})")

            # 6. 归一化
            total = sum(w.values())
            if total <= 0:
                n = len(w)
                w = {k: 1.0 / n for k in w}
            else:
                w = {k: v / total for k, v in w.items()}

            # risk_off 下，债券/黄金/现金最小约束必须在归一化后重新检查
            # 采用"截断+重归一化"的迭代方式以确保收敛
            if mode == AllocationMode.RISK_OFF:
                for _ in range(3):
                    changed = False
                    min_cash = constraints.get("min_cash", 0.20)
                    min_bond = constraints.get("bond_min", 0.25)
                    min_gold = constraints.get("gold_min", 0.15)

                    # 截断到最小值
                    for asset_key, min_w in [("cash", min_cash), ("bond", min_bond), ("gold", min_gold)]:
                        if w.get(asset_key, 0.0) < min_w - 0.001:
                            w[asset_key] = min_w
                            changed = True

                    # 同时确保非锁定资产不低于 min_per_asset
                    min_pa = constraints.get("min_per_asset", 0.05)
                    unlocked_keys_rp = [k for k in w if k not in ("cash", "bond", "gold")]
                    for k in unlocked_keys_rp:
                        if w.get(k, 0.0) < min_pa - 0.001:
                            w[k] = min_pa
                            changed = True

                    if not changed:
                        break

                    # 重归一化：优先缩减现金以给其他资产腾空间
                    locked = {"bond": min_bond, "gold": min_gold}
                    for k in unlocked_keys_rp:
                        locked[k] = min_pa

                    all_keys = list(w.keys())
                    locked_total = sum(w.get(k, 0.0) for k in locked if k in w)
                    if locked_total > 1.0:
                        # 约束过于苛刻，放宽现金
                        excess = locked_total - 1.0
                        w["cash"] = max(0.02, w.get("cash", 0.0) - excess)
                        locked_total = sum(w.get(k, 0.0) for k in locked if k in w)

                    remaining = max(0.0, 1.0 - locked_total)
                    # 剩余空间分配给债券、黄金、现金 + 其他未锁定资产
                    unlocked_all = {k for k in all_keys if k not in locked}
                    unlocked_total = sum(w.get(k, 0.0) for k in unlocked_all)
                    if unlocked_total > 0 and remaining > 0:
                        scale = remaining / unlocked_total
                        for k in unlocked_all:
                            w[k] = w.get(k, 0.0) * scale
                    elif remaining <= 0:
                        for k in unlocked_all:
                            w[k] = 0.0

            # 确保现金下限（轻量检查）
            w["cash"] = max(w.get("cash", 0.0), constraints.get("min_cash", 0.10))

            if not violations:
                print(f"  约束满足 (第{iteration + 1}轮)")
                break
            else:
                print(f"  第{iteration + 1}轮修复: {'; '.join(violations)}")
        else:
            print("  警告: 约束未能在 10 轮内完全收敛，使用最后一轮结果")

        # 最终归一化
        total = sum(w.values())
        if total > 0:
            w = {k: round(v / total, 6) for k, v in w.items()}
        else:
            w = {k: 1.0 / len(ASSET_KEYS) for k in ASSET_KEYS}

        print(f"  约束后权重: {', '.join(f'{k}={w.get(k, 0):.1%}' for k in ASSET_KEYS)}")
        return w

    # ══════════════════════════════════════════════════════════════
    # 4. 构建完整调仓计划
    # ══════════════════════════════════════════════════════════════

    def build_plan(
        self,
        etf_prices: Dict[str, pd.DataFrame],
        asset_scores: Dict[str, Any],
        mode: AllocationMode = AllocationMode.NORMAL,
        current_weights: Optional[Dict[str, float]] = None,
        current_date: Optional[str] = None,
    ) -> RebalancePlan:
        """构建完整调仓计划。

        整合 risk_parity → active_views → constraints 三步，
        产出包含权重明细、偏离度、调仓建议的完整计划。

        Parameters
        ----------
        verbose: 是否打印详细日志。回测时建议 False。
        etf_prices : dict
            asset_key → pd.DataFrame（必须有 'close' 列）。
        asset_scores : dict
            asset_key → 评分对象（有 .total_score 属性）。
        mode : AllocationMode
            构建模式。
        current_weights : dict, optional
            当前持仓权重，用于计算偏离和换手率上限。
        current_date : str, optional
            调仓日期 (YYYY-MM-DD)，默认今天。

        Returns
        -------
        RebalancePlan
        """
        if current_date is None:
            current_date = datetime.now().strftime("%Y-%m-%d")

        print(f"\n[PortfolioBuilder] 构建调仓计划 date={current_date} mode={mode.label}")

        # 1. 风险平价
        rp_weights = self.compute_risk_parity(etf_prices)

        # 2. 主动观点
        active_weights = self.apply_active_views(rp_weights, asset_scores, mode)

        # 3. 约束
        final_weights = self.apply_constraints(active_weights, mode, current_weights)

        # 构建 AssetWeight 列表
        current_w = current_weights or {k: 0.0 for k in ASSET_KEYS}
        weights_detail: Dict[str, AssetWeight] = {}

        for asset_key in ASSET_KEYS:
            rp_w = rp_weights.get(asset_key, 0.0)
            fw = final_weights.get(asset_key, 0.0)
            cw = current_w.get(asset_key, 0.0)
            adj = fw - rp_w
            drift = fw - cw

            # 判定操作
            if abs(drift) < 0.01:
                action = "持有"
            elif drift > 0:
                action = "增持"
            else:
                action = "减持"

            weights_detail[asset_key] = AssetWeight(
                asset=asset_key,
                asset_name=ASSET_NAMES.get(asset_key, asset_key),
                category=ASSET_CATEGORIES.get(asset_key, "unknown"),
                risk_parity_weight=round(rp_w, 6),
                active_adjustment=round(adj, 6),
                target_weight=round(fw, 6),
                current_weight=round(cw, 6),
                drift=round(drift, 6),
                action=action,
            )

        total_drift = sum(abs(v.drift) for v in weights_detail.values()) / 2.0
        needs_rebalance = total_drift > REBALANCE_THRESHOLD

        # 约束校验
        check = self._check_constraints(final_weights, mode)

        # 调仓建议
        suggestions = self._generate_suggestions(
            weights_detail, mode, total_drift, needs_rebalance
        )

        # 中文摘要
        summary = self._build_summary(weights_detail, mode, total_drift, needs_rebalance)

        self._prev_weights = final_weights

        print(f"[PortfolioBuilder] 计划构建完成。总偏离={total_drift:.1%}, 需调仓={needs_rebalance}\n")

        return RebalancePlan(
            date=current_date,
            mode=mode,
            mode_label=mode.label,
            weights=weights_detail,
            total_drift=round(total_drift, 4),
            needs_rebalance=needs_rebalance,
            suggestions=suggestions,
            constraints_check=check,
            summary=summary,
        )

    # ══════════════════════════════════════════════════════════════
    # 辅助方法
    # ══════════════════════════════════════════════════════════════

    def _check_constraints(
        self,
        weights: Dict[str, float],
        mode: AllocationMode,
    ) -> Dict[str, bool]:
        """校验最终权重是否满足所有约束。"""
        constraints = self._get_constraints(mode)
        checks: Dict[str, bool] = {}

        # 总和
        total = sum(weights.values())
        checks["sum_equals_1"] = abs(total - 1.0) < 0.01

        # 单资产上下限（现金豁免上限）
        min_w = constraints.get("min_per_asset", 0.05)
        max_w = constraints.get("max_per_asset", 0.35)
        checks["all_above_min"] = all(
            v >= min_w - 0.001 for v in weights.values()
        )
        checks["all_below_max"] = all(
            v <= max_w + 0.001 for k, v in weights.items() if k != "cash"
        )

        # 现金下限
        checks["cash_min"] = weights.get("cash", 0.0) >= constraints.get("min_cash", 0.10) - 0.001

        # 类别上限
        commodity_total = weights.get("oil", 0.0) + weights.get("gold", 0.0)
        checks["commodity_cap"] = commodity_total <= constraints.get("max_commodity_total", 0.30) + 0.001

        overseas_total = sum(
            w for k, w in weights.items()
            if ASSET_CATEGORIES.get(k, "") == "overseas_equity"
        )
        checks["overseas_cap"] = overseas_total <= constraints.get("max_overseas_total", 0.40) + 0.001

        # risk_off 特定
        if mode == AllocationMode.RISK_OFF:
            checks["a_share_max"] = weights.get("a_share", 0.0) <= constraints.get("a_share_max", 0.15) + 0.001
            checks["bond_min"] = weights.get("bond", 0.0) >= constraints.get("bond_min", 0.25) - 0.001
            checks["gold_min"] = weights.get("gold", 0.0) >= constraints.get("gold_min", 0.15) - 0.001

        # 债券上限（NORMAL 模式）
        max_bond = constraints.get("max_bond")
        if max_bond is not None:
            checks["bond_max"] = weights.get("bond", 0.0) <= max_bond + 0.001

        return checks

    def _generate_suggestions(
        self,
        weights_detail: Dict[str, AssetWeight],
        mode: AllocationMode,
        total_drift: float,
        needs_rebalance: bool,
    ) -> List[str]:
        """生成中文调仓建议。"""
        suggestions: List[str] = []

        if not needs_rebalance:
            suggestions.append(f"总偏离 {total_drift:.1%}，无需调仓（阈值 {REBALANCE_THRESHOLD:.0%}）。")
            return suggestions

        if mode == AllocationMode.RISK_OFF:
            suggestions.append("当前为防御模式，优先保证安全性。")

        # 增持建议
        overweight = [
            (k, v) for k, v in weights_detail.items()
            if v.action == "增持"
        ]
        overweight.sort(key=lambda x: x[1].drift, reverse=True)
        for k, v in overweight[:3]:
            suggestions.append(f"增持{v.asset_name}({k})：{v.current_weight:.1%} → {v.target_weight:.1%} (变动 +{v.drift:.1%})")

        # 减持建议
        underweight = [
            (k, v) for k, v in weights_detail.items()
            if v.action == "减持"
        ]
        underweight.sort(key=lambda x: x[1].drift)
        for k, v in underweight[:3]:
            suggestions.append(f"减持{v.asset_name}({k})：{v.current_weight:.1%} → {v.target_weight:.1%} (变动 {v.drift:.1%})")

        return suggestions

    def _build_summary(
        self,
        weights_detail: Dict[str, AssetWeight],
        mode: AllocationMode,
        total_drift: float,
        needs_rebalance: bool,
    ) -> str:
        """构建中文摘要。"""
        parts = [
            f"调仓模式：{mode.label}",
            f"总偏离：{total_drift:.1%}",
            f"需调仓：{'是' if needs_rebalance else '否'}",
        ]

        top3 = sorted(weights_detail.values(), key=lambda x: x.target_weight, reverse=True)[:3]
        parts.append("最高权重：" + "、".join(
            f"{v.asset_name} {v.target_weight:.1%}" for v in top3
        ))

        actions = {
            "增持": [],
            "减持": [],
        }
        for v in weights_detail.values():
            if v.action in actions and abs(v.drift) > 0.01:
                actions[v.action].append(f"{v.asset_name}({v.drift:+.1%})")

        if actions["增持"]:
            parts.append("增持：" + ", ".join(actions["增持"]))
        if actions["减持"]:
            parts.append("减持：" + ", ".join(actions["减持"]))

        return " | ".join(parts)

    def to_dict(self, plan: RebalancePlan) -> Dict[str, Any]:
        """将 RebalancePlan 序列化为字典。"""
        return plan.to_dict()

    # ══════════════════════════════════════════════════════════════
    # 回退配置
    # ══════════════════════════════════════════════════════════════

    def get_fallback_allocation(self) -> Dict[str, float]:
        """宏观数据不可用时的默认配置。"""
        return {
            "a_share": 0.30,
            "bond": 0.30,
            "cash": 0.20,
            "gold": 0.10,
            "hk_tech": 0.05,
            "nasdaq": 0.03,
            "oil": 0.02,
        }

    def get_risk_off_fallback(self) -> Dict[str, float]:
        """risk_off 时的默认配置。"""
        return {
            "bond": 0.30,
            "gold": 0.20,
            "cash": 0.25,
            "a_share": 0.15,
            "hk_tech": 0.05,
            "nasdaq": 0.03,
            "oil": 0.02,
        }


# ── 向后兼容数据类及函数 ──────────────────────────────────────────

@dataclass
class AllocationConstraints:
    """约束配置（向后兼容）。"""
    min_per_asset: float = 0.05
    max_per_asset: float = 0.35
    min_cash: float = 0.10
    max_turnover: float = 0.30
    max_commodity_total: float = 0.30
    max_overseas_total: float = 0.40
    risk_off_a_share_max: float = 0.15
    risk_off_bond_min: float = 0.25
    risk_off_gold_min: float = 0.15
    risk_off_cash_min: float = 0.20


DEFAULT_CONSTRAINTS = AllocationConstraints()


@dataclass
class AllocationResult:
    """资产配置结果（向后兼容）。"""
    date: str
    regime: str = ""
    regime_label: str = ""
    weights: Dict[str, float] = field(default_factory=dict)
    scores: List[Any] = field(default_factory=list)
    constraints: AllocationConstraints = field(default_factory=AllocationConstraints)
    risk_parity_weights: Dict[str, float] = field(default_factory=dict)
    active_views: Dict[str, str] = field(default_factory=dict)
    turnover_vs_prev: Optional[float] = None

    @property
    def total_weight(self) -> float:
        return sum(self.weights.values())

    @property
    def equity_weight(self) -> float:
        return sum(
            v for k, v in self.weights.items()
            if ASSET_CATEGORIES.get(k, "") in ("domestic_equity", "overseas_equity")
        )

    @property
    def commodity_weight(self) -> float:
        return sum(
            v for k, v in self.weights.items()
            if ASSET_CATEGORIES.get(k, "") == "commodity"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "regime": self.regime,
            "regime_label": self.regime_label,
            "weights": self.weights,
            "scores": [s.to_dict() if hasattr(s, 'to_dict') else s for s in self.scores],
            "active_views": self.active_views,
            "risk_parity_weights": self.risk_parity_weights,
            "turnover_vs_prev": self.turnover_vs_prev,
            "equity_weight": self.equity_weight,
            "commodity_weight": self.commodity_weight,
        }


# ── 模块级单例 ────────────────────────────────────────────────────

_builder: Optional[PortfolioBuilder] = None


def get_portfolio_builder(
    config_path: Optional[str] = None,
) -> PortfolioBuilder:
    """获取或创建模块级 PortfolioBuilder 单例。

    Parameters
    ----------
    config_path : str, optional
        配置文件路径，仅在首次创建时生效。
    """
    global _builder
    if _builder is None:
        if config_path is None:
            config_path = "d:/AgentAlpha/signal-factory/config/macro_allocation.yaml"
        _builder = PortfolioBuilder(config_path=config_path)
    return _builder