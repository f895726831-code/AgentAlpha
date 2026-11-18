"""
T3 超跌反弹策略 — RSI 超卖后缩量止跌反弹。

参考实现:
  - signal-factory: oversold_bounce (RSI<30 + 缩量 + 止跌阳线)
  - risk-policy.json: left_priority 下 T3 权重 1.0, right_priority 下禁用

策略逻辑:
  1. RSI 下穿超卖线 (默认 30) — 触发超卖信号
  2. 成交量萎缩 (当日 ≤ 5 日均量 × volume_shrink_ratio) — 抛压衰竭
  3. 止跌确认 (当日收阳, close > 前日 close) — 不再创新低

用法:
    from strategies.oversold_bounce import OversoldBounceStrategy
    strategy = OversoldBounceStrategy(StrategyParams(
        strategy_type="T3_oversold_bounce",
        mode="normal", bias="left_priority",
        filters={"rsi_period": 14, "rsi_oversold": 30},
    ))
"""

from typing import Any, Dict, Optional

from .base import BaseStrategy, Signal, StrategyParams


class OversoldBounceStrategy(BaseStrategy):
    """
    T3 超跌反弹策略 — 左侧交易, 博弈恐慌后的技术修复。

    参数 (StrategyParams.filters):
        rsi_period: int = 14
            RSI 计算周期。

        rsi_oversold: float = 30
            超卖阈值。RSI 下穿此值触发信号。

        volume_shrink_ratio: float = 0.6
            缩量比例。当日成交量 / 5 日均量 ≤ 此值。
            0.6 = 缩量 40%, 恐慌盘出清。

        stop_loss_pct: float = 3.0
            止损百分比 (超跌反弹止损应偏紧, 因为逆趋势)。

        take_profit_pct: float = 8.0
            止盈百分比 (超跌反弹空间有限, 见好就收)。

        confirmation_bars: int = 1
            止跌确认 K 线数。默认 1 (当日收阳即可)。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "rsi_period": 14,
        "rsi_oversold": 30,
        "volume_shrink_ratio": 0.6,
        "stop_loss_pct": 3.0,
        "take_profit_pct": 8.0,
        "confirmation_bars": 1,
    }

    @property
    def strategy_type(self) -> str:
        return "T3_oversold_bounce"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查超跌反弹条件。

        市场数据要求:
            close, open, high, low, volume,
            avg_volume_5d,
            rsi_14 (或 rsi_{period}),
            rsi_14_prev (前一日 RSI),
            prev_close

        条件:
            1. RSI 下穿超卖线: prev_RSI ≥ oversold > 当前 RSI
            2. 缩量: volume ≤ avg_vol_5d × volume_shrink_ratio
            3. 止跌阳线: close > prev_close (不再创新低)
        """
        rsi_period = self._get_filter("rsi_period")
        oversold = self._get_filter("rsi_oversold")
        vol_shrink = self._get_filter("volume_shrink_ratio")

        # ── RSI 数据 ──
        rsi_key = f"rsi_{rsi_period}"
        rsi_prev_key = f"{rsi_key}_prev"

        rsi_now = market_data.get(rsi_key, None)
        rsi_prev = market_data.get(rsi_prev_key, None)

        if rsi_now is None:
            return False

        # ── 条件 1: RSI 下穿超卖线 ──
        # 前一日 RSI 仍在超卖线之上 (或不存在), 当日跌破
        if rsi_now >= oversold:
            return False

        if rsi_prev is not None and rsi_prev < oversold:
            return False  # 之前已经超卖, 非首次下穿

        # ── 条件 2: 缩量确认 ──
        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)
        if avg_vol_5d > 0 and volume > avg_vol_5d * vol_shrink:
            return False  # 放量下跌, 恐慌未结束

        # ── 条件 3: 止跌阳线 ──
        close = market_data.get("close", 0)
        prev_close = market_data.get("prev_close", 0)
        if prev_close > 0 and close <= prev_close:
            return False

        # ── 附加条件: 非跌停 ──
        prev_close_val = prev_close if prev_close > 0 else close
        if close <= prev_close_val * 0.9:
            return False  # 跌停板, 不接飞刀

        return True

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
            win_rate=0.45,  # 超跌反弹胜率偏低, 靠盈亏比取胜
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_5d, 1),
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "generated_by": "TiMi_OversoldBounceStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        conf = self.params.confidence_threshold

        rsi_period = self._get_filter("rsi_period")
        rsi_key = f"rsi_{rsi_period}"
        rsi_now = market_data.get(rsi_key, 50)

        # RSI 越低 → 反弹概率越大
        if rsi_now <= 20:
            conf += 0.20  # 极度超卖
        elif rsi_now <= 25:
            conf += 0.10
        elif rsi_now <= 30:
            conf += 0.05

        # 缩量程度
        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)
        if avg_vol_5d > 0:
            vol_ratio = volume / avg_vol_5d
            if vol_ratio <= 0.4:
                conf += 0.15  # 极致缩量
            elif vol_ratio <= 0.6:
                conf += 0.05

        # 下影线
        high = market_data.get("high", 0)
        low = market_data.get("low", 0)
        close = market_data.get("close", 0)
        if high > low:
            lower_wick = (close - low) / (high - low)
            if lower_wick > 0.6:
                conf += 0.10  # 长下影线, 抄底资金进场

        return max(0.0, min(conf, 1.0))


def make_oversold_bounce(mode: str = "normal", bias: str = "left_priority", **overrides) -> OversoldBounceStrategy:
    filters = {**OversoldBounceStrategy.DEFAULT_FILTERS, **overrides}
    return OversoldBounceStrategy(StrategyParams(
        strategy_type="T3_oversold_bounce", mode=mode, bias=bias, filters=filters,
    ))