#!/usr/bin/env python3
"""对比 reward.py 和 evaluator.py 的 IC 计算差异"""
import sys, numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffo.data_loader import load_qlib_data, get_returns_matrix
from ffo.computer import FactorComputer
from ffo.parser import parse

# Method 1: reward.py style (load_qlib_data)
print("Loading data via load_qlib_data...")
data = load_qlib_data('/mnt/d/qlib/qlib_data/cn_data', 'csi500', '2014-01-01', '2024-12-31')
returns_m = get_returns_matrix(data)
returns_m = returns_m[returns_m.index >= pd.Timestamp('2015-01-01')]
computer = FactorComputer(data)

# Method 2: evaluator style (D.features)
print("Loading data via D.features...")
import qlib
qlib.init(provider_uri='/mnt/d/qlib/qlib_data/cn_data', region='cn')
from qlib.data import D
data2 = D.features(D.instruments('csi500'),
    ['$close','$open','$high','$low','$volume','$vwap'],
    '2014-01-01', '2024-12-31')
data2 = data2.rename(columns=lambda c: c.lstrip('$').lower())
returns2_m = data2['close'].unstack().T.pct_change(fill_method=None)
returns2_m = returns2_m[returns2_m.index >= pd.Timestamp('2015-01-01')]

factors = [
    'SignedPower($returns, 2)',
    '($close - $vwap) / $vwap',
    'Rank($vwap * $volume)',
    'Scale($returns)',
    'Ts_Mean($returns, 5)',
]

for formula in factors:
    ast = parse(formula)
    fv = computer.evaluate(ast)
    fv = fv[fv.index >= pd.Timestamp('2015-01-01')]

    def calc_ic(fv, rets):
        fr = rets.shift(-1)
        cd = fv.index.intersection(fr.index)
        ics = []
        for d in cd:
            a = fv.loc[d].dropna()
            b = fr.loc[d].dropna()
            c = a.index.intersection(b.index)
            if len(c) >= 30:
                ic = a.loc[c].corr(b.loc[c])
                if not np.isnan(ic):
                    ics.append(ic)
        return np.mean(ics) if ics else 0, len(ics)

    ic1, n1 = calc_ic(fv, returns_m)
    ic2, n2 = calc_ic(fv, returns2_m)
    print(f'{formula[:45]:<45s} reward_IC={ic1:+.4f} (n={n1})  evaluator_IC={ic2:+.4f} (n={n2})  diff={abs(ic1-ic2):.4f}')

# Also test with evaluator directly
print("\n--- Via FactorEvaluator ---")
from ffo.evaluator import FactorEvaluator
evaluator = FactorEvaluator()
for formula in factors:
    r = evaluator.evaluate(formula, start='2015-01-01', end='2024-12-31', universe='csi500')
    print(f'{formula[:45]:<45s} ic_mean={r.ic_mean:+.4f}  rank_ic={r.rank_ic_mean:+.4f}')

print(f'\nData1 (load_qlib): {len(returns_m)} dates, {returns_m.shape[1]} stocks')
print(f'Data2 (D.features): {len(returns2_m)} dates, {returns2_m.shape[1]} stocks')
print(f'Common dates: {len(returns_m.index.intersection(returns2_m.index))}')
print(f'Common stocks: {len(set(returns_m.columns) & set(returns2_m.columns))}')