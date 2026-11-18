"""
akshare 降级工具 — 为使用 stock_zh_a_hist 的脚本提供 baostock 回退。

东方财富 API 对 akshare IP 级阻断时，自动切换到 baostock（证监会数据源）。
baostock 提供相同字段：date, open, high, low, close, volume, amount, turn, pct_chg。

用法:
    from src.data.akshare_fallback import stock_zh_a_hist_with_fallback

    df = stock_zh_a_hist_with_fallback(
        symbol="600036",
        start_date="20240101",
        end_date="20240924",
        adjust="qfq",
    )
"""

import time
from typing import Optional

import pandas as pd


def stock_zh_a_hist_with_fallback(
    symbol: str,
    period: str = "daily",
    start_date: str = "",
    end_date: str = "",
    adjust: str = "qfq",
    max_retries: int = 2,
) -> Optional[pd.DataFrame]:
    """获取 A 股日线数据，akshare → baostock 两级降级。

    Args:
        symbol: 纯数字代码，如 "600036"
        period: "daily" 固定
        start_date: YYYYMMDD 起始日
        end_date: YYYYMMDD 结束日
        adjust: 复权方式，"qfq" 前复权 / "" 不复权
        max_retries: akshare 重试次数

    Returns:
        pd.DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 换手率, 涨跌幅
        Returns None if all data sources fail.
    """
    # ── Tier 1: akshare ──
    for attempt in range(max_retries):
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (2 ** attempt) * 2
                print(f"    akshare retry {attempt+2}/{max_retries} ({wait}s)...", end=" ", flush=True)
                time.sleep(wait)
            else:
                print(f"    akshare failed: {str(e)[:80]}")

    # ── Tier 2: baostock ──
    print(f"    Trying baostock fallback for {symbol}...", end=" ", flush=True)
    try:
        import baostock as bs
        import numpy as np

        # Map symbol to baostock exchange code
        if symbol.startswith(("6", "9")):
            bs_code = f"sh.{symbol}"
        else:
            bs_code = f"sz.{symbol}"

        # Convert YYYYMMDD → YYYY-MM-DD for baostock
        bs_start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
        bs_end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date

        lg = bs.login()
        if lg.error_code != "0":
            print(f"baostock login failed: {lg.error_msg}")
            return None

        fields = "date,open,high,low,close,volume,amount,turn,pctChg"
        rs = bs.query_history_k_data_plus(
            bs_code,
            fields,
            start_date=bs_start,
            end_date=bs_end,
            frequency="d",
            adjustflag="2" if adjust == "qfq" else "1",
        )

        if rs is None or rs.error_code != "0":
            err_msg = "query returned None" if rs is None else rs.error_msg
            print(f"baostock query failed: {err_msg}")
            bs.logout()
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        bs.logout()

        if not rows:
            print("baostock returned 0 rows")
            return None

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg"])

        # Normalize to akshare column names
        df = df.rename(columns={
            "date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
            "amount": "成交额",
            "turn": "换手率",
            "pctChg": "涨跌幅",
        })

        # Convert types
        for col in ["开盘", "最高", "最低", "收盘", "成交量", "成交额", "换手率", "涨跌幅"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")

        df = df.dropna(subset=["日期"])
        print(f"+{len(df)}条 (baostock)")
        return df

    except Exception as e:
        print(f"baostock also failed: {str(e)[:80]}")
        return None


def stock_zh_index_daily_with_fallback(
    symbol: str,
    start_date: str = "",
    end_date: str = "",
) -> Optional[pd.DataFrame]:
    """获取 A 股指数日线数据，akshare → baostock 两级降级。

    Args:
        symbol: akshare 格式指数代码，如 "sh000688" (科创50), "sz399307" (中证转债)
        start_date: YYYYMMDD 起始日
        end_date: YYYYMMDD 结束日

    Returns:
        pd.DataFrame with normalized OHLCV columns.
    """
    # ── Tier 1: akshare ──
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        print(f"    akshare index failed for {symbol}: {str(e)[:80]}")

    # ── Tier 2: baostock ──
    bs_code_map = {
        "sh000688": "sh.000688",   # 科创50
        "sz399307": "sz.399307",   # 中证转债
        "sh000300": "sh.000300",   # 沪深300
        "sh000905": "sh.000905",   # 中证500
        "sz399006": "sz.399006",   # 创业板指
    }
    bs_code = bs_code_map.get(symbol)
    if bs_code is None:
        # Try to convert: sh000688 → sh.000688
        if symbol.startswith("sh"):
            bs_code = f"sh.{symbol[2:]}"
        elif symbol.startswith("sz"):
            bs_code = f"sz.{symbol[2:]}"
        else:
            print(f"    Cannot map {symbol} to baostock code")
            return None

    print(f"    Trying baostock index fallback: {bs_code}...", end=" ", flush=True)
    try:
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            print(f"login failed: {lg.error_msg}")
            return None

        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
        )

        if rs.error_code != "0":
            print(f"query failed: {rs.error_msg}")
            bs.logout()
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        bs.logout()

        if not rows:
            print("0 rows")
            return None

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.rename(columns={"date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量", "amount": "成交额"})
        df["pct_change"] = df["收盘"].pct_change() * 100
        print(f"+{len(df)}条 (baostock)")
        return df

    except Exception as e:
        print(f"baostock failed: {str(e)[:80]}")
        return None