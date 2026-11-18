r"""
Phase 1 Factor Consensus Signal Generator

Loads all 60 Phase 1 factors via phase1_bridge, computes cross-sectional
z-scores, weights by |RankIC| from incremental IC validation, and outputs
a consensus alpha signal in the same format as the ML ensemble.

Usage:
    cd D:/AgentAlpha/my-bot/scripts/wsl2
    python scripts/generate_p1_signals.py                    # latest date
    python scripts/generate_p1_signals.py --date 2026-11-04  # specific date
    python scripts/generate_p1_signals.py --top-n 30         # top 30 output

Output:
    - signal-factory/data/output/alpha_signals_p1.json
    - AgentAlpha/my-bot/data/output/alpha_signals_p1.json
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Path setup
SCRIPT_DIR = Path(__file__).resolve().parent
WSL2_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(WSL2_DIR))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from phase1_bridge import load_p1_factors, list_p1_factors

# Output paths
QSIG_DIR = Path("D:/AgentAlpha/signal-factory")
QSIG_OUTPUT = QSIG_DIR / "data/output"
FM_TRADE_DIR = WSL2_DIR.parent.parent  # my-bot/ (wsl2 -> scripts -> my-bot)
FM_TRADE_OUTPUT = FM_TRADE_DIR / "data/output"
IC_RESULTS_PATH = WSL2_DIR / "results/phase1_incremental_ic.json"

# Calendar
CALENDAR_FILE = Path("D:/qlib/qlib_data/cn_data/calendars/day.txt")

# HMM-Macro divergence state (shared with stable_timi_bridge.py)
HMM_DIVERGENCE_STATE = Path("D:/AgentAlpha/signal-factory/data/macro_assets/hmm_divergence_state.json")

# HMM regime filter defaults
HMM_AUTO_REJECT_LEVELS = {"alert"}      # 分歧 >2周 → 拒绝生成信号
HMM_LOW_CONFIDENCE_REGIMES = {"risk_off", "bear"}  # 熊市 → 全部 low_confidence

# Consensus quality gate (W47 #1C)
MIN_CONSENSUS_RAW_SIGMA = 0.5  # raw Z-score must be at least μ + 0.5σ
LOW_CONFIDENCE_SCORE_THRESHOLD = 0.4  # raised from 0.3 (W47 #1C)

# Sector concentration limit (W47 #1D)
SECTOR_MAP_PATH = Path("D:/AgentAlpha/my-bot/data/sector-map.json")
MAX_SECTOR_CONCENTRATION = 0.30  # flag if any single sector > 30% of top_50


def load_calendar():
    """Load Qlib trading calendar."""
    with open(CALENDAR_FILE) as f:
        return pd.DatetimeIndex([line.strip() for line in f if line.strip()])


def get_latest_trading_day(calendar, target_date=None):
    """Get the latest trading day <= target_date with valid market data.

    Walks backwards through calendar to skip non-trading days that have
    NaN data (calendar may include dates without actual market data).
    """
    if target_date is None:
        target_date = pd.Timestamp.now().normalize()
    else:
        target_date = pd.Timestamp(target_date)

    valid = calendar[calendar <= target_date]
    if len(valid) == 0:
        raise ValueError(f"No trading day <= {target_date.date()}")

    # Walk backward to find a date with actual close data
    # Use sh600000 (highly liquid, always has data) as reference
    FEATURES_DIR = Path("D:/qlib/qlib_data/cn_data/features")
    close_bin = FEATURES_DIR / "sh600000" / "close.day.bin"
    if close_bin.exists():
        close_data = np.fromfile(close_bin, dtype="<f")
        close_start = int(close_data[0])
        close_vals = close_data[1:]
        for i in range(len(valid) - 1, -1, -1):
            cal_idx = calendar.get_loc(valid[i])
            arr_idx = cal_idx - close_start
            if 0 <= arr_idx < len(close_vals):
                if not np.isnan(close_vals[arr_idx]):
                    return valid[i]
        # Fallback: return last calendar date even without data
    return valid[-1]


def load_ic_weights(min_abs_ic=0.015):
    """Load incremental IC results and return factor->|IC| weight map."""
    if not IC_RESULTS_PATH.exists():
        print(f"[WARN] IC results not found at {IC_RESULTS_PATH}, using equal weights")
        return {}

    with open(IC_RESULTS_PATH, "r", encoding="utf-8") as f:
        ic_data = json.load(f)

    weights = {}
    for entry in ic_data:
        abs_ic = abs(entry["rankic_mean"])
        if abs_ic >= min_abs_ic:
            weights[entry["factor"]] = abs_ic

    print(f"[IC] {len(weights)}/{len(ic_data)} factors with |IC| >= {min_abs_ic}")
    return weights


def load_hmm_state():
    """Load HMM-Macro divergence state for regime-aware signal filtering.

    Returns:
        dict with keys: hmm_regime, diverged, level, divergence_weeks,
                        macro_regime, recommendation
    """
    if not HMM_DIVERGENCE_STATE.exists():
        print("[HMM] No divergence state file — assuming neutral regime")
        return {"hmm_regime": "neutral", "diverged": False, "level": "normal",
                "divergence_weeks": 0, "macro_regime": "neutral", "recommendation": "none"}

    try:
        with open(HMM_DIVERGENCE_STATE, "r", encoding="utf-8") as f:
            state = json.load(f)
        result = {
            "hmm_regime": state.get("hmm_regime", "neutral"),
            "diverged": state.get("diverged", False),
            "level": state.get("level", "normal"),
            "divergence_weeks": state.get("divergence_weeks", 0),
            "macro_regime": state.get("macro_regime", "neutral"),
            "recommendation": state.get("recommendation", "none"),
        }
        print(f"[HMM] regime={result['hmm_regime']} div={result['diverged']} "
              f"level={result['level']} weeks={result['divergence_weeks']}")
        return result
    except Exception as e:
        print(f"[HMM] Failed to load state: {e} — assuming neutral")
        return {"hmm_regime": "neutral", "diverged": False, "level": "normal",
                "divergence_weeks": 0, "macro_regime": "neutral", "recommendation": "none"}


def check_hmm_filter(hmm_state, no_filter=False):
    """Check whether P1 signals should be filtered based on HMM regime.

    Returns:
        (reject: bool, low_confidence_all: bool, reason: str)
    """
    if no_filter:
        return False, False, "HMM filter bypassed by --no-hmm-filter"

    regime = hmm_state.get("hmm_regime", "neutral")
    level = hmm_state.get("level", "normal")
    weeks = hmm_state.get("divergence_weeks", 0)
    diverged = hmm_state.get("diverged", False)

    # Auto-reject: HMM-Macro divergence is severe (>2 weeks + alert level)
    if diverged and level in HMM_AUTO_REJECT_LEVELS and weeks > 2:
        return True, False, (
            f"HMM-Macro divergence ALERT ({weeks}w): "
            f"HMM={regime} vs Macro={hmm_state.get('macro_regime')} — "
            f"rejecting all P1 signals for safety"
        )

    # Low confidence all: HMM regime is risk_off or bear
    if regime in HMM_LOW_CONFIDENCE_REGIMES:
        return False, True, (
            f"HMM regime={regime} — all P1 signals flagged low_confidence"
        )

    # Warning: divergence exists but not yet alert-level
    if diverged and level == "warning":
        return False, True, (
            f"HMM-Macro divergence WARNING ({weeks}w) — "
            f"P1 signals flagged low_confidence"
        )

    return False, False, f"HMM regime={regime} — signals generated normally"


# ─── Sector Concentration (W47 #1D) ──────────────────────────────────

def _p1_symbol_to_windcode(symbol: str) -> str:
    """Convert P1 symbol format (sh600157) to Wind code (600157.SH)."""
    if symbol.startswith("sh"):
        return symbol[2:] + ".SH"
    elif symbol.startswith("sz"):
        return symbol[2:] + ".SZ"
    return symbol  # fallback


def _load_sector_map():
    """Load sector classification map: Wind code → sector label.

    Returns:
        dict: {"600157.SH": "能源-煤炭", ...} or {} if unavailable
    """
    if not SECTOR_MAP_PATH.exists():
        print(f"[Sector] No sector map at {SECTOR_MAP_PATH} — skipping")
        return {}
    try:
        with open(SECTOR_MAP_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Extract top-level sector (before first dash)
        result = {}
        for code, label in raw.items():
            top = label.split("-")[0] if "-" in label else label
            result[code] = top
        print(f"[Sector] Loaded {len(result)} stock-sector mappings")
        return result
    except Exception as e:
        print(f"[Sector] Failed to load sector map: {e}")
        return {}


def check_sector_concentration(signals, sector_map):
    """Check sector concentration in top-N signals.

    Only considers stocks with known sector mapping. Unmapped stocks
    ("其他") are excluded from the denominator to avoid false alarms
    from incomplete sector coverage.

    Args:
        signals: list of {"symbol": "sh600157", ...} dicts
        sector_map: {windcode: sector_label} dict

    Returns:
        (overconcentrated_sectors: set, flagged_count: int, report: str)
    """
    if not sector_map or len(signals) < 5:
        return set(), 0, ""

    sector_counts = {}
    signal_sectors = {}  # symbol → sector
    unmapped_count = 0

    for s in signals:
        wc = _p1_symbol_to_windcode(s["symbol"])
        sector = sector_map.get(wc)
        if sector is None:
            sector = "其他"
            unmapped_count += 1
        signal_sectors[s["symbol"]] = sector
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    n = len(signals)
    n_known = n - unmapped_count

    # Skip if too few stocks have known sectors
    if n_known < n * 0.15:
        print(f"[Sector] Skipped — only {n_known}/{n} stocks mapped ({n_known/n:.0%})")
        return set(), 0, "sector_coverage_insufficient"

    # Only check concentration among KNOWN sectors (exclude "其他")
    known_counts = {k: v for k, v in sector_counts.items() if k != "其他"}
    if not known_counts:
        return set(), 0, ""

    overconc = set()
    report_lines = [f"  (known: {n_known}/{n} stocks, {len(known_counts)} sectors)"]
    for sector, count in sorted(known_counts.items(), key=lambda x: -x[1]):
        pct = count / n_known  # percentage of known-mapped stocks
        if pct > MAX_SECTOR_CONCENTRATION:
            overconc.add(sector)
            report_lines.append(
                f"  ⚠ {sector}: {count}/{n_known} ({pct:.0%}) — exceeds {MAX_SECTOR_CONCENTRATION:.0%} limit"
            )
        else:
            report_lines.append(
                f"     {sector}: {count}/{n_known} ({pct:.0%})"
            )

    if overconc:
        print(f"[Sector] Concentration ALERT ({len(overconc)} sectors):")
        for line in report_lines:
            print(line)
        flagged = sum(1 for s in signals if signal_sectors.get(s["symbol"]) in overconc)
        return overconc, flagged, "\n".join(report_lines)
    else:
        print(f"[Sector] Concentration OK — {n_known} known stocks, no sector exceeds {MAX_SECTOR_CONCENTRATION:.0%}")
        return overconc, 0, "\n".join(report_lines)


def generate_p1_consensus(date_str, ic_weights=None, top_n=50, hmm_filter=True,
                          min_consensus_sigma=0.5):
    """
    Generate IC-weighted consensus signal from Phase 1 factors.

    Args:
        date_str: Target trading day (YYYY-MM-DD)
        ic_weights: {factor_name: |IC|} weight map
        top_n: Number of top stocks to output
        hmm_filter: Enable HMM regime-based signal filtering (default: True)

    Returns:
        dict in alpha_signals.json-compatible format, or None if HMM rejects
    """
    # Phase 0: HMM regime gate (W47 #1B)
    hmm_state = load_hmm_state()
    reject, hmm_low_conf, hmm_reason = check_hmm_filter(hmm_state, no_filter=not hmm_filter)
    print(f"[HMM Gate] {hmm_reason}")
    if reject:
        print("[HMM Gate] ✗ Signals rejected — returning None")
        return None

    if ic_weights is None:
        ic_weights = load_ic_weights()

    all_factors = list_p1_factors()
    print(f"[Bridge] {len(all_factors)} Phase 1 factors available")

    # Filter to factors with IC weights
    weighted_factors = [f for f in all_factors if f in ic_weights]
    if not weighted_factors:
        print("[ERROR] No factors with IC weights available")
        return None

    print(f"[Bridge] {len(weighted_factors)} factors with IC weights")

    # Load factor values for target date
    print(f"[Bridge] Loading factors for {date_str}...")
    try:
        df = load_p1_factors(
            factor_names=weighted_factors,
            start=date_str,
            end=date_str,
        )
    except Exception as e:
        print(f"[ERROR] Failed to load factors: {e}")
        return None

    if df.empty or df.notna().sum().sum() == 0:
        print(f"[WARN] No valid factor data for {date_str}")
        return None

    # Reshape to stocks × factors
    # df has MultiIndex (instrument, datetime), columns = factor names
    # Extract cross-section for the target date using .xs()
    try:
        stock_factor = df.xs(pd.Timestamp(date_str), level="datetime")
    except KeyError:
        print(f"[WARN] Date {date_str} not found in factor data")
        return None

    # stock_factor: index=instrument, columns=factor_names
    print(f"[Bridge] Cross-section: {stock_factor.shape[0]} stocks × {stock_factor.shape[1]} factors")

    # Per-factor valid stock count
    valid_counts = stock_factor.notna().sum()
    n_good_factors = (valid_counts >= 30).sum()
    print(f"[Bridge] {n_good_factors}/{stock_factor.shape[1]} factors with >= 30 valid stocks")

    n_valid = int(stock_factor.notna().any(axis=1).sum())
    print(f"[Bridge] {n_valid}/{len(stock_factor)} stocks with any factor data")

    if n_valid < 10:
        print("[WARN] Too few valid stocks for consensus")
        return None

    # Cross-sectional z-score per factor (with winsorization)
    z_scores = pd.DataFrame(index=stock_factor.index)
    for col in stock_factor.columns:
        vals = stock_factor[col].copy()
        valid = vals.notna()
        if valid.sum() < 30:
            continue
        # Winsorize at 1%/99% to prevent extreme outliers
        lo = vals[valid].quantile(0.01)
        hi = vals[valid].quantile(0.99)
        vals = vals.clip(lo, hi)
        mean_val = vals[valid].mean()
        std_val = vals[valid].std()
        if std_val == 0 or np.isnan(std_val):
            continue
        z_scores[col] = (vals - mean_val) / std_val

    n_factors_used = int(len(z_scores.columns))
    print(f"[Bridge] {n_factors_used} factors with >= 30 valid stocks")

    if n_factors_used == 0:
        print("[WARN] No factors passed z-score threshold")
        return None

    # IC-weighted consensus
    weights_array = np.array([ic_weights.get(f, 0) for f in z_scores.columns])
    weights_array = weights_array / weights_array.sum()  # normalize

    consensus_raw = (z_scores.fillna(0) * weights_array).sum(axis=1)

    # Quality gate: raw consensus must exceed cross-sectional threshold (W47 #1C)
    raw_mean = consensus_raw.mean()
    raw_std = consensus_raw.std()
    raw_threshold = raw_mean + min_consensus_sigma * raw_std
    n_before_filter = int(consensus_raw.notna().sum())
    quality_mask = consensus_raw >= raw_threshold
    consensus_filtered = consensus_raw[quality_mask]
    n_filtered_out = n_before_filter - len(consensus_filtered)

    print(f"[Quality] raw μ={raw_mean:+.4f} σ={raw_std:.4f} "
          f"threshold={raw_threshold:+.4f} ({min_consensus_sigma}σ)")
    print(f"[Quality] {n_filtered_out}/{n_before_filter} stocks filtered out "
          f"({n_filtered_out/n_before_filter*100:.1f}%)")

    n_quality_pass = int(len(consensus_filtered))
    if n_quality_pass < 10:
        print(f"[WARN] Too few stocks ({len(consensus_filtered)}) pass quality gate — "
              f"cross-sectional consensus too weak")
        return None

    # Rank-normalize to [-1, +1] range (robust to outliers)
    # Only rank among quality-passed stocks — weak signals excluded entirely
    ranks = consensus_filtered.rank(pct=True)  # percentile ranks [0, 1]
    consensus = (ranks - 0.5) * 2  # scale to [-1, +1]

    # Rank and output
    ranked = consensus.sort_values(ascending=False)
    top_stocks = ranked.head(top_n)

    # Build output
    # When HMM is risk_off/bear, ALL signals are low_confidence (W47 #1B)
    top_10_list = []
    for i, (symbol, score) in enumerate(top_stocks.items()):
        native_low_conf = bool(abs(float(score)) < LOW_CONFIDENCE_SCORE_THRESHOLD)
        top_10_list.append({
            "symbol": str(symbol),
            "alpha_score": round(float(score), 6),
            "rank": i + 1,
            "low_confidence": bool(hmm_low_conf or native_low_conf),
        })

    # Sector concentration check (W47 #1D)
    sector_map = _load_sector_map()
    top_50_raw = [
        {"symbol": str(s), "alpha_score": round(float(consensus[s]), 6),
         "rank": i + 1,
         "low_confidence": bool(hmm_low_conf or abs(float(consensus[s])) < LOW_CONFIDENCE_SCORE_THRESHOLD)}
        for i, s in enumerate(ranked.index[:top_n])
    ]
    overconc_sectors, sector_flagged, sector_report = check_sector_concentration(
        top_50_raw, sector_map
    )

    if overconc_sectors:
        # Retroactively flag overconcentrated stocks in both lists
        wc_lookup = {s["symbol"]: _p1_symbol_to_windcode(s["symbol"]) for s in top_50_raw}
        for s_list in (top_10_list, top_50_raw):
            for s in s_list:
                wc = wc_lookup.get(s["symbol"], "")
                sector = sector_map.get(wc, "其他")
                if sector in overconc_sectors:
                    s["low_confidence"] = True
                    s["sector_overconc"] = sector

    output = {
        "@contract": {
            "schema_version": "1.0.0",
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "as_of_date": date_str,
            "source": "phase1-factor-consensus",
            "universe": "csi500",
            "horizon": "1d",
            "model": f"ic-weighted-zscore-{n_factors_used}f",
            "total_instruments": n_valid,
            "quality_pass": n_quality_pass,
            "quality_filtered_out": n_filtered_out,
            "quality_threshold_sigma": min_consensus_sigma,
            "hmm_regime": hmm_state.get("hmm_regime", "unknown"),
            "hmm_filter_active": hmm_filter,
            "hmm_filter_reason": hmm_reason,
            "sector_overconcentrated": list(overconc_sectors) if overconc_sectors else [],
            "sector_concentration_report": sector_report,
            "staleness": {
                "design_lag_trading_days": 0,
                "data_lag_calendar_days": 0,
                "excess_lag_days": 0,
                "signal_fresh_until": (
                    pd.Timestamp(date_str) + timedelta(days=1)
                ).strftime("%Y-%m-%dT09:30:00+08:00"),
                "note": "Phase 1 bridge reads .day.bin directly, no Qlib lag",
            },
        },
        "date": date_str,
        "model": f"p1-ic-weighted-zscore-{n_factors_used}f",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "total_stocks": n_quality_pass,
        "low_confidence_count": sum(1 for s in top_10_list if s["low_confidence"]),
        "hmm_low_confidence_override": hmm_low_conf,
        "top_10": top_10_list,
        "top_50": top_50_raw,
        "factor_contributions": {
            str(f): round(float(ic_weights.get(f, 0)), 4)
            for f in z_scores.columns  # only factors that actually contributed
        },
        "factors_used": n_factors_used,
        "factors_loaded": len(weighted_factors),
        "coverage_note": f"{n_factors_used}/{len(weighted_factors)} factors had >= 30 valid stocks on {date_str}",
    }

    return output


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Phase 1 Factor Consensus Signal Generator"
    )
    parser.add_argument("--date", help="Target trading day (default: latest)")
    parser.add_argument("--top-n", type=int, default=50, help="Top N stocks (default: 50)")
    parser.add_argument("--min-ic", type=float, default=0.015,
                        help="Minimum |IC| threshold (default: 0.015)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate but don't save")
    parser.add_argument("--no-hmm-filter", action="store_true",
                        help="Bypass HMM regime filter (generate signals regardless)")
    parser.add_argument("--min-consensus-sigma", type=float, default=0.5,
                        help="Minimum raw consensus sigma above mean (default: 0.5)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show per-factor stats")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 1 Factor Consensus Signal Generator")
    print("=" * 60)

    # Determine target date
    calendar = load_calendar()
    target_date = get_latest_trading_day(calendar, args.date)
    date_str = target_date.strftime("%Y-%m-%d")
    print(f"Target date: {date_str}")
    print(f"Calendar: {calendar[0].date()} → {calendar[-1].date()} ({len(calendar)} days)")

    # Load IC weights
    ic_weights = load_ic_weights(min_abs_ic=args.min_ic)

    if args.verbose:
        sorted_ic = sorted(ic_weights.items(), key=lambda x: -x[1])
        print("\nTop 10 factors by |IC|:")
        for factor, ic in sorted_ic[:10]:
            print(f"  {ic:.4f}  {factor[:60]}")

    # Generate consensus (with HMM filter unless bypassed)
    hmm_enabled = not getattr(args, 'no_hmm_filter', False)
    if not hmm_enabled:
        print("[HMM Gate] Bypassed by --no-hmm-filter")
    output = generate_p1_consensus(date_str, ic_weights, top_n=args.top_n,
                                   hmm_filter=hmm_enabled,
                                   min_consensus_sigma=args.min_consensus_sigma)

    if output is None:
        print("\n[FAIL] Could not generate consensus signal")
        return 1

    # Display results
    print(f"\n--- Phase 1 Consensus Top 10 ---")
    for i, s in enumerate(output["top_10"]):
        conf = " ⚠" if s["low_confidence"] else "  "
        print(f"  {i+1:2d}. {s['symbol']}  score={s['alpha_score']:+.4f}{conf}")

    print(f"\nFactor contributions (top 10):")
    for f, ic in sorted(output["factor_contributions"].items(),
                         key=lambda x: -x[1])[:10]:
        print(f"  {ic:.4f}  {f[:60]}")

    if args.dry_run:
        print("\n[Dry-run] Signals not saved.")
        return 0

    # Save to signal-factory
    qsig_path = QSIG_OUTPUT / "alpha_signals_p1.json"
    QSIG_OUTPUT.mkdir(parents=True, exist_ok=True)
    with open(qsig_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n[Save] {qsig_path}")

    # Save to AgentAlpha
    fm_path = FM_TRADE_OUTPUT / "alpha_signals_p1.json"
    FM_TRADE_OUTPUT.mkdir(parents=True, exist_ok=True)
    with open(fm_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[Save] {fm_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())