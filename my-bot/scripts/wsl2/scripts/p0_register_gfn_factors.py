#!/usr/bin/env python3
"""
P0: GFlowNet Checkpoint → FactorRegistry Bridge

从 v2.2 SubTB 检查点提取 77 因子，注册到 FactorRegistry JSON。

用法:
  cd D:\AgentAlpha\my-bot\scripts\wsl2
  python scripts/p0_register_gfn_factors.py [--checkpoint PATH] [--dry-run]
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
WSL2_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(WSL2_DIR))


def main():
    parser = argparse.ArgumentParser(description="GFlowNet checkpoint → FactorRegistry bridge")
    parser.add_argument(
        "--checkpoint", type=str,
        default=str(WSL2_DIR / "checkpoints" / "gfn_v2.2_subtb_200ep.pt"),
        help="Path to GFlowNet checkpoint .pt file"
    )
    parser.add_argument("--registry", type=str, default=None,
                        help="Path to factor_registry.json (default: wsl2/data/factor_registry.json)")
    parser.add_argument("--min-ic", type=float, default=0.0,
                        help="Minimum |IC| threshold for registration (default: 0.0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract and print but don't register")
    parser.add_argument("--channel", type=str, default="gflownet",
                        help="Source channel label (default: gflownet)")
    args = parser.parse_args()

    # ─── 1. Load checkpoint ───
    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.exists():
        print(f"[ERROR] Checkpoint not found: {ckpt_path}")
        return 1

    print(f"[Load] {ckpt_path}")
    import torch
    checkpoint = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)

    factor_pool = checkpoint.get("factor_pool", [])
    if not factor_pool:
        print("[ERROR] No factor_pool in checkpoint")
        return 1

    print(f"[Extract] {len(factor_pool)} factors in checkpoint pool")

    # ─── 2. Filter by min_ic ───
    filtered = [(formula, float(ic)) for formula, ic in factor_pool
                if abs(float(ic)) >= args.min_ic]
    filtered.sort(key=lambda x: -abs(x[1]))

    print(f"[Filter] {len(filtered)} factors with |IC| >= {args.min_ic}")

    # Show top 10
    print("\nTop 10 factors:")
    for formula, ic in filtered[:10]:
        print(f"  {formula}")
        print(f"    IC={ic:.4f}")

    if args.dry_run:
        print(f"\n[Dry-run] Would register {len(filtered)} factors. Exiting without changes.")
        # Export filtered factors as JSON for inspection
        export_path = WSL2_DIR / "data" / "gfn_extracted_factors.json"
        export = {
            "source": str(ckpt_path),
            "extracted_at": datetime.now().isoformat(),
            "total_in_checkpoint": len(factor_pool),
            "filtered_count": len(filtered),
            "min_ic_threshold": args.min_ic,
            "factors": [{"formula": f, "ic": ic, "abs_ic": abs(ic)} for f, ic in filtered],
        }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(export, f, indent=2, ensure_ascii=False)
        print(f"[Export] Factors list saved to {export_path}")
        return 0

    # ─── 3. Register to FactorRegistry ───
    from factory.metadata import FactorRegistry

    registry_path = args.registry
    registry = FactorRegistry(storage_path=registry_path)

    before_count = len(registry)
    print(f"\n[Registry] Before: {before_count} factors at {registry.storage_path}")

    registry.merge_pool(filtered, channel=args.channel)

    after_count = len(registry)
    new_count = after_count - before_count
    dup_count = len(filtered) - new_count

    print(f"[Registry] After:  {after_count} factors")
    print(f"[Registry] New:    {new_count} factors")
    if dup_count > 0:
        print(f"[Registry] Dups:   {dup_count} factors (already registered, IC history updated)")

    # ─── 4. Export summary ───
    summary = registry.export_summary()
    print(f"\n[Summary]")
    print(f"  Total:     {summary['total_factors']}")
    print(f"  Active:    {summary['active_factors']} (|IC| >= 0.02)")
    print(f"  Mean |IC|: {summary['mean_abs_ic']:.4f}")
    print(f"  Channels:  {summary['by_channel']}")
    if summary['top_factors']:
        print(f"  Top factor: {summary['top_factors'][0]['formula']} "
              f"(IC={summary['top_factors'][0]['ic']:.4f})")

    # ─── 5. Also save extracted factors JSON (for export_signals.py bridge) ───
    export_path = WSL2_DIR / "data" / "gfn_extracted_factors.json"
    export = {
        "source": str(ckpt_path),
        "extracted_at": datetime.now().isoformat(),
        "total_in_checkpoint": len(factor_pool),
        "registered_count": new_count,
        "duplicate_count": dup_count,
        "min_ic_threshold": args.min_ic,
        "registry_summary": summary,
        "factors": [{"formula": f, "ic": ic, "abs_ic": abs(ic)} for f, ic in filtered],
    }
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, ensure_ascii=False)
    print(f"\n[Export] Factors list saved to {export_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())