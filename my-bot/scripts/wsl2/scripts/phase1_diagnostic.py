#!/usr/bin/env python3
"""
phase1_diagnostic.py — Phase 1 因子工厂诊断评估 (v2, simplified)

全部使用 FFO FactorEvaluator，只做 2024-2026（GFlowNet 训练区间）。
生存因子从 Qlib bin 读取后手动计算 IC。

输出: results/phase1_diagnostic.json
"""

import json, sys, time
from datetime import datetime
from pathlib import Path
from collections import OrderedDict

import numpy as np
import pandas as pd

# Ensure ffo and gflownet modules are importable
SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

QLIB_BIN = Path("/mnt/d/qlib/qlib_data/cn_data")
FEATURES_DIR = QLIB_BIN / "features"
OUTPUT_DIR = Path("/mnt/d/AgentAlpha/my-bot/scripts/wsl2/results")

# ─── Factors ───

GFLOWNET_UNIQUE = [
    "Scale($vwap)", "Log($vwap)", "($vwap * $high)",
    "((120 / $vwap) * 250)", "(Sign(($returns + 20)) <= 10)",
    "Max($vwap, $volume)", "Abs($returns)", "Scale(($returns - 5))",
]

BASELINE_FIELDS = ["$vwap", "$returns", "$close", "$volume", "$high", "$open", "$low"]

CANONICAL = [
    "($close - Delay($close, 20)) / Ts_Std($close, 20)",
    "Ts_Mean($returns, 5)", "Ts_Mean($returns, 20)",
    "Ts_Mean(Return($close, 1), 5)",
    "Rank(Ts_Mean($volume, 20))",
    "Ts_Corr($close, $volume, 20)",
    "If(($volume > Ts_Mean($volume, 20)), Rank($returns), 0)",
    "($high - $low) / $close",
    "Ts_Mean(Rank($returns), 10)",
    "Ts_Rank($close, 20)",
]

LLM_AGENT_TOP = [
    "Rank(Return($close, 20))",
    "Rank(Sign($returns) * SignedPower(Abs($returns), 0.5))",
    "Rank(Ts_Sum($returns, 20) / Ts_Std($returns, 20))",
    "Rank(Delta($close, 5) / Delay($close, 5))",
    "Rank(Ts_ArgMax($close, 60) - Ts_ArgMin($close, 60))",
    "Rank(-Ts_ArgMin($close, 60))",
    "Rank(Ts_ArgMax($close, 20))",
    "Rank(Ts_Std(If($returns > 0, $returns, 0), 20) / (Ts_Std($returns, 20) + 0.0001))",
]

ALL_FFO = OrderedDict()
ALL_FFO["gflownet"] = GFLOWNET_UNIQUE
ALL_FFO["baseline"] = BASELINE_FIELDS
ALL_FFO["canonical"] = CANONICAL
ALL_FFO["llm_agent"] = LLM_AGENT_TOP

# Interval: match GFlowNet training
EVAL_START = "2024-01-01"
EVAL_END = "2026-09-30"
UNIVERSE = "csi500"


def main():
    print(f"\n{'='*60}")
    print("Phase 1 Factor Factory — Diagnostic Evaluation")
    print(f"Period: {EVAL_START} → {EVAL_END} ({UNIVERSE})")
    print(f"{'='*60}\n")

    # Init Qlib + FFO
    print("Initializing Qlib + FFO evaluator...")
    t0 = time.time()
    import qlib
    qlib.init(provider_uri="/mnt/d/qlib/qlib_data/cn_data", region="cn")
    from ffo.evaluator import FactorEvaluator
    evaluator = FactorEvaluator()
    print(f"  Ready in {time.time()-t0:.1f}s\n")

    # Evaluate all FFO factors
    all_specs = [(cat, f) for cat, formulas in ALL_FFO.items() for f in formulas]
    results = {}

    print(f"Evaluating {len(all_specs)} FFO factors...")
    for i, (category, formula) in enumerate(all_specs):
        label = f"{category}:{formula}"
        t1 = time.time()
        try:
            r = evaluator.evaluate(formula, start=EVAL_START, end=EVAL_END, universe=UNIVERSE)
            ic = float(getattr(r, 'ic_mean', getattr(r, 'ic', 0)))
            rank_ic = float(getattr(r, 'rank_ic_mean', getattr(r, 'rank_ic', 0)))
            icir = float(getattr(r, 'ic_ir', 0))
            error = None
            status = f"IC={ic:.4f}, RankIC={rank_ic:.4f}"
        except Exception as e:
            ic, rank_ic, icir = 0, 0, 0
            error = str(e)[:120]
            status = f"ERROR: {error}"

        results[label] = {
            "category": category, "formula": formula,
            "ic_mean": ic, "rank_ic": rank_ic, "ic_ir": icir,
            "elapsed_s": round(time.time()-t1, 2), "error": error,
        }
        print(f"  [{i+1:2d}/{len(all_specs)}] {label[:55]:<55s} {status}")

    # ─── Survival factors from Qlib bin ───
    print("\nEvaluating 5 survival factors from Qlib bin...")
    from phase1_diagnostic_bin import evaluate_survival_factors

    survival_ics = evaluate_survival_factors(EVAL_START, EVAL_END, UNIVERSE)
    for name, ic_res in survival_ics.items():
        label = f"survival:{name}"
        results[label] = {
            "category": "survival", "formula": name,
            "ic_mean": ic_res["ic_mean"], "rank_ic": ic_res["ic_mean"],
            "ic_ir": ic_res["ic_ir"], "n_days": ic_res["n_days"],
            "error": None,
        }
        print(f"  {name}: IC={ic_res['ic_mean']:.4f}, IR={ic_res['ic_ir']:.3f}")

    # ─── Summary ───
    print(f"\n{'='*60}")
    print("Top 15 by |IC|:")
    print(f"{'='*60}")
    valid = {k: v for k, v in results.items() if v["error"] is None and v["ic_mean"] != 0}
    sorted_results = sorted(valid.items(), key=lambda x: -abs(x[1]["ic_mean"]))
    for i, (label, d) in enumerate(sorted_results[:15]):
        print(f"  {i+1:2d}. {d['ic_mean']:+.4f}  {d['category']:<10s} {d['formula']}")

    # ─── Category summary ───
    print(f"\nBy category:")
    for cat in ["baseline", "gflownet", "canonical", "llm_agent", "survival"]:
        cat_results = [v for v in results.values() if v["category"] == cat and v["error"] is None]
        if cat_results:
            best = max(cat_results, key=lambda x: abs(x["ic_mean"]))
            avg_ic = np.mean([abs(r["ic_mean"]) for r in cat_results])
            n = len(cat_results)
            print(f"  {cat:<12s}: n={n}, best={best['ic_mean']:+.4f} ({best['formula'][:40]}), avg|IC|={avg_ic:.4f}")

    # ─── Save ───
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "metadata": {
            "date": datetime.now().isoformat(),
            "eval_start": EVAL_START, "eval_end": EVAL_END,
            "universe": UNIVERSE,
        },
        "results": results,
    }
    out_path = OUTPUT_DIR / "phase1_diagnostic.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())