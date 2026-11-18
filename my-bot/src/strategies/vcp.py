"""
VCP 收缩突破策略 — 波动收缩形态 (Volatility Contraction Pattern) 末端放量突破。

参考实现:
  - quant-signal-factory: vcp_breakout (3 阶段收缩 + 放量突破)
  - Mark Minervini VCP 理论: 振幅逐段收缩 → 供给耗尽 → 放量突破

策略逻辑:
  1. 价格在过去 60 日呈收缩形态: 后 30 日波动率 < 前 30 日 × contraction_ratio
  2. 当日收盘突破 60 日高点 (突破收缩区间)
  3. 成交量放大确认 (量比 ≥ breakout_volume_ratio)

TiMi 离线编译参数 (LLM 可调):
  contraction_periods: 收缩阶段数 (默认 2, 即前后各 30 日)
  contraction_ratio: 后段波动/前段波动上限 (默认 0.5)
  breakout_volume_ratio: 突破量比下限 (默认 1.5)
  stop_loss_pct / take_profit_pct

用法:
    from strategies.vcp import VCPStrategy
    strategy = VCPStrategy(StrategyParams(
        strategy_type="VCP_breakout",
        mode="normal", bias="right_priority",
        filters={"contraction_ratio": 0.5, "breakout_volume_ratio": 1.5},
    ))
"""

from typing import Any, Dict, Optional
import math

from .base import BaseStrategy, Signal, StrategyParams


class VCPStrategy(BaseStrategy):
    """
    VCP 收缩突破策略。

    参数 (StrategyParams.filters):
        lookback_days: int = 60
            形态观察总周期。前 half 与后 half 的波动率比较。

        contraction_ratio: float = 0.5
            收缩比例上限。后半段波动率 / 前半段波动率 ≤ 此值。
            0.5 = 波动率减半才视为有效收缩。

        breakout_volume_ratio: float = 1.5
            突破量比下限。当日成交量 / 5 日均量 ≥ 此值。

        stop_loss_pct: float = 5.0
        take_profit_pct: float = 15.0

        min_history_bars: int = 60
            最小历史 K 线数。数据不足则跳过。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "lookback_days": 60,
        "contraction_ratio": 0.5,
        "breakout_volume_ratio": 1.5,
        "stop_loss_pct": 5.0,
        "take_profit_pct": 15.0,
        "min_history_bars": 60,
    }

    @property
    def strategy_type(self) -> str:
        return "VCP_breakout"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查 VCP 收缩突破条件。

        市场数据要求:
            close, volume, avg_volume_5d,
            price_history (60 日收盘价, 不含当日)

        条件:
            1. 收缩确认: 后段波动率 ≤ 前段 × contraction_ratio
            2. 当日收盘突破 lookback 区间高点
            3. 放量确认
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)

        lookback = self._get_filter("lookback_days")
        contraction = self._get_filter("contraction_ratio")
        vol_ratio_min = self._get_filter("breakout_volume_ratio")
        min_bars = self._get_filter("min_history_bars")

        # ── 历史数据 ──
        history = market_data.get("price_history") or []
        if len(history) < min_bars:
            return False

        # 前后各半
        half = lookback // 2
        first_half = history[-lookback : -half] if len(history) >= lookback else history[:-half]
        second_half = history[-half:]

        if len(first_half) < 10 or len(second_half) < 10:
            return False

        # ── 条件 1: 收缩确认 ──
        first_std = self._std(first_half)
        second_std = self._std(second_half)

        if first_std <= 0:
            return False

        vol_ratio = second_std / first_std
        if vol_ratio > contraction:
            return False  # 收缩不够

        # ── 条件 2: 当日突破 lookback 高点 ──
        hist_high = max(history[-lookback:])
        if close < hist_high:
            return False

        # ── 条件 3: 放量确认 ──
        avg_vol_5d = market_data.get("avg_volume_5d", 0)
        if avg_vol_5d > 0 and volume < avg_vol_5d * vol_ratio_min:
            return False

        return True

    @staticmethod
    def _std(values: list) -> float:
        """计算标准差 (避免 numpy 依赖)"""
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        return math.sqrt(variance)

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")

        trigger_price = close
        stop_loss = trigger_price * (1 - stop_loss_pct / 100)
        take_profit = [trigger_price * (1 + take_profit_pct / 100)]

        confidence = self.get_confidence(market_data)

        risk = stop_loss_pct / 100
        reward = take_profit_pct / 100
        edge = reward / max(risk, 0.001)

        return Signal(
            symbol=str(market_data.get("symbol", "")),
            strategy_type=self.strategy_type,
            direction="long",
            confidence=confidence,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            edge=edge,
            win_rate=0.55,  # VCP 胜率通常高于普通突破
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_5d, 1),
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "generated_by": "TiMi_VCPStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        conf = self.params.confidence_threshold

        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)

        # 量比评分
        if avg_vol_5d > 0:
            vol_ratio = volume / avg_vol_5d
            if vol_ratio >= 3.0:
                conf += 0.20  # 极端放量: 机构进场信号
            elif vol_ratio >= 2.0:
                conf += 0.15
            elif vol_ratio >= 1.5:
                conf += 0.05

        # 收缩质量: 收缩比越小越好
        lookback = self._get_filter("lookback_days")
        history = market_data.get("price_history") or []
        if len(history) >= lookback:
            half = lookback // 2
            first_std = self._std(history[-lookback:-half])
            second_std = self._std(history[-half:])
            if first_std > 0:
                ratio = second_std / first_std
                if ratio <= 0.3:
                    conf += 0.15  # 极度收缩
                elif ratio <= 0.5:
                    conf += 0.05

        return max(0.0, min(conf, 1.0))


def make_vcp(mode: str = "normal", bias: str = "right_priority", **overrides) -> VCPStrategy:
    filters = {**VCPStrategy.DEFAULT_FILTERS, **overrides}
    return VCPStrategy(StrategyParams(
        strategy_type="VCP_breakout", mode=mode, bias=bias, filters=filters,
    ))