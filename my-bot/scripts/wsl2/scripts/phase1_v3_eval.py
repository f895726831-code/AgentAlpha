#!/usr/bin/env python3
"""
Phase 1 v3 Evaluation — GFlowNet v3 + LLM Agent v3 因子评估
"""
import json, sys, time, os
from pathlib import Path
from collections import defaultdict
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

EVAL_START = "2015-01-01"
EVAL_END = "2024-12-31"
UNIVERSE = "csi500"
QLIB_DIR = "/mnt/d/qlib/qlib_data/cn_data"
OUTPUT = FACTOR_LAB_DIR / "results" / "phase1_v3_eval.json"

def load_gflownet_pool():
    """Load GFlowNet v3 checkpoint factor pool."""
    import torch
    ckpt_path = FACTOR_LAB_DIR / "checkpoints" / "gfn_v3_clean_200ep.pt"
    if not ckpt_path.exists():
        print(f"ERROR: checkpoint not found at {ckpt_path}")
        return [], {}
    ckpt = torch.load(str(ckpt_path), map_location='cpu', weights_only=False)
    pool = ckpt.get('factor_pool', [])
    stats = ckpt.get('pool_stats', {})
    metrics = ckpt.get('metrics_history', [])
    print(f"Loaded GFlowNet checkpoint: {len(pool)} factors, episode={ckpt.get('episode','?')}")
    print(f"  Pool stats: mean|IC|={float(stats.get('mean_ic', 0)):.4f}, max|IC|={float(stats.get('max_ic', 0)):.4f}")
    if metrics:
        try:
            first, last = metrics[0], metrics[-1]
            fl = first.get('tb_loss', first.get('loss', '?'))
            ll = last.get('tb_loss', last.get('loss', '?'))
            print(f"  Training: loss {fl} → {ll}")
        except:
            pass
    return pool, stats

def load_llm_agent_pool():
    """Load LLM Agent v3 results."""
    result_path = FACTOR_LAB_DIR / "results" / "llm_agent_v3_30r.json"
    if not result_path.exists():
        print(f"ERROR: results not found at {result_path}")
        return [], {}
    with open(result_path) as f:
        data = json.load(f)
    pool = data.get('swarm_summary', {}).get('pool', [])
    stats = data.get('swarm_summary', {})
    print(f"Loaded LLM Agent results: {len(pool)} factors")
    print(f"  Cost: CNY {stats.get('total_cost_rmb', '?'):.4f}, {stats.get('total_formulas','?')} formulas")
    return pool, stats

def evaluate_pool(evaluator, pool, label):
    """Evaluate all factors in a pool. pool = [(formula, ic), ...]"""
    results = []
    for formula, train_ic in pool:
        t0 = time.time()
        try:
            r = evaluator.evaluate(formula, start=EVAL_START, end=EVAL_END, universe=UNIVERSE)
            ic = float(getattr(r, 'ic_mean', getattr(r, 'ic', 0)))
            rank_ic = float(getattr(r, 'rank_ic_mean', getattr(r, 'rank_ic', 0)))
            icir = float(getattr(r, 'ic_ir', 0))
            error = None
        except Exception as e:
            ic, rank_ic, icir = 0, 0, 0
            error = str(e)[:120]
        elapsed = time.time() - t0
        results.append({
            'formula': formula, 'train_ic': train_ic, 'eval_ic': ic,
            'rank_ic': rank_ic, 'icir': icir, 'elapsed': round(elapsed, 2),
            'error': error,
        })
        if error:
            print(f"  [{label}] ERROR: {formula[:60]} -> {error}")
    return results

def get_canonical_factors():
    """Standard canonical factors for comparison."""
    return [
        ("($close - Delay($close, 20)) / Ts_Std($close, 20)", None),
        ("Ts_Mean($returns, 5)", None),
        ("Ts_Mean($returns, 20)", None),
        ("Ts_Mean(Return($close, 1), 5)", None),
        ("Rank(Ts_Mean($volume, 20))", None),
        ("Ts_Corr($close, $volume, 20)", None),
        ("($high - $low) / $close", None),
        ("Ts_Mean(Rank($returns), 10)", None),
        ("Ts_Rank($close, 20)", None),
        ("Rank(Delta($close, 5) / Delay($close, 5))", None),
    ]

def get_baseline_fields():
    return [("$" + f, None) for f in ['close', 'returns', 'volume', 'vwap', 'high', 'low', 'open']]

def main():
    print("=" * 60)
    print("Phase 1 v3 — Factor Factory Evaluation")
    print(f"Period: {EVAL_START} → {EVAL_END} ({UNIVERSE})")
    print("=" * 60)

    # Load factors
    gfn_pool, gfn_stats = load_gflownet_pool()
    llm_pool, llm_stats = load_llm_agent_pool()

    canonical = get_canonical_factors()
    baselines = get_baseline_fields()

    print(f"\nFactors to evaluate:")
    print(f"  GFlowNet v3:  {len(gfn_pool)}")
    print(f"  LLM Agent v3: {len(llm_pool)}")
    print(f"  Canonical:    {len(canonical)}")
    print(f"  Baseline:     {len(baselines)}")
    total = len(gfn_pool) + len(llm_pool) + len(canonical) + len(baselines)
    print(f"  Total:        {total}")

    # Init FFO
    print("\nInitializing FFO evaluator...")
    t0 = time.time()
    import qlib
    qlib.init(provider_uri=QLIB_DIR, region="cn")
    from ffo.evaluator import FactorEvaluator
    evaluator = FactorEvaluator()
    print(f"  Ready in {time.time()-t0:.1f}s\n")

    # Evaluate all
    all_results = {}

    for label, pool in [("gflownet_v3", gfn_pool), ("llm_agent_v3", llm_pool),
                         ("canonical", canonical), ("baseline", baselines)]:
        print(f"\nEvaluating {label} ({len(pool)} factors)...")
        res = evaluate_pool(evaluator, pool, label)
        all_results[label] = res

    # ─── Summary ───
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")

    # Flatten for ranking
    flat = []
    for cat, items in all_results.items():
        for r in items:
            if r['error'] is None and not np.isnan(r['eval_ic']):
                flat.append((cat, r))
    flat.sort(key=lambda x: -abs(x[1]['eval_ic']))

    print(f"\nTop 20 factors by |IC|:")
    print(f"{'Rank':<5} {'|IC|':<8} {'IC':<8} {'ICIR':<7} {'Category':<14} Formula")
    print("-" * 90)
    for i, (cat, r) in enumerate(flat[:20]):
        formula = r['formula'][:65]
        print(f"{i+1:<5} {abs(r['eval_ic']):.4f}   {r['eval_ic']:+.4f}  {r['icir']:+.2f}   {cat:<14} {formula}")

    # Category summary
    print(f"\n--- Category Summary ---")
    for cat in ["gflownet_v3", "llm_agent_v3", "canonical", "baseline"]:
        items = [r for r in all_results[cat] if r['error'] is None and not np.isnan(r['eval_ic'])]
        if not items:
            print(f"  {cat:<14}: NO VALID RESULTS")
            continue
        ics = [abs(r['eval_ic']) for r in items]
        best = max(items, key=lambda x: abs(x['eval_ic']))
        ic002 = sum(1 for ic in ics if ic > 0.02)
        ic003 = sum(1 for ic in ics if ic > 0.03)
        print(f"  {cat:<14}: n={len(items)}, best={best['eval_ic']:+.4f} max|IC|={max(ics):.4f}, "
              f"mean|IC|={np.mean(ics):.4f}, IC>0.02: {ic002}/{len(items)} ({100*ic002/max(len(items),1):.0f}%), "
              f"IC>0.03: {ic003}/{len(items)} ({100*ic003/max(len(items),1):.0f}%)")
        if best['eval_ic']:
            print(f"            best: {best['formula'][:70]}")

    # Acceptance criteria check
    print(f"\n--- Acceptance Criteria ---")
    gfn_items = [r for r in all_results['gflownet_v3'] if r['error'] is None]
    llm_items = [r for r in all_results['llm_agent_v3'] if r['error'] is None]
    all_items = gfn_items + llm_items

    gfn_pass = sum(1 for r in gfn_items if abs(r['eval_ic']) > 0.02)
    llm_pass = sum(1 for r in llm_items if abs(r['eval_ic']) > 0.02)
    total_pass = gfn_pass + llm_pass
    total_n = len(gfn_items) + len(llm_items)

    print(f"  Factor pass rate (IC>0.02): {total_pass}/{total_n} = {100*total_pass/max(total_n,1):.1f}%  [target: >30%]")
    print(f"    GFlowNet: {gfn_pass}/{len(gfn_items)} = {100*gfn_pass/max(len(gfn_items),1):.1f}%")
    print(f"    LLM Agent: {llm_pass}/{len(llm_items)} = {100*llm_pass/max(len(llm_items),1):.1f}%")
    print(f"  Effective factors (IC>0.02 from GFlowNet): {gfn_pass}  [target: >60 effective]")
    print(f"  GFlowNet pool size: {len(gfn_items)}  [target: >60]")
    print(f"  Total unique factors: {total_n}")

    # Diversity check
    if gfn_items:
        formulas = [r['formula'] for r in gfn_items]
        returns_only = sum(1 for f in formulas if
            all(field not in f for field in ['$volume', '$vwap', '$high', '$low', '$open', '$close']))
        multi_field = sum(1 for f in formulas if sum(1 for field in
            ['$volume', '$close', '$vwap', '$high', '$low', '$open'] if field in f) >= 2)
        print(f"\n  Diversity:")
        print(f"    Returns-only factors: {returns_only}/{len(formulas)} ({100*returns_only/max(len(formulas),1):.0f}%)")
        print(f"    Multi-field factors:  {multi_field}/{len(formulas)} ({100*multi_field/max(len(formulas),1):.0f}%)")

    # Save
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "metadata": {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "eval_start": EVAL_START, "eval_end": EVAL_END,
            "universe": UNIVERSE,
            "gfn_checkpoint": "gfn_v3_clean_200ep.pt",
            "llm_results": "llm_agent_v3_30r.json",
        },
        "gflownet_pool_stats": gfn_stats,
        "llm_agent_stats": llm_stats,
        "results": {cat: items for cat, items in all_results.items()},
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved to: {OUTPUT}")
    return 0

if __name__ == "__main__":
    sys.exit(main())