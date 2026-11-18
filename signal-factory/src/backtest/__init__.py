"""回测层 — Qlib 适配 + 信号生成 + Gate 1 评分 + 交叉验证 + 执行现实性门禁"""
from src.backtest.adapter import init_qlib, run_backtest
from src.backtest.signal_generator import generate_signal, list_strategies, get_strategy_info
from src.backtest.scorer import score_gate1
from src.backtest.cross_validate import cross_validate, time_window_validate, parameter_sensitivity
from src.backtest.execution_gate import ExecutionGate, RealismConfig, load_st_calendar, build_limit_maps

__all__ = [
    "init_qlib",
    "run_backtest",
    "generate_signal",
    "list_strategies",
    "get_strategy_info",
    "score_gate1",
    "cross_validate",
    "time_window_validate",
    "parameter_sensitivity",
    "ExecutionGate",
    "RealismConfig",
    "load_st_calendar",
    "build_limit_maps",
]