#!/usr/bin/env python3
"""
FFO 批量因子评估 CLI — 从 CSV 或命令行参数批量评估因子公式

用法:
  # 从 CSV 文件批量评估
  python scripts/batch_evaluate.py --csv factors.csv --start 2024-01-01 --universe csi500

  # 命令行直接评估多个公式
  python scripts/batch_evaluate.py --formulas "Rank($close,20)" "($close-Delay($close,5))/Delay($close,5)"

  # 输出到 CSV 文件
  python scripts/batch_evaluate.py --csv factors.csv --output results.csv

  # 筛选通过阈值的因子
  python scripts/batch_evaluate.py --csv factors.csv --min-ic 0.03 --min-icir 0.3

CSV 格式要求:
  formula (必需): 因子公式，如 "Rank($close, 20)"
  name (可选): 因子名称
  start (可选): 起始日期，默认 2024-01-01
  end (可选): 结束日期
  universe (可选): 股票池，默认 csi500
"""

import argparse
import sys
from pathlib import Path

# 确保 factor-lab 模块可导入
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ffo.batch import (
    batch_evaluate,
    batch_evaluate_from_csv,
    results_to_dataframe,
    results_to_csv,
    filter_results,
)
from ffo.reporter import format_report_short
from ffo.operators import get_registered_operators


def main():
    parser = argparse.ArgumentParser(
        description="FFO 批量因子评估 — AlphaBench 风格快速因子筛选",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --csv factors.csv
  %(prog)s --formulas "Rank($close, 20)" "Ts_Mean($returns, 10)"
  %(prog)s --csv factors.csv --output results.csv --min-ic 0.03
  %(prog)s --list-operators
        """,
    )

    # 输入源
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--csv", type=str, help="CSV 文件路径")
    input_group.add_argument("--formulas", nargs="+", type=str, help="因子公式列表")
    input_group.add_argument("--list-operators", action="store_true", help="列出支持的操作符")

    # 评估参数
    parser.add_argument("--start", type=str, default="2024-01-01", help="起始日期 (默认: 2024-01-01)")
    parser.add_argument("--end", type=str, default=None, help="结束日期 (默认: 今天)")
    parser.add_argument("--universe", type=str, default="csi500", help="股票池 (默认: csi500)")
    parser.add_argument("--data-dir", type=str, default=None, help="Qlib bin 数据目录")

    # 筛选
    parser.add_argument("--min-ic", type=float, default=0.0, help="最小 |IC| 阈值 (默认: 0)")
    parser.add_argument("--min-icir", type=float, default=0.0, help="最小 |ICIR| 阈值 (默认: 0)")
    parser.add_argument("--max-turnover", type=float, default=1.0, help="最大换手率 (默认: 1.0)")

    # 输出
    parser.add_argument("--output", "-o", type=str, help="输出 CSV 文件路径")
    parser.add_argument("--quiet", "-q", action="store_true", help="静默模式")
    parser.add_argument("--full-report", action="store_true", help="输出完整 Markdown 报告")

    args = parser.parse_args()

    # 列出操作符
    if args.list_operators:
        ops = get_registered_operators()
        print(f"支持的操作符 ({len(ops)} 个):")
        for op in ops:
            print(f"  {op}")
        return 0

    # 验证输入
    if not args.csv and not args.formulas:
        parser.error("必须指定 --csv 或 --formulas")
        return 1

    # 执行批量评估
    if args.csv:
        if not args.quiet:
            print(f"从 CSV 加载: {args.csv}")
        results = batch_evaluate_from_csv(
            csv_path=args.csv,
            data_dir=args.data_dir,
            verbose=not args.quiet,
        )
    else:
        if not args.quiet:
            print(f"评估 {len(args.formulas)} 个公式")
        results = batch_evaluate(
            formulas=args.formulas,
            start=args.start,
            end=args.end,
            universe=args.universe,
            data_dir=args.data_dir,
            verbose=not args.quiet,
        )

    # 筛选
    if args.min_ic > 0 or args.min_icir > 0 or args.max_turnover < 1.0:
        before = len(results)
        results = filter_results(
            results,
            min_abs_ic=args.min_ic,
            min_ic_ir=args.min_icir,
            max_turnover=args.max_turnover,
        )
        if not args.quiet:
            print(f"\n筛选后: {len(results)}/{before} 通过 (|IC|>={args.min_ic}, |ICIR|>={args.min_icir})")

    # 输出
    if args.output:
        results_to_csv(results, args.output)
        if not args.quiet:
            print(f"结果已保存: {args.output}")

    # 摘要
    if not args.quiet:
        errors = [r for r in results if r.error]
        valid = [r for r in results if not r.error]
        good = [r for r in valid if abs(r.ic_mean) >= 0.02]
        print(f"\n总计: {len(results)} 个因子")
        print(f"  有效: {len(valid)}")
        print(f"  通过 (|IC|>=0.02): {len(good)}")
        print(f"  错误: {len(errors)}")

    # 完整报告
    if args.full_report:
        from ffo.reporter import format_report
        for r in results:
            if not r.error:
                print("\n" + "=" * 80)
                print(format_report(r))

    return 0 if not any(r.error for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())