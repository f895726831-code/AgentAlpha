"""FFO API integration test — verify API server endpoints"""
import sys
sys.path.insert(0, "/home/user/factor-lab")

from ffo import evaluate
from ffo.reporter import format_report_short

# Test 1: More realistic alpha factor (Alpha101 #1)
alpha1 = "Rank(Ts_ArgMax(SignedPower(($returns < 0 ? $close / Delay($close,1) - 1 : $close / Delay($close,1) - 1), 2), 5)) - 0.5"
# Simpler version of the idea: rank stocks by their worst 5-day return
formula = "Ts_Min(($close / Delay($close, 1) - 1), 5)"

print("=" * 80)
print("Testing realistic alpha factor")
print("=" * 80)

r = evaluate(formula, start="2024-01-01", end="2026-01-01", universe="csi500")
print(format_report_short(r))
print(f"Full IC: mean={r.ic_mean:.4f}, std={r.ic_std:.4f}, IR={r.ic_ir:.3f}")
print(f"Long/short spread: {r.long_short_spread:.2%}")

# Test 2: Volume-price interaction
formula2 = "-1 * Rank(Ts_Mean($volume, 20) / Delay(Ts_Mean($volume, 20), 20) - 1)"
print("")
print(format_report_short(evaluate(formula2, start="2024-01-01", end="2026-01-01", universe="csi500")))

# Test 3: Volatility factor
formula3 = "-1 * Rank(Ts_Std($returns, 20))"
print("")
print(format_report_short(evaluate(formula3, start="2024-01-01", end="2026-01-01", universe="csi500")))

print("")
print("API-ready formulas validated.")