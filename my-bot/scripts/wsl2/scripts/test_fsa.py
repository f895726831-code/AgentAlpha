import sys
sys.path.insert(0, '/mnt/d/AgentAlpha/my-bot/scripts/wsl2')
from agent.rewards import extract_ast_skeleton
tests = [
    'Ts_Mean(($close - $open) / $open, 5)',
    'SignedPower($returns, 20)',
    '($returns * 20)',
    'Rank($vwap * $volume)',
    'Ts_Corr($close, $volume, 20)',
    'Ts_Std($returns, 5) / Ts_Std($returns, 60)',
]
for t in tests:
    sk = extract_ast_skeleton(t)
    print(f'{t[:55]:<55s} -> {sk[:70]}')