# SPDX-License-Identifier: MIT
# mvo.py — 带约束的均值方差优化器
#
# 支持约束:
#   - 权重和 = 1 (全额配置)
#   - 单资产上下限
#   - 类别上限 (商品/海外/权益)
#   - 现金下限
#   - 换手上限
#   - 最大波动率目标
#
# 方法: scipy.optimize.minimize (SLSQP)
# 目标: max Sharpe 或 min variance (给定收益目标)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from src.macro_allocation.asset_returns import ASSET_NAMES
from src.macro_allocation.conditional_stats import ASSET_ORDER

N_ASSETS = len(ASSET_ORDER)

# ── 资产类别映射 ──
ASSET_CATEGORIES: Dict[str, str] = {
    "oil": "commodity",
    "gold": "commodity",
    "bond": "bond",
    "nasdaq": "overseas_equity",
    "hk_tech": "overseas_equity",
    "a_share": "domestic_equity",
    "star_50": "domestic_equity",
    "nikkei": "overseas_equity",
    "india": "overseas_equity",
    "cb_bond": "hybrid",
}


@dataclass
class MVOConstraints:
    """MVO 约束参数"""
    # 单资产边界
    min_per_asset: float = 0.02      # 单资产最低权重
    max_per_asset: float = 0.30      # 单资产最高权重

    # 类别边界
    max_commodity: float = 0.30      # 商品类上限
    max_overseas: float = 0.40       # 海外权益上限
    max_domestic_equity: float = 0.50  # 国内权益上限

    # 特殊
    min_cash: float = 0.05           # 现金最低配置
    max_turnover: float = 0.30       # 最大换手率

    # 风险
    max_volatility: Optional[float] = None  # 最大年化波动率

    # 风险预算模式覆盖
    # risk_off 模式下更严格的约束
    risk_off_overrides: bool = True

    def get_asset_bounds(self, asset: str, is_risk_off: bool = False,
                         bond_yield: Optional[float] = None) -> Tuple[float, float]:
        """获取单个资产的权重上下界。

        bond 资产上限根据利率水位动态调整:
          - 10Y < 1.5%  → 上限 5%  (risk_off +10% → 15%)
          - 10Y < 2.0%  → 上限 15% (risk_off +10% → 25%)
          - 10Y < 2.5%  → 上限 20% (risk_off +10% → 30%)
          - 10Y < 3.0%  → 上限 30% (risk_off +10% → 40%)
          - 10Y ≥ 3.0%  → 上限 40% (risk_off +10% → 50%)

        risk_off 模式下 floor 提高至 15%，但 ceiling 仍受利率水位约束
        （利率价格风险不因 regime 而消失，仅在 risk_off 下给 +10% 额外空间）。
        """
        cat = ASSET_CATEGORIES.get(asset, "other")

        if is_risk_off and self.risk_off_overrides:
            if asset == "bond":
                # risk_off 提高 floor，但 ceiling 仍受利率水位约束（+10% 空间）
                yield_cap = self._bond_max_by_yield(bond_yield)
                risk_off_cap = min(0.60, yield_cap + 0.10)
                return (0.15, max(0.15, risk_off_cap))
            if asset == "gold":
                return (0.10, 0.40)
            if cat == "domestic_equity":
                return (0.0, 0.10)
            if cat == "overseas_equity":
                return (0.0, 0.10)

        if cat == "bond":
            lo = 0.05
            hi = self._bond_max_by_yield(bond_yield)
            return (lo, hi)
        return (self.min_per_asset, self.max_per_asset)

    @staticmethod
    def _bond_max_by_yield(bond_yield: Optional[float]) -> float:
        """根据 10Y 收益率水位返回债券权重上限。

        逻辑:
          - 极低利率 (< 1.5%)   → 债券价格上行空间极小，下行风险大 → 基本不配
          - 低利率 (1.5-2.0%)   → 盈亏比偏负 → 上限 15%
          - 中等偏低 (2.0-2.5%) → 一定吸引力 → 上限 20%
          - 正常 (2.5-3.0%)     → 默认上限 30%
          - 高利率 (> 3.0%)     → 绝对收益有吸引力 → 上限 40%
        """
        if bond_yield is None:
            return 0.30
        if bond_yield < 1.5:
            return 0.05
        elif bond_yield < 2.0:
            return 0.15
        elif bond_yield < 2.5:
            return 0.20
        elif bond_yield < 3.0:
            return 0.30
        else:
            return 0.40


@dataclass
class MVOResult:
    """MVO 优化结果"""
    weights: np.ndarray              # 最优权重 (N_ASSETS,)
    cash_weight: float               # 现金权重
    expected_return: float           # 组合预期年化收益
    expected_volatility: float       # 组合预期年化波动率
    sharpe_ratio: float              # 组合 Sharpe
    success: bool                    # 优化是否收敛
    message: str                     # 优化器消息
    asset_weights: Dict[str, float]  # {asset_id: weight}
    category_weights: Dict[str, float]  # {category: total_weight}


class MVO:
    """
    带约束的均值方差优化器。

    目标: max Sharpe ratio (或给定收益目标下 min variance)
    """

    def __init__(self, risk_free_rate: float = 0.02):
        """
        Args:
            risk_free_rate: 无风险利率 (年化)
        """
        self.rf = risk_free_rate
        # 资产索引映射
        self._asset_to_idx = {a: i for i, a in enumerate(ASSET_ORDER)}
        self._idx_to_asset = {i: a for i, a in enumerate(ASSET_ORDER)}

    def optimize(self,
                 mean_returns: np.ndarray,
                 cov_matrix: np.ndarray,
                 constraints: Optional[MVOConstraints] = None,
                 is_risk_off: bool = False,
                 current_weights: Optional[np.ndarray] = None,
                 bond_yield: Optional[float] = None,
                 ) -> MVOResult:
        """
        运行 MVO 优化。

        Args:
            mean_returns: 预期年化收益 (N_ASSETS,)
            cov_matrix: 年化协方差矩阵 (N_ASSETS, N_ASSETS)
            constraints: 约束参数
            is_risk_off: 是否风险规避模式
            current_weights: 当前持仓权重 (用于换手约束)
            bond_yield: 10Y 国债收益率 (用于动态债券上限)

        Returns:
            MVOResult
        """
        constraints = constraints or MVOConstraints()
        N = len(mean_returns)

        # ── 定义优化目标: max Sharpe ──
        def neg_sharpe(w):
            port_ret = w @ mean_returns
            port_vol = np.sqrt(w @ cov_matrix @ w)
            if port_vol < 1e-10:
                return 1e10  # 惩罚零波动
            return -(port_ret - self.rf) / port_vol  # 负 Sharpe (最小化)

        # ── 约束列表 ──
        cons = []

        # 权重和 ≤ 1 - min_cash (留现金)
        max_total = 1.0 - constraints.min_cash

        cons.append({
            "type": "eq",
            "fun": lambda w: np.sum(w) - max_total,
        })

        # ── 边界 ──
        bounds = []
        for i, asset in enumerate(ASSET_ORDER):
            lo, hi = constraints.get_asset_bounds(asset, is_risk_off, bond_yield=bond_yield)
            bounds.append((lo, hi))

        # ── 类别约束 ──
        # 商品类
        commodity_idx = [i for i, a in enumerate(ASSET_ORDER)
                        if ASSET_CATEGORIES.get(a) == "commodity"]
        if commodity_idx:
            cons.append({
                "type": "ineq",
                "fun": lambda w, ci=commodity_idx: constraints.max_commodity - np.sum(w[ci]),
            })

        # 海外权益
        overseas_idx = [i for i, a in enumerate(ASSET_ORDER)
                       if ASSET_CATEGORIES.get(a) == "overseas_equity"]
        if overseas_idx:
            cons.append({
                "type": "ineq",
                "fun": lambda w, oi=overseas_idx: constraints.max_overseas - np.sum(w[oi]),
            })

        # 国内权益
        domestic_idx = [i for i, a in enumerate(ASSET_ORDER)
                       if ASSET_CATEGORIES.get(a) == "domestic_equity"]
        if domestic_idx:
            cons.append({
                "type": "ineq",
                "fun": lambda w, di=domestic_idx: constraints.max_domestic_equity - np.sum(w[di]),
            })

        # ── 波动率上限 ──
        if constraints.max_volatility is not None:
            cons.append({
                "type": "ineq",
                "fun": lambda w: constraints.max_volatility**2 - w @ cov_matrix @ w,
            })

        # ── 换手率约束 ──
        if current_weights is not None:
            cons.append({
                "type": "ineq",
                "fun": lambda w, cw=current_weights:
                    constraints.max_turnover - np.sum(np.abs(w - cw)) / 2,
            })

        # ── 优化 ──
        # 初始猜测: 等权重
        x0 = np.ones(N) * max_total / N

        result = minimize(
            neg_sharpe,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        w_opt = result.x
        # 确保非负
        w_opt = np.maximum(w_opt, 0)
        # 归一化 (因为边界和约束可能不完全一致)
        w_sum = np.sum(w_opt)
        if w_sum > 0:
            w_opt = w_opt * max_total / w_sum

        port_ret = w_opt @ mean_returns
        port_vol = np.sqrt(w_opt @ cov_matrix @ w_opt)
        sharpe = (port_ret - self.rf) / port_vol if port_vol > 1e-8 else 0.0

        # ── 构建结果 ──
        asset_weights = {}
        for i, asset in enumerate(ASSET_ORDER):
            if w_opt[i] > 0.001:  # 只报告 > 0.1%的权重
                asset_weights[asset] = float(w_opt[i])

        # 类别汇总
        cat_weights: Dict[str, float] = {}
        for i, asset in enumerate(ASSET_ORDER):
            cat = ASSET_CATEGORIES.get(asset, "other")
            cat_weights[cat] = cat_weights.get(cat, 0.0) + float(w_opt[i])

        cash_weight = 1.0 - np.sum(w_opt)  # 剩余为现金

        return MVOResult(
            weights=w_opt,
            cash_weight=max(0.0, cash_weight),
            expected_return=float(port_ret),
            expected_volatility=float(port_vol),
            sharpe_ratio=float(sharpe),
            success=result.success,
            message=result.message,
            asset_weights=asset_weights,
            category_weights=cat_weights,
        )

    def min_variance(self,
                     mean_returns: np.ndarray,
                     cov_matrix: np.ndarray,
                     target_return: Optional[float] = None,
                     constraints: Optional[MVOConstraints] = None,
                     is_risk_off: bool = False,
                     ) -> MVOResult:
        """
        最小方差优化 (给定收益目标或全局最小方差)。

        Args:
            target_return: 目标年化收益。None → 全局最小方差
        """
        constraints = constraints or MVOConstraints()
        N = len(mean_returns)

        def portfolio_variance(w):
            return w @ cov_matrix @ w

        cons = []
        max_total = 1.0 - constraints.min_cash
        cons.append({"type": "eq", "fun": lambda w: np.sum(w) - max_total})

        if target_return is not None:
            cons.append({
                "type": "eq",
                "fun": lambda w: w @ mean_returns - target_return,
            })

        bounds = []
        for i, asset in enumerate(ASSET_ORDER):
            lo, hi = constraints.get_asset_bounds(asset, is_risk_off)
            bounds.append((lo, hi))

        x0 = np.ones(N) * max_total / N

        result = minimize(
            portfolio_variance,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        w_opt = np.maximum(result.x, 0)
        w_sum = np.sum(w_opt)
        if w_sum > 0:
            w_opt = w_opt * max_total / w_sum

        port_ret = w_opt @ mean_returns
        port_vol = np.sqrt(w_opt @ cov_matrix @ w_opt)
        sharpe = (port_ret - self.rf) / port_vol if port_vol > 1e-8 else 0.0

        asset_weights = {}
        for i, asset in enumerate(ASSET_ORDER):
            if w_opt[i] > 0.001:
                asset_weights[asset] = float(w_opt[i])

        cat_weights = {}
        for i, asset in enumerate(ASSET_ORDER):
            cat = ASSET_CATEGORIES.get(asset, "other")
            cat_weights[cat] = cat_weights.get(cat, 0.0) + float(w_opt[i])

        return MVOResult(
            weights=w_opt,
            cash_weight=max(0.0, 1.0 - np.sum(w_opt)),
            expected_return=float(port_ret),
            expected_volatility=float(port_vol),
            sharpe_ratio=float(sharpe),
            success=result.success,
            message=result.message,
            asset_weights=asset_weights,
            category_weights=cat_weights,
        )


# ── 便捷函数 ────────────────────────────────────────────────────

def get_mvo(risk_free_rate: float = 0.02) -> MVO:
    return MVO(risk_free_rate)


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    from src.macro_allocation.stable_bl import StableBL

    bl = StableBL()
    mvo = MVO()

    for regime in ["risk_on", "risk_off"]:
        print(f"\n{'='*60}")
        print(f"Regime: {regime}")
        views = bl.generate_default_views(regime)
        bl_result = bl.run(regime, views)

        is_risk_off = (regime == "risk_off")
        mvo_result = mvo.optimize(
            bl_result.posterior_mean,
            bl_result.posterior_cov,
            is_risk_off=is_risk_off,
        )

        print(f"  Sharpe: {mvo_result.sharpe_ratio:.2f}")
        print(f"  预期收益: {mvo_result.expected_return*100:.1f}%")
        print(f"  预期波动: {mvo_result.expected_volatility*100:.1f}%")
        print(f"  现金: {mvo_result.cash_weight*100:.1f}%")
        print(f"  资产权重:")
        for asset, w in sorted(mvo_result.asset_weights.items(),
                                key=lambda x: -x[1]):
            name = ASSET_NAMES.get(asset, asset)
            print(f"    {asset} ({name}): {w*100:.1f}%")
        print(f"  类别汇总:")
        for cat, w in sorted(mvo_result.category_weights.items(),
                              key=lambda x: -x[1]):
            print(f"    {cat}: {w*100:.1f}%")