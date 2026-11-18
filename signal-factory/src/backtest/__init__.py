"""回测层 — Qlib 适配、信号生成、Gate 1 评分、交叉验证、执行现实性门禁

说明：本包的 ``adapter`` 依赖 ``qlib``。为避免仅使用信号生成/评分时也被
强制安装 qlib，这里采用惰性导入（PEP 562）：``from src.backtest.signal_generator
import ...`` 不会触发 ``adapter`` 的 qlib 依赖；只有访问 ``init_qlib`` /
``run_backtest`` 等名字时才会真正导入 ``adapter``。
"""

from typing import Any

_LAZY = {
    # adapter（依赖 qlib）
    "init_qlib": ("src.backtest.adapter", "init_qlib"),
    "run_backtest": ("src.backtest.adapter", "run_backtest"),
    # signal_generator
    "generate_signal": ("src.backtest.signal_generator", "generate_signal"),
    "list_strategies": ("src.backtest.signal_generator", "list_strategies"),
    "get_strategy_info": ("src.backtest.signal_generator", "get_strategy_info"),
    # scorer
    "score_gate1": ("src.backtest.scorer", "score_gate1"),
    # cross_validate
    "cross_validate": ("src.backtest.cross_validate", "cross_validate"),
    "time_window_validate": ("src.backtest.cross_validate", "time_window_validate"),
    "parameter_sensitivity": ("src.backtest.cross_validate", "parameter_sensitivity"),
    # execution_gate
    "ExecutionGate": ("src.backtest.execution_gate", "ExecutionGate"),
    "RealismConfig": ("src.backtest.execution_gate", "RealismConfig"),
    "load_st_calendar": ("src.backtest.execution_gate", "load_st_calendar"),
    "build_limit_maps": ("src.backtest.execution_gate", "build_limit_maps"),
}

__all__ = list(_LAZY)


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
