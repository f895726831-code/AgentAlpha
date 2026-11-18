"""
One-shot backfill: patch Qlib bin files for 2026-11-03 (missing trading day).

The daily pipeline ran on 2026-11-03 but data wasn't written to Qlib bin files.
This script fetches data from baostock and patches the .day.bin files directly.

Usage:
    python backfill_20261103.py          # patch all CSI500 stocks
    python backfill_20261103.py --dry-run  # check only
"""

import sys
from pathlib import Path

import baostock as bs
import numpy as np

FEATURES_DIR = Path("D:/qlib/qlib_data/cn_data/features")
CALENDAR_FILE = Path("D:/qlib/qlib_data/cn_data/calendars/day.txt")
CSI500_FILE = Path("D:/qlib/qlib_data/cn_data/instruments/csi500.txt")
TARGET_DATE = "2026-11-03"
FIELDS = ["open", "close", "high", "low", "volume", "amount"]


def to_baostock(symbol: str) -> str:
    if symbol.startswith("sh"):
        return "sh." + symbol[2:]
    elif symbol.startswith("sz"):
        return "sz." + symbol[2:]
    return symbol


def main():
    dry_run = "--dry-run" in sys.argv

    cal = [line.strip() for line in open(CALENDAR_FILE) if line.strip()]
    target_idx = cal.index(TARGET_DATE)
    print(f"Target: calendar[{target_idx}] = {TARGET_DATE}")
    print(f"Calendar: {len(cal)} days")

    with open(CSI500_FILE) as f:
        stocks = [line.strip().split("\t")[0] for line in f if line.strip()]
    print(f"Stocks: {len(stocks)}")

    bs.login()

    patched = 0
    errors = 0
    no_data = 0

    for i, stock in enumerate(stocks):
        bs_code = to_baostock(stock)

        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,close,high,low,volume,amount",
                start_date="2026-11-02",
                end_date="2026-11-04",
                frequency="d",
                adjustflag="1",
            )
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  [{i+1}/{len(stocks)}] {stock}: query error: {e}")
            continue

        row_data = None
        while (rs.error_code == "0") & rs.next():
            row = rs.get_row_data()
            if row[0] == TARGET_DATE:
                row_data = row
                break

        if row_data is None:
            no_data += 1
            continue

        date, o, c, h, l, v, a = row_data
        try:
            open_v = float(o)
            close_v = float(c)
            high_v = float(h)
            low_v = float(l)
            volume_v = float(v)
            amount_v = float(a)
        except (ValueError, TypeError):
            no_data += 1
            continue

        if volume_v <= 0:
            no_data += 1
            continue

        vwap_v = round(amount_v / volume_v, 4)

        field_values = {
            "open": open_v,
            "close": close_v,
            "high": high_v,
            "low": low_v,
            "volume": volume_v,
            "amount": amount_v,
            "vwap": vwap_v,
        }

        stock_ok = True
        for field, value in field_values.items():
            fp = FEATURES_DIR / stock / f"{field}.day.bin"
            if not fp.exists():
                stock_ok = False
                break

            data = np.fromfile(fp, dtype="<f")
            sidx = int(data[0])
            arr_idx = target_idx - sidx

            if arr_idx < 0 or arr_idx >= len(data) - 1:
                stock_ok = False
                break

            current = data[1 + arr_idx]
            if current == current:
                # Already has valid data, skip
                continue

            data[1 + arr_idx] = np.float32(value)
            if not dry_run:
                data.tofile(fp)

        if stock_ok:
            patched += 1

        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(stocks)}] patched={patched} errors={errors} no_data={no_data}")

    bs.logout()

    print(f"\nDone: patched={patched} errors={errors} no_data={no_data}")
    if dry_run:
        print("[DRY-RUN] No changes written.")


if __name__ == "__main__":
    main()