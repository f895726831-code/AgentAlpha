#!/usr/bin/env python3
"""Extract GFlowNet v2 pool from checkpoint by sampling + FFO evaluation."""
import sys, os, json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

import torch
from gflownet import GFNConfig, load_model, sample
from ffo.evaluator import FactorEvaluator

CHECKPOINT = str(FACTOR_LAB_DIR / "checkpoints" / "gfn_final_v2.pt")
OUTPUT = str(FACTOR_LAB_DIR / "gfn_pool_p0_v2.json")

def main():
    # Load checkpoint
    print(f"Loading: {CHECKPOINT}")
    ckpt = torch.load(CHECKPOINT, map_location="cpu")
    saved_config = ckpt.get("config", {})
    ep = ckpt.get("episode", "?")
    pool_count = ckpt.get("pool_size", "?")

    config = GFNConfig()
    for k, v in saved_config.items():
        if hasattr(config, k):
            setattr(config, k, v)

    print(f"Checkpoint: ep={ep}, pool_size={pool_count}")
    print(f"Config: ic_w={config.reward_ic_weight}, div_w={config.reward_diversity_weight}")

    # Load model
    model, runner = load_model(CHECKPOINT, config)
    print("Model loaded.")

    # Sample factors (without reward evaluation to save time)
    # Use model directly to sample formulas
    print(f"\nSampling formulas (temp={config.sample_temperature})...")
    formulas = set()
    n_sample = 200
    attempts = 0
    max_attempts = n_sample * 3

    from gflownet.grammar import sequence_to_formula
    while len(formulas) < n_sample and attempts < max_attempts:
        attempts += 1
        _, _, _, formula = model.sample_trajectory(temperature=config.sample_temperature)
        if formula and formula not in formulas:
            formulas.add(formula)

    print(f"Got {len(formulas)} unique formulas in {attempts} attempts")

    # Evaluate with FFO
    print(f"\nEvaluating {len(formulas)} formulas with FFO...")
    evaluator = FactorEvaluator()
    pool = []
    for i, formula in enumerate(sorted(formulas)):
        try:
            result = evaluator.evaluate(
                formula,
                start=config.eval_start,
                end=config.eval_end,
                universe=config.eval_universe,
            )
            ic = result.ic_mean if hasattr(result, 'ic_mean') else 0.0
            pool.append([formula, round(float(ic), 6)])
        except Exception as e:
            pass

        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(formulas)}...")

    pool.sort(key=lambda x: -abs(x[1]))

    result = {
        "pool": pool,
        "source": "gflownet_v2_200ep",
        "config": {
            "ic_weight": config.reward_ic_weight,
            "diversity_weight": config.reward_diversity_weight,
            "episodes": config.n_episodes,
            "eval_period": f"{config.eval_start} -> {config.eval_end}",
            "universe": config.eval_universe,
        },
        "stats": {
            "size": len(pool),
            "max_ic": max(abs(x[1]) for x in pool) if pool else 0,
            "mean_ic": sum(abs(x[1]) for x in pool) / len(pool) if pool else 0,
        }
    }

    with open(OUTPUT, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved {len(pool)} factors to {OUTPUT}")
    print(f"max|IC|={result['stats']['max_ic']:.4f}, mean|IC|={result['stats']['mean_ic']:.4f}")

    print("\nTop 10:")
    for formula, ic in pool[:10]:
        print(f"  {formula}  IC={ic:.4f}")

if __name__ == "__main__":
    main()