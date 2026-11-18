#!/usr/bin/env python3
"""
GFlowNet 采样 CLI — 从训练好的模型采样因子并进行 FFO 评估

用法:
  # 采样 100 个因子
  python scripts/sample_gfn.py --model checkpoints/gfn_final.pt --n 100

  # 采样并保存到 CSV
  python scripts/sample_gfn.py --model checkpoints/gfn_final.pt --n 100 --output factors.csv

  # 用不同温度采样
  python scripts/sample_gfn.py --model checkpoints/gfn_final.pt --n 50 --temperature 2.0
"""

import argparse
import sys
import csv
import os
from pathlib import Path

# 确保模块可导入
SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

from gflownet import GFNConfig, sample, evaluate_factors, load_model
from gflownet.grammar import print_token_summary


def main():
    parser = argparse.ArgumentParser(
        description="GFlowNet 因子采样 + 评估",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--model", "-m", type=str, required=True,
                        help="训练好的模型检查点路径")
    parser.add_argument("--n", type=int, default=100,
                        help="采样数量 (默认: 100)")
    parser.add_argument("--temperature", "-t", type=float, default=1.5,
                        help="采样温度 (默认: 1.5, >1 = 更多样)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出 CSV 路径")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Qlib bin 数据目录")
    parser.add_argument("--ffo-eval", action="store_true",
                        help="用 FFO 完整评估采样的因子")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式")

    args = parser.parse_args()

    if not os.path.exists(args.model):
        print(f"Error: model file not found: {args.model}", file=sys.stderr)
        return 1

    # 加载模型
    if not args.quiet:
        print(f"Loading model from {args.model}...")

    model, runner = load_model(args.model)

    if not args.quiet:
        print(f"Model loaded (episode {runner.episode}, logZ={model.logZ.item():.4f})")

    # 采样
    results = sample(
        model,
        n=args.n,
        temperature=args.temperature,
        verbose=not args.quiet,
    )

    # 可选: FFO 完整评估
    if args.ffo_eval and results:
        if not args.quiet:
            print(f"\nRunning FFO full evaluation on {len(results)} factors...")

        formulas_only = [f for f, _ in results]
        eval_results = evaluate_factors(
            formulas_only,
            data_dir=args.data_dir,
        )

        # 更新结果
        enriched = []
        for (formula, reward), er in zip(results, eval_results):
            enriched.append({
                "formula": formula,
                "gfn_reward": reward,
                "ic_mean": er.ic_mean,
                "rank_ic_mean": er.rank_ic_mean,
                "ic_ir": er.ic_ir,
                "rank_ic_ir": er.rank_ic_ir,
                "long_short_spread": er.long_short_spread,
                "avg_turnover": er.avg_turnover,
                "ic_positive_rate": er.ic_positive_rate,
                "error": er.error or "",
            })

        if not args.quiet:
            good = [e for e in enriched if abs(e["ic_mean"]) >= 0.02]
            print(f"\n|IC| >= 0.02: {len(good)}/{len(enriched)}")
            if good:
                top5 = sorted(good, key=lambda x: -abs(x["ic_mean"]))[:5]
                print("\nTop 5 Factors:")
                for e in top5:
                    print(f"  {e['formula']}")
                    print(f"    IC={e['ic_mean']:.4f}, RIC={e['rank_ic_mean']:.4f}, "
                          f"ICIR={e['ic_ir']:.3f}, LS={e['long_short_spread']:.2%}, "
                          f"TO={e['avg_turnover']:.2f}")

        # 保存
        if args.output:
            _save_csv(enriched, args.output, full=True)

    else:
        # 简单结果
        enriched = [{"formula": f, "gfn_reward": r, "ic_mean": "", "rank_ic_mean": ""}
                     for f, r in results]

        if not args.quiet:
            print(f"\nTop 10 Factors (by GFN reward):")
            for formula, reward in results[:10]:
                print(f"  {formula}  (reward={reward:.3f})")

        if args.output:
            _save_csv(enriched, args.output, full=False)

    if not args.quiet and args.output:
        print(f"\nResults saved to {args.output}")

    return 0


def _save_csv(data: list, path: str, full: bool = False):
    """保存结果到 CSV"""
    if full:
        fieldnames = [
            "formula", "gfn_reward", "ic_mean", "rank_ic_mean",
            "ic_ir", "rank_ic_ir", "long_short_spread", "avg_turnover",
            "ic_positive_rate", "error",
        ]
    else:
        fieldnames = ["formula", "gfn_reward", "ic_mean", "rank_ic_mean"]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)


if __name__ == "__main__":
    sys.exit(main())