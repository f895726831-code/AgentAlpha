#!/usr/bin/env python3
"""
P0 Bridge: FactorRegistry → signal-factory contract JSON

读取 FactorRegistry 中的 GFlowNet 因子，转换为 export_signals.py 可消费的
factor_results JSON，然后生成 AgentAlpha 契约。

流程:
  1. 读取 factor_registry.json（或直接使用已注册的 FactorRegistry）
  2. 过滤 gflownet 通道的因子
  3. 转换为 factor_results 格式
  4. 调用 agent_alpha_bridge.generate_contract() 生成契约
  5. 保存到 signal-factory/data/output/signals/{date}.json

用法:
  cd D:\AgentAlpha\my-bot\scripts\wsl2
  python scripts/p0_bridge_to_contract.py [--min-ic 0.02] [--date 2026-10-22]
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
WSL2_DIR = SCRIPT_DIR.parent
FM_TRADE_DIR = WSL2_DIR.parent  # my-bot/
sys.path.insert(0, str(WSL2_DIR))

# signal-factory paths
QSIG_DIR = Path("D:/AgentAlpha/signal-factory")
QSIG_SRC = QSIG_DIR / "src"
sys.path.insert(0, str(QSIG_DIR))

from factory.metadata import FactorRegistry


def registry_to_factor_results(
    registry: FactorRegistry,
    channel: str = "gflownet",
    min_ic: float = 0.0,
) -> dict:
    """
    Convert FactorRegistry entries to the factor_results format
    expected by export_signals.py / agent_alpha_bridge.py.
    """
    factors = registry.get_by_channel(channel)

    factor_list = []
    evaluations = {}

    for i, meta in enumerate(factors):
        if abs(meta.ic_mean) < min_ic:
            continue

        fid = f"gf_{i + 1:03d}"
        entry = {
            "id": fid,
            "name": meta.formula,
            "source": meta.source_channel,
            "description": (
                f"[GFlowNet v2.2 SubTB] {meta.formula} "
                f"(IC={meta.ic_mean:.4f}, complexity={meta.complexity}, "
                f"ops: {','.join(meta.operators_used[:5])})"
            ),
            "ic_mean": meta.ic_mean,
            "pit_verified": False,  # GFlowNet factors are FFO-evaluated, not PIT-verified
            "ic_verified": True,     # IC verified via FFO backtest
            "last_trained": meta.discovery_date,
        }
        factor_list.append(entry)
        evaluations[fid] = {
            "ic_mean": meta.ic_mean,
            "ic_ir": None,  # IR not computed for GFlowNet factors yet
        }

    active_count = len([f for f in factor_list if abs(f["ic_mean"]) >= 0.02])

    return {
        "factors": factor_list,
        "evaluations": evaluations,
        "pit_results": {},  # No PIT for GFlowNet (FFO-evaluated)
        "factor_signals": {},  # No per-stock signals yet
        "summary": {
            "total_generated": len(factor_list),
            "pit_passed": 0,
            "ic_filtered": active_count,
            "model": "gflownet-v2.2-subtb",
        },
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="FactorRegistry → AgentAlpha contract bridge")
    parser.add_argument("--min-ic", type=float, default=0.02,
                        help="Minimum |IC| threshold (default: 0.02)")
    parser.add_argument("--date", type=str, default=None,
                        help="Contract date (default: today)")
    parser.add_argument("--registry", type=str, default=None,
                        help="Path to factor_registry.json")
    parser.add_argument("--output-dir", type=str,
                        default=str(QSIG_DIR / "data/output/signals"),
                        help="Output directory for contract JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate factor_results but don't write contract")
    parser.add_argument("--channel", type=str, default="gflownet",
                        help="FactorRegistry channel (default: gflownet)")
    args = parser.parse_args()

    run_date = args.date or datetime.now().strftime("%Y-%m-%d")

    # ─── 1. Load FactorRegistry ───
    registry = FactorRegistry(storage_path=args.registry)
    print(f"[Registry] {len(registry)} total factors")

    gfn_factors = registry.get_by_channel(args.channel)
    print(f"[Registry] {len(gfn_factors)} factors from channel '{args.channel}'")

    # ─── 2. Convert to factor_results ───
    factor_results = registry_to_factor_results(
        registry, channel=args.channel, min_ic=args.min_ic
    )

    n_factors = len(factor_results["factors"])
    n_active = factor_results["summary"]["ic_filtered"]
    print(f"[Convert] {n_factors} factors (min_ic={args.min_ic}), {n_active} active")

    if n_factors == 0:
        print("[ERROR] No factors met the threshold. Try --min-ic 0")
        return 1

    # Show top 5
    print("\nTop 5 factors for contract:")
    for f in factor_results["factors"][:5]:
        print(f"  {f['id']}: {f['name']} (IC={f['ic_mean']:.4f})")

    # ─── 3. Save intermediate factor_results for debugging ───
    intermediate_path = WSL2_DIR / "data" / f"gfn_factor_results_{run_date}.json"
    with open(intermediate_path, "w", encoding="utf-8") as f:
        json.dump(factor_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[Save] Intermediate factor_results → {intermediate_path}")

    if args.dry_run:
        print("[Dry-run] Stopping before contract generation.")
        return 0

    # ─── 4. Generate AgentAlpha contract ───
    from src.export.agent_alpha_bridge import generate_contract, save_contract

    contract = generate_contract(
        factor_results=factor_results,
        backtest_results=None,  # No per-factor backtest yet
        pit_verified=False,      # GFlowNet factors are FFO-evaluated, not PIT
        data_coverage={
            "stocks": 500,
            "start": "2018-01-01",
            "end": "2020-12-31",
            "universe": "csi500",
            "note": "FFO evaluation window (v2.2 SubTB training)",
        },
    )

    # Override date
    contract["meta"]["date"] = run_date

    output_path = save_contract(contract, args.output_dir)
    print(f"[Contract] Saved to {output_path}")
    print(f"[Contract] {len(contract['factors'])} factors, "
          f"PIT={contract['meta']['pit_verified']}")

    for w in contract.get("warnings", []):
        print(f"  WARNING: {w}")

    # ─── 5. Also save a copy to AgentAlpha output dir ───
    fm_output = FM_TRADE_DIR / "data/output"
    fm_output.mkdir(parents=True, exist_ok=True)
    fm_contract_path = fm_output / f"signal_factory_gfn_{run_date}.json"
    with open(fm_contract_path, "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2, ensure_ascii=False, default=str)
    print(f"[Copy] Also saved to {fm_contract_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())