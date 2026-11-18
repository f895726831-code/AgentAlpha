"""Remove comparison-based factors from FactorRegistry."""
import sys
sys.path.insert(0, 'D:/AgentAlpha/my-bot/scripts/wsl2')
from factory.metadata import FactorRegistry

COMPARISON_OPS = {'>', '<', '>=', '<=', '==', '!='}
registry = FactorRegistry()

to_remove = []
for key, meta in list(registry._index.items()):
    if meta.source_channel == 'gflownet':
        if any(op in meta.formula for op in COMPARISON_OPS):
            to_remove.append(key)

for key in to_remove:
    del registry._index[key]
registry._save()

gfn = registry.get_by_channel('gflownet')
active = [m for m in gfn if abs(m.ic_mean) >= 0.02]
print(f'Removed: {len(to_remove)} comparison factors')
print(f'Remaining gflownet: {len(gfn)} ({len(active)} active)')
for m in gfn[:5]:
    print(f'  {m.formula[:60]} IC={m.ic_mean:.4f}')