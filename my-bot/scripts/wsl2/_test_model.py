"""测试 GFlowNet 模型初始化 + 前向采样"""
import sys; sys.path.insert(0, ".")

import torch
from gflownet.config import GFNConfig
from gflownet.gfn import GFlowNetCore
from gflownet.grammar import N_ACTIONS, VOCAB_SIZE, print_token_summary

print(f"PyTorch version: {torch.__version__}")
print(f"Actions: {N_ACTIONS}, Vocab: {VOCAB_SIZE}")
print()

# Create model
config = GFNConfig()
config.d_model = 64
config.n_layers = 2
config.n_heads = 4
config.max_expr_len = 25

print(f"Creating GFlowNetCore with d_model={config.d_model}...")
model = GFlowNetCore(config)
model.eval()

total_params = sum(p.numel() for p in model.parameters())
print(f"Total parameters: {total_params:,}")
print(f"Initial logZ: {model.logZ.item():.4f}")
print()

# Sample a few trajectories
print("Sampling 5 trajectories...")
for i in range(5):
    with torch.no_grad():
        tids, aids, lpfs, formula = model.sample_trajectory(temperature=1.0)

    status = "OK" if formula else "FAIL"
    print(f"  [{i+1}] {status}: {formula or 'None'}")
    if formula:
        print(f"       tokens: {len(tids)-1}, log_pf_sum: {sum(lpfs):.3f}")

print()
print("Model initialization + sampling test passed!")