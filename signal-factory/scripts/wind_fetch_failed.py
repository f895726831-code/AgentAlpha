"""
Batch Wind MCP data fetch for failed stocks. [已弃用]

2026-09-14: 自动化管线已迁移至 akshare。本脚本仅保留用于手动 Wind MCP 数据拉取。
Fetches daily K-line data via Wind MCP CLI and appends to CSV.
"""
import json
import subprocess
import time
import sys
from pathlib import Path

import pandas as pd

CSV_DIR = Path("D:/qlib/qlib_data/source/cn_1d")
BIN_DIR = Path("D:/qlib/qlib_data/cn_data")
CLI = str(Path.home() / ".agents" / "skills" / "wind-mcp-skill" / "scripts" / "cli.mjs")
ALL_FIELDS = ["open", "close", "high", "low", "volume", "amount", "factor", "turn", "vwap", "change"]

FAILED_STOCKS = [
    "sh600019", "sh600032", "sh600085", "sh600105", "sh600177", "sh600312",
    "sh600350", "sh600380", "sh600435", "sh600486", "sh600546", "sh600764",
    "sh600816", "sh600884", "sh600906", "sh600977", "sh601001", "sh601112",
    "sh601156", "sh601225", "sh601336", "sh601567", "sh601615", "sh601788",
    "sh601799", "sh601800", "sh603077", "sh603225", "sh603728", "sh603885",
    "sh603986", "sh688027", "sh688065", "sh688498", "sh688819", "sz000050",
    "sz000100", "sz000629", "sz000723", "sz000831", "sz000895", "sz000963",
    "sz001221", "sz001979", "sz002050", "sz002265", "sz002463", "sz002532",
    "sz002603", "sz002736", "sz002797", "sz300115", "sz300316", "sz300413",
    "sz300476", "sz300558", "sz300628", "sz300718", "sz300751", "sz300757",
    "sz300857", "sz300888", "sz300919", "sz300953", "sz301200", "sz301301",
]


def symbol_to_windcode(symbol):
    """Convert sh600019 -> 600019.SH"""
    code = symbol[2:]
    if symbol.startswith("sh"):
        return f"{code}.SH"
    elif symbol.startswith("sz"):
        return f"{code}.SZ"
    elif symbol.startswith("bj"):
        return f"{code}.BJ"
    return code


def fetch_kline(symbol):
    """Fetch daily K-line for one stock via Wind MCP CLI."""
    windcode = symbol_to_windcode(symbol)
    params = json.dumps({
        "windcode": windcode,
        "begin_date": "20260811",
        "end_date": "20260814",
        "period": "10",
    })
    cmd = ["node", CLI, "call", "stock_data", "get_stock_kline", params]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding="utf-8")
        if result.returncode != 0:
            return None, f"CLI error: {result.stderr[:100]}"

        data = json.loads(result.stdout)
        if data.get("isError"):
            return None, f"API error: {data}"

        text = data["content"][0]["text"]
        kline = json.loads(text)

        if kline.get("error"):
            return None, f"Data error: {kline['error']}"

        # Parse rows
        rows = kline["data"]["rows"]
        if not rows:
            return pd.DataFrame(), "no data"

        df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "amount", "volume", "turn", "vwap"])
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        for col in ["open", "close", "high", "low", "amount", "volume", "turn", "vwap"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df, "OK"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, str(e)[:100]


def append_to_csv(symbol, new_df):
    """Append new data to CSV, deduplicating by date."""
    csv_path = CSV_DIR / f"{symbol}.csv"
    if not csv_path.exists():
        return 0

    existing = pd.read_csv(csv_path, parse_dates=["date"])
    existing["date"] = pd.to_datetime(existing["date"]).dt.tz_localize(None)
    existing_dates = set(existing["date"].dt.strftime("%Y-%m-%d"))
    new_only = new_df[~new_df["date"].dt.strftime("%Y-%m-%d").isin(existing_dates)]

    if new_only.empty:
        return 0

    combined = pd.concat([existing, new_only], ignore_index=True)
    combined = combined.sort_values("date")
    if "symbol" not in combined.columns:
        combined["symbol"] = symbol
    cols = ["symbol", "date"] + [c for c in ALL_FIELDS if c in combined.columns]
    available = [c for c in cols if c in combined.columns]
    combined[available].to_csv(csv_path, index=False)
    return len(new_only)


def main():
    print(f"Wind MCP batch fetch: {len(FAILED_STOCKS)} stocks")
    print("=" * 60)

    success = 0
    fail = 0
    no_data = 0

    for i, symbol in enumerate(FAILED_STOCKS):
        print(f"[{i+1}/{len(FAILED_STOCKS)}] {symbol} ...", end=" ", flush=True)
        df, status = fetch_kline(symbol)

        if df is None:
            fail += 1
            print(f"FAIL: {status}")
        elif df.empty:
            no_data += 1
            print(f"no data")
        else:
            n = append_to_csv(symbol, df)
            success += 1
            print(f"OK +{n}")

        # Rate limit
        if i < len(FAILED_STOCKS) - 1:
            time.sleep(0.5)

    print("=" * 60)
    print(f"Success: {success}, Fail: {fail}, NoData: {no_data}")

    if success > 0:
        print("\nIncremental bin update...")
        sys.path.insert(0, "signal-factory")
        from src.data.wind_bridge import WindBridge
        bridge = WindBridge(csv_dir=str(CSV_DIR), bin_dir=str(BIN_DIR))
        bridge.dump_to_bin(incremental=True)
        print("Bin update done")


if __name__ == "__main__":
    main()