#!/usr/bin/env python3
"""
验证基本面数据注入 + FFO 评估

用法:
    python scripts/verify_fundamental_injection.py
"""

import sys; sys.path.insert(0, ".")

from ffo.evaluator import evaluate_formula
from ffo.parser import parse, _extract_field_refs

print("=" * 60)
print("Fundamental Data Injection — Verification")
print("=" * 60)

# 1. 覆盖率检查
from pathlib import Path
d = Path("/mnt/d/qlib/qlib_data/cn_data/features")

has_both = 0
has_close = 0
has_fund = 0
for sub in d.iterdir():
    if not sub.is_dir():
        continue
    c = (sub / "close.day.bin").exists()
    r = (sub / "roe.day.bin").exists()
    if c and r:
        has_both += 1
    if c:
        has_close += 1
    if r:
        has_fund += 1

print(f"\nCoverage: {has_close} with OHLCV, {has_fund} with fundamental, {has_both} with both")

# 2. 测试基本面因子公式
test_formulas = [
    "$roe",
    "$eps / $bps",
    "Log($roe)",
    "($roe + $gross_margin) / 2",
    "Rank($eps)",
    "(Sign($roe) * $eps)",
]

print("\n--- Formula IC (csi500, 2024-2026) ---")
for formula in test_formulas:
    try:
        result = evaluate_formula(formula, start="2024-01-01", universe="csi500")
        if result.error:
            print(f"  {formula:40s} ERROR: {result.error}")
        else:
            print(f"  {formula:40s} IC={result.ic_mean:+.4f}  RankIC={result.rank_ic_mean:+.4f}  IR={result.ic_ir:+.3f}  n={result.n_days}d x {result.n_stocks}s")
    except Exception as e:
        print(f"  {formula:40s} FAIL: {e}")

# 3. 对比量价因子（确认未退化）
print("\n--- Baseline price factors ---")
for formula in ["$vwap", "$returns", "$close"]:
    try:
        result = evaluate_formula(formula, start="2024-01-01", universe="csi500")
        if result.error:
            print(f"  {formula:40s} ERROR: {result.error}")
        else:
            print(f"  {formula:40s} IC={result.ic_mean:+.4f}  RankIC={result.rank_ic_mean:+.4f}  IR={result.ic_ir:.3f}")
    except Exception as e:
        print(f"  {formula:40s} FAIL: {e}")

print("\nDone.")