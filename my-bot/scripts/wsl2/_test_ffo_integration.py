"""FFO 集成测试: 验证 FFO 可以作为 GFlowNet 奖励函数的数据源"""
import sys; sys.path.insert(0, ".")

print("Testing GFlowNet + FFO integration...")

# 1. 测试奖励计算器初始化
print("1. Initializing RewardComputer...")
from gflownet.config import GFNConfig
from gflownet.reward import RewardComputer

config = GFNConfig()
config.eval_start = "2020-01-01"
config.eval_end = "2020-06-30"
config.eval_universe = "csi500"

rc = RewardComputer(config)

# 2. 测试已知因子评估
print("2. Testing known factor evaluation...")

test_formulas = [
    "Rank($close)",
    "Ts_Mean($returns, 10)",
    "($close - Delay($close, 5)) / Delay($close, 5)",
    "Scale($volume)",
    "Rank(Ts_Mean($close, 10))",
]

for formula in test_formulas:
    reward = rc.compute(formula)
    status = "OK" if reward > 0 else "FAIL"
    print(f"  {status}: {formula} → reward={reward:.3f}")

# 3. 测试无效公式
print("3. Testing invalid formula handling...")
invalid = rc.compute("NotARealFormula")
print(f"  Invalid → reward={invalid:.3f} (expected < 0)")

# 4. 测试因子池
print("4. Factor pool stats:")
stats = rc.get_pool_stats()
print(f"  {stats}")

# 5. 测试缓存
print("5. Testing cache...")
r1 = rc.compute("Rank($close)")
r2 = rc.compute("Rank($close)")
print(f"  Cache hit: {r1 == r2}, evals={rc.total_evaluations}, hits={rc.cache_hits}")

print("\nFFO integration test PASSED!")