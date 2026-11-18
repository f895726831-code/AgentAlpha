#!/usr/bin/env python3
"""
ENSO Data Fetcher — 从 NOAA CPC 抓取 ONI/RONI 海温指数

数据来源:
  - ONI (Oceanic Niño Index): NOAA CPC ERSSTv5 三个月滑动平均
  - RONI (Relative Oceanic Niño Index): 扣除热带平均 SSTA，2026年起为NOAA主指标

输出: data/output/enso_data.json

调度: 每周一 08:00 (NOAA 通常在上周五更新周度数据)
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

# --- CONFIG ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "output"
OUTPUT_FILE = DATA_DIR / "enso_data.json"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
# Weekly Nino indices (Nino 1+2, 3, 3.4, 4)
WEEKLY_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"

# --- Classification thresholds ---
THRESHOLDS = {
    "weak": 0.5,
    "moderate": 1.3,
    "strong": 2.0,
    "super": 2.5,
}


# Season code → month number mapping (middle month of 3-month season)
SEASON_TO_MONTH = {
    "DJF": 1,   # Dec-Feb → Jan
    "JFM": 2,   # Jan-Mar → Feb
    "FMA": 3,   # Feb-Apr → Mar
    "MAM": 4,   # Mar-May → Apr
    "AMJ": 5,   # Apr-Jun → May
    "MJJ": 6,   # May-Jul → Jun
    "JJA": 7,   # Jun-Aug → Jul
    "JAS": 8,   # Jul-Sep → Aug
    "ASO": 9,   # Aug-Oct → Sep
    "SON": 10,  # Sep-Nov → Oct
    "OND": 11,  # Oct-Dec → Nov
    "NDJ": 12,  # Nov-Jan → Dec
}


def fetch_oni_data() -> list[dict]:
    """Fetch ONI data from NOAA CPC ascii file.

    Actual format:
      SEAS  YR   TOTAL   ANOM
       DJF 1950  25.01  -1.32
    Where SEAS = 3-letter season code (DJF=Dec-Feb, JFM=Jan-Mar, ...)
    """
    resp = requests.get(ONI_URL, timeout=30)
    resp.raise_for_status()

    records = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        # Column 0 must be a 3-letter season code
        if len(parts[0]) != 3 or not parts[0].isalpha():
            continue
        season = parts[0].upper()
        if season not in SEASON_TO_MONTH:
            continue
        try:
            yr = int(parts[1])
            total = float(parts[2])
            anom = float(parts[3])
            mon = SEASON_TO_MONTH[season]
            records.append({
                "year": yr,
                "month": mon,
                "season": season,
                "total": total,
                "anomaly": anom,
                "period": f"{yr}-{mon:02d}",
            })
        except (ValueError, IndexError):
            continue

    return records


def fetch_weekly_nino() -> dict:
    """Fetch weekly Nino indices from NOAA CPC.

    Returns dict with lists for each Nino region (1+2, 3, 3.4, 4).
    """
    resp = requests.get(WEEKLY_URL, timeout=30)
    resp.raise_for_status()

    # Parse fixed-width fortran-style format
    # The file header has: Nino1+2  Nino3  Nino3.4  Nino4
    # Data lines: week_ending_date  value1  value2  value3  value4
    regions = {"nino12": [], "nino3": [], "nino34": [], "nino4": []}
    region_keys = list(regions.keys())

    for line in resp.text.splitlines():
        line = line.strip()
        # Skip headers and blank lines
        if not line or "Nino" in line[:10] or "WEEK" in line[:10] or line[0].isdigit() is False:
            continue
        # Try to parse date + 4 values
        parts = line.split()
        if len(parts) < 2:
            continue
        # First part is date like "03SEP2026"
        date_str = parts[0]
        if len(date_str) < 9:
            continue
        try:
            dt = datetime.strptime(date_str, "%d%b%Y")
            values = []
            for p in parts[1:5]:
                try:
                    values.append(float(p))
                except ValueError:
                    values.append(None)
            if len(values) == 4:
                for i, key in enumerate(region_keys):
                    regions[key].append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "anomaly": values[i],
                    })
        except ValueError:
            continue

    return regions


def compute_oni_classification(records: list[dict]) -> dict:
    """Classify current ENSO state from latest ONI records.

    Returns event classification, peak info, and trend.
    Peak is restricted to the CURRENT El Nino event (last 24 months), not all-time.
    """
    if not records:
        return {
            "status": "unknown", "reason": "no_data", "classification": "unknown",
            "strength": "unknown", "latest_oni": 0, "latest_period": "",
            "peak_oni": 0, "peak_period": "", "consecutive_warm_months": 0,
            "trend": "unknown", "thresholds": THRESHOLDS,
        }

    # Sort by period
    records = sorted(records, key=lambda r: (r["year"], r["month"]))

    # Latest values
    latest = records[-1]
    last_24 = [r for r in records[-24:]]  # Last 2 years for current event

    # Determine if in El Nino (5+ consecutive months >= 0.5)
    in_el_nino = False
    consecutive = 0
    for r in reversed(records):
        if r["anomaly"] >= THRESHOLDS["weak"]:
            consecutive += 1
        else:
            break
    in_el_nino = consecutive >= 5

    # Peak from CURRENT EVENT ONLY (last 24 months to avoid historical peaks)
    current_peak_anomaly = max(r["anomaly"] for r in last_24)
    current_peak_record = max(last_24, key=lambda r: r["anomaly"])

    # Trend: compare last 3 months average vs 3 months prior
    if len(last_24) >= 6:
        recent_3m = sum(r["anomaly"] for r in last_24[-3:]) / 3
        prior_3m = sum(r["anomaly"] for r in last_24[-6:-3]) / 3
        diff = recent_3m - prior_3m
        if diff > 0.3:
            trend = "warming_rapidly"
        elif diff > 0.1:
            trend = "warming"
        elif diff < -0.3:
            trend = "cooling_rapidly"
        elif diff < -0.1:
            trend = "cooling"
        else:
            trend = "stable"
    else:
        trend = "unknown"

    return {
        "status": "el_nino" if in_el_nino else ("la_nina" if latest["anomaly"] <= -0.5 else "neutral"),
        "strength": current_peak_anomaly >= THRESHOLDS["super"],
        "classification": "super_el_nino" if (in_el_nino and current_peak_anomaly >= THRESHOLDS["super"])
                          else ("el_nino" if in_el_nino else "neutral"),
        "latest_oni": latest["anomaly"],
        "latest_period": latest["period"],
        "peak_oni": current_peak_anomaly,
        "peak_period": current_peak_record["period"],
        "consecutive_warm_months": consecutive,
        "trend": trend,
        "thresholds": THRESHOLDS,
        # Also report all-time record for context
        "all_time_peak_oni": max(r["anomaly"] for r in records),
        "all_time_peak_period": max(records, key=lambda r: r["anomaly"])["period"],
    }


def generate_trading_signals(classification: dict) -> dict:
    """Generate El Niño trading signals based on classification."""
    signals = {
        "active": False,
        "mode": "normal",
        "strategies": {},
        "alerts": [],
    }

    if classification["classification"] != "super_el_nino":
        if classification["strength"] == "strong":
            signals["active"] = True
            signals["mode"] = "el_nino_strong"
            signals["strategies"] = {
                "A_tropical_ag": "half_size",
                "B_a_share_ag_upstream": "standard",
                "G_damaged_sectors": "standard",
            }
            signals["alerts"].append("strong_el_nino_active")
        return signals

    # Super El Nino
    signals["active"] = True
    signals["mode"] = "el_nino_super"
    signals["strategies"] = {
        "A_tropical_ag": "full_size",
        "B_a_share_ag_upstream": "full_size",
        "C_energy_tactical": "monitor",
        "D_em_underweight": "active",
        "E_treasury_long": "monitor",
        "F_gold_hedge": "active",
        "G_damaged_sectors": "active",
    }

    # Alerts based on trend
    trend = classification.get("trend", "unknown")
    latest_oni = classification.get("latest_oni", 0)
    peak_oni = classification.get("peak_oni", 0)

    alerts = ["super_el_nino_active"]

    if trend in ("warming", "warming_rapidly"):
        alerts.append("still_intensifying")
        signals["strategies"]["A_tropical_ag"] = "full_size"  # Still building position
    elif trend == "stable":
        alerts.append("at_peak")
        signals["strategies"]["A_tropical_ag"] = "full_size_hold"  # Peak - hold
    elif trend in ("cooling", "cooling_rapidly"):
        alerts.append("past_peak_decaying")
        signals["strategies"]["E_treasury_long"] = "prepare_entry"

    if latest_oni >= 3.0:
        alerts.append("oni_above_3.0_extreme")

    if classification["consecutive_warm_months"] >= 12:
        alerts.append("prolonged_event_over_12m")

    signals["alerts"] = alerts
    return signals


def generate_exit_signals(classification: dict) -> dict:
    """Generate exit signals based on ONI trajectory."""
    exits = {
        "global_exit_triggered": False,
        "partial_exit_recommended": False,
        "exit_reasons": [],
    }

    latest_oni = classification.get("latest_oni", 0)
    trend = classification.get("trend", "unknown")
    consecutive = classification.get("consecutive_warm_months", 0)

    # Global exit: ONI below threshold
    if latest_oni < 1.0 and trend in ("cooling", "cooling_rapidly"):
        exits["global_exit_triggered"] = True
        exits["exit_reasons"].append("oni_below_1.0")

    # Partial exit: ONI declining below 1.5
    if latest_oni < 1.5 and trend in ("cooling", "cooling_rapidly"):
        exits["partial_exit_recommended"] = True
        exits["exit_reasons"].append("oni_below_1.5")

    # Consecutive months declining
    if consecutive >= 2 and trend == "cooling_rapidly":
        exits["exit_reasons"].append("rapid_cooling_2m")

    return exits


def fetch_enso_data(force: bool = False) -> dict:
    """Main fetcher — fetch ONI + weekly data, classify, generate signals.

    Args:
        force: If True, skip cache check and always fetch fresh data.

    Returns:
        dict with all ENSO data, classification, and trading signals.
    """
    # Check cache if not forced
    if not force and OUTPUT_FILE.exists():
        cached = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        last_fetch = datetime.fromisoformat(cached["metadata"]["fetched_at"])
        if datetime.now() - last_fetch < timedelta(hours=24):
            return cached

    # Fetch ONI data
    print("[ENSO] Fetching ONI data from NOAA CPC...")
    oni_records = fetch_oni_data()
    print(f"[ENSO] Got {len(oni_records)} ONI records")

    # Fetch weekly Nino indices
    print("[ENSO] Fetching weekly Nino indices...")
    weekly_data = fetch_weekly_nino()
    print(f"[ENSO] Got weekly data for 4 Nino regions")

    # Classify
    classification = compute_oni_classification(oni_records)

    # Generate signals
    signals = generate_trading_signals(classification)
    exits = generate_exit_signals(classification)

    # Latest weekly Nino 3.4 values
    latest_weekly = {}
    if weekly_data.get("nino34"):
        last_4_weeks = weekly_data["nino34"][-4:]
        latest_weekly["nino34"] = last_4_weeks

    # Build output
    result = {
        "metadata": {
            "fetched_at": datetime.now().isoformat(),
            "source": "NOAA CPC",
            "oni_url": ONI_URL,
            "weekly_url": WEEKLY_URL,
            "version": "1.0.0",
        },
        "classification": classification,
        "latest_oni_records": oni_records[-6:],  # Last 6 months
        "oni_full_series": oni_records,  # Full ONI history
        "weekly_indices": latest_weekly,
        "trading_signals": signals,
        "exit_signals": exits,
        "thresholds": THRESHOLDS,
        "historical_events": find_historical_events(oni_records),
    }

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"[ENSO] Saved to {OUTPUT_FILE}")
    print(f"[ENSO] Classification: {classification['classification']}")
    print(f"[ENSO] Latest ONI: {classification['latest_oni']} ({classification['latest_period']})")
    print(f"[ENSO] Trend: {classification['trend']}")
    print(f"[ENSO] Trading mode: {signals['mode']}")

    return result


def find_historical_events(records: list[dict]) -> list[dict]:
    """Identify historical El Nino/La Nina events from ONI records."""
    events = []
    in_event = False
    event_start = None
    event_type = None
    event_peak = 0
    event_peak_period = None

    for r in sorted(records, key=lambda x: (x["year"], x["month"])):
        if r["anomaly"] >= 0.5:
            if not in_event or event_type != "el_nino":
                # End previous event if any
                if in_event and event_start:
                    _close_event(events, event_type, event_start, r["period"],
                                 event_peak, event_peak_period)
                in_event = True
                event_type = "el_nino"
                event_start = r["period"]
                event_peak = r["anomaly"]
                event_peak_period = r["period"]
            else:
                if r["anomaly"] > event_peak:
                    event_peak = r["anomaly"]
                    event_peak_period = r["period"]
        else:
            if in_event and event_type == "el_nino":
                _close_event(events, event_type, event_start, r["period"],
                             event_peak, event_peak_period)
                in_event = False
                event_start = None
                event_peak = 0
                event_peak_period = None

    # Close final event if still active
    if in_event and event_start and event_peak >= 0.5:
        last_period = records[-1]["period"] if records else event_start
        _close_event(events, event_type, event_start, last_period,
                     event_peak, event_peak_period)

    # Filter to significant events (moderate or stronger)
    significant = [e for e in events if e["peak_strength"] in ("super", "strong")]
    return significant


def _close_event(events, etype, start, end, peak, peak_period):
    """Helper to close an event and determine its strength."""
    if peak >= THRESHOLDS["super"]:
        strength = "super"
    elif peak >= THRESHOLDS["strong"]:
        strength = "strong"
    elif peak >= THRESHOLDS["moderate"]:
        strength = "moderate"
    else:
        strength = "weak"

    events.append({
        "type": etype,
        "start": start,
        "end": end,
        "peak_oni": peak,
        "peak_period": peak_period,
        "peak_strength": strength,
    })


# --- CLI ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch ENSO data from NOAA CPC")
    parser.add_argument("--force", action="store_true", help="Force fresh fetch, bypass cache")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    try:
        result = fetch_enso_data(force=args.force)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            cls = result["classification"]
            sig = result["trading_signals"]
            print(f"\n{'='*60}")
            print(f"ENSO Status: {cls['classification']} | ONI: {cls['latest_oni']} | Trend: {cls['trend']}")
            print(f"Peak: {cls['peak_oni']} ({cls['peak_period']}) | Consecutive warm: {cls['consecutive_warm_months']}m")
            print(f"Trading Mode: {sig['mode']} | Active: {sig['active']}")
            print(f"Strategies: {json.dumps(sig['strategies'], indent=2)}")
            if sig["alerts"]:
                print(f"Alerts: {sig['alerts']}")
            exits = result["exit_signals"]
            if exits["exit_reasons"]:
                print(f"EXIT signals: {exits['exit_reasons']}")
            print(f"{'='*60}")

    except requests.RequestException as e:
        print(f"[ERROR] Failed to fetch ENSO data: {e}", file=sys.stderr)
        sys.exit(1)