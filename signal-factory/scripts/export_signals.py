#!/usr/bin/env python3
"""信号导出 — 生成 AgentAlpha JSON 契约"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description="导出 AgentAlpha JSON 契约")
    parser.add_argument("--date", default=None, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--factors", default=None, help="因子结果 JSON 文件")
    parser.add_argument("--backtest", default=None, help="回测结果 JSON 文件")
    parser.add_argument("--output-dir", default="data/output/signals", help="输出目录")
    parser.add_argument("--latest", action="store_true", help="读取最新契约")
    args = parser.parse_args()

    from src.export.agent_alpha_bridge import generate_contract, save_contract, get_latest_contract, load_contract

    if args.latest:
        contract = get_latest_contract(args.output_dir)
        if contract:
            print(json.dumps(contract, ensure_ascii=False, indent=2))
        else:
            print("无可用契约")
        return

    # 加载因子结果
    factor_results = {"factors": [], "evaluations": {}, "pit_results": {}, "summary": {"total_generated": 0, "pit_passed": 0, "ic_filtered": 0, "model": "unknown"}}
    if args.factors:
        with open(args.factors, "r", encoding="utf-8") as f:
            factor_results = json.load(f)

    # 加载回测结果
    backtest_results = None
    if args.backtest:
        with open(args.backtest, "r", encoding="utf-8") as f:
            backtest_data = json.load(f)
            if "results" in backtest_data and backtest_data["results"]:
                backtest_results = backtest_data["results"][0]

    # 生成契约
    run_date = args.date or datetime.now().strftime("%Y-%m-%d")
    # 动态判断 PIT 状态：所有因子必须通过 PIT 验证
    pit_results = factor_results.get("pit_results", {})
    if pit_results:
        pit_verified = all(r.get("passed", False) for r in pit_results.values())
    else:
        # 无 PIT 验证数据时，检查因子是否有 pit_verified 标记
        factors = factor_results.get("factors", [])
        pit_verified = all(f.get("pit_verified", False) for f in factors) if factors else False

    contract = generate_contract(
        factor_results=factor_results,
        backtest_results=backtest_results,
        pit_verified=pit_verified,
        data_coverage={"stocks": 10, "start": "2020-01-01", "end": run_date},
    )

    output_path = save_contract(contract, args.output_dir)
    print(f"契约已保存: {output_path}")
    print(f"因子: {len(contract['factors'])} 个")
    print(f"PIT: {'✅' if contract['meta']['pit_verified'] else '❌'}")
    for w in contract["warnings"]:
        print(f"  ⚠ {w}")


if __name__ == "__main__":
    main()