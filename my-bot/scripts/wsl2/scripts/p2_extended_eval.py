#!/usr/bin/env python3
"""
P2: GFlowNet Factor Extended Evaluation — 跨宇宙 + 长窗口重评

从 FactorRegistry 加载所有 gflownet 因子，在扩展评估窗口上重新评估：
  - 窗口: 2018-01-01 → 2026-09-30 (9年)
  - 宇宙: CSI500 + CSI300 双宇宙
  - 指标: IC_csi500, IC_csi300, cross_universe_mean_IC, cross_universe_min_IC

结果写回 FactorRegistry 并导出评估报告。

用法:
  cd D:\AgentAlpha\my-bot\scripts\wsl2
  python scripts/p2_extended_eval.py [--dry-run] [--min-ic 0.02]
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
WSL2_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(WSL2_DIR))


def evaluate_factor(
    formula: str,
    data: pd.DataFrame,
    returns: pd.DataFrame,
    computer,
    start_date: str,
    end_date: str,
) -> Optional[float]:
    """
    Evaluate a single factor formula on the given data.

    Returns:
        IC mean, or None if evaluation fails.
    """
    from ffo.parser import parse as ffo_parse

    try:
        ast = ffo_parse(formula)
        factor_values = computer.evaluate(ast)
        # factor_values is date × instrument matrix
        factor_values = factor_values[factor_values.index >= pd.Timestamp(start_date)]

        if factor_values.empty or factor_values.dropna(how="all").empty:
            return None

        # Use forward returns (next-day), matching RewardComputer._compute_ic_fast
        forward_returns = returns.shift(-1)

        # Align factor matrix with forward returns
        common_dates = factor_values.index.intersection(forward_returns.index)
        common_stocks = factor_values.columns.intersection(forward_returns.columns)

        if len(common_dates) < 10 or len(common_stocks) < 10:
            return None

        f_aligned = factor_values.loc[common_dates, common_stocks]
        r_aligned = forward_returns.loc[common_dates, common_stocks]

        ic_daily = []
        for date in common_dates:
            fv = factor_values.loc[date].dropna()
            fr = forward_returns.loc[date].dropna()
            common = fv.index.intersection(fr.index)
            if len(common) < 30:
                continue
            # Pearson correlation matching RewardComputer
            ic = fv.loc[common].corr(fr.loc[common])
            if not np.isnan(ic):
                ic_daily.append(ic)

        if not ic_daily:
            return None

        return float(np.mean(ic_daily))

    except Exception:
        return None


def load_universe_data(
    universe: str,
    start: str,
    end: str,
    data_dir: str = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, object]:
    """
    Load Qlib data for a universe. Returns (data, returns, computer).
    """
    from ffo.data_loader import load_qlib_data, get_returns_matrix
    from ffo.computer import FactorComputer

    # Extend start for warmup
    data_start = pd.Timestamp(start) - pd.DateOffset(days=365)
    data_start_str = data_start.strftime("%Y-%m-%d")

    data = load_qlib_data(
        data_dir=data_dir,
        universe=universe,
        start=data_start_str,
        end=end,
    )

    returns = get_returns_matrix(data)
    returns = returns[returns.index >= pd.Timestamp(start)]

    computer = FactorComputer(data)

    return data, returns, computer


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extended factor evaluation across universes")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate but don't update registry")
    parser.add_argument("--min-ic", type=float, default=0.0,
                        help="Minimum |IC| to include in report")
    parser.add_argument("--start", type=str, default="2018-01-01",
                        help="Eval start date")
    parser.add_argument("--end", type=str, default="2026-09-30",
                        help="Eval end date")
    parser.add_argument("--universes", type=str, default="csi500,csi300",
                        help="Comma-separated universes")
    parser.add_argument("--channel", type=str, default="gflownet",
                        help="FactorRegistry channel")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to N factors (0=all)")
    parser.add_argument("--registry", type=str, default=None,
                        help="Path to factor_registry.json")
    args = parser.parse_args()

    universes = [u.strip() for u in args.universes.split(",")]

    # ─── 1. Load FactorRegistry ───
    from factory.metadata import FactorRegistry

    registry = FactorRegistry(storage_path=args.registry)
    all_factors = registry.get_by_channel(args.channel)

    if args.limit > 0:
        all_factors = all_factors[:args.limit]

    print(f"[Registry] {len(all_factors)} factors from channel '{args.channel}'")
    print(f"[Eval] Window: {args.start} -> {args.end}")
    print(f"[Eval] Universes: {universes}")

    # ─── 2. Load data for each universe ───
    print("\nLoading data...")
    universe_data = {}
    for universe in universes:
        t0 = time.time()
        data, returns, computer = load_universe_data(universe, args.start, args.end)
        n_stocks = len(data.index.get_level_values(0).unique())
        n_dates = len(data.index.get_level_values(1).unique())
        print(f"  {universe}: {n_stocks} stocks × {n_dates} dates ({time.time() - t0:.1f}s)")
        universe_data[universe] = (data, returns, computer)

    # ─── 3. Evaluate all factors ───
    print(f"\nEvaluating {len(all_factors)} factors on {len(universes)} universes...")
    results = []

    for i, meta in enumerate(all_factors):
        formula = meta.formula
        ic_by_universe = {}

        for universe in universes:
            data, returns, computer = universe_data[universe]
            ic = evaluate_factor(formula, data, returns, computer, args.start, args.end)
            ic_by_universe[universe] = ic

        # Cross-universe metrics
        valid_ics = {u: ic for u, ic in ic_by_universe.items() if ic is not None}
        if valid_ics:
            mean_ic = float(np.mean(list(valid_ics.values())))
            min_ic = float(np.min(list(valid_ics.values())))

            # Stability: std of ICs across universes (lower = more stable)
            stability = float(np.std(list(valid_ics.values()))) if len(valid_ics) > 1 else 0.0
        else:
            mean_ic = None
            min_ic = None
            stability = None

        result = {
            "formula": formula,
            "ic_by_universe": ic_by_universe,
            "cross_mean_ic": mean_ic,
            "cross_min_ic": min_ic,
            "cross_stability": stability,
            "original_ic": meta.ic_mean,
            "n_valid_universes": len(valid_ics),
        }
        results.append(result)

        if (i + 1) % 20 == 0:
            n_with_ic = sum(1 for r in results if r["cross_mean_ic"] is not None)
            print(f"  [{i + 1}/{len(all_factors)}] {n_with_ic} with valid IC")

    # ─── 4. Analyze results ───
    valid_results = [r for r in results if r["cross_mean_ic"] is not None]
    invalid_results = [r for r in results if r["cross_mean_ic"] is None]

    print(f"\n{'='*60}")
    print(f"Results Summary")
    print(f"{'='*60}")
    print(f"  Total factors:     {len(results)}")
    print(f"  Valid (multi-U):   {len(valid_results)}")
    print(f"  Invalid:           {len(invalid_results)}")

    if valid_results:
        mean_ics = [r["cross_mean_ic"] for r in valid_results]
        min_ics = [r["cross_min_ic"] for r in valid_results]
        stabilities = [r["cross_stability"] for r in valid_results]

        print(f"  Cross-mean |IC|:   {np.mean(np.abs(mean_ics)):.4f} (max={np.max(np.abs(mean_ics)):.4f})")
        print(f"  Cross-min |IC|:    {np.mean(np.abs(min_ics)):.4f} (max={np.max(np.abs(min_ics)):.4f})")
        print(f"  Mean stability:    {np.mean(stabilities):.4f} (lower=more stable)")

        # Surviving factors (valid on all universes)
        n_all_universes = sum(1 for r in valid_results
                              if r["n_valid_universes"] == len(universes))
        print(f"  Valid on all U:    {n_all_universes}/{len(results)}")

        # 按 cross_min_ic 排名
        sorted_by_min_ic = sorted(
            valid_results,
            key=lambda r: abs(r["cross_min_ic"]) if r["cross_min_ic"] is not None else 0,
            reverse=True,
        )

        print(f"\nTop 10 Factors by Cross-Universe Min |IC|:")
        for r in sorted_by_min_ic[:10]:
            ic_str = " | ".join(
                f"{u}: {ic:.4f}" if ic is not None else f"{u}: N/A"
                for u, ic in r["ic_by_universe"].items()
            )
            print(f"  {r['formula'][:70]}")
            print(f"    ICs=[{ic_str}] cross_min={r['cross_min_ic']:.4f} "
                  f"stability={r['cross_stability']:.4f}")

    # ─── 5. Update FactorRegistry ───
    if not args.dry_run and valid_results:
        print(f"\n[Update] Adding cross-universe IC records to FactorRegistry...")
        from factory.dedup import normalize_formula

        updated = 0
        for r in valid_results:
            key = normalize_formula(r["formula"])
            if key in registry._index:
                meta = registry._index[key]
                # Add cross-universe IC record
                meta.add_ic_record(
                    ic=r["cross_mean_ic"],
                    evaluator=f"ffo_cross_universe_{args.start}_{args.end}",
                )
                updated += 1

        registry._save()
        print(f"[Update] {updated} factors updated with cross-universe IC records")

    # ─── 6. Export report ───
    report = {
        "eval_meta": {
            "date": datetime.now().isoformat(),
            "start": args.start,
            "end": args.end,
            "universes": universes,
            "channel": args.channel,
            "total_factors": len(results),
            "valid_factors": len(valid_results),
            "invalid_factors": len(invalid_results),
        },
        "summary": {
            "cross_mean_abs_ic": float(np.mean(np.abs(mean_ics))) if valid_results else None,
            "cross_min_abs_ic": float(np.mean(np.abs(min_ics))) if valid_results else None,
            "mean_stability": float(np.mean(stabilities)) if valid_results else None,
            "n_valid_all_universes": n_all_universes if valid_results else 0,
        },
        "top_factors": [
            {
                "formula": r["formula"],
                "ics": {u: round(ic, 4) if ic is not None else None
                        for u, ic in r["ic_by_universe"].items()},
                "cross_mean_ic": round(r["cross_mean_ic"], 4) if r["cross_mean_ic"] else None,
                "cross_min_ic": round(r["cross_min_ic"], 4) if r["cross_min_ic"] else None,
                "stability": round(r["cross_stability"], 4) if r["cross_stability"] else None,
            }
            for r in sorted_by_min_ic[:20]
        ] if valid_results else [],
    }

    report_dir = WSL2_DIR / "data"
    report_path = report_dir / f"gfn_cross_universe_eval_{datetime.now().strftime('%Y%m%d')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[Report] Saved to {report_path}")

    # Also save as markdown summary
    md_path = report_dir / f"gfn_cross_universe_eval_{datetime.now().strftime('%Y%m%d')}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# GFlowNet v2.2 Cross-Universe Evaluation\n\n")
        f.write(f"- **Date:** {datetime.now().strftime('%Y-%m-%d')}\n")
        f.write(f"- **Window:** {args.start} → {args.end}\n")
        f.write(f"- **Universes:** {', '.join(universes)}\n")
        f.write(f"- **Factors evaluated:** {len(results)}\n")
        f.write(f"- **Valid (all universes):** {n_all_universes if valid_results else 0}\n\n")

        if valid_results:
            f.write(f"## Summary\n\n")
            f.write(f"| Metric | Value |\n")
            f.write(f"|--------|-------|\n")
            f.write(f"| Cross-mean |IC|| {np.mean(np.abs(mean_ics)):.4f} |\n")
            f.write(f"| Cross-min |IC|| {np.mean(np.abs(min_ics)):.4f} |\n")
            f.write(f"| Max cross-min |IC|| {np.max(np.abs(min_ics)):.4f} |\n")
            f.write(f"| Mean stability | {np.mean(stabilities):.4f} |\n\n")

            f.write(f"## Top 10 Factors (by cross-universe min |IC|)\n\n")
            f.write(f"| # | Formula | CSI500 IC | CSI300 IC | Cross-Mean IC | Cross-Min IC | Stability |\n")
            f.write(f"|---|---------|-----------|-----------|---------------|-------------|----------|\n")
            for j, r in enumerate(sorted_by_min_ic[:10]):
                ics = r["ic_by_universe"]
                ic500 = f"{ics.get('csi500', 0):.4f}" if ics.get('csi500') else "N/A"
                ic300 = f"{ics.get('csi300', 0):.4f}" if ics.get('csi300') else "N/A"
                f.write(f"| {j+1} | `{r['formula'][:50]}` | {ic500} | {ic300} | "
                        f"{r['cross_mean_ic']:.4f} | {r['cross_min_ic']:.4f} | "
                        f"{r['cross_stability']:.4f} |\n")

    print(f"[Report] Markdown saved to {md_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())