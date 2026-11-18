"""P0-C: Incremental IC validation — Phase 1 factors vs v4.3 baseline."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from phase1_bridge import load_p1_factors, list_p1_factors

print("=" * 60)
print("Phase 1 Incremental IC Validation")
print("=" * 60)

# Load returns (forward 1d) for CSI500
print("\n[1] Loading returns data...")
from ffo.data_loader import load_qlib_data
df = load_qlib_data(universe="csi500", start="2015-01-05",
                    fields=["$close"])
close_matrix = df["close"].unstack().T  # date x instrument
returns = close_matrix.pct_change(fill_method=None)  # contemporaneous
forward_returns = returns.shift(-1)  # T+1 forward returns

print(f"  Returns: {returns.shape[0]} dates x {returns.shape[1]} stocks")

# Load Phase 1 factors
print("\n[2] Loading Phase 1 factors...")
p1_names = list_p1_factors()
print(f"  Available: {len(p1_names)}")

# Load all 60 factors (this will use significant memory)
# Let's load in batches of 10
all_results = []
batch_size = 10
for batch_start in range(0, len(p1_names), batch_size):
    batch = p1_names[batch_start:batch_start + batch_size]
    print(f"  Batch {batch_start//batch_size + 1}: {len(batch)} factors...")

    try:
        p1_df = load_p1_factors(factor_names=batch, start="2015-01-05", end="2024-12-31")
    except Exception as e:
        print(f"    ERROR: {e}")
        continue

    for col in p1_df.columns:
        factor_matrix = p1_df[col].unstack().T  # date x instrument

        # Align dates with returns
        common_dates = factor_matrix.index.intersection(forward_returns.index)
        if len(common_dates) < 30:
            continue

        fv = factor_matrix.loc[common_dates]
        fr = forward_returns.loc[common_dates]

        # Compute daily cross-sectional RankIC
        rankic_daily = []
        ic_daily = []
        for d_idx in range(len(common_dates)):
            f_day = fv.iloc[d_idx].values
            r_day = fr.iloc[d_idx].values
            mask = ~np.isnan(f_day) & ~np.isnan(r_day)
            if mask.sum() < 30:
                continue

            # Rank IC (Spearman)
            from scipy.stats import spearmanr
            r_ic, _ = spearmanr(f_day[mask], r_day[mask])
            if not np.isnan(r_ic):
                rankic_daily.append(r_ic)

            # Pearson IC
            p_ic = np.corrcoef(f_day[mask], r_day[mask])[0, 1]
            if not np.isnan(p_ic):
                ic_daily.append(p_ic)

        if not rankic_daily:
            continue

        rankic_arr = np.array(rankic_daily)
        ic_arr = np.array(ic_daily)

        result = {
            "factor": col,
            "rankic_mean": float(np.mean(rankic_arr)),
            "rankic_std": float(np.std(rankic_arr)),
            "rankic_ir": float(np.mean(rankic_arr) / np.std(rankic_arr)) if np.std(rankic_arr) > 0 else 0,
            "ic_mean": float(np.mean(ic_arr)),
            "ic_ir": float(np.mean(ic_arr) / np.std(ic_arr)) if np.std(ic_arr) > 0 else 0,
            "n_days": len(rankic_daily),
            "positive_rate": float((rankic_arr > 0).mean()),
        }
        all_results.append(result)

# Sort by absolute RankIC
all_results.sort(key=lambda x: -abs(x["rankic_mean"]))

# Report
print(f"\n[3] Results: {len(all_results)} factors evaluated")
print(f"\n{'Rank':<5s} {'Factor':<50s} {'RankIC':>8s} {'ICIR':>7s} {'+Rate':>6s} {'Days':>6s}")
print("-" * 85)

strong_count = 0
pass_count = 0
for i, r in enumerate(all_results):
    marker = "**" if abs(r["rankic_mean"]) > 0.03 else " *" if abs(r["rankic_mean"]) > 0.015 else "  "
    if abs(r["rankic_mean"]) > 0.015:
        pass_count += 1
    if abs(r["rankic_mean"]) > 0.03:
        strong_count += 1
    print(f" {i+1:<4d} {marker} {r['factor'][:48]:<48s} {r['rankic_mean']:>+7.4f} {r['rankic_ir']:>+7.3f} {r['positive_rate']:>5.1%} {r['n_days']:>5d}")

# Summary
rankics = [abs(r["rankic_mean"]) for r in all_results]
print(f"\n[4] Summary")
print(f"  Evaluated:  {len(all_results)}")
print(f"  Pass (>|0.015|): {pass_count} ({100*pass_count/max(len(all_results),1):.0f}%)")
print(f"  Strong (>|0.03|): {strong_count} ({100*strong_count/max(len(all_results),1):.0f}%)")
if rankics:
    print(f"  RankIC: mean={np.mean(rankics):.4f}, median={np.median(rankics):.4f}, max={np.max(rankics):.4f}")
    print(f"  RankIC P25={np.percentile(rankics, 25):.4f}, P75={np.percentile(rankics, 75):.4f}")

# Save
import json
output_path = Path("results/phase1_incremental_ic.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)
print(f"\n  Saved: {output_path}")