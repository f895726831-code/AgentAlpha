#!/usr/bin/env python3
"""测试 load_qlib_data 对 $returns 字段的处理"""
import sys, numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffo.data_loader import load_qlib_data

# 测试1: 正常字段 (不含 $returns)
print("=== Test 1: Normal fields (no $returns) ===")
fields1 = ['$close', '$open', '$high', '$low', '$volume', '$vwap']
data1 = load_qlib_data('/mnt/d/qlib/qlib_data/cn_data', 'csi500', '2014-01-01', '2015-01-05', fields=fields1)
print(f'Columns: {list(data1.columns)}')
print(f'Shape: {data1.shape}')
for col in data1.columns:
    non_null = data1[col].notna().sum()
    print(f'  {col}: {non_null}/{len(data1[col])} non-null')
    if 'returns' in col:
        print(f'    sample: {data1[col].dropna().head(3).tolist()}')

# 测试2: 含 $returns
print("\n=== Test 2: With $returns ===")
fields2 = ['$returns', '$close', '$open', '$high', '$low', '$volume', '$vwap']
data2 = load_qlib_data('/mnt/d/qlib/qlib_data/cn_data', 'csi500', '2014-01-01', '2015-01-05', fields=fields2)
print(f'Columns: {list(data2.columns)}')
print(f'Shape: {data2.shape}')
for col in data2.columns:
    non_null = data2[col].notna().sum()
    print(f'  {col}: {non_null}/{len(data2[col])} non-null')
    if 'returns' in col:
        print(f'    sample: {data2[col].dropna().head(3).tolist()}')

# 对比 returns 列
print(f"\nReturns columns match: {(data1['returns'].dropna() == data2['returns'].dropna()).all()}")
print(f"auto-computed returns (test1): {(data1['returns'].notna().sum())}")
print(f"auto-computed returns (test2): {(data2['returns'].notna().sum())}")