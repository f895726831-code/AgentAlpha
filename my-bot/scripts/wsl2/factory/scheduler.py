"""
因子工厂调度器 — 三通道并行调度

支持:
  - GFlowNets 通道 (train + sample)
  - LLM Agent 通道 (run_agent / run_swarm)
  - RD-Agent GP 通道 (optional, 需要 rd_runner)

用法:
    from factory.scheduler import FactoryScheduler, FactoryConfig

    config = FactoryConfig(gfn_episodes=200, agent_rounds=50)
    scheduler = FactoryScheduler(config)
    result = scheduler.run_all()
"""

import concurrent.futures
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════
# FactoryConfig
# ═══════════════════════════════════════════════════════════

@dataclass
class FactoryConfig:
    """因子工厂调度器配置"""

    # 通道开关
    enable_gflownet: bool = True
    enable_llm_agent: bool = True
    enable_rd_agent: bool = False  # 默认关闭 (需要 RD-Agent 环境)

    # GFlowNet 参数
    gfn_episodes: int = 200
    gfn_sample_n: int = 100
    gfn_batch_size: int = 16

    # Agent 参数
    agent_rounds: int = 50
    agent_swarm_size: int = 1
    agent_formulas_per_round: int = 5
    agent_model: str = "deepseek-chat"

    # 评估参数 (两通道共享)
    eval_start: str = "2020-01-01"
    eval_end: str = "2020-12-31"
    eval_universe: str = "csi500"

    # 并行
    parallel: bool = True
    max_workers: int = 3

    # 输出
    registry_path: str = "data/factor_registry.json"

    def to_dict(self) -> Dict:
        return {
            "enable_gflownet": self.enable_gflownet,
            "enable_llm_agent": self.enable_llm_agent,
            "enable_rd_agent": self.enable_rd_agent,
            "gfn_episodes": self.gfn_episodes,
            "gfn_sample_n": self.gfn_sample_n,
            "agent_rounds": self.agent_rounds,
            "agent_swarm_size": self.agent_swarm_size,
            "eval_start": self.eval_start,
            "eval_end": self.eval_end,
            "eval_universe": self.eval_universe,
            "parallel": self.parallel,
        }


# ═══════════════════════════════════════════════════════════
# FactoryScheduler
# ═══════════════════════════════════════════════════════════

class FactoryScheduler:
    """
    三通道因子工厂调度器。

    用法:
        config = FactoryConfig(gfn_episodes=200, agent_rounds=50)
        scheduler = FactoryScheduler(config)
        result = scheduler.run_all()  # 并行运行两通道

        # 或单独运行
        gfn_pool = scheduler.run_gflownet()
        agent_pool = scheduler.run_llm_agent()
    """

    def __init__(self, config: FactoryConfig = None, data_dir: str = None):
        self.config = config or FactoryConfig()
        self.data_dir = data_dir
        self._start_time: Optional[datetime] = None
        self._channel_results: Dict[str, Dict] = {}

    # ═══ 通道运行器 ═══

    def run_gflownet(self, verbose: bool = True) -> Dict:
        """
        运行 GFlowNet 通道: 训练 + 采样。

        返回:
            {"pool": [(formula, ic), ...], "metrics": {...}, "status": str}
        """
        cfg = self.config
        result = {
            "channel": "gflownet",
            "pool": [],
            "status": "pending",
            "error": None,
            "metrics": {},
        }

        try:
            from gflownet import train, sample
            from gflownet.config import GFNConfig

            # 构建 GFNConfig
            gfn_cfg = GFNConfig()
            gfn_cfg.n_episodes = cfg.gfn_episodes
            gfn_cfg.batch_size = cfg.gfn_batch_size
            gfn_cfg.eval_start = cfg.eval_start
            gfn_cfg.eval_end = cfg.eval_end
            gfn_cfg.eval_universe = cfg.eval_universe

            if verbose:
                print(f"[GFlowNet] Training {cfg.gfn_episodes} episodes...")
                t0 = time.time()

            # 训练
            model, runner = train(
                config=gfn_cfg,
                data_dir=self.data_dir,
                n_episodes=cfg.gfn_episodes,
                verbose=verbose,
            )

            # 采样
            if verbose:
                print(f"[GFlowNet] Sampling {cfg.gfn_sample_n} factors...")

            sampled = sample(
                model=model,
                n=cfg.gfn_sample_n,
                verbose=verbose,
            )

            # 从 RewardComputer 获取因子池 (含 IC)
            rc = runner.reward_computer
            pool = [(f, ic) for f, ic in rc.factor_pool]
            pool.sort(key=lambda x: -abs(x[1]))

            elapsed = time.time() - t0 if verbose else 0
            if verbose:
                print(f"[GFlowNet] Done: {len(pool)} factors in pool "
                      f"({elapsed:.1f}s)")

            result["pool"] = pool
            result["status"] = "done"
            result["metrics"] = {
                "n_episodes": cfg.gfn_episodes,
                "n_sampled": len(sampled),
                "pool_size": len(pool),
                "best_ic": max([abs(ic) for _, ic in pool]) if pool else 0,
                "mean_ic": sum(abs(ic) for _, ic in pool) / max(len(pool), 1),
                "elapsed_seconds": round(elapsed, 1),
            }

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            if verbose:
                print(f"[GFlowNet] Error: {e}")

        self._channel_results["gflownet"] = result
        return result

    def run_llm_agent(self, verbose: bool = True) -> Dict:
        """
        运行 LLM Agent 通道。

        返回:
            {"pool": [(formula, ic), ...], "metrics": {...}, "status": str}
        """
        cfg = self.config
        result = {
            "channel": "llm_agent",
            "pool": [],
            "status": "pending",
            "error": None,
            "metrics": {},
        }

        try:
            # 检查 API Key
            if not os.environ.get("DEEPSEEK_API_KEY"):
                raise RuntimeError(
                    "DEEPSEEK_API_KEY not set. "
                    "Create .env file or set environment variable."
                )

            from agent import AgentConfig, run_agent, run_swarm, merge_swarm_results

            # 构建 AgentConfig
            agent_cfg = AgentConfig()
            agent_cfg.n_rounds = cfg.agent_rounds
            agent_cfg.formulas_per_round = cfg.agent_formulas_per_round
            agent_cfg.llm_model = cfg.agent_model
            agent_cfg.eval_start = cfg.eval_start
            agent_cfg.eval_end = cfg.eval_end
            agent_cfg.eval_universe = cfg.eval_universe

            if verbose:
                print(f"[LLM Agent] Running {cfg.agent_rounds} rounds "
                      f"(swarm={cfg.agent_swarm_size})...")
                t0 = time.time()

            # 运行
            if cfg.agent_swarm_size > 1:
                results = run_swarm(
                    n_agents=cfg.agent_swarm_size,
                    n_rounds=cfg.agent_rounds,
                    config=agent_cfg,
                    data_dir=self.data_dir,
                    verbose=verbose,
                )
                merged = merge_swarm_results(results)
            else:
                agent_result = run_agent(
                    config=agent_cfg,
                    data_dir=self.data_dir,
                    verbose=verbose,
                )
                results = [agent_result]
                merged = {
                    "pool": [(f, ic) for f, ic in agent_result.get("factor_pool", [])],
                    "pool_size": len(agent_result.get("factor_pool", [])),
                    "best_formula": "",
                    "best_ic": 0,
                    "total_cost_rmb": agent_result.get("llm_usage", {}).get(
                        "estimated_cost_rmb", 0),
                }

            pool = merged.get("pool", [])
            pool.sort(key=lambda x: -abs(x[1]))

            elapsed = time.time() - t0 if verbose else 0
            if verbose:
                print(f"[LLM Agent] Done: {len(pool)} factors in pool "
                      f"(CNY {merged.get('total_cost_rmb', 0):.4f}, {elapsed:.1f}s)")

            result["pool"] = pool
            result["status"] = "done"
            result["metrics"] = {
                "n_rounds": cfg.agent_rounds,
                "swarm_size": cfg.agent_swarm_size,
                "pool_size": len(pool),
                "best_ic": max([abs(ic) for _, ic in pool]) if pool else 0,
                "mean_ic": sum(abs(ic) for _, ic in pool) / max(len(pool), 1),
                "cost_rmb": merged.get("total_cost_rmb", 0),
                "elapsed_seconds": round(elapsed, 1),
            }

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            if verbose:
                print(f"[LLM Agent] Error: {e}")

        self._channel_results["llm_agent"] = result
        return result

    def run_rd_agent(self, verbose: bool = True) -> Optional[Dict]:
        """
        运行 RD-Agent 通道 (如果可用)。

        返回:
            {"pool": [...], "status": str} 或 None (不可用时)
        """
        cfg = self.config
        result = {
            "channel": "rd_agent",
            "pool": [],
            "status": "skipped",
            "error": None,
            "metrics": {},
        }

        if not cfg.enable_rd_agent:
            return result

        try:
            from rd_runner import RDAgentRunner

            if verbose:
                print("[RD-Agent] Running factor mining...")
                t0 = time.time()

            runner = RDAgentRunner()
            rd_result = runner.run_mining(
                start_time=cfg.eval_start,
                end_time=cfg.eval_end,
            )

            # 提取因子
            factors = rd_result.get("factors", [])
            pool = []
            for f in factors:
                fid = f.get("id", "")
                formula = f.get("name", fid)
                ic = f.get("ic_mean", 0)
                if formula and abs(ic) > 0:
                    pool.append((formula, ic))

            pool.sort(key=lambda x: -abs(x[1]))

            elapsed = time.time() - t0 if verbose else 0
            if verbose:
                print(f"[RD-Agent] Done: {len(pool)} factors ({elapsed:.1f}s)")

            result["pool"] = pool
            result["status"] = "done"
            result["metrics"]["pool_size"] = len(pool)
            result["metrics"]["elapsed_seconds"] = round(elapsed, 1)

        except ImportError:
            if verbose:
                print("[RD-Agent] rd_runner not available, skipping.")
            result["status"] = "skipped"
            result["error"] = "rd_runner module not found"
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            if verbose:
                print(f"[RD-Agent] Error: {e}")

        self._channel_results["rd_agent"] = result
        return result

    # ═══ 主入口 ═══

    def run_all(
        self,
        channels: List[str] = None,
        parallel: bool = None,
        verbose: bool = True,
    ) -> Dict:
        """
        一键运行所有启用的通道。

        参数:
            channels: 要运行的通道列表 (默认: 所有启用的)
            parallel: 是否并行运行 (默认: 使用 config.parallel)
            verbose:  是否输出进度

        返回:
            {
                "status": "done",
                "channels": {...},
                "merged_pool": [...],
                "registry_summary": {...},
                "elapsed_seconds": float,
            }
        """
        cfg = self.config
        self._start_time = datetime.now()

        if parallel is None:
            parallel = cfg.parallel

        # 确定要运行的通道
        if channels is None:
            channels = []
            if cfg.enable_gflownet:
                channels.append("gflownet")
            if cfg.enable_llm_agent:
                channels.append("llm_agent")
            if cfg.enable_rd_agent:
                channels.append("rd_agent")

        if not channels:
            return {"status": "error", "error": "没有启用的通道"}

        if verbose:
            print("=" * 60)
            print("Factor Factory — Three-Channel Scheduler")
            print("=" * 60)
            print(f"Channels:  {', '.join(channels)}")
            print(f"Parallel:  {parallel}")
            print(f"Eval:      {cfg.eval_start} → {cfg.eval_end} ({cfg.eval_universe})")
            print()

        t0 = time.time()

        if parallel and len(channels) > 1:
            # 并行运行
            channel_results = self._run_parallel(channels, verbose)
        else:
            # 串行运行
            channel_results = self._run_sequential(channels, verbose)

        elapsed = time.time() - t0

        # ═══ 合并因子池 ═══
        if verbose:
            print(f"\n{'=' * 60}")
            print("Merging & Deduplicating Factor Pools")
            print(f"{'=' * 60}")

        merged_pool = self.merge_pools(channel_results)

        # ═══ 注册表 ═══
        from factory.metadata import FactorRegistry
        registry = FactorRegistry(storage_path=cfg.registry_path)

        pools_for_registry = {}
        for ch, cr in channel_results.items():
            if cr.get("status") == "done" and cr.get("pool"):
                pools_for_registry[ch] = cr["pool"]

        if pools_for_registry:
            registry.merge_pools(pools_for_registry)
            if verbose:
                summary = registry.export_summary()
                print(f"Registry: {summary['total_factors']} factors "
                      f"({summary['active_factors']} active)")

        # ═══ 汇总 ═══
        total_pool_size = sum(
            len(cr.get("pool", [])) for cr in channel_results.values()
        )

        result = {
            "status": "done",
            "config": cfg.to_dict(),
            "channels": channel_results,
            "merged_pool": merged_pool,
            "merged_pool_size": len(merged_pool),
            "total_raw_formulas": total_pool_size,
            "dedup_ratio": (
                1 - len(merged_pool) / max(total_pool_size, 1)
                if total_pool_size > 0 else 0
            ),
            "registry_summary": registry.export_summary(),
            "elapsed_seconds": round(elapsed, 1),
            "timestamp": datetime.now().isoformat(),
        }

        if verbose:
            self._print_summary(result)

        return result

    # ═══ 内部方法 ═══

    def _run_parallel(
        self,
        channels: List[str],
        verbose: bool = True,
    ) -> Dict[str, Dict]:
        """ThreadPoolExecutor 并行运行通道"""
        channel_funcs = {
            "gflownet": self.run_gflownet,
            "llm_agent": self.run_llm_agent,
            "rd_agent": self.run_rd_agent,
        }

        results = {}
        n_workers = min(len(channels), self.config.max_workers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {}
            for ch in channels:
                if ch in channel_funcs:
                    if verbose:
                        print(f"[Factory] Dispatching {ch} channel...")
                    future = executor.submit(
                        channel_funcs[ch],
                        verbose=verbose,
                    )
                    futures[future] = ch

            for future in concurrent.futures.as_completed(futures):
                ch = futures[future]
                try:
                    result = future.result()
                    results[ch] = result
                except Exception as e:
                    results[ch] = {
                        "channel": ch,
                        "status": "failed",
                        "error": str(e),
                        "pool": [],
                    }
                    if verbose:
                        print(f"[Factory] {ch} channel crashed: {e}")

        return results

    def _run_sequential(
        self,
        channels: List[str],
        verbose: bool = True,
    ) -> Dict[str, Dict]:
        """串行运行通道"""
        results = {}
        for ch in channels:
            if ch == "gflownet":
                results[ch] = self.run_gflownet(verbose=verbose)
            elif ch == "llm_agent":
                results[ch] = self.run_llm_agent(verbose=verbose)
            elif ch == "rd_agent":
                results[ch] = self.run_rd_agent(verbose=verbose)
        return results

    def merge_pools(
        self,
        channel_results: Dict[str, Dict] = None,
    ) -> List[Tuple[str, float, str]]:
        """
        合并多通道因子池 → 去重。

        参数:
            channel_results: 通道结果字典 (默认: 使用已存储的结果)

        返回:
            [(formula, ic, channel), ...] — 去重后, 按 |IC| 降序
        """
        from factory.dedup import deduplicate_pool

        if channel_results is None:
            channel_results = self._channel_results

        # 收集所有因子
        all_tagged: List[Tuple[str, float, str]] = []
        for ch, cr in channel_results.items():
            if cr.get("status") == "done" and cr.get("pool"):
                for formula, ic in cr["pool"]:
                    all_tagged.append((formula, ic, ch))

        if not all_tagged:
            return []

        # 去重
        deduped = deduplicate_pool(all_tagged, mode="smart")
        return deduped

    def _print_summary(self, result: Dict):
        """打印运行摘要"""
        print(f"\n{'=' * 60}")
        print("Factory Run Complete")
        print(f"{'=' * 60}")
        print(f"Elapsed:   {result['elapsed_seconds']:.1f}s")
        print(f"Raw:       {result['total_raw_formulas']} formulas")
        print(f"After dedup: {result['merged_pool_size']} unique")
        if result['total_raw_formulas'] > 0:
            print(f"Dedup ratio: {result['dedup_ratio']:.1%}")

        # 各通道统计
        for ch, cr in result["channels"].items():
            status = cr.get("status", "unknown")
            pool_size = len(cr.get("pool", []))
            metrics = cr.get("metrics", {})
            best_ic = metrics.get("best_ic", 0)
            error = cr.get("error", "")
            suffix = f"  best|IC|={best_ic:.4f}" if pool_size > 0 else ""
            if error:
                suffix += f"  error={error[:60]}"
            print(f"  {ch:12s}: {status:7s}  pool={pool_size:3d}{suffix}")

        # Top 5 合并后因子
        pool = result["merged_pool"]
        if pool:
            print(f"\nTop 5 merged factors:")
            for i, (formula, ic, channel) in enumerate(pool[:5], 1):
                print(f"  {i}. [{channel}] {formula}  |IC|={abs(ic):.4f}")

    def export_results(self, output_path: str):
        """导出完整结果为 JSON"""
        result = self.run_all(verbose=False) if not self._channel_results else {
            "channels": self._channel_results,
            "merged_pool": self.merge_pools(),
        }

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════

def run_factory(
    channels: List[str] = None,
    gfn_episodes: int = 200,
    agent_rounds: int = 50,
    agent_swarm: int = 1,
    parallel: bool = True,
    data_dir: str = None,
    verbose: bool = True,
    **kwargs,
) -> Dict:
    """
    一键运行因子工厂。

    参数:
        channels:     要运行的通道 (默认: gflownet + agent)
        gfn_episodes: GFlowNet 训练轮次
        agent_rounds: Agent 轮次
        agent_swarm:  Swarm Agent 数
        parallel:     是否并行
        data_dir:     Qlib 数据目录
        verbose:      是否输出进度

    返回:
        运行结果字典
    """
    config = FactoryConfig(
        gfn_episodes=gfn_episodes,
        agent_rounds=agent_rounds,
        agent_swarm_size=agent_swarm,
        parallel=parallel,
        **kwargs,
    )
    scheduler = FactoryScheduler(config=config, data_dir=data_dir)
    return scheduler.run_all(channels=channels, verbose=verbose)