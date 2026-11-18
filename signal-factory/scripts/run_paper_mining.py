#!/usr/bin/env python3
"""
论文驱动因子挖掘 CLI — 通过 factor-lab API 调用

用法:
    python scripts/run_paper_mining.py --mode full --max-papers 5
    python scripts/run_paper_mining.py --mode discover
    python scripts/run_paper_mining.py --paper-ids paper_001 paper_002
    python scripts/run_paper_mining.py --status
"""

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(
        description="论文驱动因子挖掘 — 从学术论文中提取可执行因子（factor-lab API）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --mode full --max-papers 5          # 完整流程：发现→提取→验证→入库
  %(prog)s --mode discover                      # 仅发现论文
  %(prog)s --paper-ids paper_001 paper_002      # 处理指定论文
  %(prog)s --status                             # 查看状态
        """,
    )

    parser.add_argument(
        "--config", default="config/papers.yaml",
        help="配置文件路径（默认: config/papers.yaml）",
    )
    parser.add_argument(
        "--mode", default="full",
        choices=["full", "discover", "extract", "validate"],
        help="运行模式: full=完整流程, discover=仅发现, extract=仅提取, validate=仅验证",
    )
    parser.add_argument(
        "--paper-ids", nargs="+",
        help="指定论文 ID（不指定则处理所有新论文）",
    )
    parser.add_argument(
        "--max-papers", type=int, default=5,
        help="最多处理论文数（默认: 5）",
    )
    parser.add_argument(
        "--symbols", nargs="+",
        help="Qlib 标的列表（用于 IC 验证，默认: csi300）",
    )
    parser.add_argument(
        "--start", default="2020-01-01",
        help="数据起始日期（默认: 2020-01-01）",
    )
    parser.add_argument(
        "--end", default=None,
        help="数据结束日期（默认: 今天）",
    )
    parser.add_argument(
        "--output", default="data/output/factors",
        help="输出目录（默认: data/output/factors）",
    )
    parser.add_argument(
        "--no-store", action="store_true",
        help="不存入 FactorStore（仅预览）",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="查看当前状态",
    )
    parser.add_argument(
        "--timeout", type=int, default=1200,
        help="任务超时（秒）",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="详细日志",
    )

    args = parser.parse_args()

    from src.factor_lab_client import FactorLabClient

    end_date = args.end or datetime.now().strftime("%Y-%m-%d")
    client = FactorLabClient()

    # 健康检查
    health = client.health_check()
    if not health.get("status") == "ok":
        print(f"❌ factor-lab 不可用: {health}")
        print("请确认 WSL2 factor-lab 已启动: bash start.sh")
        sys.exit(1)

    print(f"  ✅ factor-lab 已连接 (factors={health.get('factors_count', 0)})")

    # 状态查询
    if args.status:
        factors = client.list_factors()
        print(f"活跃因子数: {len(factors)}")
        for f in factors:
            print(f"  [{f.get('id', '?')}] {f.get('name', '?')}")
        return

    # 运行挖掘
    print(f"论文驱动因子挖掘启动")
    print(f"  模式: {args.mode}")
    print(f"  论文上限: {args.max_papers}")
    print(f"  数据范围: {args.start} ~ {end_date}")
    print(f"  存储: {'否 (预览模式)' if args.no_store else '是'}")
    print()

    task = client.mine_papers(
        mode=args.mode,
        max_papers=args.max_papers,
        date=end_date,
        config=args.config,
    )
    print(f"  任务已提交: {task['task_id']}")

    result = client.wait_for_task(
        task["task_id"],
        timeout=args.timeout,
        on_progress=lambda p, s: print(f"  进度: {p:.0%} ({s})") if p > 0 else None,
    )

    # 打印摘要
    summary = result.get("summary", {})
    print("=" * 60)
    print("论文因子挖掘完成")
    print("=" * 60)
    print(f"  处理论文: {summary.get('papers_processed', 0)}")
    print(f"  提取因子: {summary.get('factors_extracted', 0)}")
    print(f"  PIT 通过: {summary.get('pit_passed', 0)}")
    print(f"  IC 通过:  {summary.get('ic_passed', 0)}")
    print(f"  最终入库: {summary.get('stored', 0)}")

    if result.get("errors"):
        print(f"\n  错误: {len(result['errors'])}")
        for err in result["errors"][:5]:
            print(f"    - {err}")

    # 保存结果
    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / f"paper_factors_{end_date}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n结果已保存: {output_file}")


if __name__ == "__main__":
    main()