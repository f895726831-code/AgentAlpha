"""
AlphaAgentEvo 管线集成测试 — Agent 模块 → FFO 评估 → 因子入库

验证 Phase 1 Week 5-6 LLM Agent 通道的端到端功能。

用法:
    cd /mnt/d/AgentAlpha/my-bot/scripts/wsl2
    python tests/test_agent_pipeline.py
"""

import sys
import os
import time
from pathlib import Path

# 路径设置
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import AgentConfig
from agent.llm_client import DeepSeekClient
from agent.rewards import HierarchicalReward
from agent.replay_buffer import ExperienceBuffer
from agent.reflection import Reflector
from agent.prompts import (
    SYSTEM_PROMPT,
    build_user_message,
    build_reflection_message,
    get_few_shot_text,
    FFO_OPERATOR_REFERENCE,
)
from agent.factor_agent import FactorAgent


def test_prompt_generation():
    """测试 1: 提示词模板生成"""
    print("Test 1: Prompt template generation...")

    # 检查系统提示词非空
    assert len(SYSTEM_PROMPT) > 500, "System prompt too short"
    assert "Rank(expr)" in SYSTEM_PROMPT, "Missing operator reference"
    assert "JSON" in SYSTEM_PROMPT, "Missing output format instruction"

    # 检查 FFO 参考手册
    assert "Ts_Mean" in FFO_OPERATOR_REFERENCE
    assert "$close" in FFO_OPERATOR_REFERENCE
    assert "If" in FFO_OPERATOR_REFERENCE

    # 检查 few-shot 示例
    few_shot = get_few_shot_text(3)
    assert "Rank" in few_shot
    assert "Ts_Mean" in few_shot

    # 构建用户消息
    pool_stats = {
        "size": 3,
        "max_ic": 0.045,
        "mean_ic": 0.025,
        "operator_usage": {"Rank": 5, "Ts_Mean": 3, "-": 2},
    }
    recent = [{"best_formula": "Rank($close)", "best_ic": 0.04, "best_reward": 0.5}]
    msg = build_user_message(
        round_num=5, n_rounds=50, pool_stats=pool_stats,
        recent_results=recent, strategy_hint="探索新操作符"
    )
    assert "第 5/50 轮" in msg
    assert "探索新操作符" in msg
    assert "Rank($close)" in msg

    # 构建反思消息
    ref_msg = build_reflection_message(
        recent_results=recent, pool_stats=pool_stats,
        buffer_stats={"total_formulas": 25, "mean_reward": 0.3},
        n_rounds_completed=40,
    )
    assert "策略反思" in ref_msg
    assert "40 轮完成" in ref_msg
    assert "Rank" in ref_msg  # operator usage table

    print("  PASSED")


def test_llm_client():
    """测试 2: LLM 客户端 (无 API 调用)"""
    print("Test 2: LLM client initialization...")

    client = DeepSeekClient(
        model="deepseek-chat",
        timeout=30,
        max_retries=2,
    )

    assert client.model == "deepseek-chat"
    assert client.timeout == 30
    assert client.max_retries == 2

    # 测试 JSON 提取
    json_text = '```json\n{"thinking": "test", "formulas": ["Rank($close)"]}\n```'
    thinking, formulas = client.extract_formulas(json_text)
    assert thinking == "test"
    assert "Rank($close)" in formulas

    # 测试回退提取
    fallback_text = 'Here is a formula: "Rank(Ts_Mean($close, 10))"'
    _, formulas = client.extract_formulas(fallback_text)
    # 回退提取可能不完美, 但不应崩溃

    # 测试 JSON 解析
    data = client.extract_json('{"key": "value"}')
    assert data["key"] == "value"

    # 测试嵌套 JSON
    data = client.extract_json('Some text {"a": 1} more text')
    assert data["a"] == 1

    print("  PASSED")


def test_reward():
    """测试 3: 分层奖励"""
    print("Test 3: Hierarchical reward computation...")

    config = AgentConfig()
    config.eval_start = "2020-01-01"
    config.eval_end = "2020-03-31"
    config.pool_min_ic = 0.02

    rc = HierarchicalReward(config)

    # 有效因子
    reward, details = rc.compute("Rank($close)")
    assert reward > 0, f"Valid factor should get positive reward, got {reward}"
    assert details["syntax_valid"]
    assert details["ic_score"] >= 0

    # 无效因子
    reward_inv, details_inv = rc.compute("NotAFactor!!!")
    assert reward_inv < 0, f"Invalid factor should get negative reward, got {reward_inv}"
    assert not details_inv["syntax_valid"]

    # 缓存
    r1, _ = rc.compute("Ts_Mean($returns, 10)")
    r2, _ = rc.compute("Ts_Mean($returns, 10)")
    assert r1 == r2, "Cache should return same reward"

    # 批量
    formulas = ["Rank($close)", "Scale($volume)", "Ts_Mean($returns, 5)"]
    results = rc.compute_batch(formulas)
    assert len(results) == 3

    # 因子池统计
    stats = rc.get_pool_stats()
    assert "size" in stats
    assert "max_ic" in stats

    print(f"  PASSED (pool_size={stats['size']}, max|IC|={stats['max_ic']:.4f})")


def test_replay_buffer():
    """测试 4: 经验回放缓冲区"""
    print("Test 4: Experience buffer...")

    buffer = ExperienceBuffer(capacity=10)

    # 添加经验
    buffer.add(
        round_num=1,
        formulas=["Rank($close)", "Ts_Mean($close, 5)"],
        rewards=[0.5, 0.3],
        details=[
            {"syntax_valid": True, "ic_mean": 0.04, "ic_score": 0.8},
            {"syntax_valid": True, "ic_mean": 0.02, "ic_score": 0.4},
        ],
        strategy_hint="explore",
    )

    buffer.add(
        round_num=2,
        formulas=["Scale($volume)"],
        rewards=[0.6],
        details=[{"syntax_valid": True, "ic_mean": 0.05, "ic_score": 1.0}],
        strategy_hint="explore",
    )

    assert len(buffer) == 2

    # 采样
    recent = buffer.sample_recent(1)
    assert recent[0]["round"] == 2

    best = buffer.sample_best(1)
    assert best[0]["best_reward"] == 0.6

    # 统计
    stats = buffer.get_stats()
    assert stats["total_rounds"] == 2
    assert stats["total_formulas"] == 3
    assert abs(stats["best_ic"]) > 0

    # 操作符统计
    op_usage = buffer.get_operator_usage()
    assert "rank" in op_usage or "Rank" in str(op_usage)

    # 序列化
    json_str = buffer.to_json()
    assert "Rank" in json_str

    # 容量控制
    for i in range(15):
        buffer.add(round_num=i, formulas=[], rewards=[], details=[])
    assert len(buffer) <= 10

    print(f"  PASSED ({stats['total_formulas']} entries, best_ic={stats['best_ic']:.4f})")


def test_reflection_auto():
    """测试 5: 反思机制 (自动模式, 无 LLM)"""
    print("Test 5: Reflection (auto mode)...")

    reflector = Reflector()
    assert reflector.current_strategy_hint == ""

    # 测试自动反思 (不调用 LLM)
    pool_stats = {
        "size": 2,
        "max_ic": 0.03,
        "mean_ic": 0.02,
        "operator_usage": {"rank": 8, "ts_mean": 6},
    }
    buffer_stats = {
        "total_formulas": 20,
        "mean_reward": 0.2,
        "best_ic": 0.03,
    }

    result = reflector._auto_reflection(pool_stats, buffer_stats)

    assert "diagnosis" in result
    assert "patterns_detected" in result
    assert "unexplored_areas" in result
    assert "strategy_adjustment" in result

    # 应该检测到 rank 过度使用
    assert any("rank" in p.lower() for p in result["patterns_detected"]) or \
           any("rank" in str(result).lower())

    hint = result["strategy_adjustment"]
    assert len(hint) > 10

    print(f"  PASSED (patterns: {result['patterns_detected']})")


def test_config():
    """测试 6: Agent 配置"""
    print("Test 6: Agent configuration...")

    config = AgentConfig()
    assert config.n_rounds == 50
    assert config.formulas_per_round == 5
    assert config.reflection_every == 10
    assert config.llm_model == "deepseek-chat"

    # 序列化
    d = config.to_dict()
    assert d["n_rounds"] == 50
    assert d["llm_model"] == "deepseek-chat"

    # 自定义
    config2 = AgentConfig(
        n_rounds=10,
        llm_temperature=0.3,
        pool_min_ic=0.03,
    )
    assert config2.n_rounds == 10
    assert config2.llm_temperature == 0.3
    assert config2.pool_min_ic == 0.03

    print("  PASSED")


def test_module_imports():
    """测试 7: 模块导入完整性"""
    print("Test 7: Module imports...")

    # 顶层导入
    from agent import (
        AgentConfig,
        FactorAgent,
        DeepSeekClient,
        HierarchicalReward,
        ExperienceBuffer,
        Reflector,
        SYSTEM_PROMPT,
        build_user_message,
        run_agent,
        run_swarm,
        merge_swarm_results,
    )
    assert AgentConfig is not None
    assert FactorAgent is not None

    # 默认配置
    from agent.config import default_config
    assert default_config is not None

    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("AlphaAgentEvo Pipeline Integration Tests")
    print("=" * 60)
    print()

    tests = [
        test_prompt_generation,
        test_config,
        test_llm_client,
        test_module_imports,
        test_reward,
        test_replay_buffer,
        test_reflection_auto,
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