"""
Phase 1 Factor Injection to Qlib Bin (v2 — use FFO FactorComputer directly)

Usage:
    python scripts/inject_phase1_factors.py
"""

import json, sys, re
from pathlib import Path
import numpy as np
import pandas as pd

# Ensure ffo module is importable (script runs from scripts/ subdir)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ffo.data_loader import load_qlib_data
from ffo.parser import parse as parse_formula
from ffo.computer import FactorComputer

QLIB_BIN = Path("D:/qlib/qlib_data/cn_data")
FEATURES_DIR = QLIB_BIN / "features"
CALENDAR_FILE = QLIB_BIN / "calendars" / "day.txt"
RESULTS_FILES = [
    "results/llm_agent_v3_clean.json",
    "results/llm_agent_v3_run2.json",
    "results/gfn_real_data_eval.json",
]


def formula_to_name(formula: str) -> str:
    """Convert formula to a safe filename prefix."""
    safe = re.sub(r'[^a-zA-Z0-9_]', '_', formula)
    safe = re.sub(r'_+', '_', safe)
    return f"p1_{safe[:45]}"


def main():
    print("=" * 60)
    print("Phase 1 Factor Injection to Qlib Bin (v2)")
    print("=" * 60)

    # ── Collect formulas ──
    print("\nCollecting Phase 1 factors...")
    formulas = []
    for fp in RESULTS_FILES:
        if not Path(fp).exists():
            continue
        with open(fp) as f:
            data = json.load(f)
        if "swarm_summary" in data:
            formulas.extend([p[0] for p in data["swarm_summary"]["pool"]])
        elif isinstance(data, list):
            for e in data:
                if e.get("error") is None and abs(e.get("ffo_rankic", 0)) > 0.015:
                    formulas.append(e["formula"])

    # Deduplicate
    seen = set()
    unique = []
    for f in formulas:
        key = f.lower().replace(" ", "")
        if key not in seen:
            seen.add(key)
            unique.append(f)

    # Filter trivial pure $returns transforms
    nontrivial = []
    for f in unique:
        s = f.replace(" ", "")
        has_vol = "$volume" in f
        has_price = any(x in f for x in ["$close", "$open", "$high", "$low", "$vwap"])
        has_ret = "$returns" in f
        multi = (has_ret and (has_vol or has_price)) or (has_price and has_vol)
        pure_ret = (s.startswith("($returns") or s.startswith("Scale($returns")
                    or s.startswith("Abs($returns") or s.startswith("$returns")
                    or s.startswith("SignedPower($returns")
                    or s.startswith("Ts_Min($returns") or s.startswith("Ts_Max($returns")
                    or s.startswith("Ts_Mean($returns") or s.startswith("Ts_Sum($returns")
                    or s.startswith("Min($returns"))
        if pure_ret and not multi:
            continue
        nontrivial.append(f)

    print(f"  Run1: 33, Run2: 29, GFlowNet: 8 passing")
    print(f"  Total unique: {len(unique)}, Non-trivial: {len(nontrivial)}")

    # ── Load market data ──
    print("\nLoading Qlib data (CSI500, 2015-01-05 -> 2026-11-03)...")
    df = load_qlib_data(
        universe="csi500",
        start="2015-01-05",
        fields=["$close", "$open", "$high", "$low", "$volume", "$vwap"],
    )
    print(f"  Shape: {df.shape}, columns: {list(df.columns)}")
    print(f"  Date range: {df.index.get_level_values('datetime').min()} -> {df.index.get_level_values('datetime').max()}")

    computer = FactorComputer(df)

    # ── Load calendar/stock list for bin writing ──
    with open(CALENDAR_FILE) as f:
        cal_dates = pd.DatetimeIndex([line.strip() for line in f if line.strip()])
    n_days = len(cal_dates)
    print(f"  Calendar: {n_days} days")

    with open(QLIB_BIN / "instruments" / "csi500.txt", "r", encoding="utf-8") as f:
        stocks = [line.strip().split("\t")[0] for line in f if line.strip()]

    # ── Inject each factor ──
    n_ok, n_err, n_skip = 0, 0, 0
    factor_names = {}

    print(f"\nInjecting {len(nontrivial)} factors...")
    for idx, formula in enumerate(nontrivial):
        short = formula[:70]
        print(f"\n[{idx+1}/{len(nontrivial)}] {short}...")

        try:
            ast = parse_formula(formula)
            result_df = computer.evaluate(ast)  # date × instrument DataFrame

            # Align to calendar
            result_df = result_df.reindex(index=cal_dates)

            valid_pct = 100 * result_df.notna().sum().sum() / result_df.size
            print(f"  Valid: {valid_pct:.1f}%")

            if valid_pct < 1.0:
                print(f"  SKIP: too few valid values")
                n_skip += 1
                continue

            # Write per stock
            name = formula_to_name(formula)
            written = 0
            for stock in stocks:
                if stock not in result_df.columns:
                    continue
                vals = result_df[stock].values.astype(np.float32)
                mask = ~np.isnan(vals)
                if not mask.any():
                    continue

                first_idx = int(np.argmax(mask))
                valid_vals = vals[first_idx:]

                stock_dir = FEATURES_DIR / stock
                stock_dir.mkdir(parents=True, exist_ok=True)

                output = np.empty(len(valid_vals) + 1, dtype=np.float32)
                output[0] = np.float32(first_idx)
                output[1:] = valid_vals
                output.tofile(stock_dir / f"{name}.day.bin")
                written += 1

            print(f"  Wrote: {written}/{len(stocks)} stocks")
            factor_names[name] = {"formula": formula, "n_stocks": written, "valid_pct": round(valid_pct, 1)}
            n_ok += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            n_err += 1

    # ── Manifest ──
    manifest_path = Path("results/phase1_injection_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump({
            "n_total": len(nontrivial),
            "n_injected": n_ok,
            "n_errors": n_err,
            "n_skipped": n_skip,
            "factors": factor_names,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Injection complete: {n_ok} injected, {n_err} errors, {n_skip} skipped")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()