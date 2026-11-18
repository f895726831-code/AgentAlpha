"""
One-shot backfill: patch Qlib bin files for missing data in 2026-11-02 ~ 2026-11-05.

The daily pipeline (akshare→baostock migration period) extended the calendar but
failed to write actual market data for most CSI500 stocks. This script fetches
data from baostock and patches .day.bin files wherever close is NaN.

Usage:
    python backfill_recent.py              # patch all CSI500 stocks
    python backfill_recent.py --dry-run    # check only
"""

import sys
from pathlib import Path

import baostock as bs
import numpy as np

FEATURES_DIR = Path("D:/qlib/qlib_data/cn_data/features")
CALENDAR_FILE = Path("D:/qlib/qlib_data/cn_data/calendars/day.txt")
CSI500_FILE = Path("D:/qlib/qlib_data/cn_data/instruments/csi500.txt")

# Baostock → Qlib field mapping + key order
FIELD_MAP = [
    ("date", None),     # index 0, not written to bin
    ("open", "open"),
    ("close", "close"),
    ("high", "high"),
    ("low", "low"),
    ("volume", "volume"),
    ("amount", "amount"),
]


def to_baostock(symbol: str) -> str:
    if symbol.startswith("sh"):
        return "sh." + symbol[2:]
    elif symbol.startswith("sz"):
        return "sz." + symbol[2:]
    return symbol


def main():
    dry_run = "--dry-run" in sys.argv

    cal = [line.strip() for line in open(CALENDAR_FILE) if line.strip()]
    print(f"Calendar: {len(cal)} days ({cal[0][:10]} -> {cal[-1][:10]})")

    # Date range to backfill
    start_date = "2026-11-02"
    end_date = "2026-11-05"
    print(f"Target range: {start_date} -> {end_date}")

    with open(CSI500_FILE) as f:
        stocks = [line.strip().split("\t")[0] for line in f if line.strip()]
    print(f"Stocks: {len(stocks)}")

    bs.login()

    total_patched = 0
    total_errors = 0
    total_no_data = 0

    for i, stock in enumerate(stocks):
        bs_code = to_baostock(stock)

        # Fetch all dates at once from baostock
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,close,high,low,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="1",
            )
        except Exception as e:
            total_errors += 1
            if total_errors <= 3:
                print(f"  [{i+1}/{len(stocks)}] {stock}: query error: {e}")
            continue

        rows = []
        while (rs.error_code == "0") & rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            total_no_data += 1
            continue

        # Check which dates in the calendar are NaN and need patching
        stock_dir = FEATURES_DIR / stock
        close_fp = stock_dir / "close.day.bin"
        if not close_fp.exists():
            continue

        close_data = np.fromfile(close_fp, dtype="<f")
        close_sidx = int(close_data[0])
        close_vals = close_data[1:]

        stock_patched = 0
        for row in rows:
            date = row[0]
            if date not in cal:
                continue

            cal_idx = cal.index(date)
            arr_idx = cal_idx - close_sidx
            if arr_idx < 0 or arr_idx >= len(close_vals):
                continue

            # Check if close is already valid for this date
            if close_vals[arr_idx] == close_vals[arr_idx]:
                continue  # already valid

            # Parse values
            try:
                values = {
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                    "amount": float(row[6]),
                }
            except (ValueError, TypeError, IndexError):
                continue

            if values["volume"] <= 0:
                continue

            # Compute vwap
            values["vwap"] = round(values["amount"] / values["volume"], 4)

            # Patch each field's bin file
            for field, val in values.items():
                fp = stock_dir / f"{field}.day.bin"
                if not fp.exists():
                    continue
                field_data = np.fromfile(fp, dtype="<f")
                field_sidx = int(field_data[0])
                field_arr_idx = cal_idx - field_sidx
                if 0 <= field_arr_idx < len(field_data) - 1:
                    field_data[1 + field_arr_idx] = np.float32(val)
                    if not dry_run:
                        field_data.tofile(fp)

            stock_patched += 1

        if stock_patched > 0:
            total_patched += 1

        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(stocks)}] patched_stocks={total_patched} "
                  f"errors={total_errors} no_data={total_no_data}")

    bs.logout()

    print(f"\nDone: stocks_patched={total_patched} errors={total_errors} no_data={total_no_data}")
    if dry_run:
        print("[DRY-RUN] No changes written.")


if __name__ == "__main__":
    main()