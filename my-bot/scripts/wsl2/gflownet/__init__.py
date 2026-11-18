"""
GFlowNets 因子挖掘模块 — Phase 1 Week 3-4 MVP

基于 AlphaSAGE (ICLR 2026, arXiv 2509.25055) 论文设计。
使用 Trajectory Balance GFlowNet 采样多样化的 Alpha 因子公式。

快速开始:
    from gflownet import GFNConfig, train, sample

    # 训练
    model, metrics = train(n_episodes=200, batch_size=16)

    # 采样
    factors = sample(model, n=100)
    for formula, ic in factors:
        print(f"{formula}: IC={ic:.4f}")

模块组成:
    config.py   — 超参数配置
    grammar.py  — Token 词汇表 + 前缀表达式语法
    encoder.py  — Transformer 状态编码器
    policy.py   — 前向/后向策略网络
    gfn.py      — GFlowNet 核心 (轨迹采样 + TB 损失)
    rewar.py   — v4 三层奖励引擎 (L1 Quality Gate + L2 Alpha Score + L3 Incremental)
    runner.py   — 训练循环
    archive.py  — MAP-Elites 因子档案库 (v4)
    eval.py     — 多目标评价体系 (v4)
    sampler.py  — 双通道采样器 (v4 DualChannelSampler)

依赖:
    - PyTorch >= 2.0 (CPU 即可)
    - ffo 模块 (因子评估引擎)
    - Qlib 数据 (CSI 500)
"""

from .config import GFNConfig, default_config
from .gfn import GFlowNetCore
from .runner import GFlowNetRunner
from .grammar import (
    N_ACTIONS, VOCAB_SIZE,
    sequence_to_formula, validate_sequence,
    print_token_summary,
)


def train(
    config: GFNConfig = None,
    data_dir: str = None,
    n_episodes: int = None,
    verbose: bool = True,
) -> tuple:
    """
    训练 GFlowNet。

    参数:
        config:     超参数配置 (默认使用 GFNConfig())
        data_dir:   Qlib 数据目录
        n_episodes: episode 数 (默认使用 config 中的值)
        verbose:    是否输出进度

    返回:
        (GFlowNetCore, GFlowNetRunner) — 训练好的模型 + 训练器
    """
    runner = GFlowNetRunner(config=config or GFNConfig(), data_dir=data_dir)
    runner.train(n_episodes=n_episodes, verbose=verbose)
    return runner.model, runner


def sample(
    model: GFlowNetCore,
    n: int = 100,
    temperature: float = None,
    verbose: bool = True,
) -> list:
    """
    从训练好的 GFlowNet 采样因子。

    参数:
        model:       训练好的 GFlowNetCore 模型
        n:           采样数量
        temperature: 采样温度 (默认使用 config.sample_temperature)
        verbose:     是否输出进度

    返回:
        [(formula, reward), ...] — 公式字符串 + 奖励值列表
    """
    if temperature is None:
        temperature = model.config.sample_temperature

    from .reward import RewardComputer
    rc = RewardComputer(model.config)
    rc._ensure_data_loaded()

    results = []
    attempts = 0
    max_attempts = n * 3

    if verbose:
        print(f"Sampling {n} factors (temperature={temperature})...")

    while len(results) < n and attempts < max_attempts:
        attempts += 1
        _, _, _, formula = model.sample_trajectory(temperature=temperature)

        if formula is None:
            continue

        # 去重
        if formula in [r[0] for r in results]:
            continue

        # 评估
        reward = rc.compute(formula)
        if reward > 0:
            results.append((formula, reward))

        if verbose and len(results) % 20 == 0:
            print(f"  {len(results)}/{n} valid factors...")

    if verbose:
        print(f"Sampled {len(results)} valid factors in {attempts} attempts "
              f"({len(results) / max(attempts, 1) * 100:.0f}% success rate)")

    return results


def load_model(checkpoint_path: str, config: GFNConfig = None) -> tuple:
    """
    加载检查点。

    返回:
        (GFlowNetCore, GFlowNetRunner)
    """
    runner = GFlowNetRunner(config=config or GFNConfig())
    runner.load_checkpoint(checkpoint_path)
    return runner.model, runner


def evaluate_factors(
    formulas: list,
    config: GFNConfig = None,
    data_dir: str = None,
) -> list:
    """
    用 FFO 完整评估因子列表。

    返回:
        [EvaluationResult, ...]
    """
    from ffo.evaluator import FactorEvaluator

    cfg = config or GFNConfig()
    evaluator = FactorEvaluator(data_dir=data_dir)

    results = []
    for formula in formulas:
        result = evaluator.evaluate(
            formula,
            start=cfg.eval_start,
            end=cfg.eval_end,
            universe=cfg.eval_universe,
        )
        results.append(result)

    return results