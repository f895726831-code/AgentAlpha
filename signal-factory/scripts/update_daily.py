"""
增量数据更新脚本 — 将 Qlib 数据更新到最新交易日

数据源: akshare（免费，无额度限制）
  2026-09-14: 已移除 Wind MCP 回退逻辑，akshare 为唯一数据源。
  Wind MCP 因每日调用额度下调且有进一步收紧风险，不再用于自动化管线。

流程:
  1. 读取 calendars/day.txt 最后交易日
  2. 用 akshare 逐只下载增量日线数据
  3. 追加到 CSV 源文件
  4. 增量更新 bin 文件
  5. 更新日历

用法:
  python scripts/update_daily.py                  # 更新到今天
  python scripts/update_daily.py --end 2026-08-14 # 指定结束日期
  python scripts/update_daily.py --dry-run        # 仅检查，不写入
  python scripts/update_daily.py --symbols sh600519,sz000858  # 仅更新指定股票
"""

import os
import sys
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_QSF_ROOT))

from src.data.config_loader import (
    get_qlib_csv_dir,
    get_qlib_bin_dir,
    get_qlib_calendar_path,
    get_qlib_instruments_dir,
)

# ─── 路径配置 ───

CSV_DIR = Path(get_qlib_csv_dir())
BIN_DIR = Path(get_qlib_bin_dir())
FACTORS_DIR = _QSF_ROOT / "data" / "factors"
CAL_PATH = Path(get_qlib_calendar_path())
INSTRUMENTS_DIR = Path(get_qlib_instruments_dir())

# 所有字段
ALL_FIELDS = ["open", "close", "high", "low", "volume", "amount", "factor", "turn", "vwap", "change"]


def get_last_trading_day() -> Optional[str]:
    """获取日历中最后一个交易日"""
    if not CAL_PATH.exists():
        return None
    dates = []
    with open(CAL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and line != "1970-01-01":
                dates.append(line)
    return dates[-1] if dates else None


def get_stock_list(symbols_filter: Optional[List[str]] = None) -> List[str]:
    """获取要更新的股票列表"""
    if symbols_filter:
        return symbols_filter

    symbols = []
    for csv_path in sorted(CSV_DIR.glob("*.csv")):
        symbol = csv_path.stem
        # 跳过指数代码
        if symbol.startswith("sh000") or symbol.startswith("sz399"):
            continue
        if symbol.isdigit() and len(symbol) == 6 and (symbol.startswith("000") or symbol.startswith("399")):
            continue
        symbols.append(symbol)
    return symbols


def _batch_baostock_download(
    symbols: List[str],
    start_date: str,
    end_date: str,
) -> Dict[str, pd.DataFrame]:
    """批量 baostock 下载（单会话 + 自动重连），作为 akshare 大面积失败时的回退。

    baostock 单次登录可查询 ~30-50 只标的，之后需重连。
    此函数自动检测"用户未登录"错误并透明重连。
    """
    import baostock as bs

    results = {}
    errors = []
    total = len(symbols)
    bs_start = start_date.replace("-", "-") if "-" in start_date else f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    bs_end = end_date.replace("-", "-") if "-" in end_date else f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

    def _ensure_login():
        try:
            bs.logout()
        except Exception:
            pass
        lg = bs.login()
        return lg.error_code == "0"

    if not _ensure_login():
        print("  [ERROR] baostock batch login failed")
        return results

    relogin_count = 0
    processed = 0
    retry_stack = []
    i = 0

    while i < total or retry_stack:
        if retry_stack:
            symbol = retry_stack.pop(0)
        elif i < total:
            symbol = symbols[i]
            i += 1
        else:
            break

        code = symbol.lstrip("sh").lstrip("sz").lstrip("bj")
        bs_code = f"sh.{code}" if code.startswith(("6", "9")) else f"sz.{code}"

        try:
            fields = "date,open,high,low,close,volume,amount,turn,pctChg"
            rs = bs.query_history_k_data_plus(
                bs_code, fields,
                start_date=bs_start, end_date=bs_end,
                frequency="d", adjustflag="1",
            )

            if rs is None or rs.error_code != "0":
                err = "None" if rs is None else rs.error_msg
                if "未登录" in err or "login" in err.lower():
                    relogin_count += 1
                    if _ensure_login():
                        retry_stack.append(symbol)
                        continue
                errors.append((symbol, f"baostock: {err}"))
                continue

            rows = []
            while rs.next():
                rows.append(rs.get_row_data())

            processed += 1

            if not rows:
                continue

            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg"])
            df = df.rename(columns={
                "date": "日期", "open": "开盘", "high": "最高", "low": "最低",
                "close": "收盘", "volume": "成交量", "amount": "成交额",
                "turn": "换手率", "pctChg": "涨跌幅",
            })
            for col in ["开盘", "最高", "最低", "收盘", "成交量", "成交额", "换手率", "涨跌幅"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期"])
            if df.empty:
                continue

            df = _normalize_akshare_df(df)
            results[symbol] = df

            if processed % 10 == 0:
                print(f"    [batch bs: {processed}/{total}] data={len(results)}, relogin={relogin_count}")

        except Exception as e:
            errors.append((symbol, str(e)[:80]))

    try:
        bs.logout()
    except Exception:
        pass

    if errors:
        print(f"    batch baostock 失败 {len(errors)} 只: {[s for s, _ in errors[:5]]}{'...' if len(errors) > 5 else ''}")

    return results


def download_incremental(
    symbols: List[str],
    start_date: str,
    end_date: str,
    checkpoint_every: int = 50,
    dry_run: bool = False,
) -> tuple[Dict[str, pd.DataFrame], Set[str]]:
    """
    下载增量日线数据（akshare 优先，baostock 回退）。

    2026-11-03: 增加断点续传（每 N 只存盘）+ 批量 baostock 回退（单会话自动重连）。

    返回:
        (remaining_data, downloaded_dates)
        - remaining_data: 未写入磁盘的剩余数据（通常为空，断点续传已写入）
        - downloaded_dates: 本次下载覆盖的所有日期 (YYYY-MM-DD)，供日历更新使用
    """
    import time as _time
    from src.data.akshare_fallback import stock_zh_a_hist_with_fallback

    results = {}
    errors = []
    total = len(symbols)
    checkpoint_buffer = {}
    appended_total = 0
    skipped_total = 0
    downloaded_dates: Set[str] = set()  # 收集所有下载到的日期，供日历更新

    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    consecutive_ak_failures = 0
    AK_FAILURE_THRESHOLD = 10
    switched_to_batch_bs = False

    def _flush_checkpoint():
        """将累积的数据写入磁盘"""
        nonlocal appended_total, skipped_total
        if not checkpoint_buffer:
            return
        for sym, df in checkpoint_buffer.items():
            status = _append_one_symbol_to_csv(sym, df, dry_run=dry_run)
            if status == "appended":
                appended_total += 1
            else:
                skipped_total += 1
        checkpoint_buffer.clear()

    for i, symbol in enumerate(symbols):
        if switched_to_batch_bs:
            remaining = symbols[i:]
            print(f"\n  [批量 baostock 模式] 剩余 {len(remaining)} 只标的...")
            batch_results = _batch_baostock_download(remaining, start_date, end_date)
            for sym, df in batch_results.items():
                # 收集日期
                if "date" in df.columns:
                    for d in df["date"]:
                        downloaded_dates.add(pd.Timestamp(d).strftime("%Y-%m-%d"))
                status = _append_one_symbol_to_csv(sym, df, dry_run=dry_run)
                if status == "appended":
                    appended_total += 1
                else:
                    skipped_total += 1
            print(f"  断点续传: 已写入 {appended_total} 只, 跳过 {skipped_total} 只")
            return {}, downloaded_dates

        code = symbol.lstrip("sh").lstrip("sz").lstrip("bj")
        print(f"  [{i+1}/{total}] {symbol} ...", end=" ", flush=True)

        df = stock_zh_a_hist_with_fallback(
            symbol=code,
            start_date=start_fmt,
            end_date=end_fmt,
            adjust="",
        )

        if df is not None and not df.empty:
            df = _normalize_akshare_df(df)
            checkpoint_buffer[symbol] = df
            # 收集日期
            if "date" in df.columns:
                for d in df["date"]:
                    downloaded_dates.add(pd.Timestamp(d).strftime("%Y-%m-%d"))
            consecutive_ak_failures = 0
            source = "ak" if len(df.columns) >= 9 else "bs"
            print(f"{len(df)}条 [{source}]")
        elif df is not None and df.empty:
            print("无新数据")
            consecutive_ak_failures = 0
        else:
            print("失败（akshare+baostock均不可用）")
            errors.append((symbol, "akshare+baostock both failed"))
            consecutive_ak_failures += 1

            if consecutive_ak_failures >= AK_FAILURE_THRESHOLD and not switched_to_batch_bs:
                print(f"\n  ⚠ 连续 {consecutive_ak_failures} 只失败，切换批量 baostock 模式...")
                switched_to_batch_bs = True
                _flush_checkpoint()
                continue

        if len(checkpoint_buffer) >= checkpoint_every:
            _flush_checkpoint()
            print(f"    [断点] 已写入 {appended_total} 只, 跳过 {skipped_total} 只")

        if i < total - 1 and not switched_to_batch_bs:
            _time.sleep(0.3)

    _flush_checkpoint()

    ok = appended_total + len(checkpoint_buffer)
    print(f"\n  下载汇总: 成功 {ok}/{total}, 失败 {len(errors)}")
    if appended_total > 0:
        print(f"  断点续传: 已写入 {appended_total} 只, 跳过 {skipped_total} 只")
    if errors:
        print("  失败列表:")
        for sym, err in errors[:10]:
            print(f"    {sym}: {err}")
        if len(errors) > 10:
            print(f"    ... 还有 {len(errors) - 10} 只")

    return checkpoint_buffer, downloaded_dates


def _load_factor_for_symbol(symbol: str) -> Optional[pd.Series]:
    """从缓存加载单只标的的每日复权因子"""
    factor_path = FACTORS_DIR / f"{symbol}.csv"
    if not factor_path.exists():
        return None
    df = pd.read_csv(factor_path, index_col=0, parse_dates=True)
    return df["backAdjustFactor"]


def _ensure_factor_column(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    为 DataFrame 添加正确的 factor 列。

    优先从 data/factors/ 缓存读取，缓存缺失则 fallback=1.0。
    单只失败不影响全局（factor=1.0 回退）。
    """
    df = df.copy()
    try:
        factor_series = _load_factor_for_symbol(symbol)
    except Exception:
        factor_series = None

    if factor_series is not None:
        try:
            # 对齐日期：df 中的每个日期取对应的 factor（前向填充）
            factor_aligned = factor_series.reindex(
                pd.to_datetime(df["date"]).values, method="ffill"
            )
            df["factor"] = factor_aligned.fillna(1.0).values
        except Exception:
            df["factor"] = 1.0
    else:
        df["factor"] = 1.0

    return df


def _update_factors_daily(symbols: List[str]) -> int:
    """
    检查复权因子是否有更新（新的除权除息事件）。

    从 baostock 查询最新因子事件，更新本地缓存。
    返回: 更新了的标的数量
    """
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        print(f"  baostock 登录失败，跳过因子更新")
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    updated = 0

    for symbol in symbols:
        factor_path = FACTORS_DIR / f"{symbol}.csv"
        if not factor_path.exists():
            continue

        # 读取已有因子的最后日期
        try:
            existing = pd.read_csv(factor_path, parse_dates=["date"], index_col="date")
            last_date = existing.index.max().strftime("%Y-%m-%d")
        except Exception:
            continue

        # 查询 baostock 是否有新的事件
        try:
            code = f"{symbol[:2]}.{symbol[2:]}"
            rs = bs.query_adjust_factor(code=code, start_date=last_date, end_date=today)
            if rs.error_code != "0":
                continue
            new_events = rs.get_data()
            if new_events.empty:
                continue

            new_events["date"] = pd.to_datetime(new_events["dividOperateDate"])
            new_events["backAdjustFactor"] = pd.to_numeric(
                new_events["backAdjustFactor"], errors="coerce"
            )

            # 合并已有和新增
            combined = pd.concat([existing, new_events.set_index("date")[["backAdjustFactor"]]])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
            combined.to_csv(factor_path, header=["backAdjustFactor"])

            updated += 1
        except Exception:
            continue

    bs.logout()
    if updated > 0:
        print(f"  复权因子更新: {updated} 只标的")
    return updated


def _append_one_symbol_to_csv(symbol: str, new_df: pd.DataFrame, dry_run: bool = False):
    """将单只标的的增量数据追加到 CSV（断点续传用）"""
    if new_df.empty:
        return "skipped"

    csv_path = CSV_DIR / f"{symbol}.csv"
    if not csv_path.exists():
        if not dry_run:
            new_df["symbol"] = symbol
            new_df = _ensure_factor_column(new_df, symbol)
            cols = ["symbol", "date"] + [c for c in ALL_FIELDS if c in new_df.columns]
            available = [c for c in cols if c in new_df.columns]
            new_df[available].to_csv(csv_path, index=False)
        return "appended"

    existing = pd.read_csv(csv_path, parse_dates=["date"])
    existing_dates = set(existing["date"].dt.strftime("%Y-%m-%d"))
    new_only = new_df[~new_df["date"].dt.strftime("%Y-%m-%d").isin(existing_dates)]

    if new_only.empty:
        return "skipped"

    if not dry_run:
        new_only = _ensure_factor_column(new_only, symbol)
        new_only["symbol"] = symbol
        combined = pd.concat([existing, new_only], ignore_index=True)
        combined = combined.sort_values("date")
        if "symbol" in combined.columns:
            combined["symbol"] = combined["symbol"].fillna(symbol)
        else:
            combined["symbol"] = symbol
        if "factor" not in combined.columns:
            combined["factor"] = 1.0
        combined["factor"] = combined["factor"].fillna(1.0)
        cols = ["symbol", "date"] + [c for c in ALL_FIELDS if c in combined.columns]
        available = [c for c in cols if c in combined.columns]
        combined[available].to_csv(csv_path, index=False)

    return "appended"


def _normalize_akshare_df(df: pd.DataFrame) -> pd.DataFrame:
    """标准化 akshare DataFrame（中文列名 → 英文列名，volume 单位检测）"""
    df = df.copy()

    # 如果已经是英文列名（Wind MCP 数据），直接转换 date 列
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_localize(None)
    else:
        # akshare 中文列名 — 先转换 date 再重命名
        if "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"])
        df = df.rename(columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turn",
        })

    # 只保留需要的列
    available = [c for c in ["date", "open", "close", "high", "low", "volume", "amount", "turn", "vwap"] if c in df.columns]
    df = df[available]

    # ── Volume 单位检测与转换 ──
    if "volume" in df.columns and len(df) > 0:
        vol_max = df["volume"].max()
        # akshare 返回手（1手=100股），Qlib 用股
        # 正常 A 股日成交量在百万股级别，若 max < 1000000 则很可能是手
        if vol_max < 1_000_000:
            df["volume"] = df["volume"] * 100
            print(f"[vol×100]", end="", flush=True)

    # ── VWAP 计算 ──
    # akshare/baostock 不直接提供 vwap，从 amount/volume 推算
    if "vwap" not in df.columns and "amount" in df.columns and "volume" in df.columns:
        vol_safe = df["volume"].replace(0, np.nan)
        df["vwap"] = (df["amount"] / vol_safe).round(2)
        # 成交量=0 的日期 VWAP 设为 NaN
        df.loc[df["volume"] == 0, "vwap"] = np.nan

    return df


def append_to_csvs(
    new_data: Dict[str, pd.DataFrame],
    dry_run: bool = False,
):
    """将增量数据追加到现有 CSV 文件"""
    appended = 0
    skipped = 0

    for symbol, new_df in new_data.items():
        if new_df.empty:
            skipped += 1
            continue

        csv_path = CSV_DIR / f"{symbol}.csv"
        if not csv_path.exists():
            # 新股票，创建 CSV
            if not dry_run:
                new_df["symbol"] = symbol
                # 确保有正确的 factor 列
                new_df = _ensure_factor_column(new_df, symbol)
                cols = ["symbol", "date"] + [c for c in ALL_FIELDS if c in new_df.columns]
                available = [c for c in cols if c in new_df.columns]
                new_df[available].to_csv(csv_path, index=False)
            appended += 1
            continue

        # 读取现有 CSV
        existing = pd.read_csv(csv_path, parse_dates=["date"])

        # 合并去重
        existing_dates = set(existing["date"].dt.strftime("%Y-%m-%d"))
        new_only = new_df[~new_df["date"].dt.strftime("%Y-%m-%d").isin(existing_dates)]

        if new_only.empty:
            skipped += 1
            continue

        # 追加
        if not dry_run:
            # 确保有正确的 factor 列
            new_only = _ensure_factor_column(new_only, symbol)
            # 确保 new_only 有 symbol 列（新旧 CSV 格式可能不一致）
            new_only["symbol"] = symbol
            combined = pd.concat([existing, new_only], ignore_index=True)
            combined = combined.sort_values("date")
            if "symbol" in combined.columns:
                combined["symbol"] = combined["symbol"].fillna(symbol)
            else:
                combined["symbol"] = symbol
            if "factor" not in combined.columns:
                combined["factor"] = 1.0
            combined["factor"] = combined["factor"].fillna(1.0)
            cols = ["symbol", "date"] + [c for c in ALL_FIELDS if c in combined.columns]
            available = [c for c in cols if c in combined.columns]
            combined[available].to_csv(csv_path, index=False)

        appended += 1
        print(f"  {symbol}: +{len(new_only)} 条 ({new_only['date'].min().strftime('%Y-%m-%d')} ~ {new_only['date'].max().strftime('%Y-%m-%d')})")

    print(f"  追加: {appended}, 跳过: {skipped}")


def update_calendar(end_date: str, new_data: Optional[Dict[str, pd.DataFrame]] = None, dry_run: bool = False, downloaded_dates: Optional[Set[str]] = None):
    """更新日历文件，优先从实际下载数据中推断交易日

    2026-11-03: 增加 downloaded_dates 参数，支持断点续传后的日历更新。
    downloaded_dates 来自 download_incremental 返回的日期集合（数据已写入磁盘但日期仍需记录）。
    """
    if not CAL_PATH.exists():
        return

    # 读取现有日历
    existing_dates = set()
    with open(CAL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and line != "1970-01-01":
                existing_dates.add(line)

    last_date = max(existing_dates) if existing_dates else "2015-01-01"
    end_dt = pd.Timestamp(end_date)
    start_dt = pd.Timestamp(last_date) + pd.Timedelta(days=1)

    if start_dt > end_dt:
        print("  日历已是最新")
        return

    # ── 优先从实际下载数据中推断交易日 ──
    new_date_strs = []
    actual_dates = set()
    source = ""

    # 优先从 downloaded_dates 获取（断点续传路径）
    if downloaded_dates:
        for ds in downloaded_dates:
            if ds not in existing_dates and start_dt <= pd.Timestamp(ds) <= end_dt:
                actual_dates.add(ds)
        source = "断点续传日期"

    # 补充 new_data 中的日期（非断点续传或最后一批残余）
    if new_data:
        for df in new_data.values():
            if df is not None and not df.empty and "date" in df.columns:
                for d in df["date"]:
                    ds = pd.Timestamp(d).strftime("%Y-%m-%d")
                    if ds not in existing_dates and start_dt <= pd.Timestamp(ds) <= end_dt:
                        actual_dates.add(ds)
        if not source:
            source = "实际数据"
        else:
            source += "+实际数据"

    new_date_strs = sorted(actual_dates)

    # ── 不添加无实际数据的日期 ──
    if not new_date_strs:
        print("  ⚠ 无实际下载数据，跳过日历更新（不添加猜测日期）")
        return

    if new_date_strs:
        if not dry_run:
            with open(CAL_PATH, "a", encoding="utf-8") as f:
                for d in new_date_strs:
                    f.write(d + "\n")
        print(f"  新增 {len(new_date_strs)} 个交易日 ({source}) ({new_date_strs[0]} ~ {new_date_strs[-1]})")
    else:
        print("  无新增交易日")


def update_instruments(new_data: Dict[str, pd.DataFrame], dry_run: bool = False):
    """更新 instruments 文件"""
    all_path = INSTRUMENTS_DIR / "all.txt"
    if not all_path.exists():
        return

    # 读取现有 instruments
    existing = {}
    with open(all_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if parts:
                existing[parts[0]] = line

    # 追加新股票
    new_entries = []
    for symbol, df in new_data.items():
        if symbol not in existing and not df.empty:
            start = df["date"].min().strftime("%Y-%m-%d")
            end = df["date"].max().strftime("%Y-%m-%d")
            new_entries.append(f"{symbol}\t{start}\t{end}")

    if new_entries:
        if not dry_run:
            with open(all_path, "a", encoding="utf-8") as f:
                for entry in new_entries:
                    f.write(entry + "\n")
        print(f"  instruments/all.txt: +{len(new_entries)} 只")


def incremental_dump_bin(new_market: str = None):
    """增量更新 bin 文件"""
    from src.data.wind_bridge import WindBridge

    bridge = WindBridge(csv_dir=str(CSV_DIR), bin_dir=str(BIN_DIR))
    bridge.dump_to_bin(incremental=True, new_market=new_market)
    print("  Bin 增量更新完成")


def verify_bin_integrity() -> bool:
    """Spot-check bin file integrity against calendar.

    Verifies that a sample of stocks have valid close data for all calendar dates.
    Returns True if no issues found.
    """
    if not BIN_DIR.exists() or not CAL_PATH.exists():
        return True

    cal_dates = []
    with open(CAL_PATH, "r", encoding="utf-8") as f:
        cal_dates = [line.strip() for line in f if line.strip() and line.strip() != "1970-01-01"]

    if not cal_dates:
        return True

    # Use first 5 CSI500 stocks as sample
    sample_stocks = []
    csi500_path = Path("D:/qlib/qlib_data/cn_data/instruments/csi500.txt")
    if csi500_path.exists():
        with open(csi500_path, "r", encoding="utf-8") as f:
            sample_stocks = [line.strip().split("\t")[0] for line in f if line.strip()][:5]
    if not sample_stocks:
        sample_stocks = ["sh600004", "sh600008", "sh600021", "sh600032", "sh600038"]
    issues = []

    for stock in sample_stocks:
        fp = BIN_DIR / stock / "close.day.bin"
        if not fp.exists():
            issues.append(f"{stock}: bin file not found")
            continue
        data = np.fromfile(fp, dtype="<f")
        if len(data) <= 1:
            issues.append(f"{stock}: bin file empty")
            continue
        sidx = int(data[0])
        n_need = len(cal_dates) - sidx
        n_have = len(data) - 1
        if n_have < n_need:
            issues.append(f"{stock}: bin shorter than calendar (have={n_have}, need={n_need}, gap={n_need - n_have})")
        else:
            # Check the value at the latest calendar index — not the tail (may have pre-allocated padding)
            latest_idx = len(cal_dates) - 1
            rel_idx = latest_idx - sidx
            if rel_idx >= 0 and rel_idx < n_have:
                if np.isnan(data[1 + rel_idx]):
                    issues.append(f"{stock}: close is NaN at latest calendar date {cal_dates[-1]} (idx={rel_idx})")

    if issues:
        print("\n  ⚠️  BIN INTEGRITY WARNING:")
        for issue in issues:
            print(f"    - {issue}")
        print("  Possible causes: data download failed, bin conversion error, or calendar update without data.")
        return False
    else:
        print(f"  ✅ Bin integrity check passed ({len(sample_stocks)} stocks spot-checked)")
        return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="增量数据更新脚本 (akshare)")
    parser.add_argument("--end", help="结束日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--symbols", help="仅更新指定股票 (逗号分隔)")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不写入")
    parser.add_argument("--skip-bin", action="store_true", help="跳过 bin 更新")
    parser.add_argument("--skip-calendar", action="store_true", help="跳过日历更新（分批回填时使用）")
    parser.add_argument("--new-market", help="新股票注册到的 market 名称 (如 csi500)")
    args = parser.parse_args()

    end_date = args.end or datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"增量数据更新 → {end_date}")
    try:
        from src.utils.akshare_version import get_installed_version
        print(f"akshare: {get_installed_version()}")
    except Exception:
        pass
    print("=" * 60)

    # Step 1: 检查最后交易日
    last_date = get_last_trading_day()
    print(f"\n[1/4] 最后交易日: {last_date}")

    if last_date and last_date >= end_date:
        print(f"  数据已是最新 ({last_date} >= {end_date})，无需更新")
        return

    # Step 2: 获取股票列表
    symbols_filter = args.symbols.split(",") if args.symbols else None
    symbols = get_stock_list(symbols_filter)
    print(f"\n[2/5] 待更新股票: {len(symbols)} 只")

    # Step 2.5: 更新复权因子（检测新的除权除息事件）
    print(f"\n[2.5/5] 更新复权因子...")
    factor_updated = _update_factors_daily(symbols) if FACTORS_DIR.exists() else 0
    if factor_updated == 0 and FACTORS_DIR.exists():
        print(f"  复权因子无变化")

    # Step 3: 下载增量数据（内置断点续传 + 批量 baostock 回退）
    start_date = (pd.Timestamp(last_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") if last_date else "2015-01-01"
    print(f"\n[3/5] 下载增量数据 ({start_date} ~ {end_date})...")
    new_data, downloaded_dates = download_incremental(symbols, start_date, end_date, dry_run=args.dry_run)

    # Step 4: 追加剩余未写入数据（断点续传已写入大部分，此处处理最后 <50 只的尾巴）
    if new_data:
        print(f"\n[4/5] 追加剩余数据 ({len(new_data)} 只)...")
        append_to_csvs(new_data, dry_run=args.dry_run)
    else:
        print(f"\n[4/5] 数据已全部通过断点续传写入，跳过")

    # 增量更新 bin（必须在 update_calendar 之前，否则日历已更新导致 bin 不扩展）
    if not args.skip_bin and not args.dry_run:
        print("\n增量更新 bin 文件...")
        incremental_dump_bin(new_market=args.new_market)
        verify_bin_integrity()

    # 更新日历和 instruments（在 bin 更新之后，补足 bin 未覆盖的日期）
    if not args.skip_calendar:
        update_calendar(end_date, new_data, dry_run=args.dry_run, downloaded_dates=downloaded_dates)
    update_instruments(new_data, dry_run=args.dry_run)

    print("\n" + "=" * 60)
    print(f"更新完成 → {end_date}")
    print(f"  覆盖日期: {len(downloaded_dates)} 个交易日")
    print("=" * 60)


if __name__ == "__main__":
    main()