"""
FM_trade 回测适配器 — 桥接层 (底层: quant-signal-factory)

从 QSF src/backtest/adapter.py 导入，保持 FM_trade API 兼容。
"""

import json
import os
import sys
import warnings
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

# 抑制警告
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from src.backtest.adapter import (
    init_qlib,
    run_backtest,
    _extract_portfolio_metrics,
    _extract_trades,
    _compute_summary,
)


# ─── FM_trade 兼容适配器 ───

def init_qlib_for_backtest(bin_dir: str = None) -> None:
    """初始化 Qlib 环境用于回测（兼容 FM_trade 接口）"""
    if bin_dir is None:
        bin_dir = "D:/qlib/qlib_data/cn_data"
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    init_qlib(bin_dir)


def run_qlib_backtest(
    symbol: str,
    strategy_name: str,
    start_time: str,
    end_time: str,
    account: float = 1_000_000,
    benchmark: str = None,
    override_params: Optional[dict] = None,
    exchange_kwargs: Optional[dict] = None,
    bin_dir: Optional[str] = None,
    verbose: bool = False,
) -> dict:
    """
    执行单标的 Qlib 回测（兼容 FM_trade 接口）。

    委托给 QSF 的 run_backtest。先通过 QSF 生成信号，再执行回测。
    """
    from src.backtest.signal_generator import get_signal_from_qlib

    # 初始化 Qlib
    if bin_dir is None:
        bin_dir = "D:/qlib/qlib_data/cn_data"
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    init_qlib(bin_dir)

    # 生成信号
    signal = get_signal_from_qlib(
        symbol=symbol,
        strategy_name=strategy_name,
        start_time=start_time,
        end_time=end_time,
        override_params=override_params,
    )

    return run_backtest(
        symbol=symbol,
        strategy_name=strategy_name,
        signal=signal,
        start_time=start_time,
        end_time=end_time,
        account=account,
        benchmark=benchmark,
        exchange_kwargs=exchange_kwargs,
        verbose=verbose,
    )


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Qlib 回测适配器 (QSF 桥接)")
    parser.add_argument("--symbol", required=True, help="标的代码 (如 sh600519)")
    parser.add_argument("--strategy", required=True, help="策略名称")
    parser.add_argument("--start", default="2020-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--account", type=float, default=1_000_000, help="初始资金")
    parser.add_argument("--benchmark", default=None, help="基准指数")
    parser.add_argument("--output", help="输出 JSON 文件路径")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    end_time = args.end or datetime.now().strftime("%Y-%m-%d")

    result = run_qlib_backtest(
        symbol=args.symbol,
        strategy_name=args.strategy,
        start_time=args.start,
        end_time=end_time,
        account=args.account,
        benchmark=args.benchmark,
        verbose=args.verbose,
    )

    output_json = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"结果已保存: {args.output}")

    print(output_json)