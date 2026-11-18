"""端到端集成测试: GFlowNet 训练 → 采样 → FFO 评估"""
import sys; sys.path.insert(0, ".")

import torch
print("=" * 60)
print("GFlowNet Pipeline Integration Test")
print("=" * 60)

# 1. 配置
from gflownet.config import GFNConfig
config = GFNConfig()
config.n_episodes = 20       # 小量 episodes
config.batch_size = 8         # 小批量
config.d_model = 32           # 小模型
config.n_layers = 1           # 单层 Transformer
config.n_heads = 2
config.d_ff = 64
config.lr = 1e-3
config.eval_start = "2020-01-01"  # 短区间加速
config.eval_end = "2020-06-30"
config.log_every = 5         # 频繁日志

print(f"Config: {config.n_episodes} episodes, batch={config.batch_size}")
print(f"Eval: {config.eval_start} to {config.eval_end}")

# 2. 训练
from gflownet.runner import GFlowNetRunner
from gflownet.grammar import N_ACTIONS

print("\n--- Training ---")
runner = GFlowNetRunner(config)
runner.train(verbose=True)

# 3. 采样
print("\n--- Sampling ---")
results = []
with torch.no_grad():
    for i in range(50):
        tids, aids, lpfs, formula = runner.model.sample_trajectory(temperature=1.5)
        if formula:
            reward = runner.reward_computer.compute(formula)
            if reward > 0:
                results.append((formula, reward))

print(f"Sampled {len(results)} valid factors")

# 4. 分析
if results:
    # 按奖励排序
    results.sort(key=lambda x: -x[1])

    print(f"\nTop 5 discovered factors:")
    for formula, reward in results[:5]:
        print(f"  {formula}  (reward={reward:.3f})")

    # 操作符多样性
    from gflownet.grammar import TOKEN_BY_NAME
    op_counts = {}
    for formula, _ in results:
        for t in TOKEN_BY_NAME.values():
            if t.arity > 0 and t.name in formula:
                op_counts[t.name] = op_counts.get(t.name, 0) + 1

    print(f"\nOperator usage ({len(op_counts)} distinct):")
    for op, cnt in sorted(op_counts.items(), key=lambda x: -x[1]):
        print(f"  {op}: {cnt}")

# 5. 检查
total_params = sum(p.numel() for p in runner.model.parameters())
print(f"\nModel: {total_params:,} params")
print(f"Final logZ: {runner.model.logZ.item():.4f}")
print(f"Best loss: {runner.best_loss:.4f}")
print(f"Metrics entries: {len(runner.metrics_history)}")

pool = runner.reward_computer.get_pool_stats()
print(f"Factor pool: {pool}")

print(f"\nIntegration test {'PASSED' if len(results) >= 3 else 'PARTIAL'}: "
      f"{len(results)} valid factors discovered")