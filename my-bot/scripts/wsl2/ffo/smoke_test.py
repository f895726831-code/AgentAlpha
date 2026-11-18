"""FFO smoke test v2 — more comprehensive testing"""
import sys
sys.path.insert(0, "/home/user/factor-lab")

from ffo import evaluate
from ffo.reporter import format_report_short, format_report

formulas = [
    # 5-day momentum
    ("($close - Delay($close, 5)) / Delay($close, 5)", "5日动量"),
    # Cross-sectional rank of close
    ("Rank($close, 20)", "收盘价排名"),
    # Mean of daily returns over 10 days
    ("Ts_Mean($returns, 10)", "10日均收益"),
    # Negative volume rank (low vol preference)
    ("-1 * Rank($volume, 20)", "低换手"),
    # 10-day return, scaled
    ("Scale($close / Delay($close, 10) - 1)", "10日收益率标准化"),
    # Simple: close / yesterday's close - 1
    ("$close / Delay($close, 1) - 1", "日收益率"),
]

print("=" * 80)
print("FFO Smoke Test v2")
print("=" * 80)

passed = 0
failed = 0

for formula, name in formulas:
    r = evaluate(formula, start="2025-01-01", end="2026-01-01", universe="csi500")
    label = f"[{name}]"
    line = format_report_short(r)
    print(f"{label:<20s} {line}")

    if r.error:
        failed += 1
    elif abs(r.ic_mean) > 0:
        passed += 1
    else:
        failed += 1

print("=" * 80)
print(f"Results: {passed} passed, {failed} failed")

# Print the best factor's full report
best = None
for formula, name in formulas:
    r = evaluate(formula, start="2025-01-01", end="2026-01-01", universe="csi500")
    if r.error:
        continue
    if best is None or abs(r.ic_mean) > abs(best.ic_mean):
        best = r

if best:
    print("")
    print("Best factor full report:")
    print(format_report(best))

print("")
print("All tests complete.")