#!/usr/bin/env python3
"""
RD-Agent Simplified: Paper-Derived Factor Discovery

Implements 10 alpha factors from recent (2024-2026) academic papers,
using fields BEYOND the GFlowNet grammar space (especially fundamental data).

Each factor is tested against CSI500 (2015-2024) through the FFO evaluator.
Gates: Gate 1 (|IC| > 0.03) → Gate 2 (|IC| > 0.0986, beat GFlowNet ceiling)

Key innovation over GFlowNet: access to ROE, EPS, BPS, gross_margin fields
plus derived technical fields (turnover, Amihud illiquidity, tail risk).
"""

import sys
import os
import json
from pathlib import Path

# Add factor-lab root to path (parent of scripts/)
FACTOR_LAB = Path(__file__).resolve().parent.parent  # wsl2/
sys.path.insert(0, str(FACTOR_LAB))

from ffo.evaluator import FactorEvaluator


# ─── Factor Definitions ──────────────────────────────────────────
# Each entry: (name, formula, paper_source, description)

FACTORS = [
    # ── Fundamental Cross-Section (4 factors) ──
    {
        "name": "roe_momentum",
        "formula": "Ts_Mean($roe, 20) - Ts_Mean($roe, 60)",
        "paper": "Fama-French 2015 (Profitability factor); Hou-Xue-Zhang 2015 (q-factor)",
        "description": "ROE short-term minus long-term: profitability momentum. Uses $roe field (NOT in GFlowNet grammar).",
    },
    {
        "name": "eps_surprise",
        "formula": "($eps - Ts_Mean($eps, 60)) / Ts_Std($eps, 60)",
        "paper": "Post-earnings-announcement drift literature; Ball & Brown 1968 extended",
        "description": "EPS deviation from 60-day mean normalized by volatility. Standardized unexpected earnings.",
    },
    {
        "name": "bps_growth_ratio",
        "formula": "$bps / Ts_Mean($bps, 60) - 1",
        "paper": "Fama-French 2015 (Investment factor); asset growth anomaly",
        "description": "Book-value-per-share growth: BPS relative to 60-day average. Low BPS growth=conservative=premium.",
    },
    {
        "name": "margin_stability",
        "formula": "-1 * Ts_Std($gross_margin, 60) / Abs(Ts_Mean($gross_margin, 60))",
        "paper": "Novy-Marx 2013 (Gross profitability); Akbas et al. 2017",
        "description": "Gross margin stability (negative CV). Stable margins = quality signal. Uses $gross_margin (NOT in GFlowNet).",
    },

    # ── Volume/Liquidity Anomalies (3 factors) ──
    {
        "name": "amihud_illiq",
        "formula": "-1 * Ts_Mean(Abs($returns) / ($volume * $close), 20)",
        "paper": "Amihud 2002 (Illiquidity premium); Lou & Shu 2017",
        "description": "Amihud illiquidity: |return| per dollar volume. Higher illiquidity = premium (negative sign for IC).",
    },
    {
        "name": "volume_shock",
        "formula": "($volume - Ts_Mean($volume, 20)) / Ts_Std($volume, 20)",
        "paper": "Gervais-Kaniel-Mingelgrin 2001; Barber-Odean 2008 (attention)",
        "description": "Volume shock: abnormal volume relative to 20-day baseline. High attention = short-term reversal.",
    },
    {
        "name": "dollar_volume_trend",
        "formula": "Ts_Mean($volume * $close, 5) / Ts_Mean($volume * $close, 20) - 1",
        "paper": "Lo-Wang 2000; Chordia-Subrahmanyam-Anshuman 2001",
        "description": "Dollar volume trend: short-term vs medium-term liquidity change.",
    },

    # ── Cross-Sectional Price Patterns (3 factors) ──
    {
        "name": "overnight_gap",
        "formula": "($open - Ts_Mean($close, 1)) / Ts_Mean($close, 1)",
        "paper": "Berkman-Koch-Tuttle 2012; Lou-Polk-Skouras 2019 (overnight anomaly)",
        "description": "Overnight gap: open vs previous close. Large overnight gaps signal information shocks.",
    },
    {
        "name": "intraday_range",
        "formula": "Ts_Mean(($high - $low) / $close, 5)",
        "paper": "Parkinson 1980; Alizadeh-Brandt-Diebold 2002 (range-based volatility)",
        "description": "Average intraday range as % of close. Higher range = higher volatility = risk premium or reversal.",
    },
    {
        "name": "close_position",
        "formula": "($close - $low) / ($high - $low + 0.0001)",
        "paper": "Blau-van Ness-van Ness 2009 (closing price positioning)",
        "description": "Close position within day's range. Close near high = bullish sentiment, near low = bearish.",
    },
]


def main():
    evaluator = FactorEvaluator(data_dir=None)  # Uses default Qlib data

    eval_start = "2015-01-05"
    eval_end = "2024-12-31"
    universe = "csi500"
    gflownet_ceiling = 0.0986

    print("=" * 70)
    print("  RD-Agent Factor Discovery — Paper-Derived Alpha Mining")
    print(f"  Universe: {universe}  |  Period: {eval_start} → {eval_end}")
    print(f"  GFlowNet Ceiling: |IC| = {gflownet_ceiling:.4f}")
    print("=" * 70)

    results = []
    passed_gate1 = 0
    passed_gate2 = 0

    for i, factor in enumerate(FACTORS):
        name = factor["name"]
        formula = factor["formula"]
        paper = factor["paper"]

        print(f"\n{'─'*70}")
        print(f"  [{i+1}/10] {name}")
        print(f"  Formula: {formula}")
        print(f"  Paper: {paper}")

        try:
            result = evaluator.evaluate(
                formula,
                start=eval_start,
                end=eval_end,
                universe=universe,
            )

            ic_mean = result.ic_mean
            ic_abs = abs(ic_mean)
            icir = result.icir if hasattr(result, 'icir') else (result.ic_mean / (result.ic_std + 1e-8) if hasattr(result, 'ic_std') else 0)
            coverage = getattr(result, 'coverage', 1.0)
            warnings_list = getattr(result, 'warnings', [])

            gate1 = "PASS" if ic_abs >= 0.03 else "FAIL"
            gate2 = "PASS" if ic_abs > gflownet_ceiling else "FAIL"

            if ic_abs >= 0.03:
                passed_gate1 += 1
            if ic_abs > gflownet_ceiling:
                passed_gate2 += 1

            marker = ""
            if ic_abs > gflownet_ceiling:
                marker = " 🔥 BREAKTHROUGH!"
            elif ic_abs >= 0.03:
                marker = " ✅"

            print(f"  IC={ic_mean:+.4f} (|IC|={ic_abs:.4f})  |  ICIR={icir:.3f}  |  Coverage={coverage:.1%}")
            print(f"  Gate 1 (|IC|>0.03): {gate1}  |  Gate 2 (|IC|>0.0986): {gate2}{marker}")

            if warnings_list:
                print(f"  Warnings: {warnings_list}")

            results.append({
                "name": name,
                "formula": formula,
                "paper": paper,
                "ic_mean": round(ic_mean, 4),
                "ic_abs": round(ic_abs, 4),
                "icir": round(icir, 3),
                "coverage": round(coverage, 4),
                "gate1": gate1,
                "gate2": gate2,
                "warnings": warnings_list,
            })

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "name": name,
                "formula": formula,
                "paper": paper,
                "error": str(e),
                "gate1": "ERROR",
                "gate2": "ERROR",
            })

    # Summary
    print(f"\n{'='*70}")
    print(f"  Results Summary")
    print(f"{'='*70}")
    print(f"{'Name':<25} {'|IC|':<8} {'ICIR':<8} {'Cover':<8} {'Gate1':<8} {'Gate2'}")
    print(f"{'─'*60}")
    for r in results:
        if "error" in r:
            print(f"{r['name']:<25} {'ERROR':<8} {'-':<8} {'-':<8} {'ERROR':<8} {'ERROR'}")
        else:
            print(f"{r['name']:<25} {r['ic_abs']:<8.4f} {r['icir']:<8.3f} {r['coverage']:<8.1%} {r['gate1']:<8} {r['gate2']}")

    print(f"\n  Gate 1 passed: {passed_gate1}/10 (|IC| > 0.03)")
    print(f"  Gate 2 passed: {passed_gate2}/10 (|IC| > {gflownet_ceiling})")

    if passed_gate2 > 0:
        print(f"\n  🔥 BREAKTHROUGH! {passed_gate2} factor(s) exceeded GFlowNet ceiling of {gflownet_ceiling}.")
        print(f"  HYPOTHESIS CONFIRMED: Expanding field space → new alpha beyond grammar ceiling.")
    elif passed_gate1 > 0:
        print(f"\n  ⚠️  {passed_gate1} factor(s) passed Gate 1 but none broke the GFlowNet ceiling.")
        print(f"  Partial evidence: fundamental fields add value but don't exceed existing best factors.")
    else:
        print(f"\n  ✗ 0/10 passed Gate 1. Confirms the previous 0% Gate 1 pass rate finding.")
        print(f"  Search space expansion alone may not be sufficient — factor formula design matters too.")

    # Save results
    output_path = Path(__file__).resolve().parent.parent / "data" / "output" / "rd_agent_factors.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved: {output_path}")

    return 0 if passed_gate2 > 0 else 1


if __name__ == "__main__":
    sys.exit(main())