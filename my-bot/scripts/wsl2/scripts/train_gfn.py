#!/usr/bin/env python3
"""
GFlowNet 训练 CLI — 训练因子挖掘 GFlowNet 模型

用法:
  # 默认训练 (200 episodes, batch=16)
  python scripts/train_gfn.py

  # 自定义参数
  python scripts/train_gfn.py --episodes 500 --batch-size 32

  # 指定数据目录和输出
  python scripts/train_gfn.py --data-dir /mnt/d/qlib/qlib_data/cn_data --output model.pt

  # 从检查点恢复训练
  python scripts/train_gfn.py --resume checkpoints/gfn_ep0100.pt --episodes 500
"""

import argparse
import sys
import os
from pathlib import Path

# 确保模块可导入
SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

from gflownet import GFNConfig, train as train_gfn
from gflownet.grammar import print_token_summary


def main():
    parser = argparse.ArgumentParser(
        description="GFlowNet 因子挖掘模型训练",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 训练参数
    parser.add_argument("--episodes", type=int, default=200,
                        help="训练 episode 数 (默认: 200)")
    parser.add_argument("--batch-size", type=int, default=16,
                        help="每 micro-batch 采样轨迹数 (默认: 16)")
    parser.add_argument("--grad-accum", type=int, default=4,
                        help="梯度累积步数 (默认: 4, effective batch=64)")
    parser.add_argument("--subtb-lambda", type=float, default=0.9,
                        help="SubTB lambda (默认: 0.9, 0=TB only, 1=all prefixes)")
    parser.add_argument("--lr", type=float, default=1e-3,
                        help="学习率 (默认: 1e-3)")

    # 模型参数
    parser.add_argument("--d-model", type=int, default=64,
                        help="Token 嵌入维度 (默认: 64)")
    parser.add_argument("--n-layers", type=int, default=2,
                        help="Transformer 层数 (默认: 2)")
    parser.add_argument("--n-heads", type=int, default=4,
                        help="注意力头数 (默认: 4)")

    # 评估参数
    parser.add_argument("--eval-start", type=str, default="2015-01-05",
                        help="FFO 评估起始日期 (default: 2015-01-05, CSI500启动日)")
    parser.add_argument("--eval-end", type=str, default="2024-12-31",
                        help="FFO 评估结束日期 (默认: 2024-12-31)")
    parser.add_argument("--universe", type=str, default="csi500",
                        help="股票池 (默认: csi500)")

    # 路径
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Qlib bin 数据目录")
    parser.add_argument("--output", "-o", type=str, default="checkpoints/gfn_final.pt",
                        help="最终模型输出路径 (默认: checkpoints/gfn_final.pt)")
    parser.add_argument("--resume", type=str, default=None,
                        help="从检查点恢复训练")

    # 其他
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式")
    parser.add_argument("--show-grammar", action="store_true",
                        help="显示词汇表摘要")

    args = parser.parse_args()

    if args.show_grammar:
        print_token_summary()
        return 0

    # 构建配置
    config = GFNConfig()
    config.n_episodes = args.episodes
    config.batch_size = args.batch_size
    config.grad_accum_steps = args.grad_accum
    config.subtb_lambda = args.subtb_lambda
    config.lr = args.lr
    config.d_model = args.d_model
    config.n_layers = args.n_layers
    config.n_heads = args.n_heads
    config.eval_start = args.eval_start
    config.eval_end = args.eval_end
    config.eval_universe = args.universe

    if not args.quiet:
        print("=" * 60)
        print("GFlowNet Factor Mining — Training")
        print("=" * 60)
        print(f"  Model:  d_model={config.d_model}, layers={config.n_layers}, "
              f"heads={config.n_heads}")
        print(f"  Train:  episodes={config.n_episodes}, batch={config.batch_size}, "
              f"lr={config.lr}")
        print(f"  Eval:   {config.eval_start} → {config.eval_end} ({config.eval_universe})")
        print(f"  Device: {config.device}")
        print()

    # 训练
    model, runner = train_gfn(
        config=config,
        data_dir=args.data_dir,
        n_episodes=args.episodes,
        verbose=not args.quiet,
    )

    # 保存最终模型
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    runner.save_checkpoint(args.output)

    if not args.quiet:
        print(f"\n[Save] Model saved to {args.output}")

        # 输出最佳因子
        top_factors = runner.get_top_factors(10)
        if top_factors:
            print(f"\nTop {len(top_factors)} Discovered Factors:")
            for formula, ic in top_factors:
                print(f"  {formula}")
                print(f"    IC={ic:.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())