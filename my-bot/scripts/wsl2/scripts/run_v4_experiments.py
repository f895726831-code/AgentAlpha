#!/usr/bin/env python3
"""
GFlowNet v4 Phase 1 — Controlled Experiment Runner

Runs experiments A/B/C with identical seeds/grammar/data, varying only
reward mode + archive config.

Usage:
  # Run a single experiment
  python scripts/run_v4_experiments.py --exp A --episodes 200

  # Run all three experiments sequentially
  python scripts/run_v4_experiments.py --exp all --episodes 200

  # With custom data dir
  python scripts/run_v4_experiments.py --exp A --data-dir /mnt/d/qlib/qlib_data/cn_data

Experiment matrix:
  A (Baseline):   v3 reward (IC+diversity), no archive
  B (IC+Novelty): v4 reward (L1+L2+L3), no archive
  C (IC+Archive): v4 L1-only + MAP-Elites archive (L2/L3 disabled)
  D (Full v4):    v4 L1+L2+L3 + MAP-Elites + DualChannelSampler

Phase 1 (A/B/C):   Reward + Archive comparison — tests if L3 novelty or Archive alone breaks stagnation
Phase 2 (D):        Full v4 with DualChannelSampler — tests combined approach

Output per experiment:
  checkpoints/v4_exp_{name}_final.pt     — model checkpoint
  checkpoints/v4_exp_{name}_metrics.json — training metrics
  checkpoints/v4_exp_{name}_eval.json    — evaluator summary
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

from gflownet import GFNConfig
from gflownet.runner import GFlowNetRunner
from gflownet.grammar import print_token_summary


# ─── Experiment Definitions ────────────────────────────────────

EXPERIMENTS = {
    "A": {
        "label": "Baseline (v3 IC+diversity)",
        "reward_mode": "v3",
        "archive_enabled": False,
        "desc": "Replicates v3 mode collapse baseline. Expect: SignedPower monopoly, best_IC stagnant.",
    },
    "B": {
        "label": "IC+Novelty (v4 L1+L2+L3, no Archive)",
        "reward_mode": "v4",
        "archive_enabled": False,
        "l1_min_ic": 0.0,  # Open L1 gate: random formulas need gradient signal
        "l1_min_coverage": 0.0,
        "desc": "v4 three-layer reward without MAP-Elites. Tests if L3 novelty bonus alone breaks mode collapse.",
    },
    "C": {
        "label": "IC+Archive (v4 L1-only + MAP-Elites)",
        "reward_mode": "v4",
        "archive_enabled": True,
        "l1_min_ic": 0.0,  # No L1 threshold: let all formulas through for gradient signal, Archive handles diversity
        "l1_min_coverage": 0.0,  # No coverage gate either
        # Turn off L2 stability/bullbear and L3 novelty — pure IC + Archive diversity
        "l2_stability_weight": 0.0,
        "l2_bullbear_weight": 0.0,
        "l3_novelty_alpha": 0.0,
        "desc": "Pure IC reward + MAP-Elites category isolation. L1=0 (open gate), diversity via Archive only.",
    },
    "D": {
        "label": "Full v4 (L1+L2+L3 + Archive + DualChannel)",
        "reward_mode": "v4",
        "archive_enabled": True,
        "sampler_mode": "dual_channel",
        "l1_min_ic": 0.0,  # Open L1 gate: random formulas need gradient signal
        "l1_min_coverage": 0.0,
        "desc": "Full v4: three-layer reward + MAP-Elites + DualChannelSampler. Tests if combined approach breaks IC stagnation.",
    },
}


# ─── Config Builder ────────────────────────────────────────────

def build_config(exp: dict, args) -> GFNConfig:
    """Build GFNConfig from experiment definition + CLI args."""
    cfg = GFNConfig()

    # Core training params
    cfg.n_episodes = args.episodes
    cfg.batch_size = args.batch_size
    cfg.grad_accum_steps = args.grad_accum
    cfg.subtb_lambda = args.subtb_lambda
    cfg.lr = args.lr

    if args.d_model:
        cfg.d_model = args.d_model
    if args.n_layers:
        cfg.n_layers = args.n_layers

    # Eval params
    if args.eval_start:
        cfg.eval_start = args.eval_start
    if args.eval_end:
        cfg.eval_end = args.eval_end
    cfg.eval_universe = args.universe

    # v4 experiment config
    cfg.reward_mode = exp["reward_mode"]
    cfg.archive_enabled = exp.get("archive_enabled", False)

    # v4 sampler
    if "sampler_mode" in exp:
        cfg.sampler_mode = exp["sampler_mode"]
        cfg.sampler_exploit_ratio = exp.get("sampler_exploit_ratio", 0.6)
        cfg.sampler_exploit_temp = exp.get("sampler_exploit_temp", 0.5)
        cfg.sampler_explore_temp = exp.get("sampler_explore_temp", 1.5)

    if "l2_stability_weight" in exp:
        cfg.reward_v4_l2_stability_weight = exp["l2_stability_weight"]
    if "l2_bullbear_weight" in exp:
        cfg.reward_v4_l2_bullbear_weight = exp["l2_bullbear_weight"]
    if "l3_novelty_alpha" in exp:
        cfg.reward_v4_l3_novelty_alpha = exp["l3_novelty_alpha"]

    # L1 threshold override
    if "l1_min_ic" in exp:
        cfg.reward_v4_l1_min_ic = exp["l1_min_ic"]
    if "l1_min_coverage" in exp:
        cfg.reward_v4_l1_min_coverage = exp["l1_min_coverage"]

    # Logging
    cfg.log_every = args.log_every
    cfg.save_every = args.save_every or max(1, args.episodes // 2)

    return cfg


# ─── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GFlowNet v4 Phase 1 — Controlled Experiment Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--exp", type=str, default="A",
                        choices=["A", "B", "C", "D", "all"],
                        help="Experiment to run (default: A)")
    parser.add_argument("--episodes", type=int, default=200,
                        help="Training episodes (default: 200)")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--subtb-lambda", type=float, default=0.9)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--d-model", type=int, default=None)
    parser.add_argument("--n-layers", type=int, default=None)
    parser.add_argument("--eval-start", type=str, default=None)
    parser.add_argument("--eval-end", type=str, default=None)
    parser.add_argument("--universe", type=str, default="csi500")
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default="checkpoints")
    parser.add_argument("--log-every", type=int, default=10,
                        help="Log metrics every N episodes")
    parser.add_argument("--save-every", type=int, default=None,
                        help="Save checkpoint every N episodes")
    parser.add_argument("--resume", type=str, default=None,
                        help="Resume from checkpoint")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")

    args = parser.parse_args()

    # Set random seed
    import torch
    import numpy as np
    import random
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)

    # Determine experiments to run
    if args.exp == "all":
        exp_names = ["A", "B", "C", "D"]
    else:
        exp_names = [args.exp]

    results = {}

    for exp_name in exp_names:
        exp = EXPERIMENTS[exp_name]

        if exp.get("disabled"):
            print(f"[SKIP] Experiment {exp_name}: {exp['label']}")
            print(f"       {exp['desc']}")
            continue

        print("=" * 60)
        print(f"Experiment {exp_name}: {exp['label']}")
        print("=" * 60)
        print(f"  Hypothesis: {exp['desc']}")
        print()

        cfg = build_config(exp, args)

        if not args.quiet:
            print(f"  Reward mode:  {cfg.reward_mode}")
            print(f"  Archive:      {'enabled' if cfg.archive_enabled else 'disabled'}")
            print(f"  Sampler:      {cfg.sampler_mode}")
            if cfg.reward_mode == 'v4':
                print(f"  L2 stability: {cfg.reward_v4_l2_stability_weight}")
                print(f"  L2 bull/bear: {cfg.reward_v4_l2_bullbear_weight}")
                print(f"  L3 alpha:     {cfg.reward_v4_l3_novelty_alpha}")
            if cfg.sampler_mode == 'dual_channel':
                print(f"  DC exploit:   {cfg.sampler_exploit_ratio:.0%} (t={cfg.sampler_exploit_temp})")
                print(f"  DC explore:   t={cfg.sampler_explore_temp}")
            print(f"  Episodes:     {cfg.n_episodes}")
            print(f"  Seed:         {args.seed}")
            print()

        # Train
        runner = GFlowNetRunner(config=cfg, data_dir=args.data_dir)
        t0 = time.time()

        if args.resume and exp_name == exp_names[0]:
            if not args.quiet:
                print(f"[Resume] Loading {args.resume}...")
            runner.load_checkpoint(args.resume)

        runner.train(n_episodes=args.episodes, verbose=not args.quiet)

        elapsed = time.time() - t0

        # Save outputs
        os.makedirs(args.output_dir, exist_ok=True)

        model_path = os.path.join(args.output_dir, f"v4_exp_{exp_name}_final.pt")
        runner.save_checkpoint(model_path)

        metrics_path = os.path.join(args.output_dir, f"v4_exp_{exp_name}_metrics.json")
        runner.export_metrics(metrics_path)

        eval_path = os.path.join(args.output_dir, f"v4_exp_{exp_name}_eval.json")
        runner.export_eval_summary(eval_path)

        # Summary
        pool_stats = runner.reward_computer.get_pool_stats()
        eval_summary = runner.evaluator.summary()

        result = {
            "experiment": exp_name,
            "label": exp["label"],
            "reward_mode": cfg.reward_mode,
            "archive_enabled": cfg.archive_enabled,
            "sampler_mode": cfg.sampler_mode,
            "episodes": cfg.n_episodes,
            "seed": args.seed,
            "elapsed_seconds": round(elapsed, 1),
            "pool_size": pool_stats["size"],
            "pool_max_ic": pool_stats["max_ic"],
            "pool_unique_structures": pool_stats.get("unique_structures", 0),
            "best_effective_factors": eval_summary["best_metrics"]["best_effective_factors"],
            "mode_collapse_detected": eval_summary["mode_collapse_detected"],
        }

        if runner._sampler:
            ss = runner._sampler.get_stats()
            result["sampler_details"] = {"exploit_pct": ss["exploit_pct"], "explore_pct": ss["explore_pct"]}
        else:
            result["sampler_details"] = None

        results[exp_name] = result

        if not args.quiet:
            print(f"\n[Exp {exp_name}] Complete in {elapsed:.0f}s")
            print(f"  Pool: {result['pool_size']} factors, "
                  f"max|IC|={result['pool_max_ic']:.4f}, "
                  f"unique_structures={result['pool_unique_structures']}")
            print(f"  Mode collapse: {result['mode_collapse_detected']}")
            print(f"  Saved: {model_path}")

    # Cross-experiment comparison
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("Cross-Experiment Comparison")
        print("=" * 60)
        print(f"{'Exp':<6} {'Mode':<6} {'Sampler':<8} {'Archive':<8} "
              f"{'Pool':<8} {'max|IC|':<10} {'Structs':<10} "
              f"{'Collapse':<10} {'Eff':<6}")
        print("-" * 60)
        for name, r in results.items():
            sampler_str = r.get('sampler_mode', 'standard')[:8]
            print(f"{name:<6} {r['reward_mode']:<6} "
                  f"{sampler_str:<8} "
                  f"{'yes' if r['archive_enabled'] else 'no':<8} "
                  f"{r['pool_size']:<8} {r['pool_max_ic']:<10.4f} "
                  f"{r['pool_unique_structures']:<10} "
                  f"{str(r['mode_collapse_detected']):<10} "
                  f"{r['best_effective_factors']:<6}")

        # Save comparison
        comp_path = os.path.join(args.output_dir, "v4_experiment_comparison.json")
        with open(comp_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nComparison saved: {comp_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())