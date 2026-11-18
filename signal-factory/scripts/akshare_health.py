#!/usr/bin/env python3
"""
akshare pre-flight health check.

Tests critical endpoints BEFORE the pipeline runs.
Distinguishes: HEALTHY / DEGRADED / BLOCKED

Endpoint tiers (aligned with pipeline dependency):
  Tier 1 (critical): stock_zh_a_hist — blocks Step 1 data update
  Tier 2 (important): index_hist_sw — Layer 1 sector rotation (has Wind+baostock fallback)
  Tier 3 (supporting): macro_china_pmi, fund_etf_hist_em, stock_hk_spot_em — Layer 0 + HK (have fallbacks)

Classification:
  HEALTHY:  All endpoints pass
  DEGRADED: Tier 1 passes, some Tier 2+ fail (pipeline runs with fallbacks)
  BLOCKED:  Tier 1 fails (pipeline Step 1 WILL fail — abort recommended)
  OFFLINE:  akshare not installed

Usage:
  python scripts/akshare_health.py                  # full check, human-readable output
  python scripts/akshare_health.py --quick           # Tier 1 only (fast, for pipeline gate)
  python scripts/akshare_health.py --json            # JSON output for pipeline/automation
  python scripts/akshare_health.py --quick --json    # combined (use in PowerShell)
"""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Trading calendar
# ═══════════════════════════════════════════════════════════════════════════

_CALENDAR_CACHE: Optional[set] = None
_CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "calendars" / "day.txt"


def _load_trading_calendar() -> set:
    """Load trading days from Qlib calendar file. Cached after first load."""
    global _CALENDAR_CACHE
    if _CALENDAR_CACHE is not None:
        return _CALENDAR_CACHE
    _CALENDAR_CACHE = set()
    if _CALENDAR_PATH.exists():
        with open(_CALENDAR_PATH, "r", encoding="utf-8") as f:
            for line in f:
                d = line.strip()
                if d and d != "1970-01-01":
                    _CALENDAR_CACHE.add(d)
    return _CALENDAR_CACHE


def _is_trading_day(d: Optional[datetime] = None) -> bool:
    """Check if the given date (default: today) is a trading day.

    Uses Qlib calendar for known dates; for future dates beyond the calendar,
    assumes Mon-Fri are trading days. Returns False only for confirmed
    weekends and known holidays.
    """
    if d is None:
        d = datetime.now()
    date_str = d.strftime("%Y-%m-%d")
    calendar = _load_trading_calendar()

    if calendar:
        if date_str in calendar:
            return True
        # If date is beyond the last calendar date, use weekend heuristic
        max_cal = max(calendar)
        if date_str > max_cal:
            return d.weekday() < 5  # Mon-Fri are assumed trading days
        # Date is within calendar range but not in calendar → holiday
        return False
    # Fallback: no calendar available, assume Mon-Fri are trading days
    return d.weekday() < 5


def _get_last_trading_day(d: Optional[datetime] = None) -> datetime:
    """Get the most recent trading day on or before the given date."""
    if d is None:
        d = datetime.now()
    date_str = d.strftime("%Y-%m-%d")
    calendar = _load_trading_calendar()

    if calendar:
        # Find the last trading day <= date_str
        trading_days = sorted(calendar)
        last_known = None
        for td in trading_days:
            if td <= date_str:
                last_known = td
        if last_known:
            # If we're within 3 days after last known trading day, use it
            last_dt = datetime.strptime(last_known, "%Y-%m-%d")
            if d <= last_dt + timedelta(days=3):
                return last_dt
            # Otherwise, project forward assuming weekdays
            projected = d
            while projected.weekday() >= 5:
                projected -= timedelta(days=1)
            return projected
    # Fallback: assume weekdays
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# Add project root for utils import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint test definitions
# ═══════════════════════════════════════════════════════════════════════════

def _test_zh_a_hist(symbol: str) -> bool:
    """Tier 1: A-share daily k-line (used by update_daily.py, signal_tracker.py)."""
    import akshare as ak
    last_td = _get_last_trading_day()
    today = last_td.strftime("%Y%m%d")
    yesterday = (last_td - timedelta(days=3)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                            start_date=yesterday, end_date=today)
    return df is not None and not df.empty


def _test_index_hist_sw(symbol: str) -> bool:
    """Tier 2: Shenwan industry index (used by sector_data.py, has Wind+baostock fallback)."""
    import akshare as ak
    df = ak.index_hist_sw(symbol=symbol)
    return df is not None and not df.empty


def _test_macro_pmi(symbol: str) -> bool:
    """Tier 3: Manufacturing PMI (used by macro_data.py, NBS data not via Eastmoney)."""
    import akshare as ak
    df = ak.macro_china_pmi()
    return df is not None and not df.empty


def _test_etf_hist(symbol: str) -> bool:
    """Tier 3: ETF daily k-line (used by macro_data.py, has TTL cache fallback)."""
    import akshare as ak
    last_td = _get_last_trading_day()
    today = last_td.strftime("%Y%m%d")
    yesterday = (last_td - timedelta(days=5)).strftime("%Y%m%d")
    df = ak.fund_etf_hist_em(symbol=symbol, period="daily",
                             start_date=yesterday, end_date=today)
    return df is not None and not df.empty


def _test_hk_spot(symbol: str) -> bool:
    """Tier 3: HK stock snapshot (used by hk_stock_data.py, has Wind MCP fallback)."""
    import akshare as ak
    df = ak.stock_hk_spot_em()
    # Check we got data for the requested symbol
    if df is None or df.empty:
        return False
    row = df[df["代码"] == symbol]
    return not row.empty


ENDPOINTS = [
    {
        "name": "stock_zh_a_hist",
        "tier": 1,
        "description": "A股日线 (update_daily, signal_tracker)",
        "symbol": "000001",
        "test_func": _test_zh_a_hist,
        "has_fallback": True,  # baostock via stock_zh_a_hist_with_fallback
    },
    {
        "name": "index_hist_sw",
        "tier": 2,
        "description": "申万行业指数 (sector_data)",
        "symbol": "801080",
        "test_func": _test_index_hist_sw,
        "has_fallback": True,  # Wind MCP + baostock
    },
    {
        "name": "macro_china_pmi",
        "tier": 3,
        "description": "制造业PMI (macro_data)",
        "symbol": "",  # no symbol needed
        "test_func": _test_macro_pmi,
        "has_fallback": True,  # stale cache
    },
    {
        "name": "fund_etf_hist_em",
        "tier": 3,
        "description": "ETF日线 (macro_data)",
        "symbol": "510050",
        "test_func": _test_etf_hist,
        "has_fallback": True,  # TTL cache
    },
    {
        "name": "stock_hk_spot_em",
        "tier": 3,
        "description": "港股快照 (hk_stock_data)",
        "symbol": "00700",
        "test_func": _test_hk_spot,
        "has_fallback": True,  # Wind MCP stock_data
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Test runner with timeout (thread-based, Windows-safe)
# ═══════════════════════════════════════════════════════════════════════════

def _run_with_timeout(func: Callable, args: tuple, timeout: float) -> dict:
    """Run func(*args) in a thread with timeout. Returns {ok, elapsed, error}."""
    result = {"ok": False, "elapsed": 0, "error": None}
    t0 = time.time()

    def target():
        try:
            func(*args)
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    result["elapsed"] = round(time.time() - t0, 2)

    if thread.is_alive():
        # Thread is still running — timeout
        result["ok"] = False
        result["error"] = f"Timeout after {timeout}s"
        # Can't kill thread in Python, but it's daemon so won't block exit

    return result


def _classify_error(error_msg: str) -> str:
    """Classify error type for diagnosis."""
    if not error_msg:
        return "OK"
    msg_lower = error_msg.lower()
    if "remotedisconnected" in msg_lower or "connection aborted" in msg_lower:
        return "BLOCKED"
    if "timeout" in msg_lower:
        return "TIMEOUT"
    if "connection" in msg_lower or "remote" in msg_lower or "http" in msg_lower:
        return "NETWORK"
    return "ERROR"


# ═══════════════════════════════════════════════════════════════════════════
# Main health check
# ═══════════════════════════════════════════════════════════════════════════

def check_all_endpoints(quick: bool = False, timeout: float = 15) -> dict:
    """Run health checks against configured endpoints.

    Args:
        quick: Only run Tier 1 endpoints (faster, for pipeline gate).
        timeout: Per-endpoint timeout in seconds.

    Returns:
        Dict with classification, results list, version info, timestamp.
    """
    # Version check (non-blocking)
    try:
        from src.utils.akshare_version import check_version
        vinfo = check_version()
        version_info = {
            "installed": vinfo.installed,
            "latest": vinfo.latest,
            "is_latest": vinfo.is_latest,
            "behind_count": vinfo.behind_count,
        }
    except Exception as e:
        version_info = {"installed": "unknown", "latest": None,
                        "is_latest": True, "behind_count": 0,
                        "error": str(e)}

    # Endpoint tests
    results = []
    for ep in ENDPOINTS:
        if quick and ep["tier"] > 1:
            continue

        outcome = _run_with_timeout(ep["test_func"], (ep["symbol"],), timeout)
        classification = _classify_error(outcome.get("error") or "")
        results.append({
            "endpoint": ep["name"],
            "tier": ep["tier"],
            "description": ep["description"],
            "status": "ok" if outcome["ok"] else "failed",
            "elapsed_s": outcome["elapsed"],
            "error": outcome.get("error"),
            "error_class": classification,
            "has_fallback": ep["has_fallback"],
        })

    # Classification
    tier1_results = [r for r in results if r["tier"] == 1]
    tier1_ok = all(r["status"] == "ok" for r in tier1_results)
    # Check if all failed tier endpoints have fallbacks
    failed_with_fallback = all(
        r.get("has_fallback", False)
        for r in results if r["status"] != "ok"
    )

    if not results:
        classification = "OFFLINE"
    elif not tier1_ok:
        # Tier 1 failed — but if it has fallback, it's DEGRADED not BLOCKED
        tier1_has_fallback = all(r.get("has_fallback", False) for r in tier1_results if r["status"] != "ok")
        if tier1_has_fallback:
            classification = "DEGRADED"
        else:
            classification = "BLOCKED"
    elif all(r["status"] == "ok" for r in results):
        classification = "HEALTHY"
    else:
        classification = "DEGRADED"

    return {
        "classification": classification,
        "timestamp": datetime.now().isoformat(),
        "is_trading_day": _is_trading_day(),
        "last_trading_day": _get_last_trading_day().strftime("%Y-%m-%d"),
        "version": version_info,
        "results": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "failed": sum(1 for r in results if r["status"] != "ok"),
        },
    }


def print_human_report(data: dict):
    """Human-readable health report."""
    v = data["version"]
    print("=" * 60)
    print("  akshare Health Check")
    print("=" * 60)
    print(f"  Classification: {data['classification']}")
    print(f"  Timestamp:      {data['timestamp'][:19]}")
    is_td = data.get("is_trading_day", True)
    ltd = data.get("last_trading_day", "unknown")
    td_note = " (non-trading day)" if not is_td else ""
    print(f"  Trading day:    {ltd}{td_note}")
    print(f"  Version:        {v['installed']}", end="")
    if v.get("latest") and not v["is_latest"]:
        print(f" (latest: {v['latest']}, {v['behind_count']} behind)")
    else:
        print()
    print(f"  Results:        {data['summary']['ok']}/{data['summary']['total']} ok")
    print()

    for r in data["results"]:
        icon = "[OK]" if r["status"] == "ok" else "[FAIL]"
        tier_label = f"T{r['tier']}"
        fb = " [+fallback]" if r.get("has_fallback") else ""
        print(f"  {icon} [{tier_label}] {r['description']} ({r['elapsed_s']}s){fb}")
        if r.get("error"):
            print(f"       {r['error_class']}: {r['error'][:120]}")

    print()
    if not data.get("is_trading_day", True):
        last_td = data.get("last_trading_day", "unknown")
        print(f"  Note: Today is a non-trading day. Tests use last trading day ({last_td}).")
        print(f"  Pipeline: PAUSED (no trading today)")
    elif data["classification"] == "HEALTHY":
        print("  Pipeline: OK to proceed")
    elif data["classification"] == "DEGRADED":
        failed = [r["description"] for r in data["results"] if r["status"] != "ok"]
        print(f"  Pipeline: OK (degraded — fallbacks available for: {', '.join(failed)})")
    elif data["classification"] == "BLOCKED":
        print("  Pipeline: ABORT recommended — Tier 1 endpoint failed, no fallback")
    print()


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="akshare pre-flight health check")
    parser.add_argument("--quick", action="store_true",
                        help="Tier 1 only (fast, for pipeline gate)")
    parser.add_argument("--json", action="store_true",
                        help="JSON output for automation")
    parser.add_argument("--timeout", type=float, default=15,
                        help="Per-endpoint timeout in seconds (default: 15)")
    parser.add_argument("--output", help="Write JSON report to file")
    args = parser.parse_args()

    data = check_all_endpoints(quick=args.quick, timeout=args.timeout)

    if args.json:
        # For PowerShell consumption: print JSON to stdout
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print_human_report(data)

    # Write to file if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # Exit code for automation
    if data["classification"] == "BLOCKED":
        sys.exit(2)
    elif data["classification"] == "DEGRADED":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()