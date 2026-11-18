"""
Factor Factory 2.0 — 三通道因子工厂集成层 (Phase 1 Week 7-8)

三通道:
  - GFlowNets (AlphaSAGE): 多样性因子采样
  - LLM Agent (AlphaAgentEvo): DeepSeek 驱动的因子挖掘
  - RD-Agent GP (保留): 遗传规划基线

核心功能:
  - 并行调度 (scheduler)
  - AST 语义去重 (dedup)
  - 因子元数据注册表 (metadata)
  - AST 安全因子合成 (combiner)

快速开始:
    from factory import run_factory

    # 一键运行所有通道
    result = run_factory(gfn_episodes=200, agent_rounds=50)

    # 或单独使用各组件
    from factory import FactorRegistry, FactorCombiner

    registry = FactorRegistry()
    registry.register("Rank($close)", channel="gflownet", ic=0.04)

    combiner = FactorCombiner()
    combined, info = combiner.combine_ic_weighted(
        [("Rank($close)", 0.04), ("Ts_Mean($returns,5)", 0.03)]
    )
"""

from .dedup import FactorDeduplicator, are_equivalent, deduplicate_pool, normalize_formula, canonical_formula
from .metadata import FactorRegistry, FactorMetadata, get_registry
from .combiner import (
    FactorCombiner,
    combine_ic_weighted,
    combine_rank_blend,
    combine_conditional,
)
from .scheduler import FactoryScheduler, FactoryConfig, run_factory

__all__ = [
    # Scheduler
    "FactoryScheduler",
    "FactoryConfig",
    "run_factory",
    # Dedup
    "FactorDeduplicator",
    "are_equivalent",
    "deduplicate_pool",
    "normalize_formula",
    "canonical_formula",
    # Metadata
    "FactorRegistry",
    "FactorMetadata",
    "get_registry",
    # Combiner
    "FactorCombiner",
    "combine_ic_weighted",
    "combine_rank_blend",
    "combine_conditional",
]