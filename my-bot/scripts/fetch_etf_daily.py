#!/usr/bin/env python3
"""
fetch_etf_daily.py — 11类资产ETF日终表现预生成

使用 akshare fund_etf_spot_ths（同花顺端点，不走东财，不被封锁）
获取 ETF 实时净值/涨跌幅。不在此列表中的 LOF 标的（华宝油气 162411、
印度基金 164824）从 macro_data.py 的 ETF K 线缓存推算日收益。

替代失效的 WebSearch 方案。

用法:
  python scripts/fetch_etf_daily.py                        # 输出并写入 _etf_daily.json
  python scripts/fetch_etf_daily.py --no-output             # 仅打印
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ── 路径常亮 ────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_OUTPUT = PROJECT_ROOT / "data" / "output"
MACRO_DATA_DIR = Path("D:/AgentAlpha/signal-factory/data/macro_assets")

# ── 11 类资产 ETF/LOF 映射 ──────────────────────────────────────

# ETF（可从 THS spot 获取）
ETF_ASSETS: Dict[str, Dict[str, str]] = {
    "gold":     {"code": "518880", "name": "黄金ETF",       "category": "commodity"},
    "bond":     {"code": "511010", "name": "国债ETF",       "category": "bond"},
    "nasdaq":   {"code": "513100", "name": "纳指ETF",       "category": "overseas"},
    "hk_tech":  {"code": "513130", "name": "恒生科技ETF",    "category": "overseas"},
    "a_share":  {"code": "159919", "name": "沪深300ETF",     "category": "equity"},
    "star_50":  {"code": "588000", "name": "科创50ETF",      "category": "equity"},
    "nikkei":   {"code": "513880", "name": "日经225ETF",     "category": "overseas"},
    "cb_bond":  {"code": "511380", "name": "可转债ETF",      "category": "bond"},
}

# LOF（THS ETF spot 不包含，需从 K 线推算）
LOF_ASSETS: Dict[str, Dict[str, str]] = {
    "oil":      {"code": "162411", "name": "华宝油气LOF",    "category": "commodity"},
    "india":    {"code": "164824", "name": "印度基金LOF",    "category": "overseas"},
}

# ── 核心逻辑 ────────────────────────────────────────────────────


def _fetch_from_ths_spot() -> Tuple[pd.DataFrame, Dict[str, str], str]:
    """Call fund_etf_spot_ths, filter to our ETF codes, return (DataFrame, col_map, data_date)."""
    import akshare as ak

    raw = ak.fund_etf_spot_ths()
    cols = list(raw.columns)

    code_col = next((c for c in cols if "代码" in c), cols[1])
    name_col = next((c for c in cols if "简称" in c or "名称" in c), cols[2])
    nav_col = next((c for c in cols if "单位净值" in c and "当前" in c), None)
    if nav_col is None:
        nav_col = next((c for c in cols if "单位净值" in c), cols[3])
    chg_pct_col = next((c for c in cols if "增长率" in c or "涨跌幅" in c), None)
    if chg_pct_col is None:
        chg_pct_col = cols[8]
    status_col = next((c for c in cols if "交易状态" in c or "状态" in c), None)
    date_col = next((c for c in cols if "报告期" in c or "查询" in c), None)

    col_map = {
        "code": code_col, "name": name_col, "nav": nav_col,
        "chg_pct": chg_pct_col, "status": status_col, "data_date": date_col,
    }

    our_codes = {v["code"] for v in ETF_ASSETS.values()}
    mask = raw[code_col].astype(str).isin(our_codes)
    filtered = raw[mask].copy()

    data_date = ""
    if date_col and not filtered.empty:
        data_date = str(filtered[date_col].iloc[0]) if pd.notna(filtered[date_col].iloc[0]) else ""

    return filtered, col_map, data_date


def _compute_return_from_kline(asset_key: str) -> Tuple[Optional[float], Optional[str]]:
    """Compute latest daily return from cached ETF k-line CSV.

    Looks in macro_data cache directory for etf_{asset_key}.csv.
    Returns (chg_pct, data_date) or (None, None).
    """
    csv_path = MACRO_DATA_DIR / f"etf_{asset_key}.csv"
    if not csv_path.exists():
        return None, None

    try:
        df = pd.read_csv(csv_path, parse_dates=["date"], index_col="date")
        if df.empty or "close" not in df.columns:
            return None, None
        closes = df["close"].dropna()
        if len(closes) < 2:
            return None, None
        last = closes.iloc[-1]
        prev = closes.iloc[-2]
        if prev == 0:
            return None, None
        chg_pct = round((last - prev) / prev * 100.0, 2)
        data_date = closes.index[-1].strftime("%Y-%m-%d") if hasattr(closes.index[-1], "strftime") else str(closes.index[-1])[:10]
        return chg_pct, data_date
    except Exception:
        return None, None


def build_etf_daily_json() -> Dict[str, Any]:
    """Main: collect ETF/LOF data from THS spot + kline fallback, build output JSON."""
    assets: List[Dict[str, Any]] = []
    global_data_date = ""
    missing_count = 0

    # ── Step 1: THS spot (ETFs) ────────────────────────────
    try:
        spot_df, col_map, spot_date = _fetch_from_ths_spot()
    except Exception as e:
        print(f"  WARN: THS ETF spot failed ({e}), using kline fallback for all")
        spot_df = pd.DataFrame()
        col_map = {}
        spot_date = ""

    if spot_date:
        global_data_date = spot_date

    spot_codes_found: set = set()

    if not spot_df.empty:
        for _, row in spot_df.iterrows():
            code = str(row[col_map["code"]])
            spot_codes_found.add(code)

            asset_key = None
            for k, v in ETF_ASSETS.items():
                if v["code"] == code:
                    asset_key = k
                    break
            if asset_key is None:
                continue

            chg_str = str(row.get(col_map["chg_pct"], ""))
            try:
                chg_pct = float(chg_str)
            except (ValueError, TypeError):
                chg_pct = None  # NaN or invalid

            nav_str = str(row.get(col_map["nav"], ""))
            try:
                nav = float(nav_str)
            except (ValueError, TypeError):
                nav = None

            if chg_pct is None or (isinstance(chg_pct, float) and pd.isna(chg_pct)):
                # Try kline fallback for this ETF
                fallback_chg, fallback_date = _compute_return_from_kline(asset_key)
                if fallback_chg is not None:
                    chg_pct = fallback_chg
                    if not global_data_date and fallback_date:
                        global_data_date = fallback_date
                    print(f"  INFO: {asset_key} ({code}) THS chg_pct NaN → kline fallback {chg_pct:+.2f}%")
                else:
                    chg_pct = 0.0

            status = str(row.get(col_map["status"], "")) if col_map.get("status") else ""

            assets.append({
                "asset_key": asset_key,
                "name": ETF_ASSETS[asset_key]["name"],
                "code": code,
                "category": ETF_ASSETS[asset_key]["category"],
                "nav": nav,
                "chg_pct": float(chg_pct),
                "chg_direction": "up" if chg_pct > 0 else ("down" if chg_pct < 0 else "flat"),
                "source": "ths_spot",
                "status": status,
            })

    # ── Step 2: Missing ETFs → kline fallback ──────────────
    for asset_key, info in ETF_ASSETS.items():
        if info["code"] in spot_codes_found:
            continue
        chg_pct, kline_date = _compute_return_from_kline(asset_key)
        if chg_pct is not None:
            assets.append({
                "asset_key": asset_key,
                "name": info["name"],
                "code": info["code"],
                "category": info["category"],
                "nav": None,
                "chg_pct": float(chg_pct),
                "chg_direction": "up" if chg_pct > 0 else ("down" if chg_pct < 0 else "flat"),
                "source": "kline_fallback",
                "status": "",
            })
            if not global_data_date and kline_date:
                global_data_date = kline_date
            print(f"  INFO: {asset_key} ({info['code']}) not in THS spot → kline fallback {chg_pct:+.2f}%")
        else:
            missing_count += 1
            assets.append({
                "asset_key": asset_key,
                "name": info["name"],
                "code": info["code"],
                "category": info["category"],
                "nav": None,
                "chg_pct": 0.0,
                "chg_direction": "unknown",
                "source": "unavailable",
                "status": "",
            })
            print(f"  WARN: {asset_key} ({info['code']}) unavailable (no THS spot, no kline cache)")

    # ── Step 3: LOFs → kline only ──────────────────────────
    for asset_key, info in LOF_ASSETS.items():
        chg_pct, kline_date = _compute_return_from_kline(asset_key)
        if chg_pct is not None:
            assets.append({
                "asset_key": asset_key,
                "name": info["name"],
                "code": info["code"],
                "category": info["category"],
                "nav": None,
                "chg_pct": float(chg_pct),
                "chg_direction": "up" if chg_pct > 0 else ("down" if chg_pct < 0 else "flat"),
                "source": "kline_fallback",
                "status": "",
            })
            if not global_data_date and kline_date:
                global_data_date = kline_date
            print(f"  INFO: {asset_key} LOF ({info['code']}) → kline fallback {chg_pct:+.2f}%")
        else:
            missing_count += 1
            assets.append({
                "asset_key": asset_key,
                "name": info["name"],
                "code": info["code"],
                "category": info["category"],
                "nav": None,
                "chg_pct": 0.0,
                "chg_direction": "unknown",
                "source": "unavailable",
                "status": "",
            })
            print(f"  WARN: {asset_key} LOF ({info['code']}) unavailable (no kline cache)")

    # ── Step 4: Cash (synthetic) ───────────────────────────
    assets.append({
        "asset_key": "cash",
        "name": "现金/回购",
        "code": None,
        "category": "cash",
        "nav": None,
        "chg_pct": 0.0,
        "chg_direction": "flat",
        "source": "synthetic",
        "status": "",
    })

    # Rank by daily change
    ranked = sorted(assets, key=lambda x: x["chg_pct"], reverse=True)
    for i, a in enumerate(ranked):
        a["rank"] = i + 1

    data_date = global_data_date or date.today().isoformat()

    return {
        "data_date": data_date,
        "generated_at": datetime.now().isoformat(),
        "source": "akshare fund_etf_spot_ths (同花顺) + kline fallback",
        "asset_count": len(assets),
        "missing_count": missing_count,
        "top_3": [a["name"] for a in ranked[:3]],
        "bottom_3": [a["name"] for a in ranked[-3:]],
        "assets": assets,
    }


def fetch_and_save(output_path: Optional[Path] = None) -> Dict[str, Any]:
    """Main entry: fetch and optionally save."""
    print("[fetch_etf_daily] 获取 11 类资产 ETF 日终数据")

    result = build_etf_daily_json()

    print(f"  Date: {result['data_date']}")
    print(f"  Source: {result['source']}")
    print(f"  Assets: {result['asset_count']} (missing: {result['missing_count']})")
    print(f"  Top 3: {', '.join(result['top_3'])}")
    print(f"  Bottom 3: {', '.join(result['bottom_3'])}")

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  Saved: {output_path}")

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch 11-asset ETF daily performance")
    parser.add_argument("--no-output", action="store_true", help="Print only, don't save")
    args = parser.parse_args()

    output_path = None if args.no_output else (DATA_DIR_OUTPUT / "_etf_daily.json")
    result = fetch_and_save(output_path=output_path)

    # Print summary table (ASCII-safe, no emoji)
    print()
    print(f"  {'Rank':<5} {'Asset':<14} {'Code':<8} {'Chg%':>8}  {'Source':<16} {'Category'}")
    print("  " + "-" * 72)
    for a in result["assets"]:
        d = "+" if a["chg_pct"] > 0 else ("-" if a["chg_pct"] < 0 else " ")
        print(f"  {a['rank']:<5} {a['name']:<14} {a['code'] or '-':<8} {d}{abs(a['chg_pct']):>7.2f}%  {a['source']:<16} {a['category']}")


if __name__ == "__main__":
    main()