"""
Factor Factory Pipeline Integration Tests — Phase 1 Week 7-8

测试范围:
  - 字符串规范化 + AST 语义去重
  - 因子元数据注册表 CRUD
  - Factor Combiner v3.0 安全验证
  - 跨通道合并 + 调度器配置

用法:
    python tests/test_factory_pipeline.py

预期: 7/7 通过
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# 路径设置
SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

passed = 0
failed = 0


def test(name):
    """测试装饰器 (打印格式)"""

    def decorator(fn):
        def wrapper():
            global passed, failed
            print(f"Test: {name}...", end=" ")
            try:
                fn()
                print("PASSED")
                passed += 1
            except Exception as e:
                print(f"FAILED: {e}")
                failed += 1
                import traceback
                traceback.print_exc()

        return wrapper

    return decorator


# ═══════════════════════════════════════════════════════════
# Test 1: 字符串规范化
# ═══════════════════════════════════════════════════════════

@test("dedup: normalize_formula")
def test_dedup_normalization():
    from factory.dedup import normalize_formula

    # 空白移除 (注意: 函数名会小写)
    assert normalize_formula("Rank ( $close , 20 )") == "rank($close,20)"

    # 函数名大小写
    assert normalize_formula("RANK($close, 20)") == "rank($close,20)"
    assert normalize_formula("ts_mean($volume, 10)") == "ts_mean($volume,10)"

    # 字段名大小写
    assert normalize_formula("$CLOSE + $OPEN") == "$close+$open"

    # 数字归一化
    assert normalize_formula("Rank($close, 5.0)") == "rank($close,5)"

    # 冗余括号 (单层 — parser 层面处理)
    norm_with_parens = normalize_formula("(($close))")
    assert norm_with_parens in ("($close)", "($close)")

    print(f"(stripped whitespace, normalized case) ", end="")


# ═══════════════════════════════════════════════════════════
# Test 2: AST 语义等价
# ═══════════════════════════════════════════════════════════

@test("dedup: are_equivalent (AST)")
def test_dedup_equivalence():
    from factory.dedup import are_equivalent

    # 完全相同
    assert are_equivalent("Rank($close)", "Rank($close)")

    # 空白差异 → 规范化后等价
    assert are_equivalent("Rank( $close )", "Rank($close)")

    # 字段大小写差异
    assert are_equivalent("$CLOSE - $OPEN", "$close - $open")

    # 交换律: +
    assert are_equivalent("$close + $volume", "$volume + $close")

    # 交换律: *
    assert are_equivalent("$close * $returns", "$returns * $close")

    # 非交换律操作符不应等价
    assert not are_equivalent("$close - $open", "$open - $close")
    assert not are_equivalent("$close / $volume", "$volume / $close")

    # 结构不同: Rank vs Ts_Mean
    assert not are_equivalent("Rank($close)", "Ts_Mean($close, 10)")

    # 不同的参数
    assert not are_equivalent("Rank($close, 5)", "Rank($close, 20)")

    # 结合律: (a + b) + c ↔ a + (b + c) — 仅结构等价, 非递归展平
    add_assoc_1 = are_equivalent("($close + $open) + $high", "$close + ($open + $high)")
    assert add_assoc_1, f"结合律检测失败: ($close+$open)+$high vs $close+($open+$high)"

    print(f"(commutativity, structure detection) ", end="")


# ═══════════════════════════════════════════════════════════
# Test 3: 因子池去重
# ═══════════════════════════════════════════════════════════

@test("dedup: deduplicate pool")
def test_dedup_pool():
    from factory.dedup import FactorDeduplicator

    dedup = FactorDeduplicator()

    # 混合因子池
    pool = [
        ("Rank($close, 20)", 0.04, "gflownet"),
        ("rank( $close , 20 )", 0.03, "agent"),  # 重复 (空白差异)
        ("$volume + $close", 0.05, "gflownet"),
        ("$close + $volume", 0.04, "agent"),  # 重复 (交换律)
        ("$close - $open", 0.02, "gflownet"),  # 不与 $open-$close 重复
        ("Ts_Mean($returns, 5)", 0.06, "agent"),
    ]

    result = dedup.deduplicate(pool, mode="smart")

    # 应该移除 2 个重复
    assert len(result) == 4, f"期望 4 个因子, 得到 {len(result)}"

    formulas = [f for f, _, _ in result]
    # Rank($close) 变体应该只保留 IC 最大的
    assert "Rank($close, 20)" in formulas
    # + 交换律去重: 保留 |IC| 最大的 "$volume + $close"
    assert "$volume + $close" in formulas or "$close + $volume" in formulas

    # 验证保留的是 |IC| 最大的
    for formula, ic, _ in result:
        if "Rank" in formula:
            assert abs(ic) == 0.04, f"Rank 因子应该保留 IC=0.04, 得到 {ic}"

    print(f"(4/6 unique, |IC| max retained) ", end="")


# ═══════════════════════════════════════════════════════════
# Test 4: 元数据注册表 CRUD
# ═══════════════════════════════════════════════════════════

@test("metadata: FactorRegistry CRUD")
def test_metadata_registry():
    from factory.metadata import FactorRegistry, FactorMetadata

    # 使用临时文件
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # 创建注册表
        registry = FactorRegistry(storage_path=tmp_path)
        assert len(registry) == 0

        # 注册因子
        m1 = registry.register("Rank($close, 20)", channel="gflownet", ic=0.04)
        assert len(registry) == 1
        assert abs(m1.ic_mean) == 0.04
        assert m1.source_channel == "gflownet"

        # 注册第二个
        m2 = registry.register("Ts_Mean($returns, 5)", channel="agent", ic=0.06)
        assert len(registry) == 2

        # 重复注册 (不同 IC) → 应追加历史
        m1b = registry.register("Rank($close, 20)", channel="agent", ic=0.05)
        assert len(registry) == 2  # 不增加
        assert len(m1b.ic_history) == 2  # 追加了历史记录
        assert abs(m1b.ic_mean) == 0.05  # 更新为最佳 IC
        assert m1b.source_channel == "merged"  # 通道合并

        # get
        m = registry.get("Rank($close, 20)")
        assert m is not None
        assert abs(m.ic_mean) == 0.05

        # get (不存在)
        assert registry.get("NotAFactor") is None

        # list_active
        active = registry.list_active(min_ic=0.02)
        assert len(active) == 2

        # get_by_channel
        agent_factors = registry.get_by_channel("merged")
        assert len(agent_factors) == 1

        # get_top
        top = registry.get_top(5)
        assert len(top) == 2
        assert abs(top[0].ic_mean) >= abs(top[1].ic_mean)  # 降序

        # export_summary
        summary = registry.export_summary()
        assert summary["total_factors"] == 2
        assert summary["active_factors"] == 2

        # 持久化: 保存后重新加载
        registry.save()

        registry2 = FactorRegistry(storage_path=tmp_path)
        assert len(registry2) == 2
        m_loaded = registry2.get("Rank($close, 20)")
        assert m_loaded is not None
        assert abs(m_loaded.ic_mean) == 0.05

        print(f"(register, dedup, persist, reload) ", end="")

    finally:
        os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════
# Test 5: FactorMetadata 自动提取
# ═══════════════════════════════════════════════════════════

@test("metadata: FactorMetadata.from_formula")
def test_metadata_from_formula():
    from factory.metadata import FactorMetadata

    # 简单因子
    m = FactorMetadata.from_formula("Rank($close, 20)", ic=0.04, channel="gflownet")
    assert "Rank" in m.operators_used
    assert m.complexity >= 3  # Rank + FieldRef + Number

    # 复杂因子
    m2 = FactorMetadata.from_formula(
        "($close - Ts_Mean($close, 20)) / Ts_Std($close, 20)",
        ic=0.05,
        channel="agent",
    )
    assert "Ts_Mean" in m2.operators_used
    assert "Ts_Std" in m2.operators_used
    assert "-" in m2.operators_used
    assert "/" in m2.operators_used
    assert m2.complexity > 5  # 更复杂的 AST

    print(f"(operator extraction, complexity score) ", end="")


# ═══════════════════════════════════════════════════════════
# Test 6: Combiner AST 安全 + IC 加权
# ═══════════════════════════════════════════════════════════

@test("combiner: AST-safe + IC-weighted")
def test_combiner_ast_safe():
    from factory.combiner import FactorCombiner

    cb = FactorCombiner()

    # Test 1: IC 加权
    factors = [
        ("Rank($close, 20)", 0.04),
        ("Ts_Mean($returns, 5)", 0.03),
        ("($close - $open) / ($high - $low + 0.001)", 0.02),
    ]

    combined, info = cb.combine_ic_weighted(factors)
    assert info["parsed_ok"], f"IC加权合成解析失败: {combined}"
    assert info["n_factors"] == 3
    assert len(info["weights"]) == 3
    assert abs(sum(info["weights"]) - 1.0) < 1e-6, f"权重未归一化: {info['weights']}"

    # 权重递减验证: |IC| 最大的因子权重最大
    w1 = info["weights"][0]  # IC=0.04 的因子
    w3 = info["weights"][2]  # IC=0.02 的因子
    assert w1 > w3, f"高 IC 因子应有更大权重: w1={w1}, w3={w3}"

    # Test 2: Rank Blend
    combined2, info2 = cb.combine_rank_blend(factors)
    assert info2["parsed_ok"], f"Rank Blend合成解析失败: {combined2}"
    assert info2["n_factors"] == 3
    assert "/ 3" in combined2 or "/3" in combined2.replace(" ", "")

    # Test 3: 条件组合
    combined3, info3 = cb.combine_conditional(factors)
    assert info3["parsed_ok"], f"条件合成解析失败: {combined3}"
    assert "If" in combined3
    assert "Ts_Mean($volume, 20)" in combined3

    # Test 4: validate
    assert cb.validate("Rank($close, 20)")
    assert not cb.validate("Not a valid formula!!!")

    # Test 5: 单因子
    combined4, info4 = cb.combine_ic_weighted([("Rank($close)", 0.04)])
    assert info4["parsed_ok"]
    assert info4["n_factors"] == 1

    # Test 6: 时序组合
    combined5, info5 = cb.combine_temporal(factors, window=20)
    assert info5["parsed_ok"], f"时序合成解析失败: {combined5}"

    print(f"(all 4 methods parse-safe, weights normalized) ", end="")


# ═══════════════════════════════════════════════════════════
# Test 7: 跨通道合并 + 调度器配置
# ═══════════════════════════════════════════════════════════

@test("scheduler: cross-channel merge + config")
def test_scheduler_integration():
    from factory.dedup import deduplicate_pool
    from factory.metadata import FactorRegistry
    from factory.scheduler import FactoryConfig, FactoryScheduler

    # 模拟两通道因子池
    gfn_pool = [
        ("Rank($close, 20)", 0.04),
        ("$returns", 0.06),
        ("Ts_Mean($volume, 10)", 0.03),
    ]
    agent_pool = [
        ("Ts_Mean($returns, 5)", 0.05),
        ("rank( $close , 20 )", 0.035),  # 与 gfn 的 Rank 重复
        ("($close - $open) / ($high - $low + 0.001)", 0.04),
    ]

    # 合并 + 去重
    all_tagged = (
        [(f, ic, "gflownet") for f, ic in gfn_pool]
        + [(f, ic, "llm_agent") for f, ic in agent_pool]
    )
    deduped = deduplicate_pool(all_tagged, mode="smart")

    # 去重后应该只有 5 个因子 (Rank 合并)
    assert len(deduped) == 5, f"期望 5 个去重因子, 得到 {len(deduped)}"

    # 验证 Rank 因子合并 (保留 |IC| 最大的)
    rank_factors = [(f, ic) for f, ic, ch in deduped if "Rank" in f]
    assert len(rank_factors) == 1, f"Rank 因子应该合并为 1 个, 得到 {len(rank_factors)}"
    assert abs(rank_factors[0][1]) == 0.04  # 保留最大的 IC

    # 注册表合并
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        registry = FactorRegistry(storage_path=tmp_path)
        pools = {"gflownet": gfn_pool, "llm_agent": agent_pool}
        registry.merge_pools(pools)

        assert len(registry) == 5
        summary = registry.export_summary()
        assert summary["total_factors"] == 5

        # 通道分布
        by_channel = summary["by_channel"]
        assert by_channel.get("gflownet", 0) + by_channel.get("merged", 0) >= 2

    finally:
        os.unlink(tmp_path)

    # FactoryConfig 序列化
    config = FactoryConfig(
        gfn_episodes=200,
        agent_rounds=50,
        agent_swarm_size=3,
    )
    d = config.to_dict()
    assert d["gfn_episodes"] == 200
    assert d["agent_rounds"] == 50
    assert d["agent_swarm_size"] == 3
    assert d["parallel"] is True

    # FactoryScheduler 实例化
    scheduler = FactoryScheduler(config=config)
    assert scheduler.config.gfn_episodes == 200

    print(f"(5/6 unique after dedup, registry merge, config serialization) ", end="")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("Factor Factory Pipeline Integration Tests")
    print("=" * 60)
    print()

    # 运行所有测试
    test_dedup_normalization()
    test_dedup_equivalence()
    test_dedup_pool()
    test_metadata_registry()
    test_metadata_from_formula()
    test_combiner_ast_safe()
    test_scheduler_integration()

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)