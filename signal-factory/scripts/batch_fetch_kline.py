#!/usr/bin/env python3
"""
批量拉取 Wind MCP 日线数据 — 沪深300成分股 [已弃用]

2026-09-14: 自动化管线已迁移至 akshare。本脚本仅保留用于手动 Wind MCP 数据拉取。
日常数据更新请使用 update_daily.py (akshare)。

用法:
    python scripts/batch_fetch_kline.py
    python scripts/batch_fetch_kline.py --start 0 --count 50
    python scripts/batch_fetch_kline.py --symbols sh600519 sz000001
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# 配置
WIND_MCP_CLI = str(Path.home() / ".agents" / "skills" / "wind-mcp-skill" / "scripts" / "cli.mjs")
WIND_DATA_DIR = Path(__file__).resolve().parents[2] / "my-bot" / "data" / "wind"
WATCHLIST_FILE = Path(__file__).resolve().parents[2] / "my-bot" / "data" / "watchlist_csi300.json"
BEGIN_DATE = "20210101"
END_DATE = "20260807"
BATCH_DELAY = 1.5  # 每次调用间隔（秒），避免触发限流


def load_constituents():
    """加载沪深300成分股列表"""
    if WATCHLIST_FILE.exists():
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("constituents", [])
    return []


def to_wind_code(symbol: str) -> str:
    """将 sh600519 格式转换为 Wind 标准代码 600519.SH"""
    if symbol.startswith("sh"):
        return f"{symbol[2:]}.SH"
    elif symbol.startswith("sz"):
        return f"{symbol[2:]}.SZ"
    elif symbol.startswith("bj"):
        return f"{symbol[2:]}.BJ"
    return symbol


def fetch_kline(symbol: str) -> dict | None:
    """调用 Wind MCP CLI 拉取单只股票日线数据"""
    wind_code = to_wind_code(symbol)
    params = json.dumps({
        "windcode": wind_code,
        "begin_date": BEGIN_DATE,
        "end_date": END_DATE,
        "period": "10",
        "aftime": "0",
    })

    try:
        result = subprocess.run(
            ["node", WIND_MCP_CLI, "call", "stock_data", "get_stock_kline", params],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
        )

        if result.returncode != 0:
            return {"error": f"CLI exit {result.returncode}: {result.stderr[:200]}"}

        data = json.loads(result.stdout)
        if data.get("isError"):
            return {"error": "Wind MCP returned isError"}

        # 提取内层数据
        text = data["content"][0]["text"]
        inner = json.loads(text)
        if inner.get("error"):
            return {"error": str(inner["error"])}

        # 移除 null error 字段避免误判
        inner.pop("error", None)
        return inner

    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}"}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="批量拉取 Wind MCP 日线数据")
    parser.add_argument("--start", type=int, default=0, help="起始索引")
    parser.add_argument("--count", type=int, default=0, help="拉取数量（0=全部）")
    parser.add_argument("--symbols", nargs="+", help="指定标的")
    parser.add_argument("--delay", type=float, default=BATCH_DELAY, help="调用间隔/秒")
    args = parser.parse_args()

    # 确定要拉取的标的
    if args.symbols:
        symbols = args.symbols
    else:
        symbols = load_constituents()
        if not symbols:
            print("错误: 未找到成分股列表，请先运行成分股查询")
            sys.exit(1)

    # 切片
    end = args.start + args.count if args.count > 0 else len(symbols)
    batch = symbols[args.start:end]

    print(f"批量拉取日线数据: {len(batch)} 只标的")
    print(f"日期范围: {BEGIN_DATE} ~ {END_DATE}")
    print(f"输出目录: {WIND_DATA_DIR}")
    print()

    WIND_DATA_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    errors = 0
    skipped = 0

    for i, symbol in enumerate(batch):
        output_path = WIND_DATA_DIR / f"{symbol}_kline.json"

        # 跳过已存在的文件
        if output_path.exists():
            skipped += 1
            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(batch)}] 跳过 {symbol} (已存在) ... {success} ok, {errors} err, {skipped} skip")
            continue

        print(f"  [{i+1}/{len(batch)}] 拉取 {symbol} ...", end=" ", flush=True)

        data = fetch_kline(symbol)

        if data and not data.get("error"):
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            row_count = len(data.get("data", {}).get("rows", []))
            print(f"OK ({row_count} 行)")
            success += 1
        else:
            error_msg = data.get("error", "unknown") if data else "no response"
            print(f"FAIL: {error_msg}")
            errors += 1

        # 间隔延迟
        if i < len(batch) - 1:
            time.sleep(args.delay)

    print()
    print(f"完成: {success} 成功, {errors} 失败, {skipped} 跳过, {len(batch)} 总计")
    print(f"数据目录: {WIND_DATA_DIR}")


if __name__ == "__main__":
    main()