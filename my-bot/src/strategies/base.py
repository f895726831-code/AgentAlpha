"""
策略基类 — TiMi 离线编译 + 在线执行的核心抽象。

所有策略模板继承 BaseStrategy，实现两个核心方法:
  - check_conditions(market_data) → 判断是否触发
  - generate_signal(market_data) → 生成标准化 Signal

用法:
    from strategies.base import Signal, StrategyParams, BaseStrategy

    class MyBreakout(BaseStrategy):
        def check_conditions(self, market_data) -> bool:
            return (
                market_data["close"] > market_data["high_60d"] and
                market_data["volume"] > market_data["avg_volume_5d"] * self.params.filters.get("vol_ratio", 1.5)
            )

        def generate_signal(self, market_data):
            return Signal(
                symbol=market_data["symbol"],
                strategy_type=self.params.strategy_type,
                direction="long",
                confidence=self.params.confidence_threshold,
                trigger_price=market_data["close"],
                stop_loss=market_data["close"] * 0.95,
            )
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Signal:
    """标准化交易信号 — 策略模板的输出"""

    symbol: str                                    # 股票代码
    strategy_type: str                             # "T1_breakout" | "T2_pullback" | ...
    direction: str = "long"                        # "long" | "short"
    confidence: float = 0.5                        # 0-1
    trigger_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: List[float] = field(default_factory=list)
    edge: float = 0.0                              # Kelly 期望优势
    win_rate: float = 0.5                          # Kelly 胜率估计
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy_type": self.strategy_type,
            "direction": self.direction,
            "confidence": self.confidence,
            "trigger_price": self.trigger_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "edge": self.edge,
            "win_rate": self.win_rate,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Signal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StrategyParams:
    """策略参数 — LLM 编译输出的可序列化配置"""

    strategy_type: str                             # "T1_breakout" | ...
    mode: str = "normal"                           # normal / earnings_season / risk_off
    bias: str = "balanced"                         # right_priority / left_priority / balanced
    active: bool = True                            # 是否激活
    filters: Dict[str, Any] = field(default_factory=dict)
    confidence_threshold: float = 0.5               # 最低置信度 (低于此值不产生信号)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_type": self.strategy_type,
            "mode": self.mode,
            "bias": self.bias,
            "active": self.active,
            "filters": self.filters,
            "confidence_threshold": self.confidence_threshold,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StrategyParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class BaseStrategy(ABC):
    """
    策略基类 — 所有 TiMi 策略模板的抽象基类。

    子类只需实现两个方法:
      - check_conditions(market_data): 判断触发条件
      - generate_signal(market_data): 生成标准化信号

    生命周期:
      1. 离线: LLM Agent → JSON → StrategyCompiler → 实例化 → serialize → disk
      2. 在线: disk → deserialize → check_conditions() → generate_signal() → Signal
    """

    params: StrategyParams

    def __init__(self, params: StrategyParams = None):
        self.params = params or StrategyParams(strategy_type=self.strategy_type)

    @property
    @abstractmethod
    def strategy_type(self) -> str:
        """策略类型标识 (如 "T1_breakout")"""
        ...

    @abstractmethod
    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查是否满足触发条件。

        Args:
            market_data: 单只股票的市场数据字典，至少包含:
                symbol, close, open, high, low, volume,
                以及各策略所需的衍生指标

        Returns:
            True 如果满足所有触发条件
        """
        ...

    @abstractmethod
    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成标准化交易信号。

        Args:
            market_data: 同 check_conditions

        Returns:
            Signal 对象，或 None (如果不满足最低置信度)
        """
        ...

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        """
        计算信号置信度 (0-1)。

        默认实现: 基于条件满足程度的简单评分。
        子类可覆盖以实现策略特定的置信度计算。
        """
        return self.params.confidence_threshold

    def is_active_for_mode(self, mode: str, bias: str) -> bool:
        """
        判断当前模式/bias 下是否应该激活。

        默认: 只要 params.active=True 且参数 mode/bias 匹配即激活。
        """
        if not self.params.active:
            return False
        if self.params.mode != "normal" and mode != self.params.mode:
            return False
        if self.params.bias != "balanced" and bias != self.params.bias:
            return False
        return True

    def to_params_dict(self) -> Dict[str, Any]:
        """导出参数为可序列化字典"""
        return self.params.to_dict()

    @classmethod
    def from_params_dict(cls, d: Dict[str, Any]) -> "BaseStrategy":
        """从参数字典恢复策略实例"""
        params = StrategyParams.from_dict(d)
        return cls(params)


# ─── 策略类型常量 ───

STRATEGY_TYPES = {
    "T1_breakout": "强势突破",
    "T2_pullback": "回调低吸",
    "T3_oversold_bounce": "超跌反弹",
    "T4_golden_cross": "均线金叉",
    "VCP_breakout": "VCP收缩突破",
    "T5_earnings": "财报驱动",
    "T6_capital": "资金驱动",
    "canslim": "CANSLIM成长股",
    "theme": "题材驱动",
}

# 策略类型 → bias 兼容性
# right_priority 禁用 T3
# left_priority 禁用 T1, T4, VCP
STRATEGY_BIAS_COMPAT = {
    "T1_breakout": ["right_priority", "balanced"],
    "T2_pullback": ["right_priority", "left_priority", "balanced"],
    "T3_oversold_bounce": ["left_priority", "balanced"],
    "T4_golden_cross": ["right_priority", "balanced"],
    "VCP_breakout": ["right_priority", "balanced"],
    "T5_earnings": ["right_priority", "left_priority", "balanced"],
    "T6_capital": ["right_priority", "left_priority", "balanced"],
    "canslim": ["right_priority", "left_priority", "balanced"],
    "theme": ["right_priority", "left_priority", "balanced"],
}