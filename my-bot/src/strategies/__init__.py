"""策略包公开 API"""

from .base import BaseStrategy, Signal, StrategyParams, STRATEGY_TYPES, STRATEGY_BIAS_COMPAT
from .multipliers import Multipliers
from .position_sizer import PositionSizer, Position, Account, compute_position
from .breakout import BreakoutStrategy, make_breakout
from .pullback import PullbackStrategy, make_pullback
from .vcp import VCPStrategy, make_vcp
from .oversold_bounce import OversoldBounceStrategy, make_oversold_bounce
from .golden_cross import GoldenCrossStrategy, make_golden_cross
from .earnings import EarningsStrategy, make_earnings
from .capital import CapitalStrategy, make_capital
from .theme import ThemeStrategy, make_theme
from .canslim import CanslimStrategy, make_canslim
from .compiler import StrategyCompiler, CompileResult, STRATEGY_CLASS_MAP
from .serializer import StrategySerializer
from .executor import OnlineExecutor, ExecutionResult, TickResult
from .anomaly_detector import AnomalyDetector, Anomaly
from .fallback import FallbackHandler, FallbackResult
from .timi_adapter import TiMiAdapter, AdapterResult

__all__ = [
    # Base
    "BaseStrategy",
    "Signal",
    "StrategyParams",
    "STRATEGY_TYPES",
    "STRATEGY_BIAS_COMPAT",
    # Multipliers
    "Multipliers",
    # Position Sizer
    "PositionSizer",
    "Position",
    "Account",
    "compute_position",
    # Strategies
    "BreakoutStrategy",
    "make_breakout",
    "PullbackStrategy",
    "make_pullback",
    "VCPStrategy",
    "make_vcp",
    "OversoldBounceStrategy",
    "make_oversold_bounce",
    "GoldenCrossStrategy",
    "make_golden_cross",
    "EarningsStrategy",
    "make_earnings",
    "CapitalStrategy",
    "make_capital",
    "ThemeStrategy",
    "make_theme",
    "CanslimStrategy",
    "make_canslim",
    # Compiler + Serializer
    "StrategyCompiler",
    "CompileResult",
    "STRATEGY_CLASS_MAP",
    "StrategySerializer",
    # Executor
    "OnlineExecutor",
    "ExecutionResult",
    "TickResult",
    # Anomaly Detector + Fallback
    "AnomalyDetector",
    "Anomaly",
    "FallbackHandler",
    "FallbackResult",
    # TiMi Adapter
    "TiMiAdapter",
    "AdapterResult",
]