#!/usr/bin/env python3
"""
resume_gfn.py — 从检查点恢复 GFlowNet 训练（使用修复后的 reward 参数）

修复内容:
  1. IC 权重 0.4→0.6，diversity 权重 0.5→0.3
  2. IC 归一化基准 0.05→0.08（扩大动态范围）
  3. 缓存按池子大小失效（多样性分数保持新鲜）
  4. 池子入池阈值 0.02→0.015

用法:
    python scripts/resume_gfn.py checkpoints/gfn_ep0100.pt --episodes 100
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

from gflownet import GFNConfig
from gflownet.runner import GFlowNetRunner


def main():
    parser = argparse.ArgumentParser(description="Resume GFlowNet training")
    parser.add_argument("checkpoint", type=str, help="Path to checkpoint .pt file")
    parser.add_argument("--episodes", type=int, default=100, help="Additional episodes")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output path (default: same as checkpoint, ep number updated)")
    args = parser.parse_args()

    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.exists():
        print(f"[ERROR] Checkpoint not found: {ckpt_path}")
        return 1
    if ckpt_path.suffix != ".pt":
        print(f"[ERROR] Expected .pt file, got: {ckpt_path}")
        return 1

    # Use NEW config defaults (with fixed reward weights)
    config = GFNConfig()
    # Keep same model/training params from checkpoint
    config.n_episodes = args.episodes  # additional episodes to run
    config.save_every = 50  # save more often
    config.log_every = 10

    runner = GFlowNetRunner(config=config)
    runner.load_checkpoint(str(ckpt_path))

    # Override reward config from old checkpoint (keep new defaults)
    runner.config.reward_ic_weight = 0.6
    runner.config.reward_diversity_weight = 0.3
    runner.config.reward_validity_weight = 0.1

    start_ep = runner.episode
    print(f"Resuming from episode {start_ep}, running {args.episodes} more episodes")
    print(f"Reward weights: IC={config.reward_ic_weight}, "
          f"diversity={config.reward_diversity_weight}, validity={config.reward_validity_weight}")
    print(f"IC normalize: abs(IC)/0.08 (vs old 0.05)")

    # Train additional episodes
    runner.train(n_episodes=args.episodes, verbose=True)

    # Save
    if args.output:
        out_path = Path(args.output)
    else:
        total_ep = start_ep + args.episodes
        out_path = ckpt_path.parent / f"gfn_ep{total_ep:04d}.pt"

    runner.save_checkpoint(str(out_path))
    print(f"\n[Save] Model saved to {out_path}")

    # Report best factors
    top = runner.get_top_factors(10)
    if top:
        print(f"\nTop {len(top)} factors in pool:")
        for formula, ic in top:
            print(f"  IC={ic:+.4f}  {formula}")
    else:
        print("\n[WARN] No factors in pool. Pool may need data reload.")

    return 0


if __name__ == "__main__":
    sys.exit(main())