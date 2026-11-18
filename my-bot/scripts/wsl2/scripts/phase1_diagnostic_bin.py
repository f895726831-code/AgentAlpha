"""
phase1_diagnostic_bin.py — 从 Qlib bin 读取生存因子并计算 IC
"""

import numpy as np
import pandas as pd
from pathlib import Path

QLIB_BIN = Path("/mnt/d/qlib/qlib_data/cn_data")


def _load_calendar():
    with open(QLIB_BIN / "calendars" / "day.txt") as f:
        dates = [line.strip() for line in f if line.strip()]
    return dates


def _load_instruments(universe="csi500"):
    inst_file = QLIB_BIN / "instruments" / f"{universe}.txt"
    stocks = []
    with open(inst_file, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if parts and parts[0]:
                stocks.append(parts[0])
    return stocks


def _read_bin(symbol, field, n_days):
    fp = QLIB_BIN / "features" / symbol / f"{field}.day.bin"
    if not fp.exists():
        return None
    data = np.fromfile(fp, dtype="<f")
    if len(data) <= 1:
        return None
    start_idx = int(data[0])
    values = data[1:]
    result = np.full(n_days, np.nan, dtype=np.float32)
    end_idx = min(start_idx + len(values), n_days)
    copy_len = end_idx - start_idx
    if copy_len > 0:
        result[start_idx:end_idx] = values[:copy_len]
    return result


def compute_ic_matrix(factor_matrix, forward_returns_matrix):
    """Compute daily cross-sectional IC for a factor matrix."""
    ic_daily = []
    for d in range(factor_matrix.shape[1]):
        fv = factor_matrix[:, d]
        fr = forward_returns_matrix[:, d]
        mask = ~np.isnan(fv) & ~np.isnan(fr)
        if mask.sum() < 30:
            continue
        ic = np.corrcoef(fv[mask], fr[mask])[0, 1]
        if not np.isnan(ic):
            ic_daily.append(ic)

    if not ic_daily:
        return {"ic_mean": 0.0, "ic_ir": 0.0, "n_days": 0}

    arr = np.array(ic_daily)
    return {
        "ic_mean": float(np.mean(arr)),
        "ic_ir": float(np.mean(arr) / np.std(arr)) if np.std(arr) > 0 else 0.0,
        "n_days": len(arr),
    }


def evaluate_survival_factors(eval_start, eval_end, universe="csi500"):
    """
    Evaluate 5 survival factors from Qlib bin format.

    Returns: dict {factor_name: {ic_mean, ic_ir, n_days}}
    """
    dates = _load_calendar()
    n_days = len(dates)
    date_arr = pd.to_datetime(dates)

    start_d = pd.Timestamp(eval_start)
    end_d = pd.Timestamp(eval_end)
    period_mask = (date_arr >= start_d) & (date_arr <= end_d)
    period_indices = np.where(period_mask)[0]
    first_idx = period_indices[0]
    last_idx = period_indices[-1]
    period_len = last_idx - first_idx + 1

    stocks = _load_instruments(universe)

    # Load close for all stocks, compute returns
    close_matrix = np.full((len(stocks), n_days), np.nan, dtype=np.float32)
    for i, stock in enumerate(stocks):
        arr = _read_bin(stock, "close", n_days)
        if arr is not None:
            close_matrix[i, :] = arr

    ret_matrix = np.full_like(close_matrix, np.nan)
    ret_matrix[:, 1:] = (close_matrix[:, 1:] - close_matrix[:, :-1]) / np.where(
        close_matrix[:, :-1] != 0, close_matrix[:, :-1], np.nan
    )
    forward_ret = np.full_like(ret_matrix, np.nan)
    forward_ret[:, :-1] = ret_matrix[:, 1:]

    # Filter to period
    fr_period = forward_ret[:, first_idx:last_idx+1]

    factors = [
        "signed_power_ret_mean5_p3",
        "abs_returns",
        "returns_raw",
        "if_close_ret_20",
        "min_ret_open",
    ]

    results = {}
    for fname in factors:
        fm = np.full((len(stocks), period_len), np.nan, dtype=np.float32)
        for i, stock in enumerate(stocks):
            arr = _read_bin(stock, fname, n_days)
            if arr is not None:
                fm[i, :] = arr[first_idx:last_idx+1]
        results[fname] = compute_ic_matrix(fm, fr_period)

    return results