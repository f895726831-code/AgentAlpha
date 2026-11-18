# SPDX-License-Identifier: MIT
# integration.py — STABLE → macro-allocator 集成
#
# 将 STABLE BL 输出转为 risk-policy.json 的 active_views 格式，
# 供 macro-allocator Agent 使用。
#
# 输出格式:
#   {
#     "source": "stable_bl",
#     "regime": "risk_on",
#     "regime_confidence": 0.68,
#     "generated_at": "2026-10-14T16:00:00",
#     "views": [
#       {
#         "type": "relative",
#         "description": "超配 A股+科创50+纳指 vs 债券",
#         "assets_long": ["a_share", "star_50", "nasdaq"],
#         "assets_short": ["bond"],
#         "expected_excess_return": 0.10,
#         "confidence": 0.50
#       }
#     ],
#     "posterior_returns": {
#       "a_share": 0.035, "bond": 0.051, ...
#     },
#     "optimal_weights": {
#       "a_share": 0.02, "bond": 0.30, ...
#     }
#   }

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.macro_allocation.stable_bl import StableBL, BLView, BLResult
from src.macro_allocation.mvo import MVO, MVOConstraints, MVOResult
from src.macro_allocation.conditional_stats import ASSET_ORDER, ASSET_NAMES
from src.macro_allocation.regime_backtest import (
    StableRegimeClassifier, REGIME_LABELS_CN,
)

# ── 配置文件路径 ──
RISK_POLICY_PATH = Path("d:/AgentAlpha/my-bot/data/risk-policy.json")
STABLE_OUTPUT_PATH = Path("d:/AgentAlpha/signal-factory/data/macro_assets/stable_allocation.json")


@dataclass
class StableAllocation:
    """STABLE 完整配置输出"""
    source: str = "stable_bl"
    regime: str = "risk_on"
    regime_label: str = "Risk-On/增长"
    regime_confidence: float = 0.5
    generated_at: str = ""
    views: List[Dict[str, Any]] = field(default_factory=list)
    posterior_returns: Dict[str, float] = field(default_factory=dict)
    posterior_covariance: Dict[str, Dict[str, float]] = field(default_factory=dict)
    optimal_weights: Dict[str, float] = field(default_factory=dict)
    cash_weight: float = 0.05
    expected_portfolio_return: float = 0.0
    expected_portfolio_volatility: float = 0.0
    portfolio_sharpe: float = 0.0
    category_weights: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "regime": self.regime,
            "regime_label": self.regime_label,
            "regime_confidence": self.regime_confidence,
            "generated_at": self.generated_at,
            "views": self.views,
            "posterior_returns": self.posterior_returns,
            "posterior_covariance": self.posterior_covariance,
            "optimal_weights": self.optimal_weights,
            "cash_weight": self.cash_weight,
            "expected_portfolio_return": self.expected_portfolio_return,
            "expected_portfolio_volatility": self.expected_portfolio_volatility,
            "portfolio_sharpe": self.portfolio_sharpe,
            "category_weights": self.category_weights,
            "warnings": self.warnings,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class STABLEIntegration:
    """
    STABLE → macro-allocator 集成器。

    使用:
        integration = STABLEIntegration()
        allocation = integration.generate()
        integration.save(allocation)
    """

    def __init__(self, risk_free_rate: float = 0.02,
                 tau: float = 0.05, shrinkage: float = 0.5):
        self.rf = risk_free_rate
        self.tau = tau
        self.shrinkage = shrinkage
        self._bl = StableBL(tau=tau)
        self._mvo = MVO(risk_free_rate=risk_free_rate)

    def generate(self,
                 current_regime: Optional[str] = None,
                 custom_views: Optional[List[BLView]] = None,
                 save: bool = True,
                 ) -> StableAllocation:
        """
        生成 STABLE 资产配置。

        Args:
            current_regime: 覆盖 regime 检测
            custom_views: 覆盖默认 regime 观点
            save: 是否保存到磁盘

        Returns:
            StableAllocation
        """
        warnings = []

        # ── 第 1 步: 确定 regime ──
        if current_regime is None:
            current_regime = self._bl.get_current_regime()

        regime_label = REGIME_LABELS_CN.get(current_regime, current_regime)

        # ── 第 2 步: BL ──
        views = custom_views or self._bl.generate_default_views(current_regime)
        bl_result = self._bl.run(
            current_regime, views,
            tau=self.tau, shrinkage=self.shrinkage,
        )

        # ── 第 3 步: MVO ──
        is_risk_off = (current_regime == "risk_off")
        constraints = MVOConstraints()

        mvo_result = self._mvo.optimize(
            bl_result.posterior_mean,
            bl_result.posterior_cov,
            constraints=constraints,
            is_risk_off=is_risk_off,
        )

        if not mvo_result.success:
            warnings.append(f"MVO 优化未收敛: {mvo_result.message}")

        # ── 第 4 步: 构建输出 ──
        # 后验收益
        posterior_returns = {}
        for i, asset in enumerate(ASSET_ORDER):
            posterior_returns[asset] = float(bl_result.posterior_mean[i])

        # 后验协方差 (只保存对角线 + 主要相关性)
        posterior_cov = {}
        for i, asset_i in enumerate(ASSET_ORDER):
            posterior_cov[asset_i] = {}
            for j, asset_j in enumerate(ASSET_ORDER):
                if i == j or abs(bl_result.posterior_cov[i, j]) > 0.01:
                    posterior_cov[asset_i][asset_j] = float(
                        bl_result.posterior_cov[i, j]
                    )

        # 观点
        views_json = []
        for v in views:
            views_json.append({
                "type": "relative",
                "description": v.description,
                "assets_long": [a for a, w in zip(v.assets, v.weights) if w > 0],
                "assets_short": [a for a, w in zip(v.assets, v.weights) if w < 0],
                "expected_excess_return": float(v.value),
                "confidence": float(v.confidence),
            })

        allocation = StableAllocation(
            source="stable_bl",
            regime=current_regime,
            regime_label=regime_label,
            regime_confidence=0.5,  # TODO: from classifier
            generated_at=datetime.now().isoformat(),
            views=views_json,
            posterior_returns=posterior_returns,
            posterior_covariance=posterior_cov,
            optimal_weights=mvo_result.asset_weights,
            cash_weight=mvo_result.cash_weight,
            expected_portfolio_return=mvo_result.expected_return,
            expected_portfolio_volatility=mvo_result.expected_volatility,
            portfolio_sharpe=mvo_result.sharpe_ratio,
            category_weights=mvo_result.category_weights,
            warnings=warnings,
        )

        if save:
            self.save(allocation)

        return allocation

    def save(self, allocation: StableAllocation,
             path: Optional[Path] = None):
        """保存配置到磁盘"""
        path = path or STABLE_OUTPUT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(allocation.to_json(), encoding="utf-8")
        print(f"[STABLE] 配置已保存: {path}")

    def load(self, path: Optional[Path] = None) -> Optional[StableAllocation]:
        """从磁盘加载配置"""
        path = path or STABLE_OUTPUT_PATH
        if not path.exists():
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return StableAllocation(
            source=data.get("source", "stable_bl"),
            regime=data.get("regime", "risk_on"),
            regime_label=data.get("regime_label", ""),
            regime_confidence=data.get("regime_confidence", 0.5),
            generated_at=data.get("generated_at", ""),
            views=data.get("views", []),
            posterior_returns=data.get("posterior_returns", {}),
            posterior_covariance=data.get("posterior_covariance", {}),
            optimal_weights=data.get("optimal_weights", {}),
            cash_weight=data.get("cash_weight", 0.05),
            expected_portfolio_return=data.get("expected_portfolio_return", 0.0),
            expected_portfolio_volatility=data.get("expected_portfolio_volatility", 0.0),
            portfolio_sharpe=data.get("portfolio_sharpe", 0.0),
            category_weights=data.get("category_weights", {}),
            warnings=data.get("warnings", []),
        )

    def to_risk_policy_views(self, allocation: StableAllocation) -> Dict[str, Any]:
        """
        将 STABLE 配置转为 risk-policy.json 的 active_views 格式。

        Returns:
            Dict 可直接合并到 risk-policy.json 的 macro_asset_allocation.active_views
        """
        return {
            "source": "stable_bl_v1",
            "generated_at": allocation.generated_at,
            "regime": allocation.regime,
            "regime_label": allocation.regime_label,
            "views": allocation.views,
            "posterior_returns": allocation.posterior_returns,
            "reference_weights": allocation.optimal_weights,
            "cash_weight": allocation.cash_weight,
            "expected_return": allocation.expected_portfolio_return,
            "expected_volatility": allocation.expected_portfolio_volatility,
        }


# ── 便捷函数 ────────────────────────────────────────────────────

def get_integration(**kwargs) -> STABLEIntegration:
    return STABLEIntegration(**kwargs)


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("STABLE Integration — 生成资产配置")
    print("=" * 60)

    integration = STABLEIntegration()
    allocation = integration.generate()

    print(f"\nRegime: {allocation.regime_label}")
    print(f"Sharpe: {allocation.portfolio_sharpe:.2f}")
    print(f"预期收益: {allocation.expected_portfolio_return*100:.1f}%")
    print(f"预期波动: {allocation.expected_portfolio_volatility*100:.1f}%")
    print(f"现金: {allocation.cash_weight*100:.1f}%")

    print(f"\n最优权重:")
    for asset, w in sorted(allocation.optimal_weights.items(),
                            key=lambda x: -x[1]):
        name = ASSET_NAMES.get(asset, asset)
        print(f"  {asset} ({name}): {w*100:.1f}%")

    if allocation.warnings:
        print(f"\n警告:")
        for w in allocation.warnings:
            print(f"  - {w}")

    print(f"\nJSON 输出: {STABLE_OUTPUT_PATH}")