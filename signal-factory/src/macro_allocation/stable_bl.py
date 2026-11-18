# SPDX-License-Identifier: MIT
# stable_bl.py — 条件 Black-Litterman 资产配置
#
# STABLE = State-conditional Time-varying Asset Black-Litterman Engine
#
# 方法:
#   1. Prior: regime → 条件均值 + 条件协方差 (来自 ConditionalStats)
#   2. Views: 宏观观点 → 资产绝对/相对收益观点
#   3. Posterior: BL 公式结合 prior + views
#   4. 可选: MVO 优化 (调用 mvo.py)
#
# 参考: Idzorek (2005), "A Step-by-Step Guide to the Black-Litterman Model"

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.macro_allocation.conditional_stats import (
    ConditionalStats,
    ConditionalStatsResult,
    ASSET_ORDER,
    ASSET_NAMES,
)
from src.macro_allocation.regime_backtest import (
    StableRegimeClassifier,
    STABLE_REGIMES,
    REGIME_LABELS_CN,
)

# ── 资产数量 ──
N_ASSETS = len(ASSET_ORDER)  # 10 (不含 cash)


@dataclass
class BLView:
    """单个 Black-Litterman 观点"""
    description: str                    # 观点描述
    assets: List[str]                   # 涉及的资产
    weights: np.ndarray                 # 观点权重向量 (行向量, 1×N)
    value: float                        # 观点预期收益 (年化)
    confidence: float                   # 观点置信度 (0-1)


@dataclass
class BLResult:
    """Black-Litterman 计算结果"""
    regime: str
    regime_label: str
    prior_mean: np.ndarray             # 先验均值 (N,)
    prior_cov: np.ndarray              # 先验协方差 (N, N)
    posterior_mean: np.ndarray         # 后验均值 (N,)
    posterior_cov: np.ndarray          # 后验协方差 (N, N)
    views: List[BLView]                # 使用的观点
    tau: float                         # 先验不确定性参数
    weights: Optional[np.ndarray] = None  # MVO 最优权重 (N,)
    cash_weight: float = 0.0           # 现金权重


class StableBL:
    """
    STABLE Black-Litterman 引擎。

    使用条件统计作为 prior，结合投资者观点生成 posterior。
    """

    def __init__(self, tau: float = 0.05):
        """
        Args:
            tau: 先验不确定性缩放参数。τ 越小 → prior 置信越高。
                 常用值: 0.025-0.10 (Idzorek 建议 τ ≈ 1/T, T 为观测数)
        """
        self.tau = tau
        self._cond_stats = ConditionalStats()
        self._regime_clf = StableRegimeClassifier()
        self._stats_result: Optional[ConditionalStatsResult] = None

    # ── 核心 BL 计算 ────────────────────────────────────────────

    def run(self, current_regime: str,
            views: Optional[List[BLView]] = None,
            tau: Optional[float] = None,
            shrinkage: float = 0.5) -> BLResult:
        """
        运行 Black-Litterman 计算。

        Args:
            current_regime: 当前 regime (risk_on/risk_off/stagflation/overheat)
            views: 投资者观点列表 (可选, 无观点时 posterior=prior)
            tau: 覆盖默认 τ
            shrinkage: Bayes shrinkage 0=纯条件 1=纯无条件

        Returns:
            BLResult
        """
        tau = tau if tau is not None else self.tau

        # ── Step 1: 获取 prior ──
        if self._stats_result is None:
            self._stats_result = self._cond_stats.compute()

        prior_mean, prior_cov = self._cond_stats.get_bl_prior(
            current_regime, shrinkage=shrinkage
        )

        # 确保正定
        prior_cov = self._ensure_positive_definite(prior_cov)

        # ── Step 2: 处理 views ──
        views = views or []
        if not views:
            # 无观点: posterior = prior
            return BLResult(
                regime=current_regime,
                regime_label=REGIME_LABELS_CN.get(current_regime, current_regime),
                prior_mean=prior_mean,
                prior_cov=prior_cov,
                posterior_mean=prior_mean.copy(),
                posterior_cov=prior_cov.copy(),
                views=[],
                tau=tau,
            )

        # ── 构建 P, Q, Ω 矩阵 ──
        K = len(views)  # 观点数量
        P = np.zeros((K, N_ASSETS))
        Q = np.zeros(K)
        confidences = np.zeros(K)

        for k, view in enumerate(views):
            # P 矩阵: 观点对资产的暴露
            if len(view.weights) != N_ASSETS:
                # 将资产名映射到 ASSET_ORDER
                w = np.zeros(N_ASSETS)
                for asset, weight in zip(view.assets, view.weights):
                    if asset in ASSET_ORDER:
                        w[ASSET_ORDER.index(asset)] = weight
                P[k, :] = w
            else:
                P[k, :] = view.weights

            Q[k] = view.value
            confidences[k] = view.confidence

        # Ω 矩阵: 观点不确定性 (对角)
        # Ω_kk = (1/confidence_k - 1) * P_k Σ P_k'
        # 高置信度 → 小 Ω → 观点权重大
        Omega = np.zeros((K, K))
        for k in range(K):
            pk = P[k:k+1, :]  # (1, N)
            var_k = pk @ prior_cov @ pk.T  # 观点方差
            # 置信度 → 不确定性缩放
            # confidence 0.5 → scale=1.0, confidence 0.9 → scale=0.11
            uncertainty_scale = max(0.01, (1.0 / max(view.confidence, 0.01) - 1.0))
            Omega[k, k] = tau * uncertainty_scale * var_k.item()

        # ── Step 3: BL posterior ──
        # Π_bl = Π + τΣ P' (P τΣ P' + Ω)^{-1} (Q - P Π)
        # Σ_bl = Σ + τΣ - τΣ P' (P τΣ P' + Ω)^{-1} P τΣ
        tau_Sigma = tau * prior_cov

        # 中间矩阵
        M = P @ tau_Sigma @ P.T + Omega  # (K, K)

        try:
            M_inv = np.linalg.inv(M)
        except np.linalg.LinAlgError:
            # 伪逆回退
            M_inv = np.linalg.pinv(M)

        # Posterior mean
        adjustment = tau_Sigma @ P.T @ M_inv @ (Q - P @ prior_mean)
        posterior_mean = prior_mean + adjustment

        # Posterior covariance
        posterior_cov = prior_cov + tau_Sigma - tau_Sigma @ P.T @ M_inv @ P @ tau_Sigma
        posterior_cov = self._ensure_positive_definite(posterior_cov)

        return BLResult(
            regime=current_regime,
            regime_label=REGIME_LABELS_CN.get(current_regime, current_regime),
            prior_mean=prior_mean,
            prior_cov=prior_cov,
            posterior_mean=posterior_mean,
            posterior_cov=posterior_cov,
            views=views,
            tau=tau,
        )

    # ── 辅助方法 ────────────────────────────────────────────────

    def _ensure_positive_definite(self, cov: np.ndarray,
                                   min_eigenval: float = 1e-8) -> np.ndarray:
        """确保协方差矩阵正定，处理 NaN 和零方差"""
        # 替换 NaN/Inf
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)

        # 确保对角线为正
        for i in range(len(cov)):
            if cov[i, i] <= 0:
                cov[i, i] = min_eigenval * 100

        # 确保对称
        cov = (cov + cov.T) / 2

        try:
            eigenvalues, eigenvectors = np.linalg.eigh(cov)
        except np.linalg.LinAlgError:
            # 回退: 使用对角线 + 小正则化
            cov_reg = cov + np.eye(len(cov)) * min_eigenval * 100
            eigenvalues, eigenvectors = np.linalg.eigh(cov_reg)

        eigenvalues = np.maximum(eigenvalues, min_eigenval)
        return eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T

    def get_current_regime(self) -> str:
        """获取当前宏观 regime"""
        # 加载最近的 regime 标签
        if self._stats_result is None:
            self._stats_result = self._cond_stats.compute()

        labels = self._stats_result.regime_labels
        if len(labels) > 0:
            return str(labels.iloc[-1])
        return "risk_on"  # 默认

    def generate_default_views(self, current_regime: str,
                               indicators: Optional[Dict[str, Any]] = None) -> List[BLView]:
        """
        根据当前 regime 生成默认的投资者观点。

        基于 regime 资产偏好 (来自 macro_regime.py 的 REGIME_ASSET_RANKING):
          - Risk-On: 超配权益, 低配债券/现金
          - Risk-Off: 超配债券/黄金, 低配权益
          - Stagflation: 超配黄金/商品, 低配权益
          - Overheat: 超配商品, 低配债券

        ★ P1 增强 (2026-10-29): 当 indicators 提供 10Y 国债收益率时，
        额外生成利率水位观点，在低利率下自动压降债券配置。
        10Y < 2.0% → bond 下行风险大，自动生成债券低配 view。
        """
        # Regime 资产偏好 (排名 1=最优, 5=最差)
        regime_prefs = {
            "risk_on": {
                "best": ["a_share", "star_50", "nasdaq"],
                "worst": ["bond", "cash"],
            },
            "risk_off": {
                "best": ["bond", "gold"],
                "worst": ["a_share", "star_50", "oil"],
            },
            "neutral": {
                "best": ["gold", "bond"],
                "worst": ["oil"],
            },
            "stagflation": {
                "best": ["gold", "oil"],
                "worst": ["a_share", "star_50", "nasdaq", "hk_tech"],
            },
            "overheat": {
                "best": ["oil", "gold"],
                "worst": ["bond"],
            },
        }

        prefs = regime_prefs.get(current_regime, regime_prefs["risk_on"])
        views = []

        # 观点1: 最优资产相对最差资产有超额收益
        if prefs["best"] and prefs["worst"]:
            w = np.zeros(N_ASSETS)
            for asset in prefs["best"]:
                if asset in ASSET_ORDER:
                    w[ASSET_ORDER.index(asset)] = 1.0 / len(prefs["best"])
            for asset in prefs["worst"]:
                if asset in ASSET_ORDER:
                    w[ASSET_ORDER.index(asset)] = -1.0 / len(prefs["worst"])

            views.append(BLView(
                description=f"{REGIME_LABELS_CN.get(current_regime)}: "
                            f"超配{'+'.join(prefs['best'])} vs "
                            f"低配{'+'.join(prefs['worst'])}",
                assets=prefs["best"] + prefs["worst"],
                weights=w,
                value=0.10,       # 年化超额 10%
                confidence=0.50,  # 中等置信度
            ))

        # ── ★ P1: 利率水位观点 ──
        if indicators:
            interest_views = self._generate_rate_level_views(indicators)
            views.extend(interest_views)

        return views

    def _generate_rate_level_views(
        self, indicators: Dict[str, Any]
    ) -> List[BLView]:
        """根据利率绝对水平生成 BL 观点。

        当 10Y < 2.0% 时自动生成债券低配观点：
          - 收益率越低 → 置信度越高
          - 1.5-2.0%: 置信度 10-25%
          - < 1.5%: 置信度 25-40%

        同时生成现金替代观点（债券低配的对面 = 现金/短久期更优）。
        """
        views = []
        cn_10y = indicators.get("bond_yield") or indicators.get("10Y国债收益率")
        if cn_10y is None:
            return views
        try:
            cn_10y = float(cn_10y)
        except (TypeError, ValueError):
            return views

        if cn_10y >= 2.0:
            return views  # 利率正常，无需额外观点

        # 置信度: 收益率每低于 2.0% 10bp，置信度 +5%
        confidence = min(0.40, (2.0 - cn_10y) * 0.5)

        # 低配债券 vs 其余资产（等权）
        bond_w = np.zeros(N_ASSETS)
        if "bond" in ASSET_ORDER:
            bond_w[ASSET_ORDER.index("bond")] = -1.0
            # 其余资产均分 +1
            n_other = N_ASSETS - 1
            for i in range(N_ASSETS):
                if i != ASSET_ORDER.index("bond"):
                    bond_w[i] = 1.0 / n_other

        views.append(BLView(
            description=(
                f"利率水位: 10Y仅{cn_10y:.2f}%，"
                f"债券价格上行空间有限，下行风险显著"
            ),
            assets=ASSET_ORDER,
            weights=bond_w,
            value=-0.03,       # 债券相对其余资产年化低 3%
            confidence=confidence,
        ))

        return views

    def to_weights_dataframe(self, bl_result: BLResult) -> pd.DataFrame:
        """将 BL 结果转为权重 DataFrame"""
        rows = []
        for i, asset in enumerate(ASSET_ORDER):
            rows.append({
                "asset": asset,
                "asset_name": ASSET_NAMES.get(asset, asset),
                "prior_return": bl_result.prior_mean[i],
                "posterior_return": bl_result.posterior_mean[i],
                "prior_vol": np.sqrt(bl_result.prior_cov[i, i]),
                "posterior_vol": np.sqrt(bl_result.posterior_cov[i, i]),
            })
        return pd.DataFrame(rows).set_index("asset")


# ── 便捷函数 ────────────────────────────────────────────────────

def get_stable_bl(tau: float = 0.05) -> StableBL:
    return StableBL(tau)


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    bl = StableBL()

    # 获取当前 regime
    current = bl.get_current_regime()
    print(f"当前 regime: {REGIME_LABELS_CN.get(current, current)}")

    # 生成默认观点
    views = bl.generate_default_views(current)
    for v in views:
        print(f"  观点: {v.description} (值={v.value:.0%}, 置信度={v.confidence:.0%})")

    # 运行 BL
    result = bl.run(current, views)
    print(f"\nBL 结果:")
    print(f"  Prior → Posterior 收益变化:")
    for i, asset in enumerate(ASSET_ORDER):
        delta = (result.posterior_mean[i] - result.prior_mean[i]) * 100
        if abs(delta) > 0.01:
            print(f"    {asset}: {result.prior_mean[i]*100:.1f}% → "
                  f"{result.posterior_mean[i]*100:.1f}% (Δ={delta:+.1f}pp)")

    # 全 regime 对比
    print(f"\n全 regime 无条件 vs 条件收益:")
    for regime in STABLE_REGIMES:
        r = bl.run(regime, bl.generate_default_views(regime))
        a_share_idx = ASSET_ORDER.index("a_share")
        gold_idx = ASSET_ORDER.index("gold")
        bond_idx = ASSET_ORDER.index("bond")
        print(f"  {REGIME_LABELS_CN.get(regime, regime)}: "
              f"A股 posterior={r.posterior_mean[a_share_idx]*100:.1f}%, "
              f"黄金={r.posterior_mean[gold_idx]*100:.1f}%, "
              f"债券={r.posterior_mean[bond_idx]*100:.1f}%")