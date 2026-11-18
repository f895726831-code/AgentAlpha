# SPDX-License-Identifier: MIT
# timi_market_data.py — TiMi 真实市场数据获取与指标计算
#
# 从 akshare (baostock fallback) 获取日K线数据，计算技术指标，输出到标准 market_data 格式。
#
# 用法:
#     python scripts/timi_market_data.py                    # 全量扫描
#     python scripts/timi_market_data.py --watchlist-only   # 仅自选股
#     python scripts/timi_market_data.py --tickers 600036,000858  # 指定标的
#
# 输出: data/output/timi_market_data.json (供 timi_daily.py 使用)

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

MY_BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

# QSF fallback (akshare → baostock for stock_zh_a_hist)
_QSF_ROOT = Path("D:/AgentAlpha/signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

DATA_DIR = MY_BOT / "data/output"
WATCHLIST_PATH = MY_BOT / "data/signal_watchlist.json"

# Import fallback (akshare → baostock)
from src.data.akshare_fallback import stock_zh_a_hist_with_fallback


def load_watchlist() -> List[Dict]:
    """加载自选股列表"""
    if not WATCHLIST_PATH.exists():
        return []

    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    active = data.get("active", []) if isinstance(data, dict) else data
    if not isinstance(active, list):
        return []

    symbols = []
    for entry in active:
        sym = entry.get("windcode", entry.get("symbol", ""))
        if sym:
            symbols.append({
                "symbol": sym,
                "name": entry.get("name", ""),
            })
    return symbols


def fetch_daily_kline(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """
    获取 A 股日 K 线（akshare → baostock 两级降级）。

    Args:
        symbol: 股票代码 (如 "600036", "000858", 或 "600036.SH")
        days: 获取天数

    Returns:
        DataFrame: date索引, open/high/low/close/volume 列，获取失败返回 None
    """
    # 标准化代码
    clean = symbol.replace(".SH", "").replace(".SZ", "").replace("sh", "").replace("sz", "")
    if len(clean) != 6:
        return None

    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=days + 30)).strftime("%Y%m%d")

    try:
        df = stock_zh_a_hist_with_fallback(
            symbol=clean,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is not None and len(df) > 10:
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return df[["open", "high", "low", "close", "volume"]]
        return None
    except Exception as e:
        print(f"  [WARN] {symbol} 获取失败: {e}")
        return None


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算技术指标: MA, RSI, 近期高点, 连续下跌天数等"""
    close = df["close"]
    volume = df["volume"]

    # MA
    df["ma_20"] = close.rolling(20).mean()
    df["ma_60"] = close.rolling(60).mean()
    df["ma_20_prev"] = df["ma_20"].shift(1)
    df["ma_60_prev"] = df["ma_60"].shift(1)

    # Avg volume
    df["avg_volume_5d"] = volume.rolling(5).mean()
    df["avg_volume_20d"] = volume.rolling(20).mean()

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain_7 = gain.rolling(7).mean()
    avg_loss_7 = loss.rolling(7).mean()
    avg_gain_14 = gain.rolling(14).mean()
    avg_loss_14 = loss.rolling(14).mean()

    rs_7 = avg_gain_7 / avg_loss_7.replace(0, np.nan)
    rs_14 = avg_gain_14 / avg_loss_14.replace(0, np.nan)
    df["rsi_7"] = 100 - (100 / (1 + rs_7))
    df["rsi_14"] = 100 - (100 / (1 + rs_14))
    df["rsi_7_prev"] = df["rsi_7"].shift(1)
    df["rsi_14_prev"] = df["rsi_14"].shift(1)

    # 近期高点 (20日)
    df["recent_high_20d"] = close.rolling(20).max()

    # 连续下跌天数
    down = (close < close.shift(1)).astype(int)
    consecutive = pd.Series(0, index=df.index)
    for i in range(1, len(down)):
        if down.iloc[i]:
            consecutive.iloc[i] = consecutive.iloc[i-1] + 1
        else:
            consecutive.iloc[i] = 0
    df["consecutive_down_days"] = consecutive

    # prev_close
    df["prev_close"] = close.shift(1)

    # 价格历史 (60日 close 序列)
    df["price_history_list"] = [close.iloc[max(0, i-59):i+1].tolist() for i in range(len(close))]

    return df


def build_market_data(symbol: str, df: pd.DataFrame, name: str = "") -> Optional[Dict[str, Any]]:
    """从日K线DataFrame构建单标的 market_data 字典"""
    if df is None or len(df) < 60:
        return None

    # 使用最后一行（最新交易日）
    last = df.iloc[-1]

    close = float(last["close"])
    price_history = last.get("price_history_list")
    if isinstance(price_history, list) and len(price_history) >= 20:
        ph = [float(x) for x in price_history[-60:]]
    else:
        ph = [close * (1 + (i - 60) * 0.001) for i in range(60)]

    return {
        "symbol": symbol,
        "name": name,
        "close": close,
        "open": float(last.get("open", close)),
        "high": float(last.get("high", close)),
        "low": float(last.get("low", close)),
        "volume": float(last.get("volume", 0)),
        "avg_volume_5d": float(last.get("avg_volume_5d", 0)),
        "prev_close": float(last.get("prev_close", close)),
        "ma_20": float(last.get("ma_20", close)) if pd.notna(last.get("ma_20")) else close,
        "ma_60": float(last.get("ma_60", close)) if pd.notna(last.get("ma_60")) else close,
        "ma_20_prev": float(last.get("ma_20_prev", close)) if pd.notna(last.get("ma_20_prev")) else close,
        "ma_60_prev": float(last.get("ma_60_prev", close)) if pd.notna(last.get("ma_60_prev")) else close,
        "rsi_7": float(last.get("rsi_7", 50)) if pd.notna(last.get("rsi_7")) else 50.0,
        "rsi_14": float(last.get("rsi_14", 50)) if pd.notna(last.get("rsi_14")) else 50.0,
        "rsi_7_prev": float(last.get("rsi_7_prev", 50)) if pd.notna(last.get("rsi_7_prev")) else 50.0,
        "rsi_14_prev": float(last.get("rsi_14_prev", 50)) if pd.notna(last.get("rsi_14_prev")) else 50.0,
        "recent_high_20d": float(last.get("recent_high_20d", close)),
        "consecutive_down_days": int(last.get("consecutive_down_days", 0)),
        "price_history": ph,
    }


def run(watchlist_only: bool = True,
        tickers: Optional[List[str]] = None,
        output_path: Optional[str] = None) -> List[Dict]:
    """
    主函数: 获取行情 + 计算指标 → 输出 market_data_list。

    Returns:
        market_data_list
    """
    symbols_to_fetch: List[Dict] = []

    if tickers:
        for t in tickers:
            symbols_to_fetch.append({"symbol": t, "name": t})
    elif watchlist_only:
        symbols_to_fetch = load_watchlist()
        print(f"[TiMi Data] 加载自选股: {len(symbols_to_fetch)} 只")
    else:
        # 全量: 自选 + alpha top 50
        symbols_to_fetch = load_watchlist()
        # Add alpha top 50
        alpha_path = DATA_DIR / "alpha_signals_latest.json"
        if alpha_path.exists():
            with open(alpha_path, "r", encoding="utf-8") as f:
                alpha_data = json.load(f)
            top50 = alpha_data.get("top_50", [])
            existing = {s["symbol"] for s in symbols_to_fetch}
            for item in top50:
                raw = item.get("symbol", "")
                clean = raw.replace("sh", "").replace("sz", "").replace(".SH", "").replace(".SZ", "")
                if clean not in existing:
                    symbols_to_fetch.append({"symbol": clean, "name": ""})
                    existing.add(clean)
        print(f"[TiMi Data] 总扫描标的: {len(symbols_to_fetch)} 只")

    if not symbols_to_fetch:
        print("[TiMi Data] 无扫描标的")
        return []

    # ── 批量获取行情 ──
    market_data_list = []
    n_fetched = 0
    n_failed = 0

    print(f"[TiMi Data] 获取日K线数据...")
    for item in symbols_to_fetch:
        sym = item["symbol"]
        name = item.get("name", "")

        # 格式化 symbol → "600036.SH"
        clean = sym.replace(".SH", "").replace(".SZ", "").replace("sh", "").replace("sz", "")
        if len(clean) != 6:
            continue

        if clean.startswith(("6", "9")):
            display_sym = f"{clean}.SH"
        else:
            display_sym = f"{clean}.SZ"

        df = fetch_daily_kline(clean)
        if df is not None:
            df = compute_indicators(df)
            md = build_market_data(display_sym, df, name)
            if md:
                market_data_list.append(md)
                n_fetched += 1
        else:
            n_failed += 1

        # 进度
        if (n_fetched + n_failed) % 50 == 0:
            print(f"  进度: {n_fetched + n_failed}/{len(symbols_to_fetch)} "
                  f"(成功 {n_fetched}, 失败 {n_failed})")

    print(f"[TiMi Data] 完成: {n_fetched} 个标的, {n_failed} 失败")

    # ── 保存 ──
    output = {
        "generated_at": datetime.now().isoformat(),
        "source": "akshare+baostock",
        "ticker_count": len(market_data_list),
        "market_data": market_data_list,
    }

    path = output_path or str(DATA_DIR / "timi_market_data.json")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[TiMi Data] 输出: {path}")

    return market_data_list


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TiMi 市场数据获取")
    parser.add_argument("--watchlist-only", action="store_true",
                        help="仅自选股 (默认)")
    parser.add_argument("--all", action="store_true",
                        help="自选 + alpha top 50")
    parser.add_argument("--tickers", type=str, default=None,
                        help="逗号分隔的代码列表")
    parser.add_argument("--output", type=str, default=None,
                        help="输出路径")

    args = parser.parse_args()

    tickers_list = None
    if args.tickers:
        tickers_list = [t.strip() for t in args.tickers.split(",")]

    watchlist_only = not args.all

    run(watchlist_only=watchlist_only, tickers=tickers_list,
        output_path=args.output)