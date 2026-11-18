#!/usr/bin/env python3
"""Quick test of amihud_illiq factor to diagnose hang"""
import sys, time
sys.path.insert(0, ".")
from ffo.evaluator import FactorEvaluator

e = FactorEvaluator()
formula = "-1 * Ts_Mean(Abs($returns) / ($volume * $close), 20)"
print(f"Testing: {formula}")
t0 = time.time()
try:
    result = e.evaluate(formula, start="2015-01-05", end="2024-12-31", universe="csi500")
    elapsed = time.time() - t0
    print(f"IC={result.ic_mean:+.4f} (|IC|={abs(result.ic_mean):.4f})  elapsed={elapsed:.1f}s")
    print(f"Error: {result.error}")
except Exception as ex:
    elapsed = time.time() - t0
    print(f"Exception after {elapsed:.1f}s: {ex}")
    import traceback
    traceback.print_exc()