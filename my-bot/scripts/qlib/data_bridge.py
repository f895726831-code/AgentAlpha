"""
AgentAlpha 数据桥 — Passthrough to QSF src/data/wind_bridge.py（非独立 ETL）
═══════════════════════════════════════════════════════════════════════════

此模块是 QSF WindBridge 的薄封装层，不实现独立 ETL 逻辑。
Canonical ETL: signal-factory/src/data/wind_bridge.py
符号命名 canon: WindBridge._normalize_symbol()（6→sh, 0/3→sz, 8→bj）
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
import numpy as np

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

from src.data.wind_bridge import WindBridge, sync_wind_to_qlib, get_wind_data_summary

# ─── 常量 (保持 AgentAlpha 兼容) ───

WIND_COLUMN_MAP = {
    "TIME": "date",
    "OPEN": "open",
    "MATCH": "close",
    "HIGH": "high",
    "LOW": "low",
    "VOLUME": "volume",
    "TURNOVER": "amount",
}

QLIB_COLUMNS = ["symbol", "date", "open", "close", "high", "low", "volume", "factor"]

DEFAULT_WIND_JSON_DIR = "%USERPROFILE%/AppData/Local/Temp"
DEFAULT_QLIB_CSV_DIR = "D:/qlib/qlib_data/source/cn_1d"
DEFAULT_QLIB_BIN_DIR = "D:/qlib/qlib_data/cn_data"


# ─── Wind 代码转换 ───

def windcode_to_qlib_symbol(windcode: str) -> str:
    """将 Wind 代码转换为 Qlib 符号格式（使用 canonical SYMBOL_PREFIX_RULES）"""
    from src.data.wind_bridge import SYMBOL_PREFIX_RULES

    # 点分格式: "600519.SH" → 先拆后匹配
    if "." in windcode:
        code, exchange = windcode.split(".")
        prefix = exchange.lower()
        return f"{prefix}{code}"

    # 已有前缀: "sh600519" → 直接返回
    if windcode.startswith(("sh", "sz", "bj", "SH", "SZ", "BJ")):
        return windcode.lower()

    # 裸代码: 用正则规则推断交易所前缀
    import re
    for pattern, prefix, _ in SYMBOL_PREFIX_RULES:
        if pattern.match(windcode):
            if prefix:
                return f"{prefix}{windcode}"
    return windcode


# ─── 桥接函数 ───

def parse_wind_json_content(filepath: str) -> dict:
    """解析 Wind MCP JSON 文件内容"""
    bridge = WindBridge()
    return bridge.process_wind_file(filepath)


def wind_json_to_dataframe(filepath: str, windcode: str = None) -> pd.DataFrame:
    """将 Wind JSON 文件转换为 Qlib 标准 DataFrame"""
    bridge = WindBridge()
    symbol, df = bridge.process_wind_file(filepath)
    return df


def convert_json_files(
    json_dir: str = None,
    output_dir: str = None,
    mode: str = "incremental",
    symbols_filter: Optional[List[str]] = None,
) -> Tuple[int, int]:
    """
    批量转换 Wind JSON 文件为 Qlib CSV 格式。

    返回:
        (success_count, skip_count)
    """
    json_dir = json_dir or DEFAULT_WIND_JSON_DIR
    output_dir = output_dir or DEFAULT_QLIB_CSV_DIR

    bridge = WindBridge(csv_dir=output_dir)
    symbols = symbols_filter

    if mode == "full":
        result = bridge.sync(wind_json_dir=json_dir, symbols=symbols, force=True, dump_bin=False)
    else:
        result = bridge.sync(wind_json_dir=json_dir, symbols=symbols, force=False, dump_bin=False)

    return (result.get("synced", 0), result.get("skipped", 0))


def dump_to_qlib_bin(
    csv_dir: str = None,
    bin_dir: str = None,
    freq: str = "day",
    qlib_root: str = None,
) -> None:
    """将 CSV 数据 dump 为 Qlib 二进制格式"""
    csv_dir = csv_dir or DEFAULT_QLIB_CSV_DIR
    bin_dir = bin_dir or DEFAULT_QLIB_BIN_DIR

    bridge = WindBridge(csv_dir=csv_dir, bin_dir=bin_dir)
    bridge.dump_to_bin(incremental=False)


def verify_qlib_data(bin_dir: str = None, region: str = "cn") -> bool:
    """验证 Qlib 二进制数据是否可读"""
    import qlib
    bin_dir = bin_dir or DEFAULT_QLIB_BIN_DIR
    try:
        qlib.init(provider_uri=bin_dir, region=region)
        from qlib.data import D
        instruments = D.list_instruments("all", level="market")
        return len(instruments) > 0
    except Exception:
        return False


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AgentAlpha × Qlib 数据桥 (QSF 桥接)")
    parser.add_argument("--mode", default="incremental", choices=["incremental", "full", "csv-only"],
                        help="同步模式")
    parser.add_argument("--input-dir", help="Wind JSON 输入目录")
    parser.add_argument("--output-dir", help="Qlib CSV 输出目录")
    parser.add_argument("--dump-bin", action="store_true", help="转换为 bin 格式")
    parser.add_argument("--verify", action="store_true", help="验证数据")
    parser.add_argument("--symbols", help="限制标的 (逗号分隔)")
    parser.add_argument("--qlib-root", help="Qlib 根目录")

    args = parser.parse_args()

    if args.verify:
        ok = verify_qlib_data()
        print(f"验证: {'通过' if ok else '失败'}")
        sys.exit(0 if ok else 1)

    symbols = args.symbols.split(",") if args.symbols else None
    success, skipped = convert_json_files(
        json_dir=args.input_dir,
        output_dir=args.output_dir,
        mode=args.mode,
        symbols_filter=symbols,
    )
    print(f"转换完成: {success} 成功, {skipped} 跳过")

    if args.dump_bin:
        dump_to_qlib_bin(
            csv_dir=args.output_dir,
            bin_dir=args.qlib_root,
        )
        print("Bin dump 完成")