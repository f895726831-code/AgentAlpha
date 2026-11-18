import json, sys
from pathlib import Path

formulas = []
for fp in ['results/llm_agent_v3_clean.json', 'results/llm_agent_v3_run2.json', 'results/gfn_real_data_eval.json']:
    if Path(fp).exists():
        with open(fp) as f:
            data = json.load(f)
        if 'swarm_summary' in data:
            formulas.extend([p[0] for p in data['swarm_summary']['pool']])
        elif isinstance(data, list):
            for e in data:
                if e.get('error') is None and abs(e.get('ffo_rankic', 0)) > 0.015:
                    formulas.append(e['formula'])

# Deduplicate
seen = set()
unique = []
for f in formulas:
    key = f.lower().replace(' ', '')
    if key not in seen:
        seen.add(key)
        unique.append(f)

# Filter trivial
nontrivial = []
for f in unique:
    stripped = f.replace(' ', '')
    has_volume = '$volume' in f
    has_close = '$close' in f
    has_open = '$open' in f
    has_high = '$high' in f
    has_low = '$low' in f
    has_vwap = '$vwap' in f
    has_returns = '$returns' in f
    has_price = has_close or has_open or has_high or has_low
    has_multi = (has_returns and (has_volume or has_price or has_vwap)) or (has_price and has_volume)

    is_pure = (stripped.startswith('($returns') or
               stripped.startswith('Scale($returns') or
               stripped.startswith('Abs($returns') or
               stripped.startswith('$returns') or
               stripped.startswith('SignedPower($returns') or
               stripped.startswith('Ts_Min($returns') or
               stripped.startswith('Ts_Max($returns') or
               stripped.startswith('Ts_Mean($returns') or
               stripped.startswith('Ts_Sum($returns') or
               stripped.startswith('Min($returns'))

    if is_pure and not has_multi:
        continue
    nontrivial.append(f)

print(f'Total unique: {len(unique)}')
print(f'After trivial filter: {len(nontrivial)}')
print()
for i, f in enumerate(nontrivial):
    print(f'  [{i+1}] {f[:100]}')