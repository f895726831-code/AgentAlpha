#!/usr/bin/env python3
"""RD-Agent 因子挖掘入口 — 通过 factor-lab API 调用 DeepSeek-V4 驱动"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description="RD-Agent 因子挖掘（factor-lab API）")
    parser.add_argument("--config", default="config/rd_agent.yaml", help="RD-Agent 配置文件")
    parser.add_argument("--symbols", nargs="+", help="标的列表")
    parser.add_argument("--start", default="2020-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--output", default="data/output/factors", help="输出目录")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    parser.add_argument("--timeout", type=int, default=1800, help="任务超时（秒）")
    args = parser.parse_args()

    from src.factor_lab_client import FactorLabClient

    end_date = args.end or datetime.now().strftime("%Y-%m-%d")

    print(f"启动 RD-Agent 因子挖掘（factor-lab API）")
    print(f"  配置: {args.config}")
    print(f"  标的: {args.symbols or '默认'}")
    print(f"  区间: {args.start} ~ {end_date}")

    client = FactorLabClient()

    # 健康检查
    health = client.health_check()
    if not health.get("status") == "ok":
        print(f"❌ factor-lab 不可用: {health}")
        print("请确认 WSL2 factor-lab 已启动: bash start.sh")
        sys.exit(1)

    print(f"  ✅ factor-lab 已连接 (qlib={health.get('qlib_ok')}, rd_agent={health.get('rd_agent_available')})")

    # 触发因子挖掘
    task = client.mine_factors(
        instruments=args.symbols,
        date=end_date,
        max_rounds=5,
        config=args.config,
    )
    print(f"  任务已提交: {task['task_id']}")

    # 等待完成
    result = client.wait_for_task(
        task["task_id"],
        timeout=args.timeout,
        on_progress=lambda p, s: print(f"  进度: {p:.0%} ({s})") if p > 0 else None,
    )

    # 保存结果
    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / f"{end_date}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    summary = result.get("summary", {})
    print(f"\n结果已保存: {output_file}")
    print(f"因子统计:")
    print(f"  生成: {summary.get('total_generated', 0)}")
    print(f"  PIT 通过: {summary.get('pit_passed', 0)}")
    print(f"  最终保留: {summary.get('ic_filtered', 0)}")

    for f in result.get("factors", []):
        print(f"  [OK] {f['id']}: {f['name']} [{f.get('source', 'unknown')}]")


if __name__ == "__main__":
    main()