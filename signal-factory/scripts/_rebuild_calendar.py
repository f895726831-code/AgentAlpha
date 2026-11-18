#!/usr/bin/env python
"""Rebuild calendar from actual CSV data"""
import pandas as pd
from pathlib import Path

csv_dir = Path('D:/qlib/qlib_data/source/cn_1d')
cal_path = Path('D:/qlib/qlib_data/cn_data/calendars/day.txt')

all_dates = set()
count = 0
for f in csv_dir.glob('*.csv'):
    if f.stem.startswith('kline'):
        continue
    if f.stem.isdigit() and len(f.stem) == 6:
        continue
    try:
        df = pd.read_csv(f, usecols=['date'])
        all_dates.update(df['date'].tolist())
        count += 1
    except Exception:
        pass

sorted_dates = sorted(all_dates)
print(f'Scanned {count} CSVs, found {len(sorted_dates)} unique dates')
print(f'Date range: {sorted_dates[0]} ~ {sorted_dates[-1]}')
print(f'Last 5 dates: {sorted_dates[-5:]}')

with open(cal_path, 'w', encoding='utf-8') as f:
    for d in sorted_dates:
        f.write(f'{d}\n')
print(f'Calendar rebuilt: {cal_path}')