#!/bin/bash
# GFlowNet v4 Phase 1 — Experiment Launcher (WSL2)
#
# Usage from Windows:
#   wsl bash /mnt/d/AgentAlpha/my-bot/scripts/wsl2/scripts/run_v4_experiments.sh
#
# Or from within WSL2:
#   cd /mnt/d/AgentAlpha/my-bot/scripts/wsl2
#   bash scripts/run_v4_experiments.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTOR_LAB_DIR="$(dirname "$SCRIPT_DIR")"
cd "$FACTOR_LAB_DIR"

echo "========================================"
echo "GFlowNet v4 Phase 1 Experiments"
echo "========================================"
echo "Working dir: $(pwd)"
echo "Python:      $(which python3 || which python)"
echo "PyTorch:     $(python -c 'import torch; print(torch.__version__)' 2>/dev/null || echo 'NOT FOUND')"
echo ""

# Default data dir (adjust if needed)
DATA_DIR="${GFN_DATA_DIR:-/mnt/d/qlib/qlib_data/cn_data}"

# Experiment A: Baseline (v3)
echo ">>> Experiment A: Baseline (v3 IC+diversity)"
python scripts/run_v4_experiments.py \
    --exp A \
    --episodes 200 \
    --seed 42 \
    --data-dir "$DATA_DIR" \
    --output-dir checkpoints

echo ""
echo ">>> Experiment C: IC+Archive (MAP-Elites)"
python scripts/run_v4_experiments.py \
    --exp C \
    --episodes 200 \
    --seed 42 \
    --data-dir "$DATA_DIR" \
    --output-dir checkpoints

echo ""
echo ">>> Experiment B: IC+Novelty (v4, no Archive)"
python scripts/run_v4_experiments.py \
    --exp B \
    --episodes 200 \
    --seed 42 \
    --data-dir "$DATA_DIR" \
    --output-dir checkpoints

echo ""
echo ">>> Experiment D: Full v4 (L1+L2+L3 + Archive + DualChannel)"
python scripts/run_v4_experiments.py \
    --exp D \
    --episodes 200 \
    --seed 42 \
    --data-dir "$DATA_DIR" \
    --output-dir checkpoints

echo ""
echo ">>> Running cross-experiment comparison..."
python scripts/run_v4_experiments.py \
    --exp all \
    --episodes 1 \
    --data-dir "$DATA_DIR" \
    --output-dir checkpoints \
    --quiet 2>/dev/null || true

echo ""
echo "========================================"
echo "All experiments complete."
echo "Results in: $FACTOR_LAB_DIR/checkpoints/"
echo "========================================"