#!/usr/bin/env python3
"""
El Niño Commodity Futures Monitor — 监控棕榈油/橡胶/白糖期货价格

依赖: akshare (pip install akshare) [主通道]
Fallback: a-stock-data 同款新浪期货 HTTP 端点 (零依赖，仅需 requests)

品种:
  - 棕榈油 (P.DCE): 大连商品交易所
  - 天然橡胶 (RU.SHF): 上海期货交易所
  - 白糖 (SR.CZC): 郑州商品交易所

输出: data/output/el_nino_futures.json
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Fix GBK encoding on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import akshare as ak
except ImportError:
    ak = None  # 降级到 fallback

import pandas as pd
import requests

# --- CONFIG ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "output"
OUTPUT_FILE = DATA_DIR / "el_nino_futures.json"

# Futures symbol mapping (akshare format -> display)
FUTURES_CONFIG = {
    "palm_oil": {
        "symbol": "P0",
        "exchange": "DCE",
        "name_cn": "棕榈油",
        "name_en": "Palm Oil",
        "unit": "元/吨",
        "multiplier": 10,
        "el_nino_sensitivity": "highest",
        "lag_months": "8-12",
    },
    "rubber": {
        "symbol": "RU0",
        "exchange": "SHF",
        "name_cn": "天然橡胶",
        "name_en": "Natural Rubber",
        "unit": "元/吨",
        "multiplier": 10,
        "el_nino_sensitivity": "highest",
        "lag_months": "3-6",
    },
    "sugar": {
        "symbol": "SR0",
        "exchange": "CZC",
        "name_cn": "白糖",
        "name_en": "Sugar",
        "unit": "元/吨",
        "multiplier": 10,
        "el_nino_sensitivity": "highest",
        "lag_months": "6-9",
    },
}


def _try_fetch_futures_spot() -> dict:
    """Try to fetch latest futures prices from daily data (last row)."""
    result = {}
    for key, cfg in FUTURES_CONFIG.items():
        try:
            df = ak.futures_zh_daily_sina(symbol=cfg["symbol"])
            if df is None or df.empty:
                continue
            latest = df.iloc[-1]
            result[key] = {
                "symbol": cfg["symbol"],
                "name_cn": cfg["name_cn"],
                "price": float(latest.get("close", 0)) if pd.notna(latest.get("close")) else None,
                "open": float(latest.get("open", 0)) if pd.notna(latest.get("open")) else None,
                "high": float(latest.get("high", 0)) if pd.notna(latest.get("high")) else None,
                "low": float(latest.get("low", 0)) if pd.notna(latest.get("low")) else None,
                "prev_close": float(latest.get("settle", 0)) if pd.notna(latest.get("settle")) else None,
                "volume": int(latest.get("volume", 0)) if pd.notna(latest.get("volume")) else None,
                "date": str(latest.get("date", "")),
                "change_pct": None,
            }
            if result[key]["price"] and result[key]["prev_close"] and result[key]["prev_close"] != 0:
                result[key]["change_pct"] = round(
                    (result[key]["price"] - result[key]["prev_close"])
                    / result[key]["prev_close"] * 100, 2,
                )
        except Exception as e:
            print(f"[WARN] Spot fetch for {key} failed: {e}")
    return result


def _try_fetch_futures_hist() -> dict:
    """Fetch historical daily data for each futures contract from akshare sina endpoint."""
    result = {}
    for key, cfg in FUTURES_CONFIG.items():
        try:
            df = ak.futures_zh_daily_sina(symbol=cfg["symbol"])
            if df is None or df.empty:
                continue

            # Convert date, standardize columns
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                df = df.sort_values("date").tail(250)

            hist_records = []
            for _, row in df.iterrows():
                hist_records.append({
                    "date": str(row.get("date", "")),
                    "open": float(row.get("open", 0)) if pd.notna(row.get("open")) else None,
                    "high": float(row.get("high", 0)) if pd.notna(row.get("high")) else None,
                    "low": float(row.get("low", 0)) if pd.notna(row.get("low")) else None,
                    "close": float(row.get("close", 0)) if pd.notna(row.get("close")) else None,
                    "volume": int(row.get("volume", 0)) if pd.notna(row.get("volume")) else None,
                    "settle": float(row.get("settle", 0)) if "settle" in df.columns and pd.notna(row.get("settle")) else None,
                })

            result[key] = {
                "symbol": cfg["symbol"],
                "name_cn": cfg["name_cn"],
                "historical": hist_records,
                "count": len(hist_records),
            }
        except Exception as e:
            print(f"[WARN] History fetch for {key} failed: {e}")
    return result


# ── Sina HTTP fallback (same endpoint as a-stock-data §13.7 futures_kline) ──

SINA_FUT_KLINE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_{code}=/InnerFuturesNewService.getDailyKLine"
)
FALLBACK_SESSION = requests.Session()
FALLBACK_SESSION.headers.update({
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0",
})


def _fetch_one_via_sina(code: str) -> Optional[dict]:
    """Fetch futures daily K-line for a single code via Sina HTTP endpoint.

    Uses the same endpoint as a-stock-data futures_kline().
    Returns list of daily records in el_nino_futures.json format.
    """
    url = SINA_FUT_KLINE_URL.format(code=code)
    try:
        resp = FALLBACK_SESSION.get(url, params={"symbol": code}, timeout=(5, 20))
        resp.raise_for_status()
    except (requests.RequestException, OSError) as e:
        print(f"[FALLBACK] HTTP request for {code} failed: {e}", file=sys.stderr)
        return None

    text = resp.content.decode("gbk", "replace")
    match = re.search(rf"var _{re.escape(code)}=\((.*)\);?\s*$", text, re.S)
    if not match:
        print(f"[FALLBACK] Sina futures JSONP parse failed for {code}", file=sys.stderr)
        return None

    body = match.group(1).strip()
    if body == "null":
        print(f"[FALLBACK] Sina has no data for {code}", file=sys.stderr)
        return None

    try:
        items = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[FALLBACK] Sina JSON parse failed for {code}: {e}", file=sys.stderr)
        return None

    if not isinstance(items, list):
        return None

    hist_records = []
    for r in items:
        if not isinstance(r, dict):
            continue
        try:
            day = str(r.get("d", ""))
            close = float(r["c"]) if r.get("c") and float(r["c"]) != 0 else None
            open_ = float(r["o"]) if r.get("o") and float(r["o"]) != 0 else None
            high = float(r["h"]) if r.get("h") and float(r["h"]) != 0 else None
            low = float(r["l"]) if r.get("l") and float(r["l"]) != 0 else None
            settle = float(r["s"]) if r.get("s") and float(r["s"]) != 0 else None
            volume = int(r["v"]) if r.get("v") else 0
            if close is None:
                continue
            hist_records.append({
                "date": day,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "settle": settle,
            })
        except (ValueError, TypeError, KeyError):
            continue

    if not hist_records:
        print(f"[FALLBACK] No valid records for {code}", file=sys.stderr)
        return None

    hist_records.sort(key=lambda r: r["date"])
    return hist_records[-250:]  # Keep last 250 trading days


def _try_fetch_futures_hist_fallback() -> dict:
    """Fetch historical futures data via Sina HTTP (a-stock-data fallback path).

    Triggered automatically when akshare returns no data for all three contracts.
    """
    result = {}
    for key, cfg in FUTURES_CONFIG.items():
        try:
            hist = _fetch_one_via_sina(cfg["symbol"])
            if hist:
                result[key] = {
                    "symbol": cfg["symbol"],
                    "name_cn": cfg["name_cn"],
                    "historical": hist,
                    "count": len(hist),
                }
            else:
                print(f"[FALLBACK] No data for {key} ({cfg['symbol']})", file=sys.stderr)
        except Exception as e:
            print(f"[FALLBACK] {key} fetch failed: {e}", file=sys.stderr)
    return result


def _is_akshare_available() -> bool:
    """Check if akshare is importable and not blocked."""
    if ak is None:
        return False
    return True


def compute_el_nino_returns(futures_data: dict) -> dict:
    """Compute key return metrics for El Niño strategy monitoring."""
    metrics = {}
    for key, data in futures_data.items():
        if "historical" not in data or len(data["historical"]) < 20:
            continue

        hist = data["historical"]
        latest = hist[-1]
        name = data.get("name_cn", key)

        # 1-month return
        if len(hist) >= 22:
            m1_ret = (latest["close"] - hist[-22]["close"]) / hist[-22]["close"] * 100
        else:
            m1_ret = None

        # 3-month return
        if len(hist) >= 66:
            m3_ret = (latest["close"] - hist[-66]["close"]) / hist[-66]["close"] * 100
        else:
            m3_ret = None

        # 6-month return
        if len(hist) >= 132:
            m6_ret = (latest["close"] - hist[-132]["close"]) / hist[-132]["close"] * 100
        else:
            m6_ret = None

        # YTD return
        closes = [d["close"] for d in hist if d["close"] is not None]
        if closes and len(closes) > 0:
            ytd_ret = (closes[-1] - closes[0]) / closes[0] * 100
        else:
            ytd_ret = None

        # 20-day volatility (annualized)
        if len(closes) >= 21:
            import math
            daily_rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(-20, 0)]
            mean_ret = sum(daily_rets) / len(daily_rets)
            variance = sum((r - mean_ret) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            vol_20d = math.sqrt(variance) * math.sqrt(252) * 100
        else:
            vol_20d = None

        # Distance from 52-week high/low
        if len(closes) >= 252:
            high_52w = max(closes[-252:])
            low_52w = min(closes[-252:])
            pct_from_high = (closes[-1] - high_52w) / high_52w * 100
            pct_from_low = (closes[-1] - low_52w) / low_52w * 100
        else:
            high_52w = max(closes)
            low_52w = min(closes)
            pct_from_high = (closes[-1] - high_52w) / high_52w * 100 if high_52w != 0 else 0
            pct_from_low = (closes[-1] - low_52w) / low_52w * 100 if low_52w != 0 else 0

        metrics[key] = {
            "name_cn": name,
            "latest_price": latest.get("close"),
            "latest_date": latest.get("date"),
            "ret_1m_pct": round(m1_ret, 2) if m1_ret is not None else None,
            "ret_3m_pct": round(m3_ret, 2) if m3_ret is not None else None,
            "ret_6m_pct": round(m6_ret, 2) if m6_ret is not None else None,
            "ret_ytd_pct": round(ytd_ret, 2) if ytd_ret is not None else None,
            "vol_20d_annual_pct": round(vol_20d, 2) if vol_20d is not None else None,
            "pct_from_52w_high": round(pct_from_high, 2),
            "pct_from_52w_low": round(pct_from_low, 2),
            "el_nino_sensitivity": FUTURES_CONFIG[key]["el_nino_sensitivity"],
            "lag_months": FUTURES_CONFIG[key]["lag_months"],
        }

    return metrics


def generate_strategy_signals(metrics: dict, enso_classification: Optional[dict] = None) -> dict:
    """Generate strategy signals combining futures data with ENSO state."""
    signals = {}

    for key, m in metrics.items():
        name = m["name_cn"]
        ret_3m = m.get("ret_3m_pct")
        pct_from_high = m.get("pct_from_52w_high")
        vol = m.get("vol_20d_annual_pct")

        # Base signal
        signal = "hold"
        alerts = []

        # If in super El Nino, bias towards long
        is_super_el_nino = False
        if enso_classification:
            is_super_el_nino = enso_classification.get("classification") == "super_el_nino"

        if is_super_el_nino:
            # Long bias for tropical ag
            if ret_3m is not None and ret_3m < 5:
                signal = "buy"  # Still early, good entry
                alerts.append("super_el_nino_early_entry")
            elif ret_3m is not None and ret_3m > 30:
                signal = "reduce"  # Large move already, take partial profits
                alerts.append("large_move_partial_take_profit")
            else:
                signal = "hold_add_on_dip"
                alerts.append("super_el_nino_hold")

            # Risk warnings
            if pct_from_high is not None and pct_from_high > -5:
                alerts.append("near_52w_high_caution")
            if vol is not None and vol > 40:
                alerts.append("high_volatility_reduce_size")

        signals[key] = {
            "name_cn": name,
            "signal": signal,
            "alerts": alerts,
            "stop_loss_pct": -15.0,
            "partial_take_profit_pct": 25.0,
            "full_take_profit_pct": 40.0,
        }

    return signals


def fetch_all_futures_data(enso_data: Optional[dict] = None) -> dict:
    """Main entry: fetch futures data and generate signals.

    Args:
        enso_data: Optional ENSO data dict for generating ENSO-aware signals.

    Data source priority:
        1. akshare (EastMoney/Sina) — primary channel
        2. Sina HTTP direct (a-stock-data fallback) — auto-triggered on akshare failure
    """
    data_source = "akshare"
    print("[FUTURES] Fetching futures spot prices...")
    spot_data = _try_fetch_futures_spot()

    print("[FUTURES] Fetching historical futures data via akshare...")
    hist_data = _try_fetch_futures_hist()

    # ── Auto fallback: if akshare returns nothing for ALL three contracts, try Sina direct ──
    akshare_ok = len(hist_data) >= 2  # At least 2 out of 3 succeeded
    if not akshare_ok:
        print("[FUTURES] akshare returned insufficient data, trying Sina HTTP fallback...")
        fallback_data = _try_fetch_futures_hist_fallback()
        if fallback_data:
            hist_data = fallback_data
            data_source = "sina_direct (akshare failed)"
            print(f"[FUTURES] Sina fallback OK — {len(hist_data)} contracts")
        else:
            print("[FUTURES] Sina fallback also failed — output will be degraded")

    # Merge spot into historical
    for key in hist_data:
        if key in spot_data:
            hist_data[key]["spot"] = spot_data[key]

    # For keys with spot but no hist, create minimal entry
    for key in spot_data:
        if key not in hist_data:
            hist_data[key] = {
                "symbol": FUTURES_CONFIG[key]["symbol"],
                "name_cn": FUTURES_CONFIG[key]["name_cn"],
                "spot": spot_data[key],
                "historical": [],
                "count": 0,
            }

    # Compute metrics
    metrics = compute_el_nino_returns(hist_data)

    # Generate signals
    enso_classification = enso_data.get("classification") if enso_data else None
    signals = generate_strategy_signals(metrics, enso_classification)

    # Build output
    result = {
        "metadata": {
            "fetched_at": datetime.now().isoformat(),
            "version": "1.0.0",
            "data_source": data_source,
        },
        "futures_data": hist_data,
        "metrics": metrics,
        "signals": signals,
        "summary": _build_summary(metrics, signals),
    }

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"[FUTURES] Saved to {OUTPUT_FILE} (source: {data_source})")

    return result


def _build_summary(metrics: dict, signals: dict) -> dict:
    """Build human-readable summary."""
    items = []
    for key, m in metrics.items():
        items.append({
            "name": m["name_cn"],
            "price": m["latest_price"],
            "ytd_pct": m["ret_ytd_pct"],
            "3m_pct": m["ret_3m_pct"],
            "vol_annual": m["vol_20d_annual_pct"],
            "signal": signals.get(key, {}).get("signal", "unknown"),
            "alerts": signals.get(key, {}).get("alerts", []),
        })

    # Overall assessment
    buy_count = sum(1 for s in signals.values() if s.get("signal") in ("buy", "hold_add_on_dip"))
    reduce_count = sum(1 for s in signals.values() if s.get("signal") == "reduce")
    alert_count = sum(len(s.get("alerts", [])) for s in signals.values())

    return {
        "items": items,
        "overall_signal": "bullish" if buy_count >= 2 else "neutral",
        "active_alerts": alert_count,
        "buy_signals": buy_count,
        "reduce_signals": reduce_count,
    }


# --- CLI ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch El Niño commodity futures data")
    parser.add_argument("--enso-file", type=str, help="Path to enso_data.json for ENSO-aware signals")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    # Load ENSO data if available
    enso_data = None
    enso_path = args.enso_file or (DATA_DIR / "enso_data.json")
    if isinstance(enso_path, str):
        enso_path = Path(enso_path)
    if enso_path.exists():
        try:
            enso_data = json.loads(enso_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass

    try:
        result = fetch_all_futures_data(enso_data)

        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            print(f"\n{'='*60}")
            print("El Niño Commodity Futures Monitor")
            print(f"{'='*60}")
            for item in result["summary"]["items"]:
                print(f"\n  {item['name']}: ¥{item['price']}")
                print(f"    YTD: {item['ytd_pct']}% | 3M: {item['3m_pct']}% | Vol: {item['vol_annual']}%")
                print(f"    Signal: {item['signal']} | Alerts: {item['alerts']}")
            print(f"\n  Overall: {result['summary']['overall_signal']}")
            print(f"  Buy/Hold: {result['summary']['buy_signals']} | Reduce: {result['summary']['reduce_signals']}")
            print(f"{'='*60}")

    except Exception as e:
        print(f"[ERROR] Failed to fetch futures data: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)