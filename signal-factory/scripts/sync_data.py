#!/usr/bin/env python3
"""
每日数据同步 — Wind MCP JSON → Qlib CSV → Qlib bin [已弃用]

2026-09-14: 自动化管线已迁移至 akshare。本脚本仅保留用于手动 Wind MCP JSON 数据同步。
日常数据更新请使用 update_daily.py (akshare)。

薄 CLI 包装器，核心逻辑委托给 src/data/wind_bridge.py 的 WindBridge 类。

用法:
    python scripts/sync_data.py
    python scripts/sync_data.py --wind-dir D:/AgentAlpha/my-bot/data/wind --force
    python scripts/sync_data.py --symbols sh600519 sz000001
    python scripts/sync_data.py --output logs/sync_20260807.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Windows 终端 UTF-8 兼容
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.wind_bridge import WindBridge, sync_wind_to_qlib, get_wind_data_summary
from src.data.config_loader import (
    get_wind_json_dir,
    get_qlib_csv_dir,
    get_qlib_bin_dir,
)


def main():
    parser = argparse.ArgumentParser(description="每日数据同步 — Wind MCP → Qlib")
    parser.add_argument(
        "--wind-dir",
        default=get_wind_json_dir(),
        help="Wind MCP JSON 输出目录",
    )
    parser.add_argument(
        "--csv-dir",
        default=get_qlib_csv_dir(),
        help="Qlib CSV 源数据目录",
    )
    parser.add_argument(
        "--bin-dir",
        default=get_qlib_bin_dir(),
        help="Qlib bin 数据目录",
    )
    parser.add_argument("--symbols", nargs="+", help="指定标的（如 sh600519 sz000001）")
    parser.add_argument("--force", action="store_true", help="强制全量同步（默认增量）")
    parser.add_argument("--no-bin", action="store_true", help="跳过 bin 转换")
    parser.add_argument("--output", help="输出日志 JSON 文件路径")
    parser.add_argument("--summary", action="store_true", help="仅输出 Wind 数据摘要")
    args = parser.parse_args()

    if args.summary:
        summary = get_wind_data_summary(args.wind_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return

    print(f"[数据同步] Wind JSON → Qlib CSV")
    print(f"  Wind 目录: {args.wind_dir}")
    print(f"  CSV 目录:  {args.csv_dir}")
    print(f"  Bin 目录:  {args.bin_dir}")
    if args.symbols:
        print(f"  标的:      {', '.join(args.symbols)}")
    print()

    result = sync_wind_to_qlib(
        wind_json_dir=args.wind_dir,
        csv_dir=args.csv_dir,
        bin_dir=args.bin_dir,
        symbols=args.symbols,
        force=args.force,
    )

    print(f"同步完成: {result['synced_count']} 只标的, "
          f"跳过: {result.get('skipped_count', 0)}, "
          f"错误: {len(result['errors'])}")
    for err in result["errors"]:
        print(f"  [WARN] {err}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"日志已保存: {output_path}")


if __name__ == "__main__":
    main()