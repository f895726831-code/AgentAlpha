#!/usr/bin/env python3
"""
AlphaAgentEvo Swarm — 并行 LLM Agent 因子挖掘

用法:
    # 单 Agent
    python scripts/run_agent_swarm.py --rounds 50 --agents 1 --output results.json

    # Swarm (3 个并行 Agent)
    python scripts/run_agent_swarm.py --rounds 50 --agents 3 --output swarm_results.json

    # 使用不同温度
    python scripts/run_agent_swarm.py --rounds 30 --temperature 0.5 1.0 --output results.json

    # 安静模式
    python scripts/run_agent_swarm.py --rounds 50 --quiet
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

from agent import AgentConfig, run_agent, run_swarm, merge_swarm_results
from agent import evaluate_factors_with_ffo


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AlphaAgentEvo — LLM Agent 因子挖掘 Swarm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_agent_swarm.py --rounds 50 --agents 1
  python run_agent_swarm.py --rounds 30 --agents 3 --temperature 0.5 1.0
  python run_agent_swarm.py --rounds 50 --ffo-eval --output results.json
        """,
    )

    # Agent 参数
    parser.add_argument("--rounds", "-r", type=int, default=50,
                        help="每 Agent 轮次 (default: 50)")
    parser.add_argument("--agents", "-a", type=int, default=1,
                        help="并行 Agent 数 (default: 1)")
    parser.add_argument("--formulas-per-round", "-k", type=int, default=5,
                        help="每轮生成因子数 (default: 5)")
    parser.add_argument("--temperature", "-t", type=float, nargs=2,
                        metavar=("MIN", "MAX"),
                        default=[0.6, 1.0],
                        help="Agent 温度范围 (default: 0.6 1.0)")
    parser.add_argument("--model", "-m", type=str, default="deepseek-chat",
                        help="LLM 模型 (default: deepseek-chat)")
    parser.add_argument("--reflection-every", type=int, default=10,
                        help="反思间隔轮次 (default: 10)")

    # 评估参数
    parser.add_argument("--eval-start", type=str, default="2015-01-05",
                        help="FFO 评估起始日期 (default: 2015-01-05, CSI500启动日)")
    parser.add_argument("--eval-end", type=str, default="2024-12-31",
                        help="FFO 评估结束日期 (default: 2024-12-31)")
    parser.add_argument("--universe", type=str, default="csi500",
                        help="股票池 (default: csi500)")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Qlib 数据目录")
    parser.add_argument("--ffo-eval", action="store_true",
                        help="训练后用 FFO 完整评估最终因子池")

    # 输出
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="结果输出文件 (JSON)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="安静模式")

    args = parser.parse_args()

    # 构建配置
    config = AgentConfig()
    config.n_rounds = args.rounds
    config.formulas_per_round = args.formulas_per_round
    config.llm_model = args.model
    config.llm_temperature = args.temperature[1]
    config.agent_temperature_min = args.temperature[0]
    config.agent_temperature_max = args.temperature[1]
    config.reflection_every = args.reflection_every
    config.eval_start = args.eval_start
    config.eval_end = args.eval_end
    config.eval_universe = args.universe

    verbose = not args.quiet

    # 运行
    n_agents = args.agents

    if n_agents == 1:
        # 单 Agent
        result = run_agent(config=config, data_dir=args.data_dir, verbose=verbose)
        results = [result]
    else:
        # Swarm
        config.parallel_agents = n_agents
        results = run_swarm(
            n_agents=n_agents,
            n_rounds=args.rounds,
            config=config,
            data_dir=args.data_dir,
            verbose=verbose,
        )

    # 汇总
    merged = merge_swarm_results(results)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Swarm Summary ({n_agents} agents × {args.rounds} rounds)")
        print(f"{'='*60}")
        print(f"Pool:       {merged['pool_size']} unique factors")
        print(f"Formulas:   {merged['total_formulas']} "
              f"({merged['total_valid']} valid)")
        print(f"Best:       {merged['best_formula']}  "
              f"|IC|={abs(merged['best_ic']):.4f}")
        print(f"Cost:       CNY {merged['total_cost_rmb']:.4f} "
              f"({merged['total_tokens_in'] + merged['total_tokens_out']} tokens)")
        print()
        print("Top 10 factors:")
        for i, (formula, ic) in enumerate(merged["pool"][:10], 1):
            print(f"  {i:2d}. {formula}  |IC|={abs(ic):.4f}")

    # FFO 完整评估
    if args.ffo_eval and merged["pool"]:
        if verbose:
            print(f"\nRunning FFO full evaluation on {min(10, len(merged['pool']))} factors...")

        formulas = [f for f, _ in merged["pool"][:10]]
        eval_results = evaluate_factors_with_ffo(
            formulas=formulas,
            start=args.eval_start,
            end=args.eval_end,
            universe=args.universe,
            data_dir=args.data_dir,
        )

        merged["ffo_evaluations"] = []
        for er in eval_results:
            merged["ffo_evaluations"].append(er.to_dict())
            if verbose and not er.error:
                print(f"  {er.formula}")
                print(f"    IC={er.ic_mean:.4f}, RankIC={er.rank_ic_mean:.4f}, "
                      f"ICIR={er.ic_ir:.2f}")

    # 保存输出
    if args.output:
        output_path = args.output
        if not Path(output_path).is_absolute():
            output_path = str(FACTOR_LAB_DIR / output_path)

        output_data = {
            "config": config.to_dict(),
            "swarm_summary": merged,
            "agent_results": [
                {
                    "pool": r.get("factor_pool", []),
                    "pool_stats": r.get("pool", {}),
                    "llm_usage": r.get("llm_usage", {}),
                    "rounds": r.get("rounds_completed", 0),
                }
                for r in results
            ],
        }

        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False, default=str)

        if verbose:
            print(f"\nResults saved to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())