"""
Qlib 回测适配器 — 封装 Qlib backtest 完整生命周期

从 FM_trade scripts/qlib/backtest_adapter.py 迁移，增强：
  - 信号生成强制通过 PIT 验证
  - 更完善的错误处理
  - 支持批量回测
  - 使用 SingleStockSignalStrategy 替换 TopkDropoutStrategy（单标的信号执行）
"""

import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 确保 qlib 在 sys.path 上（模块导入需要，无需 qlib.init()）
_qlib_root = os.environ.get("QLIB_ROOT", "D:/qlib")
if _qlib_root not in sys.path:
    sys.path.insert(0, _qlib_root)

from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO
from qlib.data.data import Cal
from qlib.strategy.base import BaseStrategy

from src.backtest.execution_gate import ExecutionGate, RealismConfig

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


def init_qlib(bin_dir: Optional[str] = None) -> None:
    """初始化 Qlib 环境"""
    import sys
    import os

    from src.data.config_loader import get_qlib_bin_dir, get_qlib_region

    # Ensure correct Qlib path (Microsoft Qlib, not the Q-Engineering one)
    qlib_root = os.environ.get("QLIB_ROOT", "D:/qlib")
    if qlib_root not in sys.path:
        sys.path.insert(0, qlib_root)

    # Install mlflow mock (Qlib requires mlflow for experiment tracking)
    _helper_path = os.path.join(os.path.dirname(__file__), "..", "data", "qlib_init_helper.py")
    _helper_path = os.path.abspath(_helper_path)
    if os.path.exists(_helper_path) and "mlflow" not in sys.modules:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_qlib_init_helper", _helper_path)
        _helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_helper)

    import qlib
    import qlib_compat

    if bin_dir is None:
        bin_dir = get_qlib_bin_dir()

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    qlib_compat.init_qlib()

    # Force threading backend AFTER qlib.init(): the mlflow mock is only available
    # in the main process, and joblib's multiprocessing backend spawns child
    # processes that don't inherit it. Must be set after init() which resets config.
    qlib.config.C["joblib_backend"] = "threading"
    qlib.config.C["kernels"] = 1


# ═══════════════════════════════════════════════════════════════
#  单标的信号策略 — 替代 TopkDropoutStrategy
# ═══════════════════════════════════════════════════════════════

class SingleStockSignalStrategy(BaseStrategy):
    """
    单标的信号执行策略。

    根据预先计算的 0/1 信号 Series 执行买入/卖出：
      - signal=1 且空仓 → 全仓买入（按 risk_degree 比例）
      - signal=0 且持仓 → 全部卖出

    参数:
        signal: pd.Series with MultiIndex (datetime, instrument), values=1(buy)/0(sell)
        symbol: 标的代码
        risk_degree: 仓位比例 (默认 0.95)
        min_hold_days: 最小持仓天数，买入后至少持有 N 天才允许卖出 (默认 1)
    """

    def __init__(
        self,
        *,
        signal: pd.Series = None,
        symbol: str = None,
        risk_degree: float = 0.95,
        min_hold_days: int = 1,
        execution_gate: ExecutionGate = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.risk_degree = risk_degree
        self.min_hold_days = min_hold_days
        self.symbol = symbol
        self._execution_gate = execution_gate  # 执行现实性门禁

        # 提取单标的信号并转换为 date→value 字典
        self._signal_map = {}
        if signal is not None and symbol is not None:
            if isinstance(signal.index, pd.MultiIndex):
                try:
                    inst_signal = signal.xs(symbol, level="instrument")
                    self._signal_map = inst_signal.to_dict()
                except KeyError:
                    # 尝试大小写不敏感匹配
                    inst_level = signal.index.get_level_values("instrument")
                    matched = [s for s in inst_level.unique() if s.lower() == symbol.lower()]
                    if matched:
                        inst_signal = signal.xs(matched[0], level="instrument")
                        self._signal_map = inst_signal.to_dict()
            elif isinstance(signal, pd.Series):
                self._signal_map = signal.to_dict()

        self._entry_date = None  # 记录入场日期，用于最小持仓天数

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)

        # 查找当前日期的信号
        trade_date = trade_start_time
        signal_val = self._signal_map.get(trade_date, 0)
        if isinstance(trade_date, pd.Timestamp) and signal_val == 0:
            signal_val = self._signal_map.get(trade_date.strftime("%Y-%m-%d"), 0)

        # 检查当前持仓（trade_position 是 BaseStrategy 的属性）
        position = self.trade_position
        stock_list = position.get_stock_list()
        has_position = self.symbol in stock_list

        orders = []

        # ── 执行现实性门禁: 日期格式化 ──
        date_str = str(trade_date)[:10]
        gate = self._execution_gate

        # ── 门禁 1: ST/停牌检查 ──
        if gate is not None and gate.is_st_blocked(date_str):
            return TradeDecisionWO([], self)

        if signal_val == 1 and not has_position:
            # ── 门禁 2: 涨停封死 → 禁止买入 ──
            if gate is not None and gate.is_limit_up_blocked(date_str):
                return TradeDecisionWO([], self)

            # 买入信号 + 空仓 → 买入
            cash = position.get_cash()
            price = self.trade_exchange.get_deal_price(
                self.symbol, trade_start_time, trade_end_time, OrderDir.BUY
            )
            if price and price > 0 and cash > 0:
                shares = int(cash * self.risk_degree / price)

                # ── 门禁 3: 成交量占比约束 ──
                if gate is not None and shares > 0:
                    try:
                        volume = self.trade_exchange.get_volume(
                            self.symbol, trade_start_time, trade_end_time, method="sum"
                        )
                        if volume and not np.isnan(volume) and volume > 0:
                            daily_amount = float(volume) * float(price)
                            shares = gate.cap_shares(shares, daily_amount, price)
                    except Exception:
                        pass  # 获取成交量失败则保持原始股数

                if shares > 0:
                    orders.append(Order(
                        stock_id=self.symbol,
                        amount=shares,
                        direction=OrderDir.BUY,
                        start_time=trade_start_time,
                        end_time=trade_end_time,
                    ))
                    self._entry_date = trade_date

        elif signal_val == 0 and has_position:
            # ── 门禁 2b: 跌停封死 → 禁止卖出 ──
            if gate is not None and gate.is_limit_down_blocked(date_str):
                return TradeDecisionWO([], self)

            # 卖出信号 + 持仓 → 检查最小持仓天数
            can_sell = True
            if self.min_hold_days > 1 and self._entry_date is not None:
                try:
                    cal = Cal.calendar(
                        start_time=str(self._entry_date)[:10],
                        end_time=str(trade_date)[:10],
                        freq="day",
                    )
                    hold_days = len(cal) if cal else 0
                except Exception:
                    hold_days = 999
                if hold_days < self.min_hold_days:
                    can_sell = False

            if can_sell:
                shares = position.get_stock_amount(self.symbol)
                if shares > 0:
                    orders.append(Order(
                        stock_id=self.symbol,
                        amount=shares,
                        direction=OrderDir.SELL,
                        start_time=trade_start_time,
                        end_time=trade_end_time,
                    ))
                    self._entry_date = None

        return TradeDecisionWO(orders, self)


def map_strategy_to_qlib_config(
    strategy_name: str,
    signal: pd.Series,
    symbol: str = None,
    account: float = 1_000_000,
    benchmark: Optional[str] = None,
    exchange_kwargs: Optional[dict] = None,
    execution_gate: ExecutionGate = None,
) -> dict:
    """
    将策略信号映射为 Qlib backtest 配置。

    参数:
        strategy_name: 策略名称
        signal: MultiIndex Series (datetime, instrument), 1=buy
        symbol: 标的代码
        account: 初始资金
        benchmark: 基准指数 (None = 无基准)
        exchange_kwargs: 交易所参数覆盖
        execution_gate: 执行现实性门禁 (可选, None = 不启用)

    返回:
        Qlib backtest 配置字典
    """
    default_exchange = {
        "freq": "day",
        "deal_price": "close",
        "open_cost": 0.00025,   # 买入: 佣金 0.025% (万2.5)
        "close_cost": 0.00075,  # 卖出: 佣金 0.025% + 印花税 0.05% = 0.075%
        "min_cost": 5,          # 最低佣金 5 元
        "limit_threshold": 0.1,
    }
    if exchange_kwargs:
        default_exchange.update(exchange_kwargs)

    strategy_kwargs = {
        "signal": signal,
        "symbol": symbol,
        "risk_degree": 0.95,
    }
    if execution_gate is not None:
        strategy_kwargs["execution_gate"] = execution_gate

    strategy_config = {
        "class": "SingleStockSignalStrategy",
        "module_path": "src.backtest.adapter",
        "kwargs": strategy_kwargs,
    }

    executor_config = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {
            "time_per_step": "day",
            "generate_portfolio_metrics": True,
        },
    }

    return {
        "strategy": strategy_config,
        "executor": executor_config,
        "exchange": default_exchange,
        "account": account,
        "benchmark": benchmark,
    }


def run_backtest(
    symbol: str,
    strategy_name: str,
    signal: pd.Series,
    start_time: str,
    end_time: str,
    account: float = 1_000_000,
    benchmark: Optional[str] = None,
    exchange_kwargs: Optional[dict] = None,
    realism_config: RealismConfig = None,
    ohlcv_df: Optional[pd.DataFrame] = None,
    verbose: bool = False,
) -> dict:
    """
    执行单标的 Qlib 回测。

    参数:
        symbol: 标的代码
        strategy_name: 策略名称
        signal: MultiIndex Series (datetime, instrument), 1=buy
        start_time: 回测起始日期
        end_time: 回测结束日期
        account: 初始资金
        benchmark: 基准指数 (None = 无基准)
        exchange_kwargs: 交易所参数覆盖
        realism_config: 执行现实性配置 (None = 不启用门禁)
        ohlcv_df: 预加载的 OHLCV 数据 (可选, 涨跌停检查需要)

    返回:
        dict: {meta, signal, portfolio, trades, summary}
    """
    from qlib.backtest import backtest_loop, get_exchange
    from qlib.backtest.account import Account
    from qlib.backtest.utils import CommonInfrastructure
    from qlib.strategy.base import BaseStrategy
    from qlib.backtest.executor import BaseExecutor
    from qlib.utils import init_instance_by_config

    # ── 构建执行门禁 ──
    execution_gate = None
    if realism_config is not None:
        execution_gate = ExecutionGate(realism_config)
        execution_gate.load(symbol, ohlcv_df=ohlcv_df)

    qlib_config = map_strategy_to_qlib_config(
        strategy_name, signal, symbol=symbol, account=account, benchmark=benchmark,
        exchange_kwargs=exchange_kwargs,
        execution_gate=execution_gate,
    )

    try:
        trade_account = Account(
            init_cash=account,
            benchmark_config={"benchmark": None},
        )

        _ex = dict(qlib_config["exchange"])
        _ex.setdefault("start_time", start_time)
        _ex.setdefault("end_time", end_time)
        _ex["codes"] = [symbol]  # 只加载单只标的，避免全市场加载导致 get_deal_price 返回 None
        trade_exchange = get_exchange(**_ex)

        common_infra = CommonInfrastructure(
            trade_account=trade_account,
            trade_exchange=trade_exchange,
        )

        trade_strategy = init_instance_by_config(
            qlib_config["strategy"], accept_types=BaseStrategy
        )
        trade_strategy.reset_common_infra(common_infra)

        trade_executor = init_instance_by_config(
            qlib_config["executor"], accept_types=BaseExecutor
        )
        trade_executor.reset_common_infra(common_infra)

        # 回测执行（带边界处理）
        # Qlib backtest_loop has an off-by-one bug: on the last step, get_step_time(shift=0)
        # accesses calendar[end_index + 1]. Workaround: use the second-to-last trading day.
        _cal = Cal.calendar(start_time=start_time, end_time=end_time, freq="day")
        if len(_cal) >= 2:
            end_time = str(_cal[-2])
        portfolio_dict, indicator_dict = backtest_loop(
            start_time=start_time,
            end_time=end_time,
            trade_strategy=trade_strategy,
            trade_executor=trade_executor,
        )

    except Exception as e:
        return _error_result(symbol, strategy_name, start_time, end_time, str(e))

    # 提取结果
    portfolio_metrics = _extract_portfolio_metrics(portfolio_dict)
    trades = _extract_trades(portfolio_dict, indicator_dict)
    summary = _compute_summary(portfolio_metrics, trades)

    from .signal_generator import get_strategy_info
    strategy_info = get_strategy_info(strategy_name)

    return {
        "meta": {
            "symbol": symbol,
            "strategy": strategy_name,
            "strategy_name": strategy_info["name"],
            "start_time": start_time,
            "end_time": end_time,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "engine": "qlib",
            "status": "success",
            "param_count": strategy_info["param_count"],
            "pit_safe": strategy_info.get("pit_fixed", False),
        },
        "signal": {
            "buy_count": int((signal == 1).sum()),
            "date_range": [start_time, end_time],
        },
        "portfolio": portfolio_metrics,
        "trades": trades,
        "summary": summary,
    }


def _error_result(symbol, strategy, start, end, error_msg) -> dict:
    return {
        "meta": {
            "symbol": symbol, "strategy": strategy,
            "start_time": start, "end_time": end,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "engine": "qlib", "status": "error",
        },
        "error": error_msg,
        "portfolio": None, "trades": [], "summary": {"total_trades": 0, "verdict": "Abandon"},
    }


def _extract_portfolio_metrics(portfolio_dict: dict) -> Optional[dict]:
    """从 Qlib portfolio_dict 提取关键指标"""
    if not portfolio_dict:
        return None

    key = "day" if "day" in portfolio_dict else list(portfolio_dict.keys())[0]
    if key not in portfolio_dict:
        return None

    port_df, _ = portfolio_dict[key]
    if port_df.empty:
        return None

    returns = port_df.get("return", pd.Series(dtype=float))
    costs = port_df.get("cost", pd.Series(dtype=float))
    account_vals = port_df.get("account", pd.Series(dtype=float))

    net_returns = returns - costs if not costs.empty else returns
    cumulative = (1 + net_returns).cumprod()

    total_return = cumulative.iloc[-1] - 1 if len(cumulative) > 0 else 0
    n_days = len(net_returns)
    n_years = n_days / 252
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 and total_return > -1 else total_return
    annual_vol = net_returns.std() * np.sqrt(252) if len(net_returns) > 1 else 0
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0

    cummax = cumulative.cummax()
    drawdown = (cumulative - cummax) / cummax
    max_drawdown = drawdown.min()
    max_dd_idx = drawdown.idxmin() if not drawdown.empty else None

    daily_win_rate = (net_returns > 0).sum() / len(net_returns) if len(net_returns) > 0 else 0

    return {
        "total_return": round(float(total_return) * 100, 2),
        "annual_return": round(float(annual_return) * 100, 2),
        "annual_volatility": round(float(annual_vol) * 100, 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "max_drawdown": round(float(max_drawdown) * 100, 2),
        "max_drawdown_date": str(max_dd_idx) if max_dd_idx is not None else None,
        "daily_win_rate": round(float(daily_win_rate) * 100, 1),
        "trading_days": int(n_days),
        "final_account_value": round(float(account_vals.iloc[-1]), 2) if len(account_vals) > 0 else 0,
    }


def _extract_trades(portfolio_dict: dict, indicator_dict: dict) -> List[dict]:
    """从回测结果提取交易记录，使用 indicator 数据获取实际成交价计算 PnL"""
    if not portfolio_dict:
        return []

    key = "day" if "day" in portfolio_dict else list(portfolio_dict.keys())[0]
    port_df, _ = portfolio_dict[key]
    if port_df.empty:
        return []

    # 获取 indicator 数据中的实际成交信息
    ind_key = "1day" if "1day" in indicator_dict else (list(indicator_dict.keys())[0] if indicator_dict else None)
    ind_df = indicator_dict[ind_key][0] if ind_key and ind_key in indicator_dict else None

    trades = []
    value = port_df.get("value", pd.Series(dtype=float))

    for i in range(1, len(port_df)):
        date = port_df.index[i]
        curr_value = value.iloc[i] if not value.empty else 0
        prev_value = value.iloc[i - 1] if not value.empty else 0

        if curr_value > 0 and prev_value == 0:
            # 买入：从 indicator 获取实际成交金额
            deal_val = float(ind_df.loc[date, "value"]) if ind_df is not None and date in ind_df.index else float(curr_value)
            trades.append({"type": "buy", "date": str(date), "deal_value": deal_val})
        elif curr_value == 0 and prev_value > 0:
            # 卖出：从 indicator 获取实际成交金额
            deal_val = float(ind_df.loc[date, "value"]) if ind_df is not None and date in ind_df.index else float(prev_value)
            trades.append({"type": "sell", "date": str(date), "deal_value": deal_val})

    # 配对交易，使用 indicator 的实际成交金额计算 PnL
    paired = []
    buy_trades = [t for t in trades if t["type"] == "buy"]
    sell_trades = [t for t in trades if t["type"] == "sell"]

    for i, buy in enumerate(buy_trades):
        if i < len(sell_trades):
            sell = sell_trades[i]
            buy_val = buy["deal_value"]
            sell_val = sell["deal_value"]
            pnl = (sell_val - buy_val) / buy_val * 100 if buy_val > 0 else 0
            paired.append({
                "entry_date": buy["date"], "exit_date": sell["date"],
                "entry_value": round(buy_val, 2),
                "exit_value": round(sell_val, 2),
                "pnl_pct": round(pnl, 2), "exit_reason": "signal_sell",
            })
        else:
            paired.append({
                "entry_date": buy["date"], "exit_date": None,
                "entry_value": round(buy["deal_value"], 2),
                "exit_value": None, "pnl_pct": 0, "exit_reason": "open",
            })

    return paired


def _compute_summary(portfolio_metrics: Optional[dict], trades: List[dict]) -> dict:
    """计算汇总统计"""
    if not portfolio_metrics:
        return {"total_trades": 0, "verdict": "Abandon", "verdict_reason": "无 portfolio 数据"}

    # Count all trades (not just closed ones with non-zero PnL)
    closed_trades = [t for t in trades if t.get("exit_reason") not in ("open", None)]
    n_trades = len(closed_trades)

    if not closed_trades:
        return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
                "verdict": "Abandon", "verdict_reason": "无已平仓交易"}

    wins = [t for t in closed_trades if t["pnl_pct"] > 0]
    losses = [t for t in closed_trades if t["pnl_pct"] <= 0]

    win_rate = len(wins) / len(closed_trades) * 100
    total_profit = sum(t["pnl_pct"] for t in wins)
    total_loss = abs(sum(t["pnl_pct"] for t in losses))
    profit_factor = total_profit / total_loss if total_loss > 0 else 999
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    expectancy = (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss

    # 判決
    if n_trades >= 30:
        if win_rate >= 50 and profit_factor >= 1.5 and expectancy > 0:
            verdict, reason = "Deploy", f"{n_trades}笔, 胜率{win_rate:.1f}%, PF={profit_factor:.2f}"
        elif expectancy > 0:
            verdict, reason = "Refine", f"{n_trades}笔, 指标不达标"
        else:
            verdict, reason = "Abandon", f"期望值为负 {expectancy:.2f}%"
    elif n_trades >= 10:
        if win_rate >= 45 and profit_factor >= 1.3 and expectancy > 0:
            verdict, reason = "Deploy", f"{n_trades}笔(需人工确认)"
        elif expectancy > 0:
            verdict, reason = "Refine", f"{n_trades}笔, 待优化"
        else:
            verdict, reason = "Abandon", f"期望值为负 {expectancy:.2f}%"
    elif n_trades >= 5:
        verdict, reason = ("Refine", f"{n_trades}笔(小样本)") if expectancy > 0 else ("Abandon", f"期望值为负 {expectancy:.2f}%")
    elif n_trades >= 2:
        if expectancy > 0 and profit_factor > 1.5:
            verdict, reason = "Refine", f"{n_trades}笔(极小样本)"
        else:
            verdict, reason = "Abandon", f"仅{n_trades}笔"
    else:
        verdict, reason = "Abandon", "无交易信号"

    return {
        "total_trades": n_trades,
        "win_count": len(wins), "loss_count": len(losses),
        "win_rate": round(win_rate, 1), "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "sharpe_ratio": portfolio_metrics.get("sharpe_ratio", 0),
        "max_drawdown": portfolio_metrics.get("max_drawdown", 0),
        "total_return": portfolio_metrics.get("total_return", 0),
        "verdict": verdict, "verdict_reason": reason,
    }