"""
AgentAlpha 策略映射器 — 桥接层 (底层: signal-factory)

从 QSF src/backtest/signal_generator.py 和 src/backtest/adapter.py 导入。
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

from src.backtest.signal_generator import (
    STRATEGIES,
    get_strategy_info,
    generate_signal,
    list_strategies,
    get_signal_from_qlib,
    generate_breakout_60,
    generate_pullback_low,
    generate_vcp_breakout,
    generate_oversold_bounce,
    generate_golden_cross,
    generate_limit_up_chase,
)

from src.backtest.adapter import (
    SingleStockSignalStrategy,
    map_strategy_to_qlib_config,
)


# ─── AgentAlpha 兼容适配器 ───

def get_qlib_signal_from_rules(
    symbol: str,
    strategy_name: str,
    start_time: str,
    end_time: str,
    override_params: Optional[dict] = None,
) -> pd.Series:
    """
    从 Qlib 数据加载价格并使用 AgentAlpha 规则生成信号。
    委托给 QSF 的 get_signal_from_qlib。
    """
    return get_signal_from_qlib(
        symbol=symbol,
        strategy_name=strategy_name,
        start_time=start_time,
        end_time=end_time,
        override_params=override_params,
    )


def map_strategy_to_qlib(
    strategy_name: str,
    signal: Union[pd.Series, pd.DataFrame],
    symbol: str = None,
    account: float = 1_000_000,
    benchmark: str = "SH000300",
    exchange_kwargs: Optional[dict] = None,
) -> dict:
    """
    将 AgentAlpha 策略映射为 Qlib backtest 完整配置。
    委托给 QSF 的 map_strategy_to_qlib_config。
    """
    strategy_info = get_strategy_info(strategy_name)

    # 提取 symbol
    if symbol is None and isinstance(signal.index, pd.MultiIndex):
        instruments = signal.index.get_level_values("instrument").unique()
        symbol = instruments[0] if len(instruments) > 0 else None

    config = map_strategy_to_qlib_config(
        strategy_name=strategy_name,
        signal=signal,
        symbol=symbol,
        account=account,
        benchmark=benchmark,
        exchange_kwargs=exchange_kwargs,
    )
    config["strategy_info"] = strategy_info
    return config


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AgentAlpha → Qlib 策略映射器 (QSF 桥接)")
    parser.add_argument("--list", action="store_true", help="列出所有可用策略")
    parser.add_argument("--strategy", help="策略名称")
    parser.add_argument("--symbol", help="标的代码 (如 sh600519)")
    parser.add_argument("--start", default="2020-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--output", help="输出信号 CSV 文件路径")
    args = parser.parse_args()

    if args.list:
        for s in list_strategies():
            print(f"  {s['key']}: {s['name']} ({s['param_count']} params)")
            print(f"    {s['description']}")
        sys.exit(0)

    if not args.strategy or not args.symbol:
        parser.error("需要 --strategy 和 --symbol")

    end_time = args.end or datetime.now().strftime("%Y-%m-%d")

    import qlib
    qlib_bin = os.environ.get("QLIB_DATA_DIR", "D:/qlib/qlib_data/cn_data")
    qlib.init(provider_uri=qlib_bin, region="cn")

    signal = get_qlib_signal_from_rules(
        args.symbol, args.strategy, args.start, end_time
    )

    buy_count = (signal == 1).sum()
    print(f"策略: {args.strategy}")
    print(f"标的: {args.symbol}")
    print(f"区间: {args.start} ~ {end_time}")
    print(f"买入信号: {buy_count} 次")

    if args.output:
        signal.to_csv(args.output)
        print(f"信号已保存: {args.output}")