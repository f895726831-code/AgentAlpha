#!/usr/bin/env python3
"""
Factor Factory CLI — 一键运行三通道因子工厂

用法:
    # 运行所有通道
    python scripts/run_factor_factory.py --all

    # 仅运行特定通道
    python scripts/run_factor_factory.py --channels gflownet,agent

    # 自定义参数
    python scripts/run_factor_factory.py --all \
        --gfn-episodes 200 --agent-rounds 50 --agent-swarm 3 \
        --output factory_results.json

    # 仅去重 + 注册 (已有因子池 JSON)
    python scripts/run_factor_factory.py --merge-only \
        --pools agent_results.json gfn_results.json

    # 仅合成 (使用注册表中的因子)
    python scripts/run_factor_factory.py --combine-only --method ic_weighted
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径: scripts/ → wsl2/
SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Factor Factory 2.0 — 三通道因子工厂 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_factor_factory.py --all
  python run_factor_factory.py --channels gflownet,agent --gfn-episodes 100 --agent-rounds 30
  python run_factor_factory.py --merge-only --pools agent.json gfn.json
  python run_factor_factory.py --combine-only --method rank_blend
        """,
    )

    # ── 运行模式 ──
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--all", action="store_true",
                            help="运行所有启用的通道 (默认)")
    mode_group.add_argument("--merge-only", action="store_true",
                            help="仅合并已有因子池 (不运行通道)")
    mode_group.add_argument("--combine-only", action="store_true",
                            help="仅合成注册表中的因子")

    # ── 通道选择 ──
    parser.add_argument("--channels", "-c", type=str,
                        help="指定通道 (gflownet,agent,rd_agent), 逗号分隔")
    parser.add_argument("--pools", type=str, nargs="+",
                        help="因子池 JSON 文件列表 (用于 --merge-only)")

    # ── GFlowNet 参数 ──
    gfn_group = parser.add_argument_group("GFlowNet")
    gfn_group.add_argument("--gfn-episodes", type=int, default=200,
                           help="训练 episode 数 (default: 200)")
    gfn_group.add_argument("--gfn-sample", type=int, default=100,
                           help="采样因子数 (default: 100)")
    gfn_group.add_argument("--gfn-batch-size", type=int, default=16,
                           help="批次大小 (default: 16)")

    # ── Agent 参数 ──
    agent_group = parser.add_argument_group("LLM Agent")
    agent_group.add_argument("--agent-rounds", type=int, default=50,
                             help="每 Agent 轮次 (default: 50)")
    agent_group.add_argument("--agent-swarm", type=int, default=1,
                             help="Swarm Agent 数 (default: 1)")
    agent_group.add_argument("--agent-model", type=str, default="deepseek-chat",
                             help="LLM 模型 (default: deepseek-chat)")

    # ── 评估 ──
    eval_group = parser.add_argument_group("Evaluation")
    eval_group.add_argument("--eval-start", type=str, default="2020-01-01",
                            help="评估起始日期 (default: 2020-01-01)")
    eval_group.add_argument("--eval-end", type=str, default="2020-12-31",
                            help="评估结束日期 (default: 2020-12-31)")
    eval_group.add_argument("--universe", type=str, default="csi500",
                            help="股票池 (default: csi500)")

    # ── 合成 ──
    comb_group = parser.add_argument_group("Combiner")
    comb_group.add_argument("--method", type=str, default="ic_weighted",
                            choices=["ic_weighted", "rank_blend", "conditional", "temporal"],
                            help="合成方法 (default: ic_weighted)")
    comb_group.add_argument("--temporal-window", type=int, default=20,
                            help="时序平滑窗口 (default: 20)")

    # ── 输出 ──
    out_group = parser.add_argument_group("Output")
    out_group.add_argument("--output", "-o", type=str,
                           help="结果输出文件 (JSON)")
    out_group.add_argument("--registry", type=str,
                           help="注册表存储路径")
    out_group.add_argument("--data-dir", type=str,
                           help="Qlib 数据目录")
    out_group.add_argument("--no-parallel", action="store_true",
                           help="禁用并行运行")
    out_group.add_argument("--no-dedup", action="store_true",
                           help="禁用去重")
    out_group.add_argument("--quiet", "-q", action="store_true",
                           help="安静模式")

    args = parser.parse_args()

    # ── 默认: --all ──
    if not args.all and not args.merge_only and not args.combine_only:
        args.all = True

    verbose = not args.quiet

    # ── 模式: --combine-only ──
    if args.combine_only:
        return _run_combine_only(args, verbose)

    # ── 模式: --merge-only ──
    if args.merge_only:
        return _run_merge_only(args, verbose)

    # ── 模式: --all (运行通道) ──
    return _run_channels(args, verbose)


# ═══════════════════════════════════════════════════════════
# 子模式
# ═══════════════════════════════════════════════════════════

def _run_channels(args: argparse.Namespace, verbose: bool) -> int:
    """运行因子工厂通道"""
    from factory import FactoryConfig, FactoryScheduler

    # 解析通道
    channels = None
    if args.channels:
        channels = [ch.strip() for ch in args.channels.split(",")]

    # 构建配置
    config = FactoryConfig()
    config.gfn_episodes = args.gfn_episodes
    config.gfn_sample_n = args.gfn_sample
    config.gfn_batch_size = args.gfn_batch_size
    config.agent_rounds = args.agent_rounds
    config.agent_swarm_size = args.agent_swarm
    config.agent_model = args.agent_model
    config.eval_start = args.eval_start
    config.eval_end = args.eval_end
    config.eval_universe = args.universe
    config.parallel = not args.no_parallel

    if channels:
        config.enable_gflownet = "gflownet" in channels
        config.enable_llm_agent = "agent" in channels
        config.enable_rd_agent = "rd_agent" in channels

    if args.registry:
        config.registry_path = args.registry

    # 运行
    scheduler = FactoryScheduler(config=config, data_dir=args.data_dir)
    result = scheduler.run_all(channels=channels, verbose=verbose)

    # ═══ 合成 (可选) ═══
    if result.get("merged_pool") and result["merged_pool_size"] > 0:
        if verbose:
            print(f"\n--- Combiner ({args.method}) ---")

        try:
            from factory.combiner import FactorCombiner
            cb = FactorCombiner(data_dir=args.data_dir)

            pool = [(f, ic) for f, ic, _ in result["merged_pool"]]

            if args.method == "ic_weighted":
                combined, info = cb.combine_ic_weighted(pool)
            elif args.method == "rank_blend":
                combined, info = cb.combine_rank_blend(pool)
            elif args.method == "conditional":
                combined, info = cb.combine_conditional(pool)
            elif args.method == "temporal":
                combined, info = cb.combine_temporal(
                    pool, window=args.temporal_window
                )

            result["combined"] = {
                "formula": combined,
                "info": info,
            }

            if verbose:
                print(f"Combined formula: {combined}")
                print(f"Valid: {info['parsed_ok']}")

                # 评估合成因子
                try:
                    from ffo.evaluator import FactorEvaluator
                    evaluator = FactorEvaluator(data_dir=args.data_dir)
                    eval_result = evaluator.evaluate(
                        combined,
                        start=args.eval_start,
                        end=args.eval_end,
                        universe=args.universe,
                    )
                    if not eval_result.error:
                        print(f"IC={eval_result.ic_mean:.4f}, "
                              f"RankIC={eval_result.rank_ic_mean:.4f}, "
                              f"ICIR={eval_result.ic_ir:.2f}")
                except Exception as e:
                    if verbose:
                        print(f"Evaluation skipped: {e}")

        except Exception as e:
            if verbose:
                print(f"Combiner error: {e}")
            result["combined"] = {"error": str(e)}

    # ═══ 保存输出 ═══
    if args.output:
        output_path = args.output
        if not Path(output_path).is_absolute():
            output_path = str(FACTOR_LAB_DIR / output_path)

        # 清理 result 中的不可序列化对象
        clean_result = _clean_for_json(result)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(clean_result, f, indent=2, ensure_ascii=False, default=str)

        if verbose:
            print(f"\nResults saved to: {output_path}")

    return 0


def _run_merge_only(args: argparse.Namespace, verbose: bool) -> int:
    """仅合并 + 去重 + 注册"""
    from factory.dedup import deduplicate_pool
    from factory.metadata import FactorRegistry

    registry_path = args.registry or "data/factor_registry.json"
    registry = FactorRegistry(storage_path=registry_path)

    all_tagged = []

    if args.pools:
        for pool_path in args.pools:
            try:
                with open(pool_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"Error reading {pool_path}: {e}")
                continue

            # 尝试自动检测格式
            pool = _extract_pool(data)

            if verbose:
                print(f"{pool_path}: {len(pool)} factors loaded")

            # 推断通道
            channel = "unknown"
            if "gfn" in pool_path.lower():
                channel = "gflownet"
            elif "agent" in pool_path.lower():
                channel = "llm_agent"
            elif "rd" in pool_path.lower():
                channel = "rd_agent"

            for formula, ic in pool:
                all_tagged.append((formula, ic, channel))

    if not all_tagged:
        print("No factors loaded. Use --pools to specify factor pool JSON files.")
        return 1

    # 去重
    deduped = deduplicate_pool(all_tagged, mode="smart")

    if verbose:
        print(f"\nTotal raw:  {len(all_tagged)}")
        print(f"After dedup: {len(deduped)}")
        if len(all_tagged) > 0:
            print(f"Dedup ratio: {1 - len(deduped)/len(all_tagged):.1%}")

    # 注册
    pools = {}
    for formula, ic, channel in deduped:
        if channel not in pools:
            pools[channel] = []
        pools[channel].append((formula, ic))

    registry.merge_pools(pools)

    if verbose:
        summary = registry.export_summary()
        print(f"\nRegistry: {summary['total_factors']} total, "
              f"{summary['active_factors']} active")
        print(f"By channel: {summary['by_channel']}")

        top = registry.get_top(10)
        if top:
            print(f"\nTop 10 factors:")
            for i, m in enumerate(top, 1):
                print(f"  {i:2d}. [{m.source_channel}] {m.formula}  "
                      f"|IC|={abs(m.ic_mean):.4f}")

    # 保存
    if args.output:
        output_path = args.output
        if not Path(output_path).is_absolute():
            output_path = str(FACTOR_LAB_DIR / output_path)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "deduped": [(f, ic, ch) for f, ic, ch in deduped],
                "registry_summary": registry.export_summary(),
                "timestamp": datetime.now().isoformat(),
            }, f, indent=2, ensure_ascii=False, default=str)

        if verbose:
            print(f"\nSaved to: {output_path}")

    return 0


def _run_combine_only(args: argparse.Namespace, verbose: bool) -> int:
    """仅合成注册表中的因子"""
    from factory.metadata import FactorRegistry
    from factory.combiner import FactorCombiner

    registry_path = args.registry or "data/factor_registry.json"
    registry = FactorRegistry(storage_path=registry_path)

    active = registry.list_active(min_ic=0.02)
    if not active:
        print("No active factors in registry.")
        return 1

    if verbose:
        print(f"Registry: {len(active)} active factors")

    factors = [(m.formula, m.ic_mean) for m in active]
    cb = FactorCombiner(data_dir=args.data_dir)

    if args.method == "ic_weighted":
        combined, info = cb.combine_ic_weighted(factors)
    elif args.method == "rank_blend":
        combined, info = cb.combine_rank_blend(factors)
    elif args.method == "conditional":
        combined, info = cb.combine_conditional(factors)
    elif args.method == "temporal":
        combined, info = cb.combine_temporal(factors, window=args.temporal_window)

    if verbose:
        print(f"\nMethod:    {args.method}")
        print(f"Combined:  {combined}")
        print(f"Valid:     {info['parsed_ok']}")

        # 评估
        try:
            from ffo.evaluator import FactorEvaluator
            evaluator = FactorEvaluator(data_dir=args.data_dir)
            eval_result = evaluator.evaluate(
                combined,
                start=args.eval_start,
                end=args.eval_end,
                universe=args.universe,
            )
            if not eval_result.error:
                print(f"IC:        {eval_result.ic_mean:.4f}")
                print(f"Rank IC:   {eval_result.rank_ic_mean:.4f}")
                print(f"ICIR:      {eval_result.ic_ir:.2f}")
                print(f"Long/Short Spread: {eval_result.long_short_spread:.2%}")
        except Exception as e:
            if verbose:
                print(f"Evaluation: skipped ({e})")

    # 保存
    if args.output:
        output_path = args.output
        if not Path(output_path).is_absolute():
            output_path = str(FACTOR_LAB_DIR / output_path)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "combined_formula": combined,
                "method": args.method,
                "info": info,
                "factors_used": len(factors),
                "timestamp": datetime.now().isoformat(),
            }, f, indent=2, ensure_ascii=False, default=str)

        if verbose:
            print(f"\nSaved to: {output_path}")

    return 0


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════

def _extract_pool(data: dict) -> List[tuple]:
    """
    从各种格式的 JSON 数据中提取因子池。

    支持的格式:
      - {"pool": [(formula, ic), ...]}
      - {"factor_pool": [(formula, ic), ...]}
      - {"merged_pool": [(formula, ic), ...]}
      - {"factors": {"key": {"formula": ..., "ic_mean": ...}}}
      - [{"formula": ..., "ic": ...}, ...]
    """
    if isinstance(data, list):
        pool = []
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                pool.append((str(item[0]), float(item[1])))
            elif isinstance(item, dict):
                f = item.get("formula", item.get("id", ""))
                ic = item.get("ic", item.get("ic_mean", 0))
                if f:
                    pool.append((f, float(ic)))
        return pool

    if isinstance(data, dict):
        # 直接找 pool 列表
        for key in ("pool", "factor_pool", "merged_pool"):
            if key in data and isinstance(data[key], list):
                pool = []
                for item in data[key]:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        pool.append((str(item[0]), float(item[1])))
                return pool

        # 带 channel 的三元组格式
        for key in ("pool", "merged_pool"):
            if key in data and isinstance(data[key], list):
                pool = []
                for item in data[key]:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        pool.append((str(item[0]), float(item[1])))
                return pool

        # Agent swarm 结果格式
        if "agent_results" in data:
            pool = []
            for ag in data["agent_results"]:
                ag_pool = ag.get("pool", ag.get("factor_pool", []))
                for item in ag_pool:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        pool.append((str(item[0]), float(item[1])))
            return pool

        # 注册表格式
        if "factors" in data and isinstance(data["factors"], dict):
            pool = []
            for key, meta in data["factors"].items():
                f = meta.get("formula", key)
                ic = meta.get("ic_mean", meta.get("ic", 0))
                pool.append((f, float(ic)))
            return pool

    return []


def _clean_for_json(obj: Any) -> Any:
    """递归清理对象以便 JSON 序列化"""
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_for_json(v) for v in obj]
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)


if __name__ == "__main__":
    sys.exit(main())