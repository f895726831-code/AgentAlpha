#!/usr/bin/env python3
"""测试 evaluator 中 $returns 字段加载的实际行为"""
import sys, numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffo.data_loader import load_qlib_data, get_returns_matrix
from ffo.computer import FactorComputer
from ffo.parser import parse

# 用 evaluator 的方式加载: 显式传 $returns
print("=== Evaluator-style loading (includes $returns in fields) ===")
all_fields_dollar = ['$returns', '$close', '$open', '$high', '$low', '$volume', '$vwap']
data = load_qlib_data('/mnt/d/qlib/qlib_data/cn_data', 'csi500', '2014-01-01', '2024-12-31', fields=all_fields_dollar)
print(f'Columns: {list(data.columns)}')

# 检查 returns 列是否有数据
returns_col = data['returns']
n_returns = returns_col.notna().sum()
print(f'returns column: {n_returns}/{len(returns_col)} non-null')
if n_returns > 0:
    print(f'  sample: {returns_col.dropna().head(5)}')

# 对比: get_returns_matrix 算出来的 returns
returns_from_close = get_returns_matrix(data)
returns_from_close = returns_from_close[returns_from_close.index >= pd.Timestamp('2015-01-01')]
print(f'get_returns_matrix: {returns_from_close.shape}, non-null: {returns_from_close.notna().sum().sum()}')

# 现在用 FactorComputer 算 SignedPower($returns, 2)
print("\n=== Factor evaluation ===")
computer = FactorComputer(data)

for formula in ['SignedPower($returns, 2)', 'Scale($returns)', '($close - $vwap) / $vwap']:
    ast = parse(formula)
    fv = computer.evaluate(ast)
    fv_eval = fv[fv.index >= pd.Timestamp('2015-01-01')]
    n_nan = fv_eval.isna().sum().sum()
    n_total = fv_eval.size
    print(f'{formula}: factor values non-null={n_total - n_nan}/{n_total}')

# 手动计算 IC
    rets = returns_from_close.shift(-1)
    common = fv_eval.index.intersection(rets.index)
    ics = []
    for d in common:
        a = fv_eval.loc[d].dropna()
        b = rets.loc[d].dropna()
        c = a.index.intersection(b.index)
        if len(c) >= 30:
            ic = a.loc[c].corr(b.loc[c])
            if not np.isnan(ic):
                ics.append(ic)
    ic_mean = np.mean(ics) if ics else 0
    print(f'  IC (manual) = {ic_mean:+.4f} (n_days={len(ics)})')

# 对比: 用 evaluator
print("\n=== Via FactorEvaluator ===")
from ffo.evaluator import FactorEvaluator
evaluator = FactorEvaluator()
for formula in ['SignedPower($returns, 2)', 'Scale($returns)', '($close - $vwap) / $vwap']:
    r = evaluator.evaluate(formula, start='2015-01-01', end='2024-12-31', universe='csi500')
    print(f'{formula}: ic_mean={r.ic_mean:+.4f}, rank_ic={r.rank_ic_mean:+.4f}, error={r.error}')