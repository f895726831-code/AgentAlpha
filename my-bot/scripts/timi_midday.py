"""
TiMi 盘中重跑脚本 — 用实时价格更新策略信号。

在午间（11:30）运行，使用当前盘中的 OHLCV 数据重新执行策略条件检查，
生成更新后的信号供午后参考。

工作流:
  1. 获取实时行情 (akshare spot)
  2. 加载已编译策略 (data/strategies/{today}.json)
  3. 构建更新后的 market_data（价格含今日盘中变动）
  4. 运行 TiMiAdapter
  5. 输出 data/output/timi_signals_midday.json

与 timi_daily.py 的区别:
  - 不重新编译策略（使用盘前已编译的策略）
  - 使用实时价格而非昨日收盘价
  - 仅更新 price_history 的最后一项 + MA/RSI

用法:
    python scripts/timi_midday.py                     # 使用实时行情
    python scripts/timi_midday.py --mock              # 使用昨日收盘价（akshare 不可用时）
    python scripts/timi_midday.py --output custom.json # 自定义输出路径
"""

import json
import sys
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MY_BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.timi_adapter import TiMiAdapter

OUTPUT_PATH = MY_BOT / "data" / "output" / "timi_signals_midday.json"
MARKET_DATA_PATH = MY_BOT / "data" / "output" / "timi_market_data.json"
STRATEGIES_DIR = MY_BOT / "data" / "strategies"


# ─── 实时行情 ────────────────────────────────────────────────────────────

def fetch_realtime_quotes() -> Dict[str, Dict[str, float]]:
    """
    从 akshare 获取实时行情。

    返回: {symbol: {price, change_pct, open, high, low, volume}}
          失败返回空 dict
    """
    try:
        import akshare as ak
    except ImportError:
        print("[TiMi Midday] akshare not available, using mock data", file=sys.stderr)
        return {}

    try:
        df = ak.stock_zh_a_spot_em()
    except Exception as e:
        print(f"[TiMi Midday] realtime fetch failed: {e}", file=sys.stderr)
        return {}

    if df is None or df.empty:
        return {}

    quotes = {}
    for _, row in df.iterrows():
        try:
            code = str(row.get("代码", ""))
            if not code:
                continue

            # 构建 symbol: "600036" → "600036.SH", "000001" → "000001.SZ"
            if code.startswith("6"):
                symbol = f"{code}.SH"
            elif code.startswith(("0", "3")):
                symbol = f"{code}.SZ"
            else:
                continue

            price = float(row.get("最新价", 0) or 0)
            if price <= 0:
                continue

            quotes[symbol] = {
                "price": price,
                "change_pct": float(row.get("涨跌幅", 0) or 0),
                "open": float(row.get("今开", 0) or price),
                "high": float(row.get("最高", 0) or price),
                "low": float(row.get("最低", 0) or price),
                "volume": float(row.get("成交量", 0) or 0),
            }
        except (ValueError, TypeError):
            continue

    print(f"[TiMi Midday] fetched {len(quotes)} realtime quotes", file=sys.stderr)
    return quotes


# ─── 市场数据更新 ────────────────────────────────────────────────────────

def update_market_data(
    base_data: List[Dict],
    quotes: Dict[str, Dict[str, float]],
) -> List[Dict]:
    """
    用实时报价更新市场数据。

    对每个标的:
      - 更新 close/open/high/low/volume 为今日盘中数据
      - 更新 price_history：替换最后一项为当前价格
      - 重新计算 MA20/MA60（基于更新后的 price_history）
      - 重新计算 RSI7/RSI14
      - 添加 is_midday_update=True 标记
    """
    updated = []
    updated_count = 0

    for item in base_data:
        symbol = item.get("symbol", "")
        if not symbol:
            continue

        quote = quotes.get(symbol)
        if not quote:
            # 无实时报价 → 保持原数据，标记为未更新
            item["is_midday_update"] = False
            updated.append(item)
            continue

        # ── 更新价格 ──
        item["close"] = quote["price"]
        item["open"] = quote["open"]
        item["high"] = quote["high"]
        item["low"] = quote["low"]
        item["volume"] = quote["volume"]
        item["change_pct"] = quote["change_pct"]
        item["is_midday_update"] = True

        # ── 更新 price_history ──
        history = item.get("price_history", [])
        if history:
            # 替换最后一项（最新收盘价→当前价）
            history[-1] = quote["price"]
        else:
            history = [quote["price"]]
        item["price_history"] = history

        # ── 重新计算 MA ──
        if len(history) >= 20:
            item["ma_20"] = round(np.mean(history[-20:]), 2)
        if len(history) >= 60 and "ma_60" in item:
            item["ma_60"] = round(np.mean(history[-60:]), 2)

        # ── 重新计算 RSI (简化: 基于 price_history) ──
        if len(history) >= 15:
            for period in [7, 14]:
                key = f"rsi_{period}"
                try:
                    diffs = np.diff(history[-(period + 1):])
                    gains = np.maximum(diffs, 0)
                    losses = np.abs(np.minimum(diffs, 0))
                    avg_gain = np.mean(gains)
                    avg_loss = np.mean(losses)
                    if avg_loss > 0:
                        rs = avg_gain / avg_loss
                        item[key] = round(100.0 - (100.0 / (1.0 + rs)), 1)
                    else:
                        item[key] = 100.0
                except Exception:
                    pass

        updated_count += 1
        updated.append(item)

    print(f"[TiMi Midday] updated {updated_count}/{len(base_data)} symbols "
          f"with realtime prices", file=sys.stderr)
    return updated


# ─── 主入口 ──────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="TiMi 盘中重跑")
    parser.add_argument("--mock", action="store_true",
                        help="使用昨日收盘价（跳过实时行情获取）")
    parser.add_argument("--output", type=str, default=None,
                        help="输出 JSON 路径 (default: timi_signals_midday.json)")
    parser.add_argument("--account-value", type=float, default=500_000)
    parser.add_argument("--cash", type=float, default=250_000)
    parser.add_argument("--date", type=str, default=date.today().isoformat(),
                        help="策略日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    today = args.date
    output_path = Path(args.output) if args.output else OUTPUT_PATH

    # ── Step 1: 获取实时行情 ──
    quotes = {} if args.mock else fetch_realtime_quotes()

    # ── Step 2: 加载市场数据 ──
    if not MARKET_DATA_PATH.exists():
        print(f"[TiMi Midday] market data not found: {MARKET_DATA_PATH}", file=sys.stderr)
        print("[TiMi Midday] skipping — no base data to update", file=sys.stderr)
        output_path.write_text(json.dumps({
            "success": False,
            "source": "timi_midday",
            "error": "market_data_not_found",
            "timestamp": datetime.now().isoformat(),
            "signals": [],
            "positions": [],
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    with open(MARKET_DATA_PATH, "r", encoding="utf-8") as f:
        md = json.load(f)

    base_data = md.get("market_data", [])
    if not base_data:
        print("[TiMi Midday] market data is empty, skipping", file=sys.stderr)
        return

    # ── Step 3: 更新市场数据 ──
    if quotes:
        market_data_list = update_market_data(base_data, quotes)
    else:
        market_data_list = base_data
        print("[TiMi Midday] no realtime quotes — using base data unchanged",
              file=sys.stderr)

    # ── Step 4: 加载已编译策略 ──
    strategy_path = STRATEGIES_DIR / f"{today}.json"
    if not strategy_path.exists():
        print(f"[TiMi Midday] strategy not found: {strategy_path} — using latest",
              file=sys.stderr)

    # ── Step 5: 运行 TiMiAdapter ──
    adapter = TiMiAdapter(storage_dir=STRATEGIES_DIR)
    result = adapter.run(
        market_data_list=market_data_list,
        mode="normal",
        bias="balanced",
        account_value=args.account_value,
        cash=args.cash,
        date_str=today,
    )

    # ── Step 6: 输出 ──
    output = {
        "success": result.success,
        "source": "timi_midday",
        "timestamp": datetime.now().isoformat(),
        "signals": result.signals,
        "positions": result.positions,
        "execution_summary": result.execution_summary,
        "anomalies": result.anomalies,
        "fallback": result.fallback,
        "warnings": result.warnings,
        "latency_ms": result.latency_ms,
        "_meta": {
            "midday_update": bool(quotes),
            "symbols_updated": sum(
                1 for item in market_data_list if item.get("is_midday_update")
            ) if quotes else 0,
            "total_symbols": len(market_data_list),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    signal_count = len(result.signals)
    position_count = len(result.positions)
    print(f"\n[TiMi Midday] {signal_count} signals, {position_count} positions "
          f"→ {output_path}")


if __name__ == "__main__":
    main()