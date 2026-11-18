"""
cn_data 重建脚本 — 添加缺失字段 + 清理 instruments

流程:
  1. 读取现有 CSV 源数据
  2. 用 akshare 批量下载换手率 (turn) 数据
  3. 计算 vwap = amount / volume, change = close / prev_close - 1
  4. 写回 CSV（新增 turn, vwap, change 列）
  5. 调用 WindBridge.dump_to_bin() 全量重建 bin
  6. 清理 instruments（移除指数代码）

用法:
  python scripts/rebuild_cn_data.py              # 全量重建
  python scripts/rebuild_cn_data.py --skip-turn  # 跳过换手率下载
  python scripts/rebuild_cn_data.py --dry-run    # 仅检查，不写入
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_QSF_ROOT))

from src.data.config_loader import (
    get_qlib_csv_dir,
    get_qlib_bin_dir,
    get_qlib_instruments_dir,
    get_qlib_features_dir,
)

# ─── 路径配置 ───

CSV_DIR = Path(get_qlib_csv_dir())
BIN_DIR = Path(get_qlib_bin_dir())
INSTRUMENTS_DIR = Path(get_qlib_instruments_dir())
FEATURES_DIR = Path(get_qlib_features_dir())

# 指数代码模式（需要从 instruments 中移除）
INDEX_CODE_PATTERNS = [
    "sh000001", "sh000300", "sh000905", "sh000016", "sh000688",
    "sz399001", "sz399006", "sz399005", "sz399300",
    "000001", "000300", "000905",
    "kline_raw",
]

# 所有字段（含新增）
ALL_FIELDS = ["open", "close", "high", "low", "volume", "amount", "factor", "turn", "vwap", "change"]


def is_index_code(symbol: str) -> bool:
    """判断是否为指数代码"""
    for pattern in INDEX_CODE_PATTERNS:
        if symbol == pattern or symbol.startswith(pattern):
            return True
    # 匹配 sh000xxx, sz399xxx 等
    if symbol.startswith("sh000") or symbol.startswith("sz399"):
        return True
    # 匹配纯数字且 < 100000 的（指数代码）
    if symbol.isdigit() and len(symbol) == 6 and symbol.startswith("000"):
        return True
    if symbol.isdigit() and len(symbol) == 6 and symbol.startswith("399"):
        return True
    return False


def load_existing_csvs() -> Dict[str, pd.DataFrame]:
    """加载所有现有 CSV 文件"""
    csvs = {}
    for csv_path in sorted(CSV_DIR.glob("*.csv")):
        symbol = csv_path.stem
        if is_index_code(symbol):
            continue
        try:
            df = pd.read_csv(csv_path, parse_dates=["date"])
            df = df.sort_values("date")
            csvs[symbol] = df
        except Exception as e:
            print(f"  跳过 {symbol}: {e}")
    return csvs


def download_turn_data(symbols: List[str], start_date: str = "2015-01-01") -> Dict[str, pd.Series]:
    """
    用 akshare 批量下载换手率数据。

    返回:
        {symbol: Series(date→turnover)}
    """
    import akshare as ak

    results = {}
    total = len(symbols)

    for i, symbol in enumerate(symbols):
        code = symbol.lstrip("sh").lstrip("sz").lstrip("bj")
        print(f"  [{i+1}/{total}] {symbol} ...", end=" ", flush=True)

        try:
            from src.data.akshare_fallback import stock_zh_a_hist_with_fallback
            df = stock_zh_a_hist_with_fallback(
                symbol=code,
                start_date=start_date.replace("-", ""),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="",  # 不复权
            )
            if df is not None and not df.empty:
                df["日期"] = pd.to_datetime(df["日期"])
                ts = df.set_index("日期")["换手率"]
                results[symbol] = ts
                print(f"{len(ts)} 条")
            else:
                print("无数据")
        except Exception as e:
            print(f"失败: {e}")

        # 限速
        if i < total - 1:
            time.sleep(0.3)

    return results


def enrich_csvs(csvs: Dict[str, pd.DataFrame], turn_data: Dict[str, pd.Series]) -> Dict[str, pd.DataFrame]:
    """为 CSV 数据添加 turn, vwap, change 字段"""
    enriched = {}

    for symbol, df in csvs.items():
        df = df.copy()

        # 1. 添加 turn（换手率）
        if symbol in turn_data:
            turn_series = turn_data[symbol]
            df["turn"] = df["date"].map(lambda d: turn_series.get(d, np.nan))
        else:
            df["turn"] = np.nan

        # 2. 计算 vwap = amount / volume
        if "amount" in df.columns and "volume" in df.columns:
            df["vwap"] = np.where(
                df["volume"] > 0,
                df["amount"] / df["volume"],
                np.nan,
            )
        else:
            df["vwap"] = np.nan

        # 3. 计算 change = close / prev_close - 1
        if "close" in df.columns:
            df["change"] = df["close"].pct_change()
        else:
            df["change"] = np.nan

        enriched[symbol] = df

    return enriched


def save_csvs(csvs: Dict[str, pd.DataFrame], dry_run: bool = False):
    """保存增强后的 CSV 文件"""
    for symbol, df in csvs.items():
        csv_path = CSV_DIR / f"{symbol}.csv"
        # 确保列顺序一致
        cols = ["symbol", "date"] + ALL_FIELDS
        available = [c for c in cols if c in df.columns]
        df_out = df[available]

        if not dry_run:
            df_out.to_csv(csv_path, index=False)
            print(f"  已保存: {symbol} ({len(df_out)} 行, {len(available)} 列)")


def clean_instruments(dry_run: bool = False):
    """清理 instruments 文件中的指数代码"""
    for inst_file in INSTRUMENTS_DIR.glob("*.txt"):
        if not inst_file.exists():
            continue

        lines = inst_file.read_text(encoding="utf-8").splitlines()
        original_count = len(lines)
        cleaned = []
        removed = []

        for line in lines:
            line = line.strip()
            if not line:
                cleaned.append(line)
                continue
            # 提取 symbol（第一列）
            parts = line.split("\t")
            symbol = parts[0] if parts else line
            if is_index_code(symbol):
                removed.append(symbol)
            else:
                cleaned.append(line)

        if removed:
            print(f"  {inst_file.name}: 移除 {len(removed)} 个指数代码: {removed[:5]}...")
            if not dry_run:
                inst_file.write_text("\n".join(cleaned) + "\n", encoding="utf-8")


def rebuild_bin():
    """调用 WindBridge 全量重建 bin 文件"""
    from src.data.wind_bridge import WindBridge

    bridge = WindBridge(csv_dir=str(CSV_DIR), bin_dir=str(BIN_DIR))
    success = bridge.dump_to_bin(incremental=False)
    if success:
        print("  Bin 重建完成")
    else:
        print("  Bin 重建失败")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="cn_data 重建脚本")
    parser.add_argument("--skip-turn", action="store_true", help="跳过换手率下载")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不写入")
    parser.add_argument("--skip-bin", action="store_true", help="跳过 bin 重建")
    args = parser.parse_args()

    print("=" * 60)
    print("cn_data 重建")
    print("=" * 60)

    # Step 1: 加载现有 CSV
    print("\n[1/5] 加载现有 CSV...")
    csvs = load_existing_csvs()
    print(f"  已加载 {len(csvs)} 只股票")

    # Step 2: 下载换手率（可选）
    turn_data = {}
    if not args.skip_turn:
        print(f"\n[2/5] 下载换手率数据 (akshare)...")
        symbols = list(csvs.keys())
        turn_data = download_turn_data(symbols)
        print(f"  已下载 {len(turn_data)} 只股票的换手率")
    else:
        print("\n[2/5] 跳过换手率下载")

    # Step 3: 计算衍生字段
    print("\n[3/5] 计算 vwap, change...")
    enriched = enrich_csvs(csvs, turn_data)

    # Step 4: 保存 CSV
    print("\n[4/5] 保存 CSV...")
    save_csvs(enriched, dry_run=args.dry_run)

    # Step 5: 清理 instruments + 重建 bin
    print("\n[5/5] 清理 instruments...")
    clean_instruments(dry_run=args.dry_run)

    if not args.skip_bin and not args.dry_run:
        print("\n重建 bin 文件...")
        rebuild_bin()

    print("\n" + "=" * 60)
    print("重建完成!")
    print(f"  股票数: {len(enriched)}")
    print(f"  字段: {ALL_FIELDS}")
    print(f"  换手率数据: {len(turn_data)} 只")
    print("=" * 60)


if __name__ == "__main__":
    main()