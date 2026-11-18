"""
FM_trade 回测 CLI — 桥接层 (底层: quant-signal-factory)

从 QSF src/backtest/adapter.py 导入，保持 FM_trade CLI 兼容。
调用方: cross_validate.py (line 461), evaluate_backtest.py (line 448)
"""

import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Optional

# 确保 QSF 和当前目录在 sys.path 中
_QSF_ROOT = Path("signal-factory")
_QLIB_DIR = Path(__file__).resolve().parent  # scripts/qlib/
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))
if str(_QLIB_DIR) not in sys.path:
    sys.path.insert(0, str(_QLIB_DIR))

from backtest_adapter import run_qlib_backtest
from gate1_scorer import score_gate1, load_risk_policy
from strategy_mapper import list_strategies, get_strategy_info


def run_single_backtest(
    symbol: str,
    strategy_name: str,
    start_time: str,
    end_time: str,
    account: float = 1_000_000,
    benchmark: str = "SH000300",
    output_dir: str = "data/backtest_results",
    run_gate1: bool = True,
    verbose: bool = False,
) -> dict:
    """执行单标的回测并保存结果"""
    result = run_qlib_backtest(
        symbol=symbol,
        strategy_name=strategy_name,
        start_time=start_time,
        end_time=end_time,
        account=account,
        benchmark=benchmark,
        verbose=verbose,
    )

    # 保存回测结果
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    safe_symbol = symbol.replace(".", "_")
    filename = f"{safe_symbol}_{strategy_name}_{date_str}.json"
    output_path = output_dir / filename

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    if verbose:
        print(f"回测结果已保存: {output_path}")

    # Gate 1 评分
    gate1_result = None
    if run_gate1:
        gate1_result = score_gate1(result)

        gate1_filename = f"{safe_symbol}_{strategy_name}_{date_str}_gate1.json"
        gate1_path = output_dir / gate1_filename

        gate1_path.write_text(
            json.dumps(gate1_result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        if verbose:
            print(f"Gate 1 评分卡已保存: {gate1_path}")

    return {"backtest": result, "gate1": gate1_result}


def run_batch_backtest(
    symbols: List[str],
    strategy_name: str,
    start_time: str,
    end_time: str,
    **kwargs,
) -> List[dict]:
    """批量回测多个标的"""
    results = []
    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"回测: {symbol} / {strategy_name}")
        print(f"{'='*60}")

        try:
            result = run_single_backtest(
                symbol=symbol,
                strategy_name=strategy_name,
                start_time=start_time,
                end_time=end_time,
                **kwargs,
            )
            results.append(result)

            summary = result["backtest"].get("summary", {})
            print(
                f"  结果: {summary.get('verdict', 'N/A')} | "
                f"交易: {summary.get('total_trades', 0)}笔 | "
                f"胜率: {summary.get('win_rate', 0)}% | "
                f"PF: {summary.get('profit_factor', 0)}"
            )

            if result["gate1"]:
                g1 = result["gate1"]
                print(
                    f"  Gate 1: {g1.get('verdict', 'N/A')} | "
                    f"原始: {g1.get('raw_score', 0)} | "
                    f"调整后: {g1.get('adjusted_score', 0)}"
                )

        except Exception as e:
            print(f"  错误: {e}")
            results.append({"error": str(e)})

    return results


def run_cross_validate(
    strategy_name: str,
    start_time: str,
    end_time: str,
    **kwargs,
) -> dict:
    """跨标的交叉验证"""
    import qlib
    from qlib.data import D

    bin_dir = kwargs.pop("bin_dir", None) or "D:/qlib/qlib_data/cn_data"
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    qlib.init(provider_uri=bin_dir, region="cn")

    instruments = D.list_instruments(D.instruments("all"), start_time=start_time, end_time=end_time)
    symbols = list(instruments.get("instruments", [])) if isinstance(instruments, dict) else list(instruments)

    if not symbols:
        print("错误: 无可用标的")
        return {"error": "无可用标的"}

    print(f"交叉验证: {strategy_name} 在 {len(symbols)} 个标的上")

    results = run_batch_backtest(
        symbols=symbols,
        strategy_name=strategy_name,
        start_time=start_time,
        end_time=end_time,
        **kwargs,
    )

    valid_results = [
        r for r in results
        if "backtest" in r and r["backtest"].get("summary", {}).get("total_trades", 0) > 0
    ]

    deploy_count = sum(1 for r in valid_results if r["backtest"]["summary"].get("verdict") == "Deploy")
    refine_count = sum(1 for r in valid_results if r["backtest"]["summary"].get("verdict") == "Refine")
    abandon_count = sum(1 for r in valid_results if r["backtest"]["summary"].get("verdict") == "Abandon")

    if valid_results:
        exps = [r["backtest"]["summary"].get("expectancy", 0) for r in valid_results]
        avg_exp = sum(exps) / len(exps)
        exp_std = (sum((e - avg_exp) ** 2 for e in exps) / len(exps)) ** 0.5 if len(exps) > 1 else 0
        consistency = "HIGH" if exp_std < 2 else ("MEDIUM" if exp_std < 5 else "LOW")

        summary = {
            "total_symbols": len(symbols),
            "valid_results": len(valid_results),
            "deploy_count": deploy_count,
            "refine_count": refine_count,
            "abandon_count": abandon_count,
            "avg_expectancy": round(avg_exp, 2),
            "expectancy_std": round(exp_std, 2),
            "cross_symbol_consistency": consistency,
        }
    else:
        summary = {"error": "无有效回测结果"}

    output_dir = Path(kwargs.get("output_dir", "data/backtest_results"))
    date_str = datetime.now().strftime("%Y%m%d")
    cv_path = output_dir / f"cross_validate_{strategy_name}_{date_str}.json"
    cv_path.write_text(
        json.dumps({"strategy": strategy_name, "summary": summary, "results": results},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print(f"\n交叉验证结果已保存: {cv_path}")
    print(f"  一致性: {summary.get('cross_symbol_consistency', 'N/A')}")
    print(f"  Deploy: {deploy_count} | Refine: {refine_count} | Abandon: {abandon_count}")

    return {"strategy": strategy_name, "summary": summary, "results": results}


def main():
    parser = argparse.ArgumentParser(description="Qlib 回测入口 (QSF 桥接)")
    parser.add_argument("--symbol", help="单个标的代码")
    parser.add_argument("--symbols", help="逗号分隔的多个标的代码")
    parser.add_argument("--strategy", help="策略名称")
    parser.add_argument("--list-strategies", action="store_true", help="列出所有可用策略")
    parser.add_argument("--cross-validate", action="store_true", help="跨标的交叉验证")
    parser.add_argument("--gate1", action="store_true", help="输出 Gate 1 评分卡")
    parser.add_argument("--start", default="2020-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--account", type=float, default=1_000_000, help="初始资金")
    parser.add_argument("--benchmark", default=None, help="基准指数")
    parser.add_argument("--output-dir", default="data/backtest_results", help="输出目录")
    parser.add_argument("--output", help="输出文件路径")
    parser.add_argument("--no-gate1", action="store_true", help="跳过 Gate 1 评分")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")

    args = parser.parse_args()

    if args.list_strategies:
        print("可用策略:")
        for s in list_strategies():
            print(f"  {s['key']}: {s['name']} ({s['param_count']} params)")
            print(f"    {s['description']}")
        return

    if not args.strategy:
        parser.error("需要 --strategy (使用 --list-strategies 查看可用策略)")

    if not args.symbol and not args.symbols and not args.cross_validate:
        parser.error("需要 --symbol, --symbols, 或 --cross-validate")

    end_time = args.end or datetime.now().strftime("%Y-%m-%d")

    kwargs = {
        "account": args.account,
        "benchmark": args.benchmark,
        "output_dir": args.output_dir,
        "run_gate1": not args.no_gate1,
        "verbose": args.verbose,
    }

    if args.cross_validate:
        result = run_cross_validate(
            strategy_name=args.strategy,
            start_time=args.start,
            end_time=end_time,
            **kwargs,
        )
        return

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
        run_batch_backtest(
            symbols=symbols,
            strategy_name=args.strategy,
            start_time=args.start,
            end_time=end_time,
            **kwargs,
        )
        return

    result = run_single_backtest(
        symbol=args.symbol,
        strategy_name=args.strategy,
        start_time=args.start,
        end_time=end_time,
        **kwargs,
    )

    summary = result["backtest"].get("summary", {})
    print(f"\n{'='*60}")
    print(f"回测完成: {args.symbol} / {args.strategy}")
    print(f"{'='*60}")
    print(f"  判決: {summary.get('verdict', 'N/A')}")
    print(f"  交易: {summary.get('total_trades', 0)} 笔")
    print(f"  胜率: {summary.get('win_rate', 0)}%")
    print(f"  盈亏比: {summary.get('profit_factor', 0)}")
    print(f"  期望值: {summary.get('expectancy', 0)}%")
    print(f"  Sharpe: {summary.get('sharpe_ratio', 0)}")
    print(f"  最大回撤: {summary.get('max_drawdown', 0)}%")

    if result["gate1"]:
        g1 = result["gate1"]
        print(f"\n  Gate 1 评分卡:")
        print(f"    判決: {g1.get('verdict', 'N/A')}")
        print(f"    原始分: {g1.get('raw_score', 0)}")
        print(f"    调整后: {g1.get('adjusted_score', 0)}")

    if result["backtest"].get("error"):
        print(f"\n  错误: {result['backtest']['error']}")


if __name__ == "__main__":
    main()