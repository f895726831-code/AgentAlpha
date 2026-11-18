"""
Phase 1 Factor Bridge - Load injected factors directly from .day.bin files

Qlib doesn't auto-discover newly added .day.bin files. This bridge reads them
directly and returns a DataFrame compatible with FactorComputer / ML pipeline.

Usage:
    from phase1_bridge import load_p1_factors, list_p1_factors
    p1_df = load_p1_factors(['p1__close_Delay_close_5_....day'])
    combined = qlib_df.join(p1_df)
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

# Paths
QLIB_BIN = Path('D:/qlib/qlib_data/cn_data')
FEATURES_DIR = QLIB_BIN / 'features'
CALENDAR_FILE = QLIB_BIN / 'calendars' / 'day.txt'
INSTRUMENTS_FILE = QLIB_BIN / 'instruments' / 'csi500.txt'

# Caches
_calendar = None
_instruments = None

def _load_calendar():
    global _calendar
    if _calendar is None:
        with open(CALENDAR_FILE) as f:
            _calendar = pd.DatetimeIndex([line.strip() for line in f if line.strip()])
    return _calendar

def _load_instruments():
    global _instruments
    if _instruments is None:
        with open(INSTRUMENTS_FILE, 'r', encoding='utf-8') as f:
            _instruments = [line.strip().split('	')[0] for line in f if line.strip()]
    return _instruments

def list_p1_factors():
    """List all available Phase 1 factor names."""
    stocks = _load_instruments()
    for stock in stocks:
        stock_dir = FEATURES_DIR / stock
        if stock_dir.exists():
            p1_files = sorted(stock_dir.glob('p1_*.day.bin'))
            if p1_files:
                return [f.stem for f in p1_files]
    return []

def load_p1_factors(factor_names=None, stocks=None, start=None, end=None):
    """
    Load Phase 1 factor values.

    Args:
        factor_names: list of factor names (stems without .bin). None = all.
        stocks: list of stock symbols. None = all CSI500.
        start: start date string. None = first calendar date.
        end: end date string. None = last calendar date.

    Returns:
        pd.DataFrame with MultiIndex (instrument, datetime), columns = factor_names
    """
    dates = _load_calendar()
    n_days = len(dates)

    if stocks is None:
        stocks = _load_instruments()

    if factor_names is None:
        factor_names = list_p1_factors()

    # Filter date range
    start_idx = 0
    end_idx = n_days
    if start:
        s = pd.Timestamp(start)
        mask = dates >= s
        if mask.any():
            start_idx = int(mask.argmax())
    if end:
        e = pd.Timestamp(end)
        mask = dates <= e
        if mask.any():
            end_idx = int(np.where(mask)[0][-1]) + 1

    date_slice = dates[start_idx:end_idx]
    n_slice = end_idx - start_idx

    # Build inventory: which stocks have p1_ files
    available_stocks = []
    for stock in stocks:
        stock_dir = FEATURES_DIR / stock
        if stock_dir.exists() and list(stock_dir.glob('p1_*.day.bin')):
            available_stocks.append(stock)

    if not available_stocks:
        raise ValueError('No stocks with p1_ factor files found')

    # Pre-allocate arrays
    n_stocks = len(available_stocks)
    arrays = {}
    for name in factor_names:
        arrays[name] = np.full((n_stocks, n_slice), np.nan, dtype=np.float32)

    # Read each factor for each stock
    print(f'Loading {len(factor_names)} factors x {n_stocks} stocks...')
    for i, stock in enumerate(available_stocks):
        stock_dir = FEATURES_DIR / stock
        for name in factor_names:
            fp = stock_dir / f'{name}.bin'
            if not fp.exists():
                continue
            data = np.fromfile(fp, dtype='<f')
            if len(data) <= 1:
                continue
            f_start = int(data[0])
            values = data[1:]

            # Map to our date slice
            # f_start is index in full calendar
            rel_start = f_start - start_idx
            if rel_start < 0:
                trim = -rel_start
                values = values[trim:]
                rel_start = 0
            n_copy = max(0, min(len(values), n_slice - rel_start))
            if n_copy > 0:
                arrays[name][i, rel_start:rel_start + n_copy] = values[:n_copy]

    # Build MultiIndex DataFrame
    if n_slice < 100:
        print(f'  Dates: {date_slice[0].date()} -> {date_slice[-1].date()}')
    else:
        print(f'  Dates: {date_slice[0].date()} -> {date_slice[-1].date()} ({n_slice} days)')

    records = []
    for i, stock in enumerate(available_stocks):
        for j, dt in enumerate(date_slice):
            row = {'instrument': stock, 'datetime': dt}
            for name in factor_names:
                row[name] = arrays[name][i, j]
            records.append(row)

    df = pd.DataFrame(records)
    df = df.set_index(['instrument', 'datetime']).sort_index()

    # Convert to float32
    for col in df.columns:
        df[col] = df[col].astype(np.float32)

    print(f'  Result: {df.shape[0]} rows x {df.shape[1]} cols')
    valid_pct = 100 * df.notna().sum().sum() / max(df.size, 1)
    print(f'  Valid: {valid_pct:.1f}%')

    return df

if __name__ == '__main__':
    # Quick test
    names = list_p1_factors()
    print(f'Phase 1 factors available: {len(names)}')
    print(f'First 3: {names[:3]}')

    # Load 3 factors for 5 stocks
    df = load_p1_factors(factor_names=names[:3], stocks=None, start='2015-01-05', end='2015-01-20')
    print(df.head(10))
    print('OK')
