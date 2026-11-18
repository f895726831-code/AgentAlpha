#!/usr/bin/env python3
"""
TiMi Market Data Pipeline — 从 akshare 获取真实 OHLCV 并计算技术指标。

输出: data/output/timi_market_data.json
供 timi_daily.py 消费，替代当前的合成数据回退路径。

用法:
    python scripts/build_timi_market_data.py                  # 从 watchlist 构建
    python scripts/build_timi_market_data.py --universe csi500  # 从 alpha_signals 构建
    python scripts/build_timi_market_data.py --watchlist-only   # 仅 watchlist
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "output" / "timi_market_data.json"

# QSF fallback (akshare → baostock for stock_zh_a_hist)
_QSF_ROOT = Path("D:/AgentAlpha/signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))
from src.data.akshare_fallback import stock_zh_a_hist_with_fallback

# 技术指标参数
MA_PERIODS = [20, 60]
RSI_PERIODS = [7, 14]
HISTORY_DAYS = 120  # 足够 60 日 MA + lookback


def fetch_akshare_kline(symbol: str, days: int = HISTORY_DAYS) -> pd.DataFrame | None:
    """从 akshare 获取个股日 K 线"""
    import akshare as ak

    # 清理 symbol: "600036.SH" → "600036", market="SH"
    code = symbol.replace(".SH", "").replace(".SZ", "").replace(".HK", "")
    if symbol.endswith(".SH") or (symbol.startswith("6") and len(code) == 6):
        market = "SH"
    elif symbol.endswith(".SZ") or (symbol.startswith(("0", "3")) and len(code) == 6):
        market = "SZ"
    else:
        # 港股
        try:
            df = ak.stock_hk_hist(symbol=code, period="daily",
                                  start_date=(datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d"),
                                  end_date=datetime.now().strftime("%Y%m%d"),
                                  adjust="qfq")
            if df is not None and not df.empty:
                df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                        "最高": "high", "最低": "low", "成交量": "volume"})
                df["date"] = pd.to_datetime(df["date"])
                return df[["date", "open", "close", "high", "low", "volume"]]
            return None
        except Exception:
            return None

    # A 股 (akshare → baostock 两级降级)
    try:
        df = stock_zh_a_hist_with_fallback(
            symbol=code,
            period="daily",
            start_date=(datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is not None and not df.empty:
            df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                    "最高": "high", "最低": "low", "成交量": "volume"})
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df[["date", "open", "close", "high", "low", "volume"]]
    except Exception:
        pass

    return None


def compute_indicators(df: pd.DataFrame) -> dict:
    """从 OHLCV DataFrame 计算技术指标"""
    if df.empty or len(df) < 60:
        return None

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values

    # 最新值（最后一行 = 当日）
    latest_close = float(close[-1])
    latest_open = float(open_v := df["open"].values[-1])
    latest_high = float(high[-1])
    latest_low = float(low[-1])
    latest_volume = float(volume[-1])
    prev_close = float(close[-2]) if len(close) > 1 else latest_close

    # MA
    result = {
        "close": latest_close,
        "open": latest_open,
        "high": latest_high,
        "low": latest_low,
        "volume": latest_volume,
        "prev_close": prev_close,
        "avg_volume_5d": float(np.mean(volume[-6:-1])) if len(volume) >= 6 else float(np.mean(volume[-5:])),
        "price_history": [float(x) for x in close[:-1]],  # 不含当日
    }

    for period in MA_PERIODS:
        ma = np.mean(close[-period:])
        ma_prev = np.mean(close[-period-1:-1])
        result[f"ma_{period}"] = float(ma)
        result[f"ma_{period}_prev"] = float(ma_prev)

    # RSI
    for period in RSI_PERIODS:
        # Current RSI (using last period+1 bars)
        curr_window = close[-(period + 2):]
        deltas_curr = np.diff(curr_window)
        gains_curr = np.maximum(deltas_curr, 0)
        losses_curr = np.abs(np.minimum(deltas_curr, 0))
        avg_gain = np.mean(gains_curr) if np.any(gains_curr) else 0.0
        avg_loss = np.mean(losses_curr) if np.any(losses_curr) else 1e-10
        rs = avg_gain / max(avg_loss, 1e-10)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        result[f"rsi_{period}"] = float(rsi)

        # Previous RSI (shifted by 1 bar)
        if len(close) >= period + 3:
            prev_window = close[-(period + 3):-1]
            deltas_prev = np.diff(prev_window)
            gains_prev = np.maximum(deltas_prev, 0)
            losses_prev = np.abs(np.minimum(deltas_prev, 0))
            avg_gain_prev = np.mean(gains_prev) if np.any(gains_prev) else 0.0
            avg_loss_prev = np.mean(losses_prev) if np.any(losses_prev) else 1e-10
            rs_prev = avg_gain_prev / max(avg_loss_prev, 1e-10)
            rsi_prev = 100.0 - (100.0 / (1.0 + rs_prev))
            result[f"rsi_{period}_prev"] = float(rsi_prev)

    # recent_high_20d
    result["recent_high_20d"] = float(np.max(high[-21:-1])) if len(high) >= 21 else float(np.max(high[:-1]))

    # consecutive_down_days
    consecutive = 0
    for i in range(len(close) - 2, max(len(close) - 22, -1), -1):
        if close[i] < close[i + 1]:
            consecutive += 1
        else:
            break
    result["consecutive_down_days"] = consecutive

    return result


def load_watchlist_symbols() -> list[tuple[str, str]]:
    """从 signal_watchlist.json 加载标的代码，返回 [(symbol, name), ...]"""
    wl_path = PROJECT_ROOT / "data" / "signal_watchlist.json"
    if not wl_path.exists():
        return []

    with open(wl_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    active = data.get("active", []) if isinstance(data, dict) else data
    if not isinstance(active, list):
        return []

    symbols = []
    seen = set()
    for entry in active:
        sym = entry.get("windcode", entry.get("symbol", ""))
        if sym and sym not in seen:
            seen.add(sym)
            symbols.append((sym, entry.get("name", "")))
    return symbols


def load_alpha_symbols() -> list[tuple[str, str]]:
    """从 alpha_signals_latest.json 加载 Top 50 标的"""
    alpha_path = PROJECT_ROOT / "data" / "output" / "alpha_signals_latest.json"
    if not alpha_path.exists():
        return []

    with open(alpha_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    symbols = []
    for entry in data.get("top_50", [])[:50]:
        sym = entry.get("symbol", "")
        if sym:
            # 转换: sz300857 → 300857.SZ
            if sym.startswith("sz"):
                symbols.append((f"{sym[2:]}.SZ", ""))
            elif sym.startswith("sh"):
                symbols.append((f"{sym[2:]}.SH", ""))
            else:
                symbols.append((sym, ""))
    return symbols


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TiMi 市场数据构建")
    parser.add_argument("--universe", default=None, choices=["csi500", "csi300"],
                        help="使用 alpha_signals Top 50 作为标的池")
    parser.add_argument("--watchlist-only", action="store_true",
                        help="仅使用 watchlist，不加载 alpha")
    parser.add_argument("--max-stocks", type=int, default=100,
                        help="最大处理标的数")
    args = parser.parse_args()

    # 收集标的
    symbols = []
    if not args.watchlist_only:
        # 从 alpha signals Top 50 作为核心池
        alpha_syms = load_alpha_symbols()
        symbols.extend(alpha_syms)
        print(f"[TiMi-MD] Alpha Top 50: {len(alpha_syms)} 标的")

    # 补充 watchlist
    wl_syms = load_watchlist_symbols()
    seen = {s[0] for s in symbols}
    for sym, name in wl_syms:
        if sym not in seen:
            seen.add(sym)
            symbols.append((sym, name))
    print(f"[TiMi-MD] Watchlist 补充: +{len(wl_syms)} 标的")
    print(f"[TiMi-MD] 总计: {len(symbols)} 标的")

    # 限制数量
    if len(symbols) > args.max_stocks:
        symbols = symbols[:args.max_stocks]
        print(f"[TiMi-MD] 截断到 {args.max_stocks} 标的")

    # 逐标获取数据
    market_data_list = []
    success_count = 0
    fail_count = 0

    for i, (symbol, name) in enumerate(symbols):
        print(f"\r[TiMi-MD] 处理 {i+1}/{len(symbols)}: {symbol}...", end="", flush=True)

        try:
            df = fetch_akshare_kline(symbol)
            if df is None or df.empty:
                fail_count += 1
                continue

            indicators = compute_indicators(df)
            if indicators is None:
                fail_count += 1
                continue

            indicators["symbol"] = symbol
            if name:
                indicators["name"] = name
            market_data_list.append(indicators)
            success_count += 1

        except Exception as e:
            fail_count += 1
            if fail_count <= 3:
                print(f"\n[TiMi-MD] {symbol} 失败: {e}")

    print(f"\n[TiMi-MD] 完成: {success_count} 成功, {fail_count} 失败")

    if not market_data_list:
        print("[TiMi-MD] ⚠ 无有效数据")
        return 1

    # 输出
    output = {
        "generated_at": datetime.now().isoformat(),
        "source": "akshare+baostock",
        "universe": args.universe or "watchlist+alpha",
        "total_stocks": len(market_data_list),
        "market_data": market_data_list,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[TiMi-MD] 输出: {OUTPUT_PATH} ({len(market_data_list)} stocks)")

    # 快速统计
    stocks_with_ma = sum(1 for md in market_data_list if md.get("ma_20"))
    stocks_with_rsi = sum(1 for md in market_data_list if md.get("rsi_14"))
    avg_history_len = int(np.mean([len(md.get("price_history", [])) for md in market_data_list]))
    print(f"[TiMi-MD]   MA: {stocks_with_ma}, RSI: {stocks_with_rsi}, "
          f"avg history: {avg_history_len} bars")

    return 0


if __name__ == "__main__":
    sys.exit(main())