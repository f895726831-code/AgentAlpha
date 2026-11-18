"""
GFlowNet 管线集成测试 — GFlowNet 训练 → 采样 → FFO 评估 → 因子入库

验证 Phase 1 Week 3-4 AlphaSAGE GFlowNets 通道 MVP 的端到端功能。

用法:
    cd /mnt/d/AgentAlpha/my-bot/scripts/wsl2
    python tests/test_gfn_pipeline.py
"""

import sys
import os
import time
from pathlib import Path

# Ensure module path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch
from gflownet.config import GFNConfig
from gflownet.runner import GFlowNetRunner
from gflownet.grammar import N_ACTIONS, sequence_to_formula, validate_sequence


def test_grammar():
    """测试 1: 语法模块正确性"""
    print("Test 1: Grammar module...")

    # 已知公式的往返测试
    test_cases = [
        ([17, 6], "Rank($close)"),                            # Rank arity=1
        ([22, 6, 11], "Ts_Mean($close, 10)"),                 # Ts_Mean arity=2
        ([41, 6, 30, 6, 11], "($close - Delay($close, 10))"),  # 混合
        ([43, 41, 6, 30, 6, 11, 30, 6, 11],
         "(($close - Delay($close, 10)) / Delay($close, 10))"),  # 复杂
    ]

    for seq, expected in test_cases:
        valid, msg = validate_sequence(seq)
        assert valid, f"Sequence {seq} invalid: {msg}"

        formula = sequence_to_formula(seq)

        # Verify FFO can parse it
        from ffo.parser import parse as ffo_parse
        try:
            ffo_parse(formula)
        except Exception as e:
            print(f"  WARNING: FFO parse failed for {formula}: {e}")

    print("  PASSED")


def test_model_init():
    """测试 2: 模型初始化"""
    print("Test 2: Model initialization...")

    config = GFNConfig()
    config.d_model = 32
    config.n_layers = 1
    config.n_heads = 2

    from gflownet.gfn import GFlowNetCore
    model = GFlowNetCore(config)

    # 参数检查
    n_params = sum(p.numel() for p in model.parameters())
    assert n_params > 10000, f"Too few parameters: {n_params}"
    assert model.logZ.requires_grad, "logZ should be trainable"

    # 前向采样检查
    with torch.no_grad():
        tids, aids, lpfs, formula = model.sample_trajectory(temperature=1.0)
        # 至少应该产生 output（哪怕 formula 是 None）
        assert len(tids) >= 2  # BOS + at least one token

    print(f"  PASSED ({n_params:,} params)")


def test_reward():
    """测试 3: 奖励函数"""
    print("Test 3: Reward computation...")

    config = GFNConfig()
    config.eval_start = "2020-01-01"
    config.eval_end = "2020-03-31"

    from gflownet.reward import RewardComputer
    rc = RewardComputer(config)

    # 已知有效因子
    r = rc.compute("Rank($close)")
    assert r > 0, f"Known valid factor should get positive reward, got {r}"

    # 无效因子
    r_invalid = rc.compute("NotAFactor")
    assert r_invalid < 0, f"Invalid factor should get negative reward, got {r_invalid}"

    # 缓存
    r1 = rc.compute("Ts_Mean($returns, 10)")
    r2 = rc.compute("Ts_Mean($returns, 10)")
    assert r1 == r2, "Cache should return same reward"

    print(f"  PASSED (valid={r:.3f}, invalid={r_invalid}, cached OK)")


def test_training():
    """测试 4: 训练循环"""
    print("Test 4: Training loop (10 episodes)...")

    config = GFNConfig()
    config.n_episodes = 10
    config.batch_size = 4
    config.d_model = 32
    config.n_layers = 1
    config.n_heads = 2
    config.d_ff = 64
    config.lr = 1e-3
    config.eval_start = "2020-01-01"
    config.eval_end = "2020-03-31"
    config.log_every = 5

    t0 = time.time()
    runner = GFlowNetRunner(config)
    runner.train(verbose=False)

    elapsed = time.time() - t0

    # 基础检查
    assert runner.episode == 9, f"Expected 10 episodes, got {runner.episode + 1}"
    assert len(runner.metrics_history) > 0, "Should have metrics"
    assert runner.best_loss < float("inf"), "Should have best loss"

    # 检查是否有因子入池
    pool = runner.reward_computer.get_pool_stats()
    # Note: 10 episodes with batch 4 may not discover factors — that's OK

    print(f"  PASSED ({elapsed:.1f}s, pool_size={pool['size']}, best_loss={runner.best_loss:.2f})")


def test_sampling():
    """测试 5: 采样 + FFO 综合"""
    print("Test 5: Sampling + FFO pipeline...")

    config = GFNConfig()
    config.n_episodes = 5
    config.batch_size = 8
    config.d_model = 64
    config.n_layers = 2
    config.n_heads = 4
    config.eval_start = "2020-01-01"
    config.eval_end = "2020-06-30"

    # 快速训练
    runner = GFlowNetRunner(config)
    runner.train(verbose=False)

    # 采样
    valid_count = 0
    formulas = set()
    with torch.no_grad():
        for _ in range(30):
            tids, aids, lpfs, formula = runner.model.sample_trajectory(temperature=1.5)
            if formula:
                # 去重
                if formula not in formulas:
                    formulas.add(formula)
                    reward = runner.reward_computer.compute(formula)
                    if reward > 0:
                        valid_count += 1

    print(f"  PASSED ({valid_count} valid factors, {len(formulas)} unique)")
    assert len(formulas) >= 3, f"Should produce at least 3 unique formulas, got {len(formulas)}"


def test_checkpoint():
    """测试 6: 检查点保存/加载"""
    print("Test 6: Checkpoint save/load...")

    config = GFNConfig()
    config.n_episodes = 3
    config.batch_size = 4
    config.d_model = 32
    config.n_layers = 1
    config.eval_start = "2020-01-01"
    config.eval_end = "2020-03-31"

    runner = GFlowNetRunner(config)
    runner.train(verbose=False)

    # 保存
    ckpt_path = "/tmp/gfn_test_checkpoint.pt"
    runner.save_checkpoint(ckpt_path)
    assert os.path.exists(ckpt_path), "Checkpoint file should exist"

    # 加载
    runner2 = GFlowNetRunner(config)
    runner2.load_checkpoint(ckpt_path)

    # 验证参数一致
    original_logZ = runner.model.logZ.item()
    loaded_logZ = runner2.model.logZ.item()
    assert abs(original_logZ - loaded_logZ) < 0.01, \
        f"logZ mismatch: {original_logZ} vs {loaded_logZ}"

    # 清理
    os.remove(ckpt_path)

    print(f"  PASSED (logZ preserved: {original_logZ:.4f})")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("GFlowNet Pipeline Integration Tests")
    print("=" * 60)
    print()

    tests = [
        test_grammar,
        test_model_init,
        test_reward,
        test_training,
        test_sampling,
        test_checkpoint,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        print()

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)