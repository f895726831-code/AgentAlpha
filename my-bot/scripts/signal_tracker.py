#!/usr/bin/env python
"""ML Signal Tracker — 追踪 Top 20 alpha 信号 5 日表现 + 基准对比

用法: python scripts/signal_tracker.py [--report-only] [--backfill-benchmark]
  --report-only         仅生成报告，不更新追踪数据
  --backfill-benchmark  回填所有已关闭记录的历史基准收益（一次性操作）
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from statistics import mean, stdev

# Fix console encoding on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

SIGNAL_FILE = Path("D:/AgentAlpha/my-bot/data/output/alpha_signals.json")
LATEST_FILE = Path("D:/AgentAlpha/my-bot/data/output/alpha_signals_latest.json")
P1_SIGNAL_FILE = Path("D:/AgentAlpha/my-bot/data/output/alpha_signals_p1.json")
MANIFEST_FILE = Path("D:/AgentAlpha/my-bot/data/output/manifest.json")
TRACKER_FILE = Path("D:/AgentAlpha/my-bot/data/output/signal_tracker.json")
P1_TRACKER_FILE = Path("D:/AgentAlpha/my-bot/data/output/signal_tracker_p1.json")
BLACKLIST_FILE = Path("D:/AgentAlpha/my-bot/data/blacklist.json")
REPORT_FILE = Path("D:/AgentAlpha/my-bot/reports/signals/signal_tracker_report.md")
TRACK_TOP_N = 20
HOLD_DAYS = 5  # calendar days (≈4 trading days)
BLACKLIST_DAYS = 30  # 黑名单持续天数
BLACKLIST_LOSS_THRESHOLD = -15.0  # 5日亏损超过此阈值 → 加入黑名单

# QSF fallback (akshare → baostock for stock_zh_a_hist)
_QSF_ROOT = str(Path("D:/AgentAlpha/signal-factory"))
if _QSF_ROOT not in sys.path:
    sys.path.insert(0, _QSF_ROOT)


def symbol_to_windcode(symbol: str) -> str:
    if symbol.startswith("sh"):
        return symbol[2:] + ".SH"
    elif symbol.startswith("sz"):
        return symbol[2:] + ".SZ"
    return symbol


def windcode_to_plain(windcode: str) -> str:
    return windcode.split(".")[0]


def load_signals(path=SIGNAL_FILE):
    """加载 ML alpha 信号，优先通过 manifest.json 判断 staleness。"""
    if not path.exists():
        print(f"  ⚠️ {path} not found")
        return []

    # ── 优先使用 manifest.json 做快速 staleness 检查 ──
    manifest_key = None
    if path == SIGNAL_FILE:
        manifest_key = "alpha_signals"
    elif path == LATEST_FILE:
        manifest_key = "alpha_signals_latest"

    manifest = None
    if manifest_key and MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            pass

    if manifest:
        art = manifest.get("artifacts", {}).get(manifest_key, {})
        status = art.get("status", "unknown")
        excess = art.get("staleness", {}).get("excess_lag_days", -1) if art.get("staleness") else -1
        if status == "stale":
            print(f"  ⚠️ {path.name} stale (excess={excess}d per manifest)")
            # Try latest as fresher alternative
            if LATEST_FILE.exists() and path != LATEST_FILE:
                latest_art = manifest.get("artifacts", {}).get("alpha_signals_latest", {})
                latest_status = latest_art.get("status", "unknown")
                if latest_status != "stale":
                    print(f"  → Switching to alpha_signals_latest.json (status={latest_status})")
                    path = LATEST_FILE

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ── 兜底 staleness check（manifest 缺失或旧格式时走这里）──
    contract = data.get("@contract", {})
    staleness = contract.get("staleness", {})
    excess = staleness.get("excess_lag_days", -1)
    if excess < 0:
        # 旧格式，无 staleness 元数据
        pass
    elif excess > 2:
        print(f"  ⚠️ Signals stale: excess={excess}d "
              f"(as_of={contract.get('as_of_date', '?')}, "
              f"design={staleness.get('design_lag_trading_days', '?')}td)")
        # Try latest as fresher alternative
        if LATEST_FILE.exists() and path != LATEST_FILE:
            with open(LATEST_FILE, "r", encoding="utf-8") as f:
                latest_data = json.load(f)
            latest_excess = latest_data.get("@contract", {}).get("staleness", {}).get("excess_lag_days", 99)
            if latest_excess <= excess:
                print(f"  → Switching to alpha_signals_latest.json (excess={latest_excess}d)")
                data = latest_data

    scores = data.get("all_scores", {})
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:TRACK_TOP_N]
    return [
        {"symbol": s, "alpha": round(a, 4), "rank": i + 1}
        for i, (s, a) in enumerate(sorted_scores)
    ]


def load_p1_signals(path=P1_SIGNAL_FILE):
    """加载 Phase 1 因子共识信号（格式不同于 ML alpha）。

    P1 信号格式: {"date": "2026-11-05", "top_50": [{"symbol": "sh600157", "score": 1.0}, ...], ...}
    """
    if not path.exists():
        print(f"  ⚠️ P1 {path.name} not found")
        return [], None

    # Manifest staleness check
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            p1_art = manifest.get("artifacts", {}).get("alpha_signals_p1", {})
            status = p1_art.get("status", "unknown")
            excess = p1_art.get("staleness", {}).get("excess_lag_days", -1) if p1_art.get("staleness") else -1
            if status == "stale":
                print(f"  ⚠️ P1 signals stale (excess={excess}d), skipping")
                return [], None
        except Exception:
            pass

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    signal_date = data.get("date", "unknown")
    top_50 = data.get("top_50", data.get("top_10", []))

    signals = []
    for i, entry in enumerate(top_50[:TRACK_TOP_N]):
        score_val = entry.get("score", entry.get("alpha_score", 0))
        signals.append({
            "symbol": entry["symbol"],
            "score": round(score_val, 4),
            "rank": entry.get("rank", i + 1),
        })

    coverage = data.get("coverage_note", "")
    factors_used = data.get("factors_used", 0)
    factors_loaded = data.get("factors_loaded", 0)

    meta = {
        "date": signal_date,
        "coverage": coverage,
        "factors_used": factors_used,
        "factors_loaded": factors_loaded,
    }

    return signals, meta


def get_spot_data(codes: list[str]) -> dict:
    """Batch lookup stock names + latest prices.
    Tries akshare spot first, falls back to per-stock history, then Wind MCP.
    """
    result = {}

    # Method 1: akshare spot (fast batch)
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        code_set = set(codes)
        for _, row in df.iterrows():
            c = str(row["代码"])
            if c in code_set:
                try:
                    price = float(row["最新价"])
                except (ValueError, TypeError):
                    price = 0.0
                result[c] = {"name": str(row.get("名称", c)), "price": price}
        missing = [c for c in codes if c not in result]
        if not missing:
            return result
    except Exception as e:
        print(f"  akshare spot unavailable ({type(e).__name__}), trying per-stock...")
        missing = codes
        result = {}

    # Method 2: akshare/baostock per-stock history (two-tier fallback)
    still_missing = []
    from src.data.akshare_fallback import stock_zh_a_hist_with_fallback
    for code in missing:
        try:
            df = stock_zh_a_hist_with_fallback(symbol=code, period="daily", start_date="20260901",
                                               end_date="20260916", adjust="qfq")
            if df is not None and not df.empty:
                price = float(df["收盘"].iloc[-1])
                name = str(df.iloc[-1].get("股票名称", code)) if "股票名称" in df.columns else code
                result[code] = {"name": name, "price": price}
            else:
                still_missing.append(code)
        except Exception:
            still_missing.append(code)

    # Method 3: Wind MCP get_stock_quote (last resort, costs credits)
    for code in still_missing:
        try:
            windcode = f"{code}.{'SH' if code.startswith('6') else 'SZ'}"
            r = _wind_quote(windcode)
            if r:
                result[code] = r
            else:
                result[code] = {"name": code, "price": 0.0}
        except Exception:
            result[code] = {"name": code, "price": 0.0}

    return result


def _wind_quote(windcode: str) -> dict | None:
    """Single stock quote via Wind MCP CLI."""
    import subprocess
    cli = os.environ.get(
        "WIND_MCP_CLI",
        str(Path.home() / ".agents" / "skills" / "wind-mcp-skill" / "scripts" / "cli.mjs"),
    )
    repo_root = str(Path(__file__).resolve().parents[2])
    try:
        p = subprocess.run(
            ["node", cli, "call", "stock_data", "get_stock_quote",
             json.dumps({"windcode": windcode})],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, cwd=repo_root,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
        )
        if p.returncode != 0:
            return None
        data = json.loads(p.stdout)
        text = data.get("content", [{}])[0].get("text", "{}")
        inner = json.loads(text)
        rows = inner.get("data", {}).get("rows", [])
        cols = inner.get("data", {}).get("columns", [])
        if rows and cols:
            col_names = [c["name"] for c in cols]
            row = rows[-1]  # last minute = most recent tick
            d = dict(zip(col_names, row))
            # MATCH = latest trade price (from minute bar)
            price = float(d.get("MATCH", 0))
            name = windcode.split(".")[0]
            return {"name": name, "price": price}
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
# 基准收益计算 (CSI300 + CSI500, akshare → baostock fallback)
# ═══════════════════════════════════════════════════════════════

# 缓存已获取的基准数据 (start_date → {csi300_return, csi500_return})
_benchmark_cache: dict = {}


def _get_index_close(index_symbol: str, target_date: str, lookback_days: int = 5) -> float | None:
    """获取指数在 target_date 附近的收盘价（最近交易日向前回溯）。

    Args:
        index_symbol: "sh000300" (CSI300) 或 "sh000905" (CSI500)
        target_date: YYYY-MM-DD 目标日期
        lookback_days: 向前查找的天数

    Returns:
        收盘价 或 None
    """
    from src.data.akshare_fallback import stock_zh_index_daily_with_fallback

    start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=lookback_days + 10)).strftime("%Y%m%d")
    end = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y%m%d")

    try:
        import pandas as pd
        df = stock_zh_index_daily_with_fallback(symbol=index_symbol, start_date=start, end_date=end)
        if df is None or df.empty:
            return None
        # 兼容中文和英文列名（akshare 返回英文，baostock 返回中文）
        date_col = "日期" if "日期" in df.columns else "date"
        close_col = "收盘" if "收盘" in df.columns else "close"
        df[date_col] = pd.to_datetime(df[date_col])
        target = pd.Timestamp(target_date)
        before = df[df[date_col] <= target]
        if before.empty:
            return None
        return float(before[close_col].iloc[-1])
    except Exception as e:
        return None


def get_benchmark_returns(signal_date: str, hold_days: int = HOLD_DAYS) -> dict:
    """计算信号持有期间的 CSI300/CIS500 基准收益。

    Args:
        signal_date: YYYY-MM-DD 信号生成日
        hold_days: 持有天数

    Returns:
        {"csi300": return_pct, "csi500": return_pct, "source": "akshare"|"cached"|"failed"}
    """
    end_date_obj = datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=hold_days)
    end_date = end_date_obj.strftime("%Y-%m-%d")

    cache_key = f"{signal_date}_{hold_days}"
    if cache_key in _benchmark_cache:
        return {**_benchmark_cache[cache_key], "source": "cached"}

    import pandas as pd

    result = {"csi300": None, "csi500": None, "source": "akshare"}

    # CSI300
    entry = _get_index_close("sh000300", signal_date)
    exit_price = _get_index_close("sh000300", end_date)
    if entry and exit_price and entry > 0:
        result["csi300"] = round((exit_price - entry) / entry * 100, 2)

    # CSI500
    entry = _get_index_close("sh000905", signal_date)
    exit_price = _get_index_close("sh000905", end_date)
    if entry and exit_price and entry > 0:
        result["csi500"] = round((exit_price - entry) / entry * 100, 2)

    if result["csi300"] is None and result["csi500"] is None:
        result["source"] = "failed"

    _benchmark_cache[cache_key] = result
    return result


def load_tracker():
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"records": [], "stats": None, "updated": None}


def save_tracker(tracker):
    tracker["updated"] = datetime.now().isoformat()
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2, ensure_ascii=False)


def compute_stats(records: list) -> dict:
    closed = [r for r in records if r.get("return_5d") is not None]
    if not closed:
        return {"total_closed": 0, "avg_return_pct": 0, "win_rate_pct": 0,
                "max_gain_pct": 0, "max_loss_pct": 0, "sharpe_annual": 0,
                "total_tracked": len(records),
                "avg_excess_csi300": 0, "avg_excess_csi500": 0, "excess_win_csi300": 0}

    returns = [r["return_5d"] for r in closed]
    wins = [r for r in returns if r > 0]
    avg = mean(returns)
    std = stdev(returns) if len(returns) > 1 else 1.0
    sharpe = (avg / std) * (252 ** 0.5) if std > 0 else 0

    # 超额收益统计
    excess_csi300 = [r.get("excess_csi300") for r in closed if r.get("excess_csi300") is not None]
    excess_csi500 = [r.get("excess_csi500") for r in closed if r.get("excess_csi500") is not None]

    stats = {
        "total_closed": len(closed),
        "avg_return_pct": round(avg, 2),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1),
        "max_gain_pct": round(max(returns), 2),
        "max_loss_pct": round(min(returns), 2),
        "sharpe_annual": round(sharpe, 3),
        "total_tracked": len(records),
    }

    if excess_csi300:
        avg_ex = mean(excess_csi300)
        win_ex = len([x for x in excess_csi300 if x > 0]) / len(excess_csi300) * 100
        stats["avg_excess_csi300"] = round(avg_ex, 2)
        stats["excess_win_csi300"] = round(win_ex, 1)

    if excess_csi500:
        avg_ex = mean(excess_csi500)
        win_ex = len([x for x in excess_csi500 if x > 0]) / len(excess_csi500) * 100
        stats["avg_excess_csi500"] = round(avg_ex, 2)
        stats["excess_win_csi500"] = round(win_ex, 1)

    return stats


def update_tracker():
    print("Signal Tracker: loading signals...")
    signals = load_signals()
    if not signals:
        print("  No signals found, exiting.")
        return

    codes = [windcode_to_plain(symbol_to_windcode(s["symbol"])) for s in signals]
    unique_codes = list(set(codes))

    print(f"Signal Tracker: looking up {len(unique_codes)} stocks via akshare...")
    spot = get_spot_data(unique_codes)

    tracker = load_tracker()
    existing_keys = {(r["code"], r["signal_date"]) for r in tracker["records"]}
    today = datetime.now().strftime("%Y-%m-%d")
    new_count = 0

    for s in signals:
        code = windcode_to_plain(symbol_to_windcode(s["symbol"]))
        info = spot.get(code, {})
        name = info.get("name", code)
        price = info.get("price", 0.0)
        key = (code, today)
        if key not in existing_keys:
            tracker["records"].append({
                "code": code,
                "name": name,
                "alpha": s["alpha"],
                "rank": s["rank"],
                "signal_date": today,
                "entry_price": price,
                "status": "active",
                "current_price": None,
                "return_5d": None,
            })
            new_count += 1
            existing_keys.add(key)

    # Close matured signals (with benchmark comparison)
    closed_count = 0
    for r in tracker["records"]:
        if r.get("return_5d") is not None:
            continue
        sd = datetime.strptime(r["signal_date"], "%Y-%m-%d")
        if (datetime.now() - sd).days >= HOLD_DAYS:
            info = spot.get(r["code"], {})
            cp = info.get("price", 0.0)
            if cp > 0 and r.get("entry_price", 0) > 0:
                r["current_price"] = cp
                r["return_5d"] = round((cp - r["entry_price"]) / r["entry_price"] * 100, 2)
                r["status"] = "closed"
                # 基准对比
                bm = get_benchmark_returns(r["signal_date"])
                if bm["csi300"] is not None:
                    r["benchmark_csi300"] = bm["csi300"]
                    r["excess_csi300"] = round(r["return_5d"] - bm["csi300"], 2)
                if bm["csi500"] is not None:
                    r["benchmark_csi500"] = bm["csi500"]
                    r["excess_csi500"] = round(r["return_5d"] - bm["csi500"], 2)
                closed_count += 1

    tracker["stats"] = compute_stats(tracker["records"])
    save_tracker(tracker)

    s = tracker["stats"]
    print(f"  ✅ New: {new_count}  |  Matured: {closed_count}  |  Tracked: {s['total_tracked']}")
    if s["total_closed"] > 0:
        ex_msg = ""
        if s.get("avg_excess_csi300") is not None:
            ex_msg = f"  excess(vs300)={s['avg_excess_csi300']:+.2f}%  win={s['excess_win_csi300']:.0f}%"
        print(f"  📊 Closed {s['total_closed']}: avg={s['avg_return_pct']}%  win={s['win_rate_pct']}%  max↑{s['max_gain_pct']}%  max↓{s['max_loss_pct']}%  Sharpe={s['sharpe_annual']}{ex_msg}")

    # W46 #3: 将巨亏标的加入止损黑名单
    process_blacklist_updates(tracker["records"])

    return tracker

# ─── 止损黑名单 (W46 #3) ───

def load_blacklist(path=BLACKLIST_FILE):
    """加载黑名单，过期条目自动清理。"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    today = datetime.now().strftime("%Y-%m-%d")
    cleaned = {code: entry for code, entry in data.items()
               if entry.get("ban_until", "2000-01-01") >= today}
    if len(cleaned) != len(data):
        save_blacklist(cleaned, path)
    return cleaned


def save_blacklist(data, path=BLACKLIST_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def add_to_blacklist(code, return_5d, signal_date, path=BLACKLIST_FILE):
    """将亏损超过阈值的股票加入黑名单。"""
    blacklist = load_blacklist(path)
    today = datetime.now()
    ban_until = (today + timedelta(days=BLACKLIST_DAYS)).strftime("%Y-%m-%d")

    if code in blacklist:
        existing = blacklist[code]
        occurrences = existing.get("occurrences", 1) + 1
        current_ban = existing.get("ban_until", ban_until)
        extended_ban = (today + timedelta(days=BLACKLIST_DAYS + occurrences * 5)).strftime("%Y-%m-%d")
        ban_until = max(current_ban, extended_ban)
        blacklist[code] = {
            "ban_until": ban_until,
            "last_loss": round(return_5d, 2),
            "last_date": signal_date,
            "occurrences": occurrences,
            "reason": f"5d loss {return_5d:+.1f}% on {signal_date} (x{occurrences})",
        }
    else:
        blacklist[code] = {
            "ban_until": ban_until,
            "last_loss": round(return_5d, 2),
            "last_date": signal_date,
            "occurrences": 1,
            "reason": f"5d loss {return_5d:+.1f}% on {signal_date}",
        }

    save_blacklist(blacklist, path)
    return blacklist[code]


def process_blacklist_updates(records):
    """遍历新平仓的记录，将巨亏标的加入黑名单。"""
    blacklist = load_blacklist()
    added = []
    for r in records:
        if r.get("status") != "closed":
            continue
        ret = r.get("return_5d")
        if ret is not None and ret <= BLACKLIST_LOSS_THRESHOLD:
            code = r.get("code", "")
            sd = r.get("signal_date", "unknown")
            entry = add_to_blacklist(code, ret, sd)
            if entry["occurrences"] == 1:
                added.append(f"{code} (loss={ret:+.1f}%, ban until {entry['ban_until']})")
            else:
                added.append(f"{code} (loss={ret:+.1f}%, recur x{entry['occurrences']}, ban until {entry['ban_until']})")

    if added:
        print(f"\n  Blacklist: {len(added)} added/updated")
        for a in added:
            print(f"     {a}")

    return blacklist


def update_p1_tracker():
    """追踪 Phase 1 因子共识 Top 20 信号表现。"""
    print("\nP1 Signal Tracker: loading signals...")
    p1_signals, meta = load_p1_signals()
    if not p1_signals:
        print("  No P1 signals found, skipping.")
        return None

    if meta:
        print(f"  P1 date={meta['date']} coverage={meta['coverage']} "
              f"factors={meta['factors_used']}/{meta['factors_loaded']}")

    codes = [windcode_to_plain(symbol_to_windcode(s["symbol"])) for s in p1_signals]
    unique_codes = list(set(codes))

    print(f"P1 Signal Tracker: looking up {len(unique_codes)} stocks...")
    spot = get_spot_data(unique_codes)

    # Load or create P1 tracker
    if P1_TRACKER_FILE.exists():
        with open(P1_TRACKER_FILE, "r", encoding="utf-8") as f:
            p1_tracker = json.load(f)
    else:
        p1_tracker = {"records": [], "stats": None, "updated": None}

    existing_keys = {(r["code"], r["signal_date"]) for r in p1_tracker["records"]}
    signal_date = meta.get("date", datetime.now().strftime("%Y-%m-%d")) if meta else datetime.now().strftime("%Y-%m-%d")
    new_count = 0

    for s in p1_signals:
        code = windcode_to_plain(symbol_to_windcode(s["symbol"]))
        info = spot.get(code, {})
        name = info.get("name", code)
        price = info.get("price", 0.0)
        key = (code, signal_date)
        if key not in existing_keys:
            p1_tracker["records"].append({
                "code": code,
                "name": name,
                "score": s["score"],
                "rank": s["rank"],
                "signal_date": signal_date,
                "entry_price": price,
                "status": "active",
                "current_price": None,
                "return_5d": None,
            })
            new_count += 1
            existing_keys.add(key)

    # Close matured signals
    closed_count = 0
    for r in p1_tracker["records"]:
        if r.get("return_5d") is not None:
            continue
        sd = datetime.strptime(r["signal_date"], "%Y-%m-%d")
        if (datetime.now() - sd).days >= HOLD_DAYS:
            info = spot.get(r["code"], {})
            cp = info.get("price", 0.0)
            if cp > 0 and r.get("entry_price", 0) > 0:
                r["current_price"] = cp
                r["return_5d"] = round((cp - r["entry_price"]) / r["entry_price"] * 100, 2)
                r["status"] = "closed"
                # Benchmark comparison
                bm = get_benchmark_returns(r["signal_date"])
                if bm["csi300"] is not None:
                    r["benchmark_csi300"] = bm["csi300"]
                    r["excess_csi300"] = round(r["return_5d"] - bm["csi300"], 2)
                if bm["csi500"] is not None:
                    r["benchmark_csi500"] = bm["csi500"]
                    r["excess_csi500"] = round(r["return_5d"] - bm["csi500"], 2)
                closed_count += 1

    p1_tracker["stats"] = compute_stats(p1_tracker["records"])
    p1_tracker["updated"] = datetime.now().isoformat()
    P1_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(P1_TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(p1_tracker, f, indent=2, ensure_ascii=False)

    s = p1_tracker["stats"]
    print(f"  ✅ P1 New: {new_count}  |  Matured: {closed_count}  |  Tracked: {s['total_tracked']}")
    if s["total_closed"] > 0:
        ex_msg = ""
        if s.get("avg_excess_csi300") is not None:
            ex_msg = f"  excess(vs300)={s['avg_excess_csi300']:+.2f}%  win={s['excess_win_csi300']:.0f}%"
        print(f"  📊 P1 Closed {s['total_closed']}: avg={s['avg_return_pct']}%  win={s['win_rate_pct']}%  Sharpe={s['sharpe_annual']}{ex_msg}")

    return p1_tracker


def load_p1_tracker():
    """Load P1 tracker (read-only)."""
    if P1_TRACKER_FILE.exists():
        with open(P1_TRACKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"records": [], "stats": None, "updated": None}


def generate_report(tracker, p1_tracker=None):
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    stats = tracker["stats"] or {}
    records = tracker["records"]

    active = [r for r in records if r.get("status") == "active"]
    closed = [r for r in records if r.get("status") == "closed"]
    # Sort: most recent first
    active.sort(key=lambda r: r["signal_date"], reverse=True)
    closed.sort(key=lambda r: r["signal_date"], reverse=True)

    lines = [
        f"# {'ML + P1 ' if p1_tracker and p1_tracker.get('records') else 'ML '}信号追踪报告",
        "",
        f"> 更新: {tracker.get('updated', 'N/A')} | 追踪数量: Top {TRACK_TOP_N} | 持有期: ~5 交易日",
        "",
        "## 累计统计",
        "",
        f"| 指标 | 数值 |",
        f"|---|---|",
        f"| 已到期信号 | {stats.get('total_closed', 0)} 条 |",
        f"| 平均 5 日收益 | {stats.get('avg_return_pct', 0):+.2f}% |",
        f"| 胜率 | {stats.get('win_rate_pct', 0)}% |",
        f"| 最大单笔收益 | {stats.get('max_gain_pct', 0):+.2f}% |",
        f"| 最大单笔亏损 | {stats.get('max_loss_pct', 0):+.2f}% |",
        f"| 年化 Sharpe | {stats.get('sharpe_annual', 0):.3f} |",
        f"| 总追踪数 | {stats.get('total_tracked', 0)} 条 |",
    ]

    # 基准对比 (P1-5)
    if stats.get("avg_excess_csi300") is not None:
        lines += [
            "",
            "## 基准对比",
            "",
            "| 基准 | 超额收益 | 超额胜率 |",
            "|------|---------|---------|",
            f"| vs CSI300 | {stats['avg_excess_csi300']:+.2f}% | {stats['excess_win_csi300']:.0f}% |",
        ]
        if stats.get("avg_excess_csi500") is not None:
            lines.append(f"| vs CSI500 | {stats['avg_excess_csi500']:+.2f}% | {stats.get('excess_win_csi500', 0):.0f}% |")
        lines.append("")

    if closed:
        # 检查是否有基准数据
        has_benchmark = any(r.get("excess_csi300") is not None for r in closed)
        lines += [
            "## 已到期信号",
            "",
        ]
        if has_benchmark:
            lines.append("| 信号日 | 代码 | 名称 | Alpha | 5日收益 | CSI300 | 超额(vs300) |")
            lines.append("|--------|------|------|-------|---------|--------|-------------|")
            for r in closed[:50]:
                ex300 = f"{r.get('excess_csi300', 0):+.2f}%" if r.get('excess_csi300') is not None else "—"
                bm300 = f"{r.get('benchmark_csi300', 0):+.2f}%" if r.get('benchmark_csi300') is not None else "—"
                lines.append(
                    f"| {r['signal_date']} | {r['code']} | {r['name']} | {r['alpha']:.4f} | "
                    f"{r['return_5d']:+.2f}% | {bm300} | {ex300} |"
                )
        else:
            lines += [
                "| 信号日 | 代码 | 名称 | Alpha | 入场价 | 当前价 | 5日收益 |",
                "|--------|------|------|-------|--------|--------|---------|",
            ]
            for r in closed[:50]:
                lines.append(
                    f"| {r['signal_date']} | {r['code']} | {r['name']} | {r['alpha']:.4f} | "
                    f"{r['entry_price']:.2f} | {r.get('current_price', 0):.2f} | "
                    f"{r['return_5d']:+.2f}% |"
                )
        lines.append("")

    if active:
        lines += [
            "## 活跃追踪",
            "",
            "| 信号日 | 代码 | 名称 | Rank | Alpha | 入场价 | 持有天数 |",
            "|--------|------|------|------|-------|--------|----------|",
        ]
        today = datetime.now()
        for r in active[:30]:
            days = (today - datetime.strptime(r["signal_date"], "%Y-%m-%d")).days
            lines.append(
                f"| {r['signal_date']} | {r['code']} | {r['name']} | "
                f"#{r['rank']} | {r['alpha']:.4f} | {r['entry_price']:.2f} | "
                f"{days}d |"
            )
        lines.append("")

    # ── Phase 1 因子共识追踪 (v5.0) ──
    if p1_tracker and p1_tracker.get("records"):
        p1_records = p1_tracker["records"]
        p1_stats = p1_tracker.get("stats") or {}
        p1_active = [r for r in p1_records if r.get("status") == "active"]
        p1_closed = [r for r in p1_records if r.get("status") == "closed"]
        p1_active.sort(key=lambda r: r["signal_date"], reverse=True)
        p1_closed.sort(key=lambda r: r["signal_date"], reverse=True)

        lines += [
            "",
            "# Phase 1 因子共识信号追踪 (v5.0 FactorFactory)",
            "",
            f"> 模型: IC-weighted Z-score | 覆盖: 499 stocks | 持有期: ~5 交易日",
            "",
            "## P1 累计统计",
            "",
            f"| 指标 | 数值 |",
            f"|---|---|",
            f"| 已到期信号 | {p1_stats.get('total_closed', 0)} 条 |",
            f"| 平均 5 日收益 | {p1_stats.get('avg_return_pct', 0):+.2f}% |",
            f"| 胜率 | {p1_stats.get('win_rate_pct', 0)}% |",
            f"| 最大单笔收益 | {p1_stats.get('max_gain_pct', 0):+.2f}% |",
            f"| 最大单笔亏损 | {p1_stats.get('max_loss_pct', 0):+.2f}% |",
            f"| 年化 Sharpe | {p1_stats.get('sharpe_annual', 0):.3f} |",
            f"| 总追踪数 | {p1_stats.get('total_tracked', 0)} 条 |",
        ]

        if p1_stats.get("avg_excess_csi300") is not None:
            lines += [
                "",
                "## P1 基准对比",
                "",
                "| 基准 | 超额收益 | 超额胜率 |",
                "|------|---------|---------|",
                f"| vs CSI300 | {p1_stats['avg_excess_csi300']:+.2f}% | {p1_stats['excess_win_csi300']:.0f}% |",
            ]
            if p1_stats.get("avg_excess_csi500") is not None:
                lines.append(f"| vs CSI500 | {p1_stats['avg_excess_csi500']:+.2f}% | {p1_stats.get('excess_win_csi500', 0):.0f}% |")
            lines.append("")

        if p1_closed:
            has_bm = any(r.get("excess_csi300") is not None for r in p1_closed)
            lines += ["## P1 已到期信号", ""]
            if has_bm:
                lines.append("| 信号日 | 代码 | 名称 | Score | 5日收益 | CSI300 | 超额(vs300) |")
                lines.append("|--------|------|------|-------|---------|--------|-------------|")
                for r in p1_closed[:20]:
                    ex300 = f"{r.get('excess_csi300', 0):+.2f}%" if r.get('excess_csi300') is not None else "—"
                    bm300 = f"{r.get('benchmark_csi300', 0):+.2f}%" if r.get('benchmark_csi300') is not None else "—"
                    lines.append(
                        f"| {r['signal_date']} | {r['code']} | {r['name']} | {r['score']:.4f} | "
                        f"{r['return_5d']:+.2f}% | {bm300} | {ex300} |"
                    )
            else:
                lines.append("| 信号日 | 代码 | 名称 | Score | 5日收益 |")
                lines.append("|--------|------|------|-------|---------|")
                for r in p1_closed[:20]:
                    lines.append(f"| {r['signal_date']} | {r['code']} | {r['name']} | {r['score']:.4f} | {r['return_5d']:+.2f}% |")
            lines.append("")

        if p1_active:
            lines += [
                "## P1 活跃追踪",
                "",
                "| 信号日 | 代码 | 名称 | Rank | Score | 入场价 | 持有天数 |",
                "|--------|------|------|------|-------|--------|----------|",
            ]
            today_now = datetime.now()
            for r in p1_active[:20]:
                days = (today_now - datetime.strptime(r["signal_date"], "%Y-%m-%d")).days
                lines.append(
                    f"| {r['signal_date']} | {r['code']} | {r['name']} | "
                    f"#{r['rank']} | {r['score']:.4f} | {r['entry_price']:.2f} | "
                    f"{days}d |"
                )
            lines.append("")

    lines += [
        "---",
        "",
        "> SIGNAL_TRACKER_COMPLETE",
    ]

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  📄 Report: {REPORT_FILE}")


def backfill_benchmarks():
    """回填所有已关闭记录的历史基准收益（一次性操作）。

    遍历所有 status=="closed" 且缺少 benchmark 字段的记录，
    用 akshare/baostock 获取对应持有期的 CSI300/CIS500 基准收益并写入。
    """
    tracker = load_tracker()
    records = tracker["records"]
    closed = [r for r in records if r.get("status") == "closed" and r.get("return_5d") is not None]

    missing = [r for r in closed if r.get("excess_csi300") is None]
    if not missing:
        print(f"  ✅ All {len(closed)} closed records already have benchmark data")
        return tracker

    print(f"  Backfilling benchmark for {len(missing)}/{len(closed)} closed records...")
    filled = 0
    for r in missing:
        bm = get_benchmark_returns(r["signal_date"])
        if bm["csi300"] is not None:
            r["benchmark_csi300"] = bm["csi300"]
            r["excess_csi300"] = round(r["return_5d"] - bm["csi300"], 2)
        if bm["csi500"] is not None:
            r["benchmark_csi500"] = bm["csi500"]
            r["excess_csi500"] = round(r["return_5d"] - bm["csi500"], 2)
        if bm["source"] != "failed":
            filled += 1
        if filled % 20 == 0:
            print(f"    {filled}/{len(missing)}...")

    print(f"  ✅ Filled {filled}/{len(missing)}, failed {len(missing) - filled}")

    tracker["stats"] = compute_stats(records)
    save_tracker(tracker)
    return tracker


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ML/P1 Signal Tracker (v5.0)")
    parser.add_argument("--report-only", action="store_true", help="仅生成报告")
    parser.add_argument("--backfill-benchmark", action="store_true", help="回填历史基准收益")
    parser.add_argument("--p1-only", action="store_true", help="仅追踪 P1 信号")
    parser.add_argument("--ml-only", action="store_true", help="仅追踪 ML 信号")
    args = parser.parse_args()

    if args.backfill_benchmark:
        tracker = backfill_benchmarks()
        if tracker and tracker["records"]:
            generate_report(tracker)
    elif args.report_only:
        tracker = load_tracker()
        p1_tracker = load_p1_tracker() if not args.ml_only else None
        if tracker["records"] or (p1_tracker and p1_tracker.get("records")):
            generate_report(tracker, p1_tracker)
            s = tracker.get("stats", {})
            if s:
                print(f"  📊 ML: Closed {s.get('total_closed',0)}: avg={s.get('avg_return_pct',0)}%  win={s.get('win_rate_pct',0)}%")
            ps = (p1_tracker or {}).get("stats", {})
            if ps:
                print(f"  📊 P1: Closed {ps.get('total_closed',0)}: avg={ps.get('avg_return_pct',0)}%  win={ps.get('win_rate_pct',0)}%")
        else:
            print("  No tracker data yet.")
    else:
        if not args.p1_only:
            update_tracker()
        if not args.ml_only:
            try:
                p1_tracker = update_p1_tracker()
            except Exception as e:
                print(f"  ⚠ P1 tracking failed: {e}")
                import traceback
                traceback.print_exc()
                p1_tracker = None
        if not args.p1_only:
            # Regenerate combined report
            tracker = load_tracker()
            p1_tracker = load_p1_tracker() if not args.ml_only else None
            generate_report(tracker, p1_tracker)