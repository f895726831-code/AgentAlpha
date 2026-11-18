#!/usr/bin/env python3
"""#9 Orthogonality Monitoring — ML signal overlap with P1"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'output')

# Load ML signals
ml_path = os.path.join(DATA_DIR, 'alpha_signals.json')
with open(ml_path) as f:
    ml = json.load(f)

# Load P1 signals
p1_path = os.path.join(DATA_DIR, 'alpha_signals_p1.json')
with open(p1_path) as f:
    p1 = json.load(f)

# Extract all stocks
def extract_stocks(signal_data):
    """Extract all stock symbols from signal file"""
    stocks = []
    if 'data' in signal_data:
        for item in signal_data['data']:
            if isinstance(item, dict) and 'symbol' in item:
                stocks.append(item)
    elif 'top_10' in signal_data:
        for item in signal_data['top_10']:
            if isinstance(item, dict) and 'symbol' in item:
                stocks.append(item)
    return stocks

ml_stocks = extract_stocks(ml)
p1_stocks = extract_stocks(p1)

print("=" * 60)
print("#9 Orthogonality Monitoring — ML vs P1 Signal Overlap")
print("=" * 60)
print(f"ML file:  date={ml.get('date')}, model={ml.get('model')}, stocks={len(ml_stocks)}")
print(f"P1 file:  date={p1.get('date')}, model={p1.get('model')}, stocks={len(p1_stocks)}")

# Sort by alpha_score (descending)
ml_sorted = sorted(ml_stocks, key=lambda x: x.get('alpha_score', 0), reverse=True)
p1_sorted = sorted(p1_stocks, key=lambda x: x.get('alpha_score', 0), reverse=True)

print(f"\nML top-5:  {', '.join(s['symbol'] for s in ml_sorted[:5])}")
print(f"P1 top-5:  {', '.join(s['symbol'] for s in p1_sorted[:5])}")

print(f"\n{'N':<6} {'ML':<6} {'P1':<6} {'Overlap':<8} {'Jaccard':<8} {'Verdict'}")
print("-" * 52)

for N in [10, 20, 50, 100]:
    ml_top = set(s['symbol'] for s in ml_sorted[:N])
    p1_top = set(s['symbol'] for s in p1_sorted[:N])
    overlap = ml_top & p1_top
    union = ml_top | p1_top
    jaccard = len(overlap) / len(union) * 100 if union else 0

    if jaccard < 5:
        verdict = "PASS (low overlap)"
    elif jaccard < 15:
        verdict = "WARN (moderate)"
    else:
        verdict = "FAIL (converging)"

    print(f"{N:<6} {len(ml_top):<6} {len(p1_top):<6} {len(overlap):<8} {jaccard:<8.1f}% {verdict}")

# Full universe overlap
ml_all_symbols = set(s['symbol'] for s in ml_stocks)
p1_all_symbols = set(s['symbol'] for s in p1_stocks)
all_overlap = ml_all_symbols & p1_all_symbols
all_union = ml_all_symbols | p1_all_symbols
all_jaccard = len(all_overlap) / len(all_union) * 100 if all_union else 0

print(f"\nFull universe: ML={len(ml_all_symbols)}, P1={len(p1_all_symbols)}, "
      f"Overlap={len(all_overlap)}, Jaccard={all_jaccard:.1f}%")

# Check if the signal sources use different stock code prefixes
ml_prefixes = {s[:2] for s in ml_all_symbols}
p1_prefixes = {s[:2] for s in p1_all_symbols}
print(f"ML prefixes:  {ml_prefixes}")
print(f"P1 prefixes:  {p1_prefixes}")

# Score correlation on overlapping stocks
if all_overlap:
    ml_scores = {}
    for s in ml_sorted:
        ml_scores[s['symbol']] = s.get('alpha_score', 0)
    p1_scores = {}
    for s in p1_sorted:
        p1_scores[s['symbol']] = s.get('alpha_score', 0)

    overlap_scores = [(ml_scores[sym], p1_scores[sym]) for sym in all_overlap]
    if len(overlap_scores) >= 5:
        import math
        n = len(overlap_scores)
        mx = sum(a for a, b in overlap_scores) / n
        my = sum(b for a, b in overlap_scores) / n
        num = sum((a - mx) * (b - my) for a, b in overlap_scores)
        dx = math.sqrt(sum((a - mx)**2 for a, b in overlap_scores))
        dy = math.sqrt(sum((b - my)**2 for a, b in overlap_scores))
        corr = num / (dx * dy) if dx * dy > 0 else 0
        print(f"\nScore correlation on {n} overlapping stocks: r={corr:.4f}")
        verdict = "PASS (uncorrelated)" if abs(corr) < 0.3 else "WARN (correlated)" if abs(corr) < 0.5 else "FAIL (highly correlated)"
        print(f"  Verdict: {verdict}")
        if abs(corr) > 0.3:
            print("  ⚠️  ML and P1 scores correlated on shared stocks — not fully orthogonal")

print("\n✓ Orthogonality monitoring complete")