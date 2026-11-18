"""分析扩展 LLM Agent 结果"""
import json

with open('/mnt/d/AgentAlpha/my-bot/scripts/wsl2/results/llm_agent_v3_extended.json') as f:
    data = json.load(f)

print('Top-level keys:', list(data.keys()))
print()

for i, ar in enumerate(data['agent_results']):
    print(f'=== Agent {i+1} ===')
    print(f'  keys: {list(ar.keys())}')

    # Pool stats
    pool = ar.get('pool', {})
    if isinstance(pool, dict):
        print(f'  elite_size={pool.get("size",0)}, max|IC|={pool.get("max_ic",0):.4f}')
        print(f'  obs_size={pool.get("obs_size",0)}, obs_max={pool.get("obs_max_ic",0):.4f}')
        print(f'  obs_recent_mean={pool.get("obs_recent_mean_ic",0):.4f}')
        print(f'  FSA_banned={pool.get("fsa_banned",0)}')
        fails = pool.get('fsa_top_failures', [])
        if fails:
            for s, c in fails[:5]:
                print(f'    FAIL: {s[:60]}: {c}')

    # Factor pool (elite)
    fp = ar.get('factor_pool', [])
    if not fp:
        fp = ar.get('pool', [])
    if isinstance(fp, list):
        print(f'  factor_pool entries: {len(fp)}')

    # Observation pool
    obs = ar.get('observation_pool', [])
    print(f'  observation_pool entries: {len(obs)}')
    if obs:
        obs_sorted = sorted(obs, key=lambda x: -abs(x[1]))
        print(f'  Top 5 obs:')
        for formula, ic, skel, rn in obs_sorted[:5]:
            print(f'    [{ic:+.4f}] R{rn} {formula[:65]}')
            print(f'            skel: {skel[:65]}')

    # FSA stats
    fsa = ar.get('fsa_stats', {})
    if fsa:
        print(f'  FSA stats: {fsa}')

    print()

# Swarm summary
sm = data.get('swarm_summary', {})
print(f'=== Swarm Summary ===')
print(f'  pool_size: {sm.get("pool_size", 0)}')
print(f'  total_formulas: {sm.get("total_formulas", 0)}')
print(f'  total_valid: {sm.get("total_valid", 0)}')
print(f'  best: {sm.get("best_formula", "")[:60]} |IC|={abs(sm.get("best_ic", 0)):.4f}')

# FFO evaluations
ffo = sm.get('ffo_evaluations', [])
if ffo:
    print(f'\n  FFO Top 10:')
    for ev in sorted(ffo, key=lambda x: -abs(x.get('ic_mean', 0)))[:10]:
        print(f'    [{ev.get("ic_mean",0):+.4f}] {ev.get("formula","")[:65]}')