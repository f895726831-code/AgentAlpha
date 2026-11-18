"""
Incremental Phase 1 Factor Update — extend .day.bin files for new calendar dates.

After daily data pipeline adds new trading days to Qlib bin, P1 .day.bin files
are statically frozen at their injection-time calendar length. This script:
  1. Appends new factor values for calendar dates added since injection
  2. Patches any NaN values in the most recent existing dates (e.g. vwap/returns lag)

Key design: each factor formula is evaluated ONCE (not per-stock), producing a
full date×instrument DataFrame. Each stock's bin file is then updated from the
pre-computed result — 60 evaluations instead of 60×500.

Usage:
    cd D:/AgentAlpha/my-bot/scripts/wsl2
    python scripts/increment_p1_factors.py                 # update last 5 days
    python scripts/increment_p1_factors.py --lookback 30   # longer recent window
    python scripts/increment_p1_factors.py --dry-run       # check only
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
WSL2_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(WSL2_DIR))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ffo.data_loader import load_qlib_data
from ffo.parser import parse as parse_formula
from ffo.computer import FactorComputer

QLIB_BIN = Path("D:/qlib/qlib_data/cn_data")
FEATURES_DIR = QLIB_BIN / "features"
CALENDAR_FILE = QLIB_BIN / "calendars" / "day.txt"
MANIFEST_PATH = WSL2_DIR / "results" / "phase1_injection_manifest.json"


def load_calendar():
    with open(CALENDAR_FILE) as f:
        return pd.DatetimeIndex([line.strip() for line in f if line.strip()])


def load_formula_map():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return {k: v["formula"] for k, v in manifest["factors"].items()}


def get_stock_list():
    inst_path = QLIB_BIN / "instruments" / "csi500.txt"
    with open(inst_path, "r", encoding="utf-8") as f:
        return [line.strip().split("\t")[0] for line in f if line.strip()]


def scan_factor_bins(factor_names, stocks, cal_len):
    """
    Scan bin files for each factor×stock.
    Returns (missing_dates, recent_nan) counts per factor.
    """
    missing = {}   # factor -> n stocks missing dates
    rec_nan = {}   # factor -> n cells with NaN in recent window
    n_skipped = {} # factor -> n stocks with no bin file

    for fname in factor_names:
        n_miss, n_nan, n_skip = 0, 0, 0
        for stock in stocks:
            fp = FEATURES_DIR / stock / f"{fname}.day.bin"
            if not fp.exists():
                n_skip += 1
                continue
            data = np.fromfile(fp, dtype="<f")
            if len(data) <= 1:
                n_skip += 1
                continue
            sidx = int(data[0])
            vals = data[1:]
            n_exist = len(vals)
            n_need = cal_len - sidx
            if n_exist < n_need:
                n_miss += 1
            # Check NaN in last 10 existing values
            window = min(10, n_exist)
            n_nan += int(np.isnan(vals[-window:]).sum())
        missing[fname] = n_miss
        rec_nan[fname] = n_nan
        n_skipped[fname] = n_skip

    return missing, rec_nan, n_skipped


def update_stock_bin(stock_dir, fname, sidx, existing_vals, new_series, cal_len):
    """
    Patch or extend a single stock's bin file.

    - existing_vals: current data[1:] from the bin file
    - new_series: computed factor values for this stock (full calendar-length pd.Series)

    Returns number of values updated (NaN→value or appended).
    """
    n_exist = len(existing_vals)
    n_need = cal_len - sidx
    updated = 0

    # 1. Patch NaN in existing values (last 10 days only, to be safe)
    recent_start = max(0, n_exist - 10)
    for i in range(recent_start, n_exist):
        if np.isnan(existing_vals[i]):
            abs_idx = sidx + i
            new_val = new_series.iloc[abs_idx] if abs_idx < len(new_series) else np.nan
            if not np.isnan(new_val):
                existing_vals[i] = new_val
                updated += 1

    # 2. Append for missing dates (always write, even if values are NaN)
    needs_append = n_exist < n_need
    if needs_append:
        n_append = n_need - n_exist
        new_vals = np.full(n_append, np.nan, dtype="<f")
        for j in range(n_append):
            abs_idx = sidx + n_exist + j
            if abs_idx < len(new_series):
                v = new_series.iloc[abs_idx]
                if not np.isnan(v):
                    new_vals[j] = v
        existing_vals = np.concatenate([existing_vals, new_vals])
        updated += (~np.isnan(new_vals)).sum()

    if updated > 0 or needs_append:
        new_data = np.concatenate([[np.float32(sidx)], existing_vals.astype("<f")])
        new_data.tofile(stock_dir / f"{fname}.day.bin")

    return int(updated)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extend P1 factor bin files for new calendar dates")
    parser.add_argument("--lookback", type=int, default=5,
                        help="Recent trading days to check/repair NaN (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check status without updating")
    parser.add_argument("--max-factors", type=int, default=0,
                        help="Limit factors (0=all, for testing)")
    args = parser.parse_args()

    print("=" * 60)
    print("Incremental P1 Factor Update")
    print("=" * 60)

    cal_dates = load_calendar()
    cal_len = len(cal_dates)
    print(f"Calendar: {cal_len} days ({cal_dates[0].date()} -> {cal_dates[-1].date()})")

    stocks = get_stock_list()
    print(f"CSI500 stocks: {len(stocks)}")

    formula_map = load_formula_map()
    all_factors = sorted(formula_map.keys())
    print(f"Factor formulas: {len(all_factors)}")
    if args.max_factors > 0:
        all_factors = all_factors[:args.max_factors]

    # ── Scan ──
    print("\n[1/3] Scanning bin files...")
    missing, rec_nan, skipped = scan_factor_bins(all_factors, stocks, cal_len)

    n_miss_any = sum(1 for v in missing.values() if v > 0)
    n_nan_any = sum(1 for v in rec_nan.values() if v > 0)
    total_miss = sum(missing.values())
    total_nan = sum(rec_nan.values())
    print(f"  Factors missing dates: {n_miss_any}/{len(all_factors)} ({total_miss} stock×factor)")
    print(f"  Factors with recent NaN: {n_nan_any}/{len(all_factors)} ({total_nan} cells)")

    # Identify factors needing work
    factors_to_update = sorted(set(
        [f for f in all_factors if missing.get(f, 0) > 0 or rec_nan.get(f, 0) > 0]
    ))
    print(f"  Factors needing update: {len(factors_to_update)}")

    if not factors_to_update:
        print("\nAll P1 factors up to date — nothing to patch.")
        return 0

    if args.dry_run:
        print("\n[Dry-run] Factors needing update:")
        for f in factors_to_update:
            short = f[:65]
            print(f"  missing={missing.get(f,0):4d}  recent_nan={rec_nan.get(f,0):5d}  {short}")
        print("\nRe-run without --dry-run to apply updates.")
        return 0

    # ── Load market data ──
    data_start = cal_dates[max(0, cal_len - args.lookback - 120)]
    data_end = cal_dates[-1]
    print(f"\n[2/3] Loading market data: {data_start.date()} -> {data_end.date()}...")
    df = load_qlib_data(
        universe="csi500",
        start=data_start.strftime("%Y-%m-%d"),
        fields=["$close", "$open", "$high", "$low", "$volume", "$vwap"],
    )
    print(f"  Loaded: {df.shape}, columns={sorted(df.columns)}")
    computer = FactorComputer(df)

    # ── Update each factor (evaluate ONCE, then update each stock) ──
    print(f"\n[3/3] Updating {len(factors_to_update)} factors...")
    total_updated_cells = 0
    total_errors = 0

    for fi, fname in enumerate(factors_to_update):
        formula = formula_map[fname]
        short = fname[:60]

        # Evaluate factor ONCE for all stocks
        try:
            ast = parse_formula(formula)
            result_df = computer.evaluate(ast)  # date × instrument DataFrame
            result_df = result_df.reindex(index=cal_dates)
        except Exception as e:
            print(f"  [{fi+1}/{len(factors_to_update)}] {short}: EVAL ERROR: {e}")
            total_errors += 1
            continue

        factor_updated = 0
        for stock in stocks:
            stock_dir = FEATURES_DIR / stock
            fp = stock_dir / f"{fname}.day.bin"
            if not fp.exists():
                continue

            data = np.fromfile(fp, dtype="<f")
            if len(data) <= 1:
                continue

            sidx = int(data[0])
            existing_vals = data[1:].copy()
            n_exist = len(existing_vals)
            n_need = cal_len - sidx

            # Skip if up-to-date and no recent NaN
            if n_exist >= n_need:
                recent_window = min(10, n_exist)
                if not np.isnan(existing_vals[-recent_window:]).any():
                    continue

            if stock not in result_df.columns:
                continue

            new_series = result_df[stock]
            n_upd = update_stock_bin(stock_dir, fname, sidx, existing_vals, new_series, cal_len)
            factor_updated += n_upd

        total_updated_cells += factor_updated
        if factor_updated > 0:
            pct = factor_updated / max(1, len(stocks))
            print(f"  [{fi+1:2d}/{len(factors_to_update)}] {short}: {factor_updated} cells ({pct:.0f}/stock)")

    print(f"\nDone: {total_updated_cells} cells updated across {len(factors_to_update)} factors")
    if total_errors > 0:
        print(f"  Errors: {total_errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())