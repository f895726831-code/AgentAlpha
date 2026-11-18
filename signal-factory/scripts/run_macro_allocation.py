#!/usr/bin/env python3
"""
run_macro_allocation.py — Layer 0 宏观资产配置完整管线

串联 macro_data → macro_regime → asset_scorer → portfolio_builder → contract JSON。
产出 d:/AgentAlpha/my-bot/data/output/asset_allocation.json（v1.0 contract 格式）。

用法:
    python scripts/run_macro_allocation.py                          # 完整管线
    python scripts/run_macro_allocation.py --mode risk_off          # 指定模式
    python scripts/run_macro_allocation.py --skip-collect           # 跳过数据采集（用缓存）
    python scripts/run_macro_allocation.py --dry-run                # 打印结果，不写文件
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

# Force UTF-8 on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

# ── 路径配置 ──

QF_ROOT = Path("signal-factory")
FM_TRADE_DATA = Path("D:/AgentAlpha/my-bot/data/output")

# 确保 quant-signal-factory 在 sys.path
if str(QF_ROOT) not in sys.path:
    sys.path.insert(0, str(QF_ROOT))


# ═══════════════════════════════════════════════════════════════════════
# Step helpers
# ═══════════════════════════════════════════════════════════════════════


def step_header(title: str, n: int):
    print(f"\n{'='*60}")
    print(f"  Step {n}: {title}")
    print(f"{'='*60}")


def step_ok(duration_s: float):
    print(f"  [OK] ({duration_s:.1f}s)")


def step_warn(msg: str):
    print(f"  [WARN] {msg}")


def step_error(msg: str):
    print(f"  [ERROR] {msg}")


# ═══════════════════════════════════════════════════════════════════════
# ★ P0: STABLE-aware summary builder
# ═══════════════════════════════════════════════════════════════════════

def _build_stable_summary(
    stable_regime: str,
    mvo_result: Any,
    bond_yield_val: Optional[float] = None,
    div_status: Any = None,
    asset_weights: Optional[Dict[str, float]] = None,
    asset_names_map: Optional[Dict[str, str]] = None,
) -> str:
    """生成与 STABLE BL+MVO 权重逻辑一致的 summary 文本。

    与 rule-based classifier 的 summary 不同，此文本：
      - 基于 HMM regime（而非 6 象限）
      - 包含利率水位评估
      - 包含 HMM-Macro 分歧状态
      - 反映 MVO 的实际优化方向
    """
    from src.macro_allocation.regime_backtest import REGIME_LABELS_CN

    regime_cn = REGIME_LABELS_CN.get(stable_regime, stable_regime)
    parts = [f"STABLE BL+MVO优化配置。"]

    # HMM regime
    parts.append(f"HMM判定: {regime_cn}。")

    # Top 3 allocations
    if asset_weights:
        names = asset_names_map or {}
        top3 = sorted(asset_weights.items(), key=lambda x: -x[1])[:3]
        top3_str = "、".join(
            f"{names.get(a, a)}({w*100:.0f}%)" for a, w in top3
        )
        parts.append(f"配置重心: {top3_str}。")

    # Rate level assessment (regime-aware — risk_off gives +10% extra ceiling)
    if bond_yield_val is not None:
        try:
            y = float(bond_yield_val)
            is_risk_off = stable_regime == "risk_off"
            if y < 1.5:
                cap = 0.15 if is_risk_off else 0.05
                risk_note = "（risk_off放宽至15%）" if is_risk_off else ""
                parts.append(
                    f"⚠️10Y仅{y:.2f}%极低水位，债券上行空间极小，"
                    f"建议上限{cap:.0%}{risk_note}。"
                )
            elif y < 2.0:
                cap = 0.25 if is_risk_off else 0.15
                risk_note = "（risk_off放宽至25%）" if is_risk_off else ""
                parts.append(
                    f"⚠️10Y{y:.2f}%低水位，债券盈亏比偏负，"
                    f"建议上限{cap:.0%}{risk_note}。"
                )
            elif y < 2.5:
                cap = 0.30 if is_risk_off else 0.20
                risk_note = "（risk_off放宽至30%）" if is_risk_off else ""
                parts.append(
                    f"10Y{y:.2f}%中等偏低，债券配置中性，"
                    f"建议上限{cap:.0%}{risk_note}。"
                )
            else:
                parts.append(f"10Y{y:.2f}%正常区间，债券配置价值可期。")
        except (TypeError, ValueError):
            pass

    # Sharpe
    parts.append(
        f"组合Sharpe={mvo_result.sharpe_ratio:.2f}, "
        f"预期收益={mvo_result.expected_return*100:.1f}%, "
        f"预期波动={mvo_result.expected_volatility*100:.1f}%。"
    )

    # Divergence
    if div_status and div_status.diverged:
        parts.append(
            f"⚠️HMM-Macro分歧{div_status.divergence_weeks}周"
            f"(HMM={div_status.hmm_regime}, Macro={div_status.macro_regime})，"
            f"已向无条件分布收缩。"
        )

    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════


def run_pipeline(
    mode: str = "risk_off",
    skip_collect: bool = False,
    dry_run: bool = False,
    use_stable: bool = False,
    stable_shrinkage: float = 0.5,
    regime_source: str = "rule",
) -> Optional[dict]:
    """运行完整 Layer 0 宏观资产配置管线。

    Args:
        mode: 配置模式 (normal / risk_off / earnings_season)
        skip_collect: 跳过数据采集，仅用缓存
        dry_run: 打印结果但不写入文件
        regime_source: regime 分类器 ("rule" 或 "hmm")

    Returns:
        生成的 asset_allocation dict，失败返回 None
    """
    t0 = datetime.now()
    today = date.today().isoformat()

    print("=" * 60)
    print("  Layer 0 宏观资产配置管线")
    print(f"  日期: {today}  模式: {mode}  采集: {'跳过' if skip_collect else '执行'}")
    print("=" * 60)

    # ── Step 1: Data Collection ──
    step_header("宏观数据采集", 1)

    from src.macro_allocation.macro_data import get_macro_collector

    collector = get_macro_collector()
    t1 = datetime.now()

    if not skip_collect:
        try:
            print("  采集 EDB 宏观指标...")
            edb_df = collector.collect_all_edb_indicators()
            if edb_df is not None and not edb_df.empty:
                print(f"    EDB: {len(edb_df)} 行, {len(edb_df.columns)} 列, "
                      f"范围 {edb_df.index[0]} ~ {edb_df.index[-1]}")
            else:
                step_warn("EDB 采集返回空")

            print("  采集 ETF 日线数据...")
            etf_data = collector.collect_all_etfs()
            available_etfs = [k for k, v in etf_data.items() if v is not None and not v.empty]
            missing_etfs = [k for k, v in etf_data.items() if v is None or v.empty]
            print(f"    ETF: {len(available_etfs)}/11 可用, 缺失: {missing_etfs if missing_etfs else '无'}")

            print("  采集指数数据...")
            index_data = collector.collect_all_index_data()
            print(f"    指数: {len(index_data)} 个")

        except Exception as e:
            step_warn(f"部分数据采集失败: {e}")
            traceback.print_exc()
    else:
        print("  跳过数据采集，使用缓存")

    # Load cached ETF data
    print("  加载 ETF 缓存...")
    etf_prices: Dict[str, pd.DataFrame] = {}
    etf_keys = list(ASSET_UNIVERSE.keys()) if "ASSET_UNIVERSE" in dir() else [
        "oil", "gold", "bond", "nasdaq", "hk_tech", "a_share", "cash",
        "star_50", "nikkei", "india", "cb_bond",
    ]
    # Use ASSET_UNIVERSE from macro_data for canonical list
    from src.macro_allocation.macro_data import ASSET_UNIVERSE as _AU
    etf_keys = list(_AU.keys())
    for key in etf_keys:
        df = collector.load_etf_cache(key)
        if df is not None and not df.empty:
            etf_prices[key] = df
            print(f"    {key}: {len(df)} 行, {df.index[-1]}")
        else:
            # Cash doesn't have ETF data — create synthetic
            if key == "cash":
                # Create synthetic cash price (always 100 with tiny noise for volatility calc)
                if "a_share" in etf_prices:
                    idx = etf_prices["a_share"].index.copy()
                    cash_df = pd.DataFrame({"close": np.ones(len(idx)) * 100.0}, index=idx)
                    etf_prices["cash"] = cash_df
                    print(f"    cash: synthetic ({len(cash_df)} 行)")
                else:
                    step_warn("cash 无合成基准，跳过")
            else:
                step_warn(f"{key}: 无缓存数据")

    # Get indicators
    indicators = collector.get_latest_indicators()
    print(f"  指标: {len(indicators)} 个可用")
    for k, v in list(indicators.items())[:5]:
        print(f"    {k}: {v}")
    if len(indicators) > 5:
        print(f"    ... 共 {len(indicators)} 个")

    step_ok((datetime.now() - t1).total_seconds())

    # ── Step 2: Macro Regime ──
    step_header("宏观象限判断", 2)
    t2 = datetime.now()

    from src.macro_allocation.macro_regime import (
        classify_from_dict,
        MacroRegime,
        REGIME_LABELS,
        is_risk_off,
    )

    regime_result = classify_from_dict(indicators)
    regime_label = REGIME_LABELS.get(regime_result.regime, regime_result.regime.value)

    print(f"  象限: {regime_label} ({regime_result.regime.value})")
    print(f"  置信度: {regime_result.confidence:.0%}")
    print(f"  增长: {regime_result.growth.judgment} ({regime_result.growth.direction})")
    print(f"  通胀: {regime_result.inflation.judgment} ({regime_result.inflation.direction})")
    print(f"  流动性: {regime_result.liquidity.judgment} ({regime_result.liquidity.direction})")
    print(f"  风险偏好: {regime_result.risk_appetite.judgment} ({regime_result.risk_appetite.direction})")
    print(f"  最优资产: {regime_result.get_best_assets(3)}")
    print(f"  最差资产: {regime_result.get_worst_assets(3)}")

    risk_off = is_risk_off(indicators)
    print(f"  risk_off 判断: {'是' if risk_off else '否'}")

    step_ok((datetime.now() - t2).total_seconds())

    # ── Step 3: Asset Scoring ──
    step_header("11 类资产多因子评分", 3)
    t3 = datetime.now()

    from src.macro_allocation.asset_scorer import get_asset_scorer

    scorer = get_asset_scorer()

    # Pass regime as (enum, scores) tuple for compatibility with both old and new code paths
    from src.macro_allocation.macro_regime import RegimeScores
    regime_tuple = (regime_result.regime, RegimeScores.from_result(regime_result))

    score_result = scorer.score_all(
        etf_prices=etf_prices,
        indicators=indicators,
        regime_result=regime_tuple,
        current_date=today,
    )

    print(f"  评分排名:")
    for i, asset_key in enumerate(score_result.ranking, 1):
        s = score_result.scores[asset_key]
        rec_icon = {"overweight": "↑超配", "neutral": "→中性", "underweight": "↓低配", "avoid": "✗回避"}.get(
            s.recommendation, s.recommendation
        )
        print(f"    #{i} {s.asset_name:<10} 总分: {s.total_score:.0f}  {rec_icon}")
        # Print factor breakdown
        factors_str = ", ".join(f"{fs.name}={fs.score:.0f}" for fs in s.factor_scores)
        print(f"         {factors_str}")

    step_ok((datetime.now() - t3).total_seconds())

    # ── Step 4: Portfolio Construction ──
    # Indicator key mapping (extracted early for BL views and MVO bond cap)
    _ik_step4 = lambda *keys: next((indicators.get(k) for k in keys if k in indicators), None)
    bond_yield_val = _ik_step4('10Y国债收益率', 'bond_10y_yield')
    div_status = None  # ★ P0: init before if-block for summary builder access

    if use_stable:
        step_header("组合构建 (STABLE BL + MVO)", 4)
    else:
        step_header("组合构建 (风险平价 + 主动观点)", 4)
    t4 = datetime.now()

    if use_stable:
        # ═══ STABLE 路径: 条件 Black-Litterman + MVO ═══
        from src.macro_allocation.stable_bl import StableBL
        from src.macro_allocation.mvo import MVO, MVOConstraints
        from src.macro_allocation.regime_backtest import STABLE_REGIMES, REGIME_LABELS_CN
        from src.macro_allocation.conditional_stats import ASSET_ORDER

        if regime_source == "hmm":
            # HMM 直接从资产收益推断 regime（无需宏观指标映射）
            from src.macro_allocation.regime_hmm import HMMRegimeClassifier
            hmm_clf = HMMRegimeClassifier.load()
            stable_regime = hmm_clf.get_current_regime()
            trans_risk = hmm_clf.get_regime_transition_risk(horizon_months=3)
            print(f"  HMM regime: {REGIME_LABELS_CN.get(stable_regime, stable_regime)}")
            print(f"  3-month transition risk: { {k: f'{v:.1%}' for k, v in trans_risk.items()} }")

            # ── HMM-Macro divergence monitor ──
            div_status = None
            try:
                from src.macro_allocation.hmm_divergence_monitor import DivergenceMonitor
                div_monitor = DivergenceMonitor()
                div_status = div_monitor.check_macro(hmm_clf)
                if div_status.diverged:
                    print(f"  ⚠️ HMM-Macro DIVERGENCE: HMM={div_status.hmm_regime}, "
                          f"Macro={div_status.macro_regime}, "
                          f"weeks={div_status.divergence_weeks}, level={div_status.level}")
                    if div_status.recommendation == "fallback_rule":
                        print(f"  🚨 DIVERGENCE ALERT: >2 weeks — switching to rule-based fallback")
                        # 自动切换：用规则型 regime 替代 HMM regime
                        from src.macro_allocation.regime_backtest import StableRegimeClassifier
                        rule_clf = StableRegimeClassifier()
                        rule_history = rule_clf.classify()
                        fallback_regime = rule_history.daily_labels.get(today, stable_regime)
                        print(f"  📋 Fallback regime (rule): {REGIME_LABELS_CN.get(fallback_regime, fallback_regime)}")
                        stable_regime = fallback_regime
                        # 标记分歧状态供 ModeOverride 使用
                        if div_status.risk_weight_multiplier < 1.0:
                            print(f"  ⚠️ 风险权重乘数: {div_status.risk_weight_multiplier:.1%}")
                else:
                    macro_regime = div_status.macro_regime or ""
                    macro_info = f", Macro={macro_regime}" if macro_regime and macro_regime != "unknown" else ""
                    print(f"  ✅ HMM-Macro consistent: {div_status.hmm_regime}{macro_info}")
            except Exception as e:
                print(f"  ⚠️ Divergence monitor skipped: {e}")
        else:
            # Rule-based: 映射 6-regime → 4-regime STABLE
            regime_6_to_4 = {
                "expansion": "risk_on",
                "recovery": "risk_on",
                "overheating": "overheat",
                "stagflation": "stagflation",
                "recession": "risk_off",
                "deflation": "risk_off",
            }
            stable_regime = regime_6_to_4.get(
                regime_result.regime.value if hasattr(regime_result.regime, 'value') else str(regime_result.regime),
                "risk_on"
            )
            print(f"  STABLE regime: {REGIME_LABELS_CN.get(stable_regime, stable_regime)}")

        bl = StableBL()
        mvo = MVO()

        # BL views — ★ P1: pass indicators for rate-level awareness
        views = bl.generate_default_views(stable_regime, indicators=indicators)
        for v in views:
            print(f"  观点: {v.description} (置信度={v.confidence:.0%})")

        # ★ P2: Divergence timeout → shrink toward unconditional prior
        effective_shrinkage = stable_shrinkage
        if div_status and div_status.diverged and div_status.divergence_weeks >= 3:
            extra = min(0.40, (div_status.divergence_weeks - 2) * 0.10)
            effective_shrinkage = min(1.0, stable_shrinkage + extra)
            print(f"  ⚠️ HMM-Macro分歧{div_status.divergence_weeks}周 → "
                  f"shrinkage {stable_shrinkage}→{effective_shrinkage} "
                  f"(向无条件分布收缩)")

        bl_result = bl.run(stable_regime, views, shrinkage=effective_shrinkage)

        # MVO — ★ P4: pass bond yield for dynamic bond cap
        is_risk_off = (stable_regime == "risk_off")
        constraints = MVOConstraints()
        mvo_result = mvo.optimize(
            bl_result.posterior_mean,
            bl_result.posterior_cov,
            constraints=constraints,
            is_risk_off=is_risk_off,
            bond_yield=bond_yield_val,  # from indicators collected in Step 1
        )

        # 构建与原有接口兼容的 plan 对象
        from dataclasses import dataclass

        @dataclass
        class _StableWeight:
            asset_name: str
            risk_parity_weight: float
            target_weight: float
            current_weight: float = 0.0
            drift: float = 0.0
            action: str = "持有"

        @dataclass
        class _StablePlan:
            needs_rebalance: bool = True
            total_drift: float = 0.5
            weights: Dict[str, Any] = None
            suggestions: List[str] = None
            constraints_check: Dict[str, bool] = None

            def __post_init__(self):
                if self.weights is None:
                    self.weights = {}
                if self.suggestions is None:
                    self.suggestions = []
                if self.constraints_check is None:
                    self.constraints_check = {}

        # 构建权重
        asset_names = {
            "oil": "华宝油气", "gold": "华安黄金ETF", "bond": "国债ETF",
            "nasdaq": "纳指ETF", "hk_tech": "恒生科技ETF", "a_share": "沪深300ETF",
            "cash": "现金/回购", "star_50": "科创50ETF", "nikkei": "日经225ETF",
            "india": "印度基金LOF", "cb_bond": "转债ETF",
        }
        stable_weights: Dict[str, _StableWeight] = {}
        for i, asset in enumerate(ASSET_ORDER):
            w = mvo_result.weights[i]
            stable_weights[asset] = _StableWeight(
                asset_name=asset_names.get(asset, asset),
                risk_parity_weight=w,
                target_weight=w,
                current_weight=0.0,
                drift=w,
                action="增持" if w > 0.05 else "持有",
            )
        # 现金
        stable_weights["cash"] = _StableWeight(
            asset_name="现金/回购",
            risk_parity_weight=mvo_result.cash_weight,
            target_weight=mvo_result.cash_weight,
            action="持有",
        )

        suggestions = [
            f"STABLE 优化配置: Sharpe={mvo_result.sharpe_ratio:.2f}, "
            f"预期收益={mvo_result.expected_return*100:.1f}%, "
            f"预期波动={mvo_result.expected_volatility*100:.1f}%"
        ]
        # Top 3 allocations
        top3 = sorted(mvo_result.asset_weights.items(), key=lambda x: -x[1])[:3]
        for asset, w in top3:
            name = asset_names.get(asset, asset)
            suggestions.append(f"超配 {name}({asset})：目标 {w*100:.1f}%")

        plan = _StablePlan(
            weights=stable_weights,
            suggestions=suggestions,
            constraints_check={
                "sharpe_gt_0": mvo_result.sharpe_ratio > 0,
                "vol_lt_15pct": mvo_result.expected_volatility < 0.15,
                "optimizer_converged": mvo_result.success,
            },
        )

        print(f"  Sharpe: {mvo_result.sharpe_ratio:.2f}")
        print(f"  预期收益: {mvo_result.expected_return*100:.1f}%")
        print(f"  预期波动: {mvo_result.expected_volatility*100:.1f}%")
        print(f"  目标权重 (STABLE):")
        for asset, w in sorted(mvo_result.asset_weights.items(), key=lambda x: -x[1]):
            name = asset_names.get(asset, asset)
            print(f"    {asset} ({name}): {w*100:.1f}%")
        print(f"  现金: {mvo_result.cash_weight*100:.1f}%")

    else:
        # ═══ 原有路径: 风险平价 + 主动观点 ═══
        from src.macro_allocation.portfolio_builder import (
            get_portfolio_builder,
            AllocationMode,
        )

        builder = get_portfolio_builder()

        mode_map = {
            "normal": AllocationMode.NORMAL,
            "risk_off": AllocationMode.RISK_OFF,
            "earnings_season": AllocationMode.EARNINGS_SEASON,
        }
        alloc_mode = mode_map.get(mode, AllocationMode.RISK_OFF)

        plan = builder.build_plan(
            etf_prices=etf_prices,
            asset_scores=score_result.scores,
            mode=alloc_mode,
            current_date=today,
        )

    # ── 通用输出 ──
    print(f"  调仓需求: {'是' if plan.needs_rebalance else '否'} (总偏离: {plan.total_drift:.1%})")
    print(f"  目标权重:")
    for asset_key in plan.weights:
        w = plan.weights[asset_key]
        print(
            f"    {w.asset_name:<10} "
            f"RP:{w.risk_parity_weight:.1%} "
            f"→ 目标:{w.target_weight:.1%} "
            f"({w.action if hasattr(w, 'action') else ''})"
        )

    print(f"\n  建议:")
    for suggestion in plan.suggestions:
        print(f"    • {suggestion}")

    print(f"\n  约束检查:")
    for constraint, passed in plan.constraints_check.items():
        icon = "OK" if passed else "FAIL"
        print(f"    {icon} {constraint}")

    step_ok((datetime.now() - t4).total_seconds())

    # ── Step 5: Build Contract ──
    step_header("生成 contract JSON", 5)
    t5 = datetime.now()

    from src.macro_allocation.macro_contract import (
        AssetAllocation,
        MacroQuadrant,
        DimensionJudgment,
        AssetTargetWeight,
        RebalanceAction,
        ModeOverride,
        signal_to_dict,
        save_allocation,
        normalize_asset_key,
        CONTRACT_VERSION,
    )

    # Indicator key mapping: contract lookups → Chinese column names from EDB cache
    _ik = lambda *keys: next((indicators[k] for k in keys if k in indicators), None)

    pmi_val = _ik('制造业PMI', 'pmi_manufacturing')
    cpi_val = _ik('CPI同比', 'cpi_yoy')
    ppi_val = _ik('PPI同比', 'ppi_yoy')
    m2_val = _ik('M2同比', 'm2_yoy')
    bond_yield_val = _ik('10Y国债收益率', 'bond_10y_yield')
    shibor_val = _ik('SHIBOR_ON', 'shibor_overnight')

    # Build macro_quadrant
    macro_quadrant = MacroQuadrant(
        quadrant=regime_result.regime.value,
        quadrant_label_cn=regime_label,
        confidence=regime_result.confidence,
        growth=DimensionJudgment(
            value=f"PMI {pmi_val}" if pmi_val is not None else "PMI N/A",
            direction=regime_result.growth.direction.value if regime_result.growth.direction else "→",
            judgment=regime_result.growth.judgment,
            signal=regime_result.growth.signal,
        ),
        inflation=DimensionJudgment(
            value=f"CPI {cpi_val}% / PPI {ppi_val}%"
            if cpi_val is not None and ppi_val is not None
            else f"CPI {cpi_val or 'N/A'}% / PPI {ppi_val or 'N/A'}%",
            direction=regime_result.inflation.direction.value if regime_result.inflation.direction else "→",
            judgment=regime_result.inflation.judgment,
            signal=regime_result.inflation.signal,
        ),
        liquidity=DimensionJudgment(
            value=f"M2 {m2_val}% / 10Y {bond_yield_val}%"
            if m2_val is not None and bond_yield_val is not None
            else f"M2 {m2_val or 'N/A'}% / 10Y {bond_yield_val or 'N/A'}%",
            direction=regime_result.liquidity.direction.value if regime_result.liquidity.direction else "→",
            judgment=regime_result.liquidity.judgment,
            signal=regime_result.liquidity.signal,
        ),
        risk_appetite=DimensionJudgment(
            value=f"SHIBOR {shibor_val}%" if shibor_val is not None else "SHIBOR N/A%",
            direction=regime_result.risk_appetite.direction.value if regime_result.risk_appetite.direction else "→",
            judgment=regime_result.risk_appetite.judgment,
            signal=regime_result.risk_appetite.signal,
        ),
    )

    # Build asset_details
    asset_details = []
    # Asset key list (works for both STABLE and original paths)
    all_asset_keys = list(plan.weights.keys())
    for asset_key in all_asset_keys:
        w = plan.weights.get(asset_key)
        s = score_result.scores.get(asset_key) if not use_stable else None
        asset_details.append(
            AssetTargetWeight(
                asset=normalize_asset_key(asset_key),
                asset_name=w.asset_name if w else asset_key,
                etf_code={
                    "oil": "162411", "gold": "518880", "bond": "511010",
                    "nasdaq": "513100", "hk_tech": "513130", "a_share": "159919", "cash": "GC001",
                    "star_50": "588000", "nikkei": "513880", "india": "164824", "cb_bond": "511380",
                }.get(asset_key, ""),
                category={
                    "oil": "commodity", "gold": "commodity", "bond": "bond",
                    "nasdaq": "overseas_equity", "hk_tech": "overseas_equity",
                    "a_share": "domestic_equity", "cash": "cash",
                    "star_50": "domestic_equity", "nikkei": "overseas_equity",
                    "india": "overseas_equity", "cb_bond": "domestic_hybrid",
                }.get(asset_key, ""),
                target_weight=w.target_weight if w else 0.0,
                current_weight=w.current_weight if w else 0.0,
                score=s.total_score if s else 0.0,
                recommendation=s.recommendation if s else "neutral",
                method="stable_bl_mvo" if use_stable else "risk_parity_active",
            )
        )

    # Build rebalance_suggestions
    rebalance_actions = []
    for suggestion in plan.suggestions:
        for asset_key in all_asset_keys:
            w = plan.weights.get(asset_key)
            if w and w.asset_name and w.asset_name in suggestion:
                urgency = "now" if abs(w.drift) > 0.10 else ("soon" if abs(w.drift) > 0.05 else "normal")
                rebalance_actions.append(
                    RebalanceAction(
                        asset=normalize_asset_key(asset_key),
                        asset_name=w.asset_name,
                        action=w.action if hasattr(w, 'action') else "持有",
                        current_weight=w.current_weight if hasattr(w, 'current_weight') else 0.0,
                        target_weight=w.target_weight,
                        drift=w.drift if hasattr(w, 'drift') else 0.0,
                        suggestion=suggestion,
                        urgency=urgency,
                    )
                )
                break

    # Build mode_override
    if use_stable and regime_source == "hmm":
        # HMM regime drives the mode — no hard caps from CLI mode arg
        hmm_mode = stable_regime  # risk_on / neutral / risk_off

        # Build note with divergence status if available
        note_parts = [
            f"HMM regime: {REGIME_LABELS_CN.get(stable_regime, stable_regime)}.",
            f"3m transition risk: {trans_risk if 'trans_risk' in dir() else 'N/A'}",
        ]
        if div_status and div_status.diverged:
            note_parts.append(
                f"DIVERGENCE: Macro={div_status.macro_regime}, "
                f"weeks={div_status.divergence_weeks}, level={div_status.level}"
            )

        mode_override = ModeOverride(
            mode=hmm_mode,
            note=" ".join(note_parts),
        )
    elif mode == "risk_off":
        mode_override = ModeOverride(
            mode="risk_off",
            a_share_cap=0.15,
            bond_floor=0.25,
            gold_floor=0.15,
            cash_floor=0.20,
            note=f"连续亏损熔断触发，自动进入 risk_off。{regime_result.summary[:100]}",
        )
    else:
        mode_override = ModeOverride(mode=mode, note=regime_result.summary[:100])

    # Target weights (normalize keys)
    target_weights = {
        normalize_asset_key(k): w.target_weight
        for k, w in plan.weights.items()
    }

    # Build final allocation object
    # ★ P0: STABLE 模式下生成与权重逻辑一致的 summary
    if use_stable:
        summary = _build_stable_summary(
            stable_regime=stable_regime,
            mvo_result=mvo_result,
            bond_yield_val=bond_yield_val,
            div_status=div_status,
            asset_weights=mvo_result.asset_weights,
            asset_names_map={
                "oil": "华宝油气", "gold": "华安黄金ETF", "bond": "国债ETF",
                "nasdaq": "纳指ETF", "hk_tech": "恒生科技ETF", "a_share": "沪深300ETF",
                "star_50": "科创50ETF", "nikkei": "日经225ETF",
                "india": "印度基金LOF", "cb_bond": "转债ETF",
            },
        )
    else:
        summary = regime_result.summary

    allocation = AssetAllocation(
        version=CONTRACT_VERSION,
        updated=today,
        generated_at=datetime.now().isoformat(),
        macro_quadrant=macro_quadrant,
        target_weights=target_weights,
        current_weights={
            normalize_asset_key(k): w.current_weight
            for k, w in plan.weights.items()
        },
        asset_details=asset_details,
        rebalance_suggestions=rebalance_actions,
        total_drift=plan.total_drift,
        needs_rebalance=plan.needs_rebalance,
        mode_override=mode_override,
        constraints_check=plan.constraints_check,
        summary=summary,
    )

    allocation_dict = signal_to_dict(allocation)

    if dry_run:
        print("\n  [DRY RUN] 不写入文件，输出 JSON:")
        print(json.dumps(allocation_dict, ensure_ascii=False, indent=2))
    else:
        success = save_allocation(allocation)
        if not success:
            step_error("保存失败")
            return None

    step_ok((datetime.now() - t5).total_seconds())

    # ── Summary ──
    total_s = (datetime.now() - t0).total_seconds()
    print(f"\n{'='*60}")
    print(f"  管线完成 ({total_s:.1f}s)")
    print(f"  象限: {regime_label}")
    print(f"  最优资产: {', '.join(regime_result.get_best_assets(3))}")
    print(f"  输出: {FM_TRADE_DATA / 'asset_allocation.json'}")
    print(f"{'='*60}")

    return allocation_dict


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════


def run_backtest(
    start_date: str = "2005-01-01",
    end_date: Optional[str] = None,
    rebalance_freq: str = "W-MON",
    output_dir: Optional[Path] = None,
) -> int:
    """运行 Layer 0 大类资产配置回测（三策略对比）

    Args:
        start_date: 回测起始日期
        end_date: 回测结束日期，默认昨天
        rebalance_freq: 调仓频率 (W-MON=周频, M=月频, Q=季频)
        output_dir: 结果输出目录，默认 FM_trade data/output/

    Returns:
        exit code (0=success, 1=failure)
    """
    import json

    from src.macro_allocation.macro_backtest import MacroBacktester

    if end_date is None:
        end_date = (datetime.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    PROXY_DIR = Path("d:/AgentAlpha/signal-factory/data/macro_assets/backtest_proxy")

    t0 = datetime.now()
    print("=" * 60)
    print("  Layer 0 大类资产配置回测")
    print(f"  周期: {start_date} ~ {end_date}  频率: {rebalance_freq}")
    print("=" * 60)

    # Step 1: 加载代理数据
    print("\n[1/3] 加载代理数据...")
    asset_files = {
        "oil": "oil.csv", "gold": "gold.csv", "bond": "bond.csv",
        "nasdaq": "nasdaq.csv", "hk_tech": "hk_tech.csv",
        "a_share": "a_share.csv", "cash": "cash.csv",
        "star_50": "star_50.csv", "nikkei": "nikkei.csv",
        "india": "india.csv", "cb_bond": "cb_bond.csv",
    }
    price_data: Dict[str, pd.DataFrame] = {}
    missing = []
    for asset_id, fname in asset_files.items():
        path = PROXY_DIR / fname
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"], index_col="date")
            df = df[["close"]].dropna()
            if len(df) > 0:
                price_data[asset_id] = df
                print(f"  {asset_id}: {len(df)} rows, {df.index[0].date()} ~ {df.index[-1].date()}")
            else:
                missing.append(asset_id)
        else:
            missing.append(asset_id)

    if missing:
        print(f"\n  ⚠️ 缺失代理数据: {missing}")
        print(f"  请先运行: python scripts/download_backtest_proxy.py")
        return 1

    if len(price_data) < 7:
        print(f"  ERROR: 仅有 {len(price_data)} 类资产，至少需要 7 类")
        return 1

    # Auto-adjust start_date to the latest common start among all loaded assets
    asset_starts = [df.index[0] for df in price_data.values() if len(df) > 0]
    common_start = max(asset_starts)
    requested_start = pd.Timestamp(start_date)
    if common_start > requested_start:
        limiting_asset = min(
            [(k, df.index[0]) for k, df in price_data.items() if len(df) > 0],
            key=lambda x: x[1]
        )
        print(f"\n  ⚠️ 数据限制: 最早共同起始日 {common_start.date()} (请求 {start_date})")
        print(f"     限制因素: {limiting_asset[0]} (起始于 {limiting_asset[1].date()})")
        start_date = common_start.strftime("%Y-%m-%d")

    print(f"\n  有效回测区间: {start_date} ~ {end_date}  ({len(price_data)} 类资产)")
    for asset_id in sorted(price_data.keys()):
        df = price_data[asset_id]
        print(f"    {asset_id}: {df.index[0].date()} ~ {df.index[-1].date()}")

    # Step 2: 运行三策略对比
    print("\n[2/3] 运行三策略对比回测...")
    bt = MacroBacktester()

    try:
        results = bt.run_comparison(
            price_data,
            rebalance_freq=rebalance_freq,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        print(f"  ERROR: 回测失败: {e}")
        traceback.print_exc()
        return 1

    # Step 3: 输出结果
    t1 = datetime.now()
    print(f"\n[3/3] 回测完成 ({((t1 - t0).total_seconds()):.1f}s)\n")

    strategy_labels = {
        "macro_active": "风险平价+主动观点",
        "equal_weight": "等权配置",
        "60_40": "60/40 股债",
    }

    print(f"{'策略':<25} {'年化收益':>10} {'年化波动':>10} {'Sharpe':>8} {'最大回撤':>10} {'Calmar':>8}")
    print("-" * 75)

    best_sharpe = -999
    best_name = ""

    for name, r in results.items():
        label = strategy_labels.get(name, name)
        print(f"{label:<25} {r.annual_return:>9.2%} {r.annual_volatility:>9.2%} "
              f"{r.sharpe_ratio:>8.2f} {r.max_drawdown:>9.2%} {r.calmar_ratio:>8.2f}")
        if r.sharpe_ratio > best_sharpe:
            best_sharpe = r.sharpe_ratio
            best_name = label

    print("-" * 75)

    # 基准对比
    print(f"\n  ★ 最优策略: {best_name} (Sharpe={best_sharpe:.2f})")
    print(f"  基准 (CSI 300): 年化={results['macro_active'].benchmark_return:.2%}, "
          f"Sharpe={results['macro_active'].benchmark_sharpe:.2f}, "
          f"MaxDD={results['macro_active'].benchmark_max_dd:.2%}")

    # 详细结果
    print(f"\n═══ 风险平价+主动观点 详细结果 ═══")
    print(results["macro_active"].summary())

    # 危机表现
    r = results["macro_active"]
    print("\n  危机压力测试:")
    for crisis, dd in [("2008", r.crisis_2008_dd), ("2015", r.crisis_2015_dd), ("2020", r.crisis_2020_dd)]:
        if dd is not None:
            print(f"    {crisis}: 最大回撤 {dd:.2%}")

    # 保存结果
    if output_dir is None:
        output_dir = Path("d:/AgentAlpha/my-bot/data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    backtest_output = {
        "run_at": datetime.now().isoformat(),
        "period": {"start": start_date, "end": end_date},
        "rebalance_freq": rebalance_freq,
        "risk_free_rate": bt.RISK_FREE_RATE,
        "strategies": {},
    }
    for name, r in results.items():
        backtest_output["strategies"][name] = r.to_dict()

    out_path = output_dir / "layer0_backtest_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(backtest_output, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {out_path}")

    # 保存可读摘要
    summary_path = output_dir / "_layer0_backtest_summary.md"
    lines = [
        "## Layer 0 大类资产配置回测结果",
        "",
        f"> 周期: {start_date} ~ {end_date} | 调仓: {rebalance_freq} | 无风险利率: {bt.RISK_FREE_RATE:.1%}",
        "",
        "### 三策略对比",
        "",
        f"| 策略 | 年化收益 | 年化波动 | Sharpe | 最大回撤 | Calmar |",
        f"|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        label = strategy_labels.get(name, name)
        lines.append(f"| {label} | {r.annual_return:.2%} | {r.annual_volatility:.2%} | "
                     f"{r.sharpe_ratio:.2f} | {r.max_drawdown:.2%} | {r.calmar_ratio:.2f} |")
    lines.append("")
    lines.append(f"基准 (CSI 300): 年化 {results['macro_active'].benchmark_return:.2%}, "
                 f"Sharpe {results['macro_active'].benchmark_sharpe:.2f}, "
                 f"MaxDD {results['macro_active'].benchmark_max_dd:.2%}")
    lines.append("")
    lines.append("### 验证检查")
    lines.append("")
    for check, passed in (results["macro_active"].checks or {}).items():
        icon = "✅" if passed else "❌"
        lines.append(f"- {icon} {check}")
    lines.append("")
    lines.append(f"<!-- LAYER0_BACKTEST_COMPLETE -->")
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  摘要已保存: {summary_path}")

    return 0


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Layer 0 宏观资产配置 — 日常配置 + 回测"
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # ── 日常配置子命令 (默认) ──
    alloc_parser = sub.add_parser("allocate", help="日常宏观资产配置 (默认)")
    alloc_parser.add_argument(
        "--mode", choices=["normal", "risk_off", "earnings_season"],
        default="risk_off",
        help="配置模式 (默认: risk_off)"
    )
    alloc_parser.add_argument(
        "--skip-collect", action="store_true",
        help="跳过数据采集，仅用缓存"
    )
    alloc_parser.add_argument(
        "--dry-run", action="store_true",
        help="打印结果但不写入文件"
    )
    alloc_parser.add_argument(
        "--stable", action="store_true",
        help="使用 STABLE BL+MVO 替代风险平价+主动观点"
    )
    alloc_parser.add_argument(
        "--stable-shrinkage", type=float, default=0.5,
        help="STABLE Bayes shrinkage (0=纯条件, 1=纯无条件, 默认 0.5)"
    )
    alloc_parser.add_argument(
        "--regime", choices=["rule", "hmm"], default="rule",
        help="Regime 分类器: rule (6象限规则, 默认) / hmm (GaussianHMM 无监督)"
    )

    # ── 回测子命令 ──
    bt_parser = sub.add_parser("backtest", help="大类资产配置回测 (三策略对比)")
    bt_parser.add_argument(
        "--start", default="2005-01-01",
        help="起始日期 (默认: 2005-01-01)"
    )
    bt_parser.add_argument(
        "--end", default=None,
        help="结束日期 (默认: 昨天)"
    )
    bt_parser.add_argument(
        "--freq", default="W-MON",
        choices=["W-MON", "M", "Q"],
        help="调仓频率 (默认: W-MON 每周一)"
    )

    # 向后兼容: 无子命令时默认为 allocate
    raw_args = sys.argv[1:]
    if raw_args and raw_args[0] not in ("allocate", "backtest", "-h", "--help"):
        raw_args = ["allocate"] + raw_args

    args = parser.parse_args(raw_args)

    try:
        if args.command == "backtest":
            exit_code = run_backtest(
                start_date=args.start,
                end_date=args.end,
                rebalance_freq=args.freq,
            )
            sys.exit(exit_code)
        else:
            result = run_pipeline(
                mode=args.mode,
                skip_collect=args.skip_collect,
                dry_run=args.dry_run,
                use_stable=args.stable,
                stable_shrinkage=args.stable_shrinkage,
                regime_source=args.regime,
            )
            if result is None:
                sys.exit(1)
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"  管线失败: {e}")
        print(f"{'='*60}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()