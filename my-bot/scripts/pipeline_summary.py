#!/usr/bin/env python3
"""
#10 Pipeline Summary Automation

Prints a daily one-page summary of pipeline health after each cron run.
Designed to be called at the end of daily-startup.ps1 / midday.ps1.

Reads: data/output/manifest.json + individual signal files
Output: Console table with artifact status, signal counts, anomaly alerts
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUTPUT_DIR = os.path.join(ROOT, "data", "output")
MANIFEST_PATH = os.path.join(OUTPUT_DIR, "manifest.json")

TZ_CST = timezone(timedelta(hours=8))


def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        return None
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def load_signal_summary(filename):
    """Extract summary stats from a signal file"""
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return {"status": "MISSING", "count": 0, "top_3": [], "date": "N/A"}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"status": "CORRUPT", "count": 0, "top_3": [], "date": "N/A"}

    # Extract stocks
    stocks = data.get("data", data.get("top_10", []))
    count = data.get("total_stocks", data.get("total_instruments", len(stocks)))
    as_of = data.get("as_of_date", data.get("date", "N/A"))
    staleness = data.get("@contract", {}).get("staleness", data.get("staleness", {}))
    excess_lag = staleness.get("excess_lag_days", 0)
    design_lag = staleness.get("design_lag_trading_days", 0)

    # Top-3
    sorted_stocks = sorted(
        [s for s in stocks if isinstance(s, dict)],
        key=lambda x: x.get("alpha_score", 0),
        reverse=True,
    )
    top_3 = [s["symbol"] for s in sorted_stocks[:3]]

    # Status
    if excess_lag > 0:
        status = f"STALE(+{excess_lag}d)"
        flag = "ALERT"
    elif design_lag > 0:
        status = f"OK (lag={design_lag}d)"
        flag = "OK"
    else:
        status = "OK"
        flag = "OK"

    return {
        "status": status,
        "count": count,
        "top_3": top_3,
        "date": as_of,
        "excess_lag": excess_lag,
        "design_lag": design_lag,
    }


def check_anomalies(manifest, signals):
    """Detect pipeline anomalies"""
    alerts = []

    now = datetime.now(TZ_CST)
    today_str = now.strftime("%Y-%m-%d")

    for name, info in signals.items():
        if info["status"] == "MISSING":
            alerts.append(f"MISSING: {name} file not found")
        elif info["status"] == "CORRUPT":
            alerts.append(f"CORRUPT: {name} JSON unreadable")
        elif "STALE" in info["status"]:
            alerts.append(f"STALE: {name} excess_lag={info['excess_lag']}d (data as_of={info['date']})")
        elif info["date"] and info["date"] < today_str:
            # Check if data is from a previous day (might be expected or not)
            pass

    # Check manifest staleness flags
    if manifest:
        for art_name, art in manifest.get("artifacts", {}).items():
            s = art.get("staleness")
            if s and s.get("excess_lag_days", 0) > 0:
                alerts.append(f"MANIFEST: {art_name} excess_lag={s['excess_lag_days']}d")

    return alerts


def main():
    print("=" * 66)
    print("  AgentAlpha Pipeline Summary —", datetime.now(TZ_CST).strftime("%Y-%m-%d %H:%M CST"))
    print("=" * 66)

    manifest = load_manifest()
    if not manifest:
        print("ERROR: manifest.json not found — pipeline may not have run")
        return 1

    # Gather signal summaries
    signal_files = {
        "ML Ensemble (CSI500)":     "alpha_signals.json",
        "ML Ensemble (CSI300)":     "alpha_signals_csi300.json",
        "ML Latest  (CSI500)":      "alpha_signals_latest.json",
        "ML Latest  (CSI300)":      "alpha_signals_latest_csi300.json",
        "P1 Consensus":             "alpha_signals_p1.json",
        "Signal Factory":           "signal_factory_latest.json",
        "Sector Signals":           "sector_signals.json",
    }

    signals = {}
    for label, filename in signal_files.items():
        signals[label] = load_signal_summary(filename)

    # Print table
    print(f"\n{'Signal Source':<28} {'Date':<12} {'Stocks':<8} {'Status':<16} {'Top-3'}")
    print("-" * 66)

    for label, info in signals.items():
        status = info["status"]
        date = str(info["date"]) if info["date"] else "N/A"
        count = info["count"] if info["count"] else "-"
        top3 = ", ".join(info["top_3"]) if info["top_3"] else "—"

        # Color markers
        flag = ""
        if "STALE" in status or status == "MISSING" or status == "CORRUPT":
            flag = "⚠️  "
        elif "OK" in status:
            flag = "✅ "

        print(f"{flag}{label:<26} {date:<12} {str(count):<8} {status:<16} {top3}")

    # Anomaly alerts
    alerts = check_anomalies(manifest, signals)

    # Data freshness summary
    print(f"\n{'='*66}")
    print(f"  Data Freshness")
    print(f"{'='*66}")

    ml_date = signals["ML Ensemble (CSI500)"]["date"]
    p1_date = signals["P1 Consensus"]["date"]
    latest_ml = signals["ML Latest  (CSI500)"]["date"]

    print(f"  ML Alpha signals:     as_of={ml_date} (5d design lag)")
    print(f"  ML Latest signals:    as_of={latest_ml} (direct .day.bin)")
    print(f"  P1 Consensus signals: as_of={p1_date} (direct .day.bin, no lag)")
    print(f"  Manifest updated:     {manifest.get('updated', 'N/A')}")

    # Alerts section
    if alerts:
        print(f"\n{'='*66}")
        print(f"  ⚠️  Alerts ({len(alerts)})")
        print(f"{'='*66}")
        for alert in alerts:
            print(f"  - {alert}")
    else:
        print(f"\n✅ No pipeline alerts")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())