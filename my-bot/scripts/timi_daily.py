"""
TiMi 日度运行脚本 — 供管线 / cron 调用。

管线的 TiMi 集成点:
  signal-gen / trade-planner 执行前调用此脚本:
    python scripts/timi_daily.py

bias/mode 自动从编译策略 data/strategies/{today}.json 读取（由 LLM 编译器产出）。
CLI --mode/--bias 参数仅用于手动覆盖。

输出: data/output/timi_signals.json (与 signal-gen 兼容的 JSON 格式)
返回码: 0 = TiMi 成功, 1 = 回退 LLM

用法:
    # 盘前运行 (cron) — 自动加载编译策略
    python scripts/timi_daily.py

    # 手动覆盖 bias/mode
    python scripts/timi_daily.py --mode risk_off --bias left_priority

    # 自定义账户
    python scripts/timi_daily.py --account-value 1000000 --cash 500000
"""

import json
import sys
import os
from datetime import date
from pathlib import Path

# 确保 my-bot/src 在 path 中
MY_BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.timi_adapter import TiMiAdapter


def load_watchlist() -> list:
    """加载自选股列表，返回 [{symbol, close, ...}] 格式"""
    watchlist_path = MY_BOT / "data/signal_watchlist.json"
    if not watchlist_path.exists():
        return []

    with open(watchlist_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # v4.3 格式: {"active": [...], "version": ..., ...}
    active = data.get("active", []) if isinstance(data, dict) else data
    if not isinstance(active, list):
        return []

    items = []
    for entry in active:
        sym = entry.get("windcode", entry.get("symbol", ""))
        daily = entry.get("daily_prices", [])
        latest_close = daily[-1]["close"] if daily else entry.get("entry_price", 0)

        items.append({
            "symbol": sym,
            "close": latest_close,
            "name": entry.get("name", ""),
            "entry_price": entry.get("entry_price", 0),
            "alpha_score": entry.get("alpha_score", 0),
        })
    return items


def load_alpha_signals() -> list:
    """加载 Qlib alpha 信号，返回 [{symbol, close, alpha_score}]"""
    alpha_path = MY_BOT / "data/output/alpha_signals_latest.json"
    if not alpha_path.exists():
        return []

    with open(alpha_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    scores = data.get("all_scores", {})
    items = []
    for raw_sym, score in scores.items():
        # raw_sym = "sh600967" → "600967.SH"
        if raw_sym.startswith("sh"):
            sym = raw_sym[2:] + ".SH"
        elif raw_sym.startswith("sz"):
            sym = raw_sym[2:] + ".SZ"
        else:
            sym = raw_sym

        items.append({
            "symbol": sym,
            "alpha_score": float(score) if score else 0,
            "close": 0,
        })
    return items


def load_hk_quotes() -> dict:
    """加载港股行情"""
    hk_path = MY_BOT / "data/output/hk_quotes.json"
    if hk_path.exists():
        with open(hk_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def build_market_data_list(watchlist, alpha_signals, hk_quotes) -> list:
    """
    从自选股 + alpha 信号 + 港股行情构造 market_data_list。

    注意：这是一个简化版。完整的 market_data 需要 OHLCV + MA + RSI 等指标。
    实际运行时应该从实时行情接口获取。
    这里从已有数据构建基础结构。
    """
    market_data_list = []
    seen = set()

    for item in watchlist:
        sym = item.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)

        md = {
            "symbol": sym,
            "close": item.get("close", 0),
            "open": item.get("open", item.get("close", 0)),
            "high": item.get("high", item.get("close", 0)),
            "low": item.get("low", item.get("close", 0)),
            "volume": item.get("volume", 0),
            "avg_volume_5d": item.get("avg_volume_5d", item.get("volume", 0)),
            "prev_close": item.get("prev_close", item.get("close", 0)),
            "name": item.get("name", ""),
        }

        # 补充 MA 数据 (如果可用)
        for period in [20, 60]:
            key = f"ma_{period}"
            if key in item:
                md[key] = item[key]
                md[f"{key}_prev"] = item.get(f"{key}_prev", item[key])

        # 补充 RSI 数据
        for period in [7, 14]:
            key = f"rsi_{period}"
            if key in item:
                md[key] = item[key]
                md[f"{key}_prev"] = item.get(f"{key}_prev", item[key])

        # 补充价格历史 (模拟)
        close = md.get("close")
        if close is not None and close > 0:
            md["price_history"] = [close - 5 + i * 0.1 for i in range(60)]

        market_data_list.append(md)

    # 补充 alpha 信号中的标的
    for item in alpha_signals:
        sym = item.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)

        close = item.get("close", 0)
        md = {
            "symbol": sym,
            "close": close,
            "open": close,
            "high": close,
            "low": close,
            "volume": 0,
            "avg_volume_5d": 0,
            "prev_close": close,
            "price_history": [close - 5 + i * 0.1 for i in range(60)] if close > 0 else [50 + i * 0.1 for i in range(60)],
        }
        market_data_list.append(md)

    # 补充港股
    stocks = hk_quotes.get("stocks", {}) if isinstance(hk_quotes, dict) else {}
    for sym, quote in stocks.items():
        if sym in seen:
            continue
        seen.add(sym)
        # 港股代码格式: 00700 → 00700.HK
        hk_sym = f"{sym}.HK"

        close = quote.get("price", 0)
        md = {
            "symbol": hk_sym,
            "close": close,
            "open": quote.get("open", close),
            "high": quote.get("high", close),
            "low": quote.get("low", close),
            "volume": quote.get("volume", 0),
            "avg_volume_5d": quote.get("volume", 0),  # 近似
            "prev_close": quote.get("prev_close", close),
            "price_history": [close - 5 + i * 0.1 for i in range(60)] if close > 0 else [50 + i * 0.1 for i in range(60)],
        }
        market_data_list.append(md)

    return market_data_list


def load_compiled_strategy(date_str: str = None) -> dict | None:
    """加载 LLM 编译器输出的当日策略，提取 bias/mode/regime"""
    if date_str is None:
        date_str = date.today().isoformat()
    strategy_path = MY_BOT / "data" / "strategies" / f"{date_str}.json"
    if not strategy_path.exists():
        return None
    with open(strategy_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="TiMi 日度运行")
    parser.add_argument("--mode", type=str, default=None,
                        help="运行模式 (默认从编译策略自动读取)")
    parser.add_argument("--bias", type=str, default=None,
                        help="策略偏好 (默认从编译策略自动读取)")
    parser.add_argument("--gate2", type=str, default="green",
                        help="Gate 2 信号灯 (green/yellow/orange/red)")
    parser.add_argument("--crowding", type=int, default=0)
    parser.add_argument("--account-value", type=float, default=500000)
    parser.add_argument("--account-cash", type=float, default=500000)
    parser.add_argument("--output", type=str,
                        default=str(MY_BOT / "data/output/timi_signals.json"))

    args = parser.parse_args()

    # ── 自动加载编译策略的 bias/mode ──
    compiled = load_compiled_strategy()
    mode = args.mode or (compiled.get("mode", "normal") if compiled else "normal")
    bias = args.bias or (compiled.get("bias", "balanced") if compiled else "balanced")
    regime = compiled.get("regime", "") if compiled else ""

    if compiled:
        print(f"[TiMi] 加载编译策略: regime={regime} bias={bias} mode={mode}",
              file=sys.stderr)
    if args.mode or args.bias:
        print(f"[TiMi] CLI 覆盖: mode={mode} bias={bias}", file=sys.stderr)

    # ── 加载数据 ──
    print(f"[TiMi] 加载数据...", file=sys.stderr)

    # 优先使用 timi_market_data.json (含真实技术指标)
    market_data_list = []
    md_path = MY_BOT / "data/output/timi_market_data.json"
    if md_path.exists():
        with open(md_path, "r", encoding="utf-8") as f:
            md_data = json.load(f)
        market_data_list = md_data.get("market_data", [])
        if market_data_list:
            print(f"[TiMi] 从 timi_market_data.json 加载 {len(market_data_list)} 个标的",
                  file=sys.stderr)

    if not market_data_list:
        # 回退: watchlist + alpha + hk
        watchlist = load_watchlist()
        alpha_signals = load_alpha_signals()
        hk_quotes = load_hk_quotes()
        market_data_list = build_market_data_list(watchlist, alpha_signals, hk_quotes)
        print(f"[TiMi] 使用回退数据: {len(market_data_list)} 个标的", file=sys.stderr)
    print(f"[TiMi] 已构造 {len(market_data_list)} 个标的的市场数据", file=sys.stderr)

    if not market_data_list:
        print("[TiMi] 无可用市场数据 → 回退 LLM", file=sys.stderr)
        print(json.dumps({"success": False, "source": "llm_fallback",
                          "reason": "no_market_data"}))
        sys.exit(1)

    # ── 执行 ──
    print(f"[TiMi] 执行 mode={mode} bias={bias} gate2={args.gate2}...",
          file=sys.stderr)

    adapter = TiMiAdapter()
    result = adapter.run(
        market_data_list=market_data_list,
        mode=mode,
        bias=bias,
        gate2_light=args.gate2,
        crowding_score=args.crowding,
        account_value=args.account_value,
        cash=args.account_cash,
        date_str=date.today().isoformat(),
    )

    # ── 输出 ──
    output_data = result.to_dict()

    # 添加管线兼容字段
    output_data["generated_at"] = date.today().isoformat()
    output_data["pipeline_version"] = "4.3-timi"
    output_data["ticker_count"] = len(market_data_list)

    # 写入文件
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[TiMi] 输出: {output_path}", file=sys.stderr)
    exec_summary = result.execution_summary or {}
    print(f"[TiMi] 信号={exec_summary.get('signal_count', 0)} "
          f"仓位={exec_summary.get('position_count', 0)} "
          f"延迟={result.latency_ms:.1f}ms "
          f"源={result.source}",
          file=sys.stderr)

    # 同时输出到 stdout (供管线管道)
    print(json.dumps(output_data, indent=2, ensure_ascii=False))

    # 返回码: 0 = TiMi 成功, 1 = 回退
    if result.source == "llm_fallback":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()